"""Process-local observations of existing loops; never an execution authority."""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
import os
import threading
import time
import uuid

from cluster.infrastructure.request_diagnostics import mapping, timestamp


class ProgressObservation:
    """Bounded, read-only snapshots. No probing, recovery, or job mutation.

    Monotonic freshness is local to this serving instance. Durable child state
    is explicitly historical and does not establish process/Worker liveness.
    """

    def __init__(self, *, read_job=None, cached_workers=None, monotonic=time.monotonic,
                 now=None, stale_after_s=10.0, capacity=512):
        self.instance_id = uuid.uuid4().hex
        self.pid = os.getpid()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._clock = monotonic
        self._now = now or (lambda: datetime.now(timezone.utc).isoformat())
        self._read_job = read_job
        self._cached_workers = cached_workers
        self.stale_after_s = stale_after_s
        self.capacity = capacity
        self._lock = threading.Lock()
        self._loops = OrderedDict()

    def started(self, key):
        with self._lock:
            if key not in self._loops and len(self._loops) >= self.capacity:
                old = next((k for k, v in self._loops.items() if not v['active']), None)
                if old is None:
                    return  # observation unavailable; execution is unaffected
                del self._loops[old]
            self._loops[key] = dict(active=True, state='STARTING', error_code=None,
                                    last_tick_at=None, last_success_at=None, tick_mono=None)

    def tick(self, key):
        with self._lock:
            if key in self._loops:
                self._loops[key].update(last_tick_at=self._now(), tick_mono=self._clock(), state='TICKING')

    def success(self, key, *, gate_blocked=False):
        with self._lock:
            if key in self._loops:
                self._loops[key].update(last_success_at=self._now(),
                                         state='GATE_BLOCKED' if gate_blocked else 'WAITING')

    def failed(self, key):
        with self._lock:
            if key in self._loops:
                self._loops[key].update(state='ERROR', error_code='PROGRESS_LOOP_ERROR')

    def stopped(self, key):
        with self._lock:
            if key in self._loops:
                value = self._loops[key]
                value['active'] = False
                if value['state'] != 'ERROR':
                    value['state'] = 'STOPPED'

    def snapshot(self, key, manifest=None, *, external=False, events=()):
        manifest = mapping(manifest)
        with self._lock:
            loop = dict(self._loops.get(key) or {}) if not external else {}
        age = max(0.0, self._clock() - loop['tick_mono']) if loop.get('tick_mono') is not None else None
        freshness = 'UNKNOWN' if age is None else 'FRESH' if age <= self.stale_after_s else 'STALE'
        status = manifest.get('status')
        children = []
        dispatches = [e["at"] for e in events if e.get("type") == "campaign_attempt_started" and timestamp(e.get("at"))]
        for item in manifest.get('trials', manifest.get('cells', [])) or []:
            attempts = item.get('attempts') or []
            for attempt in attempts:
                # Campaign preflight timestamps are not dispatch evidence.
                if 'trials' in manifest and attempt.get('backend_job_id') and timestamp(attempt.get('started_at')):
                    dispatches.append(attempt['started_at'])
            if item.get('status') != 'running' or not attempts:
                continue
            attempt = attempts[-1]
            job_id = attempt.get('backend_job_id')
            job = {}
            if self._read_job and job_id and not external:
                try:
                    candidate = self._read_job(job_id)
                    config = mapping(candidate.get('config'))
                    bound = (candidate.get('suite_id') == attempt.get('suite_id') if 'trials' in manifest else
                             config.get('campaign_id') == manifest.get('campaign_id') and
                             config.get('campaign_cell_id') == item.get('campaign_cell_id') and
                             config.get('campaign_attempt_id') == attempt.get('attempt_id'))
                    if candidate.get('job_id') == job_id and bound:
                        job = candidate
                except (OSError, ValueError):
                    pass
            children.append({
                'trial_id': item.get('trial_id'),
                'cell_id': item.get('cell_id') or item.get('campaign_cell_id'),
                'attempt_id': attempt.get('attempt_id'), 'job_id': job_id,
                'status': job.get('status') or attempt.get('status') or 'UNKNOWN',
                'phase': job.get('phase') or 'UNKNOWN',
                'observed_at': timestamp(job.get('updated_at')),
                'source': 'durable_job' if job else 'durable_manifest',
                'process_liveness': 'UNKNOWN',
                'cleanup_status': mapping(job.get('resource_reservation')).get('status') or attempt.get('cleanup_status') or 'UNKNOWN',
                'nodes': list(job.get('nodes') or item.get('node_set') or []),
            })
        phases = {c['phase'] for c in children} | {manifest.get('phase')}
        child_states = {c['status'] for c in children}
        display = 'UNKNOWN'
        if loop.get('state') == 'ERROR':
            display = 'LOOP_ERROR'
        elif status in {'completed', 'partial', 'failed', 'cancelled'}:
            display = str(status).upper()
        elif status == 'paused':
            display = 'PAUSED'
        elif not loop.get('active') or freshness != 'FRESH':
            display = 'UNKNOWN'
        elif loop.get('state') == 'GATE_BLOCKED':
            display = 'GATE_BLOCKED'
        elif phases.intersection({'resource_cooldown', 'model_cooldown', 'cooldown'}) or self._cooling(manifest):
            display = 'COOLDOWN'
        elif phases.intersection({'cleanup', 'model_cleanup', 'cleaning_up', 'unloading', 'rpc_cleanup'}) or 'cleaning_up' in child_states:
            display = 'CLEANUP'
        elif 'waiting_resources' in child_states or 'waiting_resources' in phases:
            display = 'RESOURCE_WAIT'
        elif children:
            display = 'CHILD_ACTIVE'
        else:
            display = 'PROGRESSING'
        workers = {'source': 'existing_status_cache', 'freshness': 'UNKNOWN', 'nodes': []}
        if self._cached_workers and not external:
            workers = self._cached_workers()
            names = {name for child in children for name in child['nodes']}
            workers = {**workers, 'nodes': [v for v in workers.get('nodes', []) if v.get('name') in names]}
        return {
            'schema_version': 1, 'serving_instance_id': self.instance_id,
            'serving_pid': self.pid, 'serving_started_at': self.started_at,
            'server': 'RESPONDING', 'observed_at': self._now(),
            'owner_evidence': 'LOCAL_LOOP_OBSERVED' if loop else 'EXTERNAL_RUNNER' if external else 'UNKNOWN',
            'other_instances': 'UNKNOWN', 'loop_instance_id': self.instance_id if loop else None,
            'loop_state': loop.get('state', 'UNKNOWN'), 'loop_active': loop.get('active'),
            'last_tick_at': loop.get('last_tick_at'), 'last_success_at': loop.get('last_success_at'),
            'last_dispatch_at': max(dispatches, default=None),
            'dispatch_source': 'durable_attempt_start',
            'last_error_code': loop.get('error_code'), 'freshness': freshness,
            'age_s': age, 'stale_after_s': self.stale_after_s,
            'display_state': display, 'children': children, 'workers': workers,
            'request_progress': 'UNKNOWN',
        }

    def _cooling(self, manifest):
        until = timestamp(manifest.get('cooldown_not_before'))
        return bool(until and datetime.fromisoformat(until) > datetime.fromisoformat(self._now()))
