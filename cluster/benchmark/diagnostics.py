"""Request correlation and explicit, bounded post-run evidence collection.

No measurement retry, result rewrite, background polling or cleanup lives here.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from cluster.infrastructure.request_diagnostics import (
    DiagnosticJournal, HEADER, GAPS, identity, utc_now,
    mapping, clean_event, number, timestamp,
)

TERMINAL = {'completed', 'timeout', 'transport_error', 'stream_incomplete', 'worker_error'}


def owner_from_headers(headers):
    headers = {k.lower(): v for k, v in headers.items()}
    try:
        return {'owner_id': headers['x-cluster-resource-owner'],
                'lease_id': headers['x-cluster-resource-lease'],
                'fencing_epoch': int(headers['x-cluster-fencing-epoch'])}
    except (KeyError, ValueError):
        return None


class RunDiagnostics:
    def __init__(self, directory, run_id, config, cancel_event=None):
        self.run_id = run_id
        self.config = config
        self.attempt_id = (config.sweep or {}).get('attempt_id') or config.campaign_attempt_id or uuid.uuid4().hex
        self.cancel_event = cancel_event
        self._lock = threading.Lock()
        self._traces = {}
        self.journal = DiagnosticJournal(Path(directory) / 'diagnostics' / 'controller', on_tick=self.observe_cancel)
        self.journal.start()

    def begin(self, node, task, warmup, headers):
        sweep = self.config.sweep or {}
        owner = owner_from_headers(headers)
        value = identity({
            'diagnostic_id': uuid.uuid4().hex, 'run_id': self.run_id,
            'attempt_id': self.attempt_id, 'job_id': (owner or {}).get('owner_id', ''),
            'sweep_id': sweep.get('sweep_id', ''), 'campaign_id': self.config.campaign_id,
            'trial_id': sweep.get('trial_id', ''),
            'cell_id': sweep.get('cell_id', '') or self.config.campaign_cell_id,
            'node': node.name, 'scenario_id': task.scenario_id,
            'request_id': task.request_id, 'logical_request_id': task.logical_request_id,
            'replica_index': task.replica_index, 'warmup': warmup,
        })
        trace = RequestTrace(self.journal, value, owner, run=self)
        with self._lock:
            if len(self._traces) < 1024:
                self._traces[value['diagnostic_id']] = trace
            else:
                self.journal._active_overflow = True
        trace.emit('submitted')
        return trace

    def observe_cancel(self):
        # Observe the existing in-process cancellation flag on the writer's tick.
        # This does not cancel a socket, terminate a Worker or release a lease.
        if self.cancel_event is not None and self.cancel_event.is_set():
            with self._lock:
                traces = list(self._traces.values())
            for trace in traces:
                trace.cancel_observed()

    def finish(self, trace):
        with self._lock:
            self._traces.pop(trace.identity['diagnostic_id'], None)


class RequestTrace:
    def __init__(self, journal=None, value=None, owner=None, run=None):
        self.journal, self.identity, self.owner = journal, value, owner
        self.run = run
        self._lock = threading.Lock()
        self._finished = False
        self._cancelled = False

    def emit(self, stage, *, error_code=None):
        if self.run and stage in TERMINAL and self.run.cancel_event is not None and self.run.cancel_event.is_set():
            self.cancel_observed()
        with self._lock:
            if self.journal:
                self.journal.emit(self.identity, stage, owner=self.owner, error_code=error_code)
            if stage in TERMINAL:
                self._finished = True
        if self.run and stage in TERMINAL:
            self.run.finish(self)

    def cancel_observed(self):
        with self._lock:
            if not self._finished and not self._cancelled:
                self.journal.emit(self.identity, 'cancel_requested', owner=self.owner)
                self._cancelled = True

    def headers(self):
        return {HEADER: json.dumps(self.identity, separators=(',', ':'))} if self.identity else {}


def begin(config, node, task, warmup, headers):
    run = getattr(config, '_diagnostic_run', None)
    if run:
        try:
            return run.begin(node, task, warmup, headers)
        except (ValueError, TypeError, AttributeError):
            pass
    return RequestTrace()


def validate_evidence(value, wanted):
    wanted = identity(wanted)
    if not isinstance(value, dict) or value.get('schema_version') != 1 or value.get('identity') != wanted:
        raise ValueError('DIAGNOSTIC_IDENTITY_MISMATCH')
    events = value.get('events')
    if not isinstance(events, list) or len(events) > 32:
        raise ValueError('DIAGNOSTIC_EVIDENCE_INVALID')
    clean = [clean_event(row, wanted) for row in events]
    # Never combine replayed physical requests, even under the same header.
    if any(sum(row['stage'] == stage for row in clean) > 1 for stage in ('submitted', 'received')):
        raise ValueError('DIAGNOSTIC_PHYSICAL_REQUEST_AMBIGUOUS')
    gaps = value.get('gaps', [])
    if not isinstance(gaps, list) or len(gaps) > 32 or any(not isinstance(g, str) for g in gaps):
        raise ValueError('DIAGNOSTIC_EVIDENCE_INVALID')
    state = mapping(value.get('journal'))
    activity = {'observed_at': timestamp(state.get('observed_at')), 'source': 'diagnostic_journal',
                'unit': 'requests', 'scope': 'correlated_requests_only',
                'active_requests': number(state.get('active_requests')),
                'observed_lock_waiters': number(state.get('observed_lock_waiters')),
                'pending_events': number(state.get('pending_events'))}
    return {'schema_version': 1, 'identity': wanted, 'events': clean,
            'gaps': sorted(GAPS.intersection(gaps)), 'journal': activity}


def summarize_request(controller=None, worker=None, *, cleanup=None):
    """Pure additive diagnosis. No metrics, result edits, control or cause guesses."""
    if controller is None:
        return {'schema_version': 1, 'availability': 'NO_DIAGNOSTIC_RECORD',
                'cause': {'classification': 'INSUFFICIENT_EVIDENCE', 'code': 'LEGACY_OR_DISABLED'}}
    wanted = identity(controller['identity'])
    controller = validate_evidence(controller, wanted)
    worker = validate_evidence(worker, wanted) if worker is not None else {'events': [], 'gaps': ['WORKER_RECORD_NOT_ACQUIRED']}
    crows, wrows = controller['events'], worker['events']
    missing = set(controller['gaps']) | set(worker['gaps'])

    def duration(rows, start, end):
        first = next((r for r in rows if r['stage'] == start), None)
        last = next((r for r in rows if r['stage'] == end), None)
        if (first and last and first['instance_id'] == last['instance_id']
                and first['monotonic_s'] is not None and last['monotonic_s'] is not None
                and last['monotonic_s'] >= first['monotonic_s']):
            return {'available': True, 'seconds': last['monotonic_s'] - first['monotonic_s'], 'source': 'same_host_monotonic'}
        return {'available': False, 'seconds': None, 'reason': 'BOUNDARY_OR_SAME_INSTANCE_EVIDENCE_MISSING'}

    terminal = next((r for r in reversed(crows) if r['stage'] in TERMINAL), None)
    outcome = next((r['stage'] for r in reversed(wrows) if r['stage'] in {'generation_completed', 'exception'}), 'UNKNOWN')
    resources = next((r['resources'] for r in reversed(wrows) if 'resources' in r), None)
    if resources is None or not any(v['available'] for v in resources['values'].values()):
        missing.add('RESOURCE_OBSERVATION_UNAVAILABLE')
    cleanup_status = 'UNKNOWN'
    if cleanup is not None:
        if any(cleanup.get(k) != wanted[k] for k in ('run_id', 'job_id', 'attempt_id')):
            raise ValueError('DIAGNOSTIC_CLEANUP_IDENTITY_MISMATCH')
        if cleanup.get('status') in {'VERIFIED', 'UNCERTAIN', 'PENDING'}:
            cleanup_status = cleanup['status']
    if cleanup_status != 'VERIFIED':
        missing.add('OWNERSHIP_CLEANUP_NOT_VERIFIED')
    return {
        'schema_version': 1, 'identity': wanted, 'availability': 'RECORDED',
        'controller': {'last_stage': crows[-1]['stage'] if crows else 'UNKNOWN',
                       'last_observed_at': crows[-1]['at'] if crows else None,
                       'terminal_stage': terminal['stage'] if terminal else 'UNKNOWN',
                       'failure_code': terminal.get('error_code') if terminal else None},
        'worker': {'last_stage': wrows[-1]['stage'] if wrows else 'UNKNOWN',
                   'last_observed_at': wrows[-1]['at'] if wrows else None,
                   'observed_outcome': outcome,
                   'record_status': 'ACQUIRED' if wrows else 'WORKER_RECORD_NOT_ACQUIRED'},
        'worker_completion_found_after_controller_failure': bool(terminal and terminal['stage'] != 'completed' and outcome == 'generation_completed'),
        'durations': {'controller_ttft': duration(crows, 'transport_started', 'first_token_received'),
                      'worker_lock_wait': duration(wrows, 'lock_wait', 'lock_acquired'),
                      'worker_to_first_token': duration(wrows, 'inference_started', 'first_token_generated'),
                      'cross_host_latency': {'available': False, 'seconds': None, 'reason': 'CLOCK_SYNCHRONIZATION_NOT_ESTABLISHED'}},
        'resources': resources,
        'activity_at_recovery': worker.get('journal'),
        'cleanup': {'status': cleanup_status, 'scope': 'durable_job_ownership'},
        'missing_evidence': sorted(missing),
        'observations': [{'classification': 'CONFIRMED', 'code': terminal['stage']}] if terminal else [],
        'cause': {'classification': 'INSUFFICIENT_EVIDENCE', 'code': 'ROOT_CAUSE_NOT_ESTABLISHED'},
    }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, 'DIAGNOSTIC_REDIRECT_REFUSED', headers, fp)


def collect_worker_evidence(run_dir, wanted, node, headers, *, timeout=3.0, opener=None, cleanup=None):
    """Explicit one-shot GET, after measurement; no execution or retry.

    The caller must have resolved wanted from this run's Controller journal and
    ownership from its matching durable job. Every collection appends a new file.
    """
    wanted = identity(wanted)
    run_dir = Path(run_dir)
    if run_dir.name != wanted['run_id'] or node.name != wanted['node']:
        raise ValueError('DIAGNOSTIC_IDENTITY_MISMATCH')
    owner = owner_from_headers(headers)
    known = DiagnosticJournal(run_dir / 'diagnostics' / 'controller').read(wanted, owner=owner)
    if not known['events']:
        raise ValueError('DIAGNOSTIC_REQUEST_NOT_KNOWN')
    endpoint = urllib.parse.urlsplit(node.api_url)
    if endpoint.scheme not in {'http', 'https'} or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise ValueError('DIAGNOSTIC_ENDPOINT_INVALID')
    request = urllib.request.Request(
        f'{node.api_url}/cluster/diagnostics/{wanted["diagnostic_id"]}',
        headers={**headers, HEADER: json.dumps(wanted, separators=(',', ':'))})
    result = {'schema_version': 1, 'collected_at': utc_now(), 'identity': wanted,
              'status': 'WORKER_RECORD_NOT_ACQUIRED'}
    try:
        with (opener or urllib.request.build_opener(NoRedirect()).open)(request, timeout=min(max(timeout, .01), 5.0)) as response:
            raw = response.read(128*1024 + 1)
        if len(raw) > 128*1024:
            raise ValueError('DIAGNOSTIC_RESPONSE_TOO_LARGE')
        evidence = validate_evidence(json.loads(raw), wanted)
        result.update(status='ACQUIRED' if evidence['events'] else 'WORKER_RECORD_NOT_ACQUIRED', evidence=evidence)
    except ValueError:
        result['collection_error'] = 'INVALID_OR_MISMATCHED_EVIDENCE'
    except urllib.error.HTTPError as exc:
        result['collection_error'] = ('WORKER_DIAGNOSTICS_UNAVAILABLE' if exc.code == 404
                                      else 'COLLECTION_AUTH_FAILED' if exc.code in {401, 403}
                                      else 'COLLECTION_HTTP_ERROR')
    except (OSError, urllib.error.URLError):
        result['collection_error'] = 'COLLECTION_TRANSPORT_ERROR'
    result['summary'] = summarize_request(known, result.get('evidence'), cleanup=cleanup)
    if result.get('collection_error'):
        result['summary']['missing_evidence'].append(result['collection_error'])
    destination = run_dir / 'diagnostics' / 'collections'
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Fixed, exclusive slots give a hard cap even with concurrent collectors.
    encoded = json.dumps(result, ensure_ascii=True).encode()
    if len(encoded) > 128 * 1024:
        raise ValueError('DIAGNOSTIC_COLLECTION_TOO_LARGE')
    for slot in range(256):
        path = destination / f'{slot:03d}.json'
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encoded)
        return result
    raise ValueError('DIAGNOSTIC_COLLECTION_LIMIT')


def find_identity(run_dir, diagnostic_id):
    """Read only four fixed, bounded local segments. No caller-controlled filename."""
    found = None
    for index in range(4):
        path = Path(run_dir) / 'diagnostics' / 'controller' / (f'events.{index}.jsonl' if index else 'events.jsonl')
        if path.is_symlink():
            continue
        try:
            with path.open('rb') as stream:
                raw = stream.read(1024 * 1024 + 1)
        except FileNotFoundError:
            continue
        if len(raw) > 1024 * 1024:
            continue
        for line in raw.splitlines():
            try:
                row = json.loads(line)
                value = identity(mapping(row).get('identity'))
            except (ValueError, TypeError):
                continue
            if value['diagnostic_id'] == diagnostic_id:
                if found is not None and value != found:
                    raise ValueError('DIAGNOSTIC_IDENTITY_MISMATCH')
                found = value
    if found is None:
        raise ValueError('DIAGNOSTIC_REQUEST_NOT_KNOWN')
    return found


def job_context(wanted, config, job):
    """Validate a saved run/job/attempt link before deriving historical ownership."""
    sweep = mapping(config.get('sweep'))
    if (not config.get('suite_id') or config['suite_id'] != job.get('suite_id')
            or wanted['job_id'] != job.get('id') or wanted['node'] not in job.get('nodes', [])):
        raise ValueError('DIAGNOSTIC_JOB_MISMATCH')
    expected = {'sweep_id': sweep.get('sweep_id', ''), 'trial_id': sweep.get('trial_id', ''),
                'cell_id': sweep.get('cell_id', '') or config.get('campaign_cell_id', ''),
                'campaign_id': config.get('campaign_id', '')}
    attempt = sweep.get('attempt_id') or config.get('campaign_attempt_id')
    if any(wanted[k] != v for k, v in expected.items()) or (attempt and wanted['attempt_id'] != attempt):
        raise ValueError('DIAGNOSTIC_JOB_MISMATCH')
    reservation = mapping(job.get('resource_reservation'))
    if reservation.get('owner_job_id') != wanted['job_id']:
        raise ValueError('DIAGNOSTIC_JOB_MISMATCH')
    headers = {'X-Cluster-Resource-Owner': wanted['job_id'],
               'X-Cluster-Resource-Lease': reservation['lease_id'],
               'X-Cluster-Fencing-Epoch': str(reservation['fencing_epoch'])}
    cleanup = {'run_id': wanted['run_id'], 'job_id': wanted['job_id'], 'attempt_id': wanted['attempt_id'],
               'status': 'VERIFIED' if reservation.get('status') == 'released' and reservation.get('cleanup_verified') is True
               else 'UNCERTAIN' if reservation.get('status') == 'quarantined' else 'PENDING'}
    return headers, cleanup


def latest_worker_evidence(run_dir, wanted):
    """Offline fixed-slot scan. Failed collections never erase earlier evidence."""
    for slot in reversed(range(256)):
        path = Path(run_dir) / 'diagnostics' / 'collections' / f'{slot:03d}.json'
        if path.is_symlink():
            continue
        try:
            with path.open('rb') as stream:
                raw = stream.read(128 * 1024 + 1)
        except FileNotFoundError:
            continue
        if len(raw) > 128 * 1024:
            continue
        try:
            value = json.loads(raw)
            if mapping(value).get('identity') != wanted or value.get('status') != 'ACQUIRED':
                continue
            return validate_evidence(value.get('evidence'), wanted)
        except ValueError:
            continue
    return None

def main():
    parser = argparse.ArgumentParser(description='Explicit one-request diagnostic recovery; never retries inference')
    parser.add_argument('--results-root', required=True, type=Path)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--diagnostic-id', required=True)
    parser.add_argument('--inventory', required=True, type=Path)
    parser.add_argument('--jobs-dir', required=True, type=Path)
    parser.add_argument('--summary-only', action='store_true', help='Offline summary; no Worker connection')
    args = parser.parse_args()
    from cluster.domain.identifiers import validate_run_id
    from cluster.infrastructure.storage import FilesystemRunRepository, FilesystemJobRepository
    from cluster.clusterctl import load_nodes, select_nodes, worker_auth_headers
    validate_run_id(args.run_id)
    if not re.fullmatch('[0-9a-f]{32}', args.diagnostic_id):
        raise ValueError('DIAGNOSTIC_IDENTITY_INVALID')
    repo = FilesystemRunRepository(args.results_root)
    run_dir = repo._run_dir(args.run_id)
    if args.summary_only and not (run_dir / 'diagnostics/controller').exists():
        if not (run_dir / 'config.json').is_file():
            raise ValueError('DIAGNOSTIC_RUN_NOT_KNOWN')
        print(json.dumps(summarize_request()))
        return
    wanted = find_identity(run_dir, args.diagnostic_id)
    if wanted['run_id'] != args.run_id:
        raise ValueError('DIAGNOSTIC_IDENTITY_MISMATCH')
    headers, cleanup, job = {}, None, None
    if wanted['job_id']:
        job = FilesystemJobRepository(args.jobs_dir).read(wanted['job_id'])
        config = json.loads((run_dir / 'config.json').read_text())
        headers, cleanup = job_context(wanted, config, job)
    if args.summary_only:
        known = DiagnosticJournal(run_dir / 'diagnostics' / 'controller').read(wanted, owner=owner_from_headers(headers))
        print(json.dumps(summarize_request(known, latest_worker_evidence(run_dir, wanted), cleanup=cleanup)))
        return
    if job is None or job.get('status') not in {'completed', 'failed', 'cancelled'}:
        raise ValueError('DIAGNOSTIC_TERMINAL_JOB_REQUIRED')
    headers.update(worker_auth_headers())
    nodes = select_nodes(load_nodes(args.inventory, require_legacy_head=False), [wanted['node']])
    if len(nodes) != 1: raise ValueError('DIAGNOSTIC_NODE_NOT_KNOWN')
    value = collect_worker_evidence(run_dir, wanted, nodes[0], headers, cleanup=cleanup)
    print(json.dumps({'status': value['status'], 'diagnostic_id': args.diagnostic_id}))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({'status': 'UNAVAILABLE', 'error_code': 'DIAGNOSTIC_RECOVERY_FAILED'}))
        raise SystemExit(1) from None
