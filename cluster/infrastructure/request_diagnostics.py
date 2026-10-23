"""Bounded, best-effort lifecycle evidence. Never an execution/ownership authority."""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import queue
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from cluster.domain.errors import ErrorCode

ERROR_CODES = frozenset(code.value for code in ErrorCode)

SCHEMA_VERSION = 1
HEADER = "X-Cluster-Diagnostic-Identity"
MAX_EVENT_BYTES = 4096
GAPS = frozenset({
    "UNFLUSHED_CRASH_LOSS_POSSIBLE", "UNREADABLE_SEGMENT", "OVERSIZED_SEGMENT",
    "TRUNCATED_RECORD", "BUFFER_LOSS", "WRITE_FAILURE", "RETENTION_ROTATED",
    "PROCESS_RESTART", "PENDING_FLUSH", "WORKER_RECORD_NOT_ACQUIRED",
    "PRIOR_PROCESS_GAPS_UNKNOWN", "EVENT_LIMIT", "WRITER_UNAVAILABLE",
    "TRACKING_LIMIT", "CLOSED_JOURNAL",
})
STAGES = frozenset({
    'submitted', 'transport_started', 'response_started', 'first_token_received',
    'completed', 'timeout', 'transport_error', 'stream_incomplete', 'worker_error',
    'cancel_requested', 'received', 'input_preparation_started',
    'input_preparation_completed', 'lock_wait', 'lock_acquired', 'inference_started',
    'first_token_generated', 'generation_completed', 'exception', 'lock_released',
    'stream_closed', 'disconnect_observed',
})
IDENTITY_TEXT = ('run_id', 'attempt_id', 'job_id', 'sweep_id', 'campaign_id',
                 'trial_id', 'cell_id', 'node', 'scenario_id')


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def identity(value: Mapping[str, Any]) -> dict:
    """Closed allowlist; never permit payload text, paths or query strings."""
    if not isinstance(value, Mapping) or set(value) != set(IDENTITY_TEXT) | {
        'diagnostic_id', 'request_id', 'logical_request_id', 'replica_index', 'warmup'
    }:
        raise ValueError('DIAGNOSTIC_IDENTITY_INVALID')
    result = dict(value)
    if not isinstance(result['diagnostic_id'], str) or not re.fullmatch(r'[0-9a-f]{32}', result['diagnostic_id']):
        raise ValueError('DIAGNOSTIC_IDENTITY_INVALID')
    for key in IDENTITY_TEXT:
        if not isinstance(result[key], str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{0,128}', result[key]):
            raise ValueError('DIAGNOSTIC_IDENTITY_INVALID')
    if not result['run_id'] or not result['attempt_id'] or not result['node']:
        raise ValueError('DIAGNOSTIC_IDENTITY_INVALID')
    for key in ('request_id', 'logical_request_id', 'replica_index'):
        if type(result[key]) is not int or abs(result[key]) > 1_000_000:
            raise ValueError('DIAGNOSTIC_IDENTITY_INVALID')
    if type(result['warmup']) is not bool:
        raise ValueError('DIAGNOSTIC_IDENTITY_INVALID')
    return result


def owner_key(context: Mapping[str, Any] | None) -> str:
    selected = {k: (context or {}).get(k) for k in ('owner_id', 'lease_id', 'fencing_epoch')}
    return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()


def number(value):
    try:
        return value if type(value) in (float, int) and math.isfinite(value) and 0 <= value <= 1e30 else None
    except OverflowError:
        return None


def mapping(value):
    return value if isinstance(value, Mapping) else {}


def timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return value if parsed.tzinfo is not None else None
    except ValueError:
        return None


def resource_observation(raw: Mapping[str, Any] | None) -> dict:
    """Freshness is reported by the sampling host, never cross-host clock math."""
    raw = mapping(raw)
    sampled = timestamp(raw.get('sampled_at'))
    age = number(raw.get('telemetry_cache_age_s'))
    interval = number(raw.get('telemetry_collection_interval_s'))
    fresh = (sampled is not None and age is not None and interval is not None
             and 0 <= age <= max(3 * interval, 3.0))
    memory, swap = mapping(raw.get('memory')), mapping(raw.get('swap'))
    process = mapping(raw.get('worker_process'))
    values = {
        'system_available_bytes': number(memory.get('available_mb')),
        'system_total_bytes': number(memory.get('total_mb')),
        'swap_used_bytes': number(swap.get('used_mb')),
        'worker_rss_bytes': number(process.get('rss_bytes')),
    }
    for key in ('system_available_bytes', 'system_total_bytes', 'swap_used_bytes'):
        if values[key] is not None:
            values[key] = number(values[key] * 1024 * 1024)
    return {
        'sampled_at': sampled, 'age_s': age,
        'source': 'worker_telemetry_cache', 'collection_interval_s': interval,
        'values': {k: {'value': v if fresh else None, 'unit': 'bytes',
                       'available': fresh and v is not None,
                       'reason': None if fresh and v is not None else 'STALE_OR_MISSING'} for k, v in values.items()},
        'process': {'pid': number(process.get('pid')) if fresh else None,
                    'started_at_unix': number(process.get('started_at_unix')) if fresh else None,
                    'available': fresh and number(process.get('started_at_unix')) is not None,
                    'source': 'psutil', 'unit': 'unix_seconds'},
        'model_process': {'available': False, 'reason': 'SEPARATE_PROCESS_NOT_OBSERVED'},
    }


def clean_resources(value):
    """Validate normalized remote observations without assigning new sample times."""
    value = mapping(value)
    sampled = timestamp(value.get('sampled_at'))
    age = number(value.get('age_s'))
    interval = number(value.get('collection_interval_s'))
    fresh = sampled is not None and age is not None and interval is not None and age <= max(3 * interval, 3.0)
    vals = mapping(value.get('values'))
    result = {'sampled_at': sampled, 'age_s': age,
              'collection_interval_s': interval, 'source': 'worker_telemetry_cache', 'values': {}}
    for key in ('system_available_bytes', 'system_total_bytes', 'swap_used_bytes', 'worker_rss_bytes'):
        metric = mapping(vals.get(key))
        number_value = number(metric.get('value')) if fresh and metric.get('available') is True else None
        result['values'][key] = {'value': number_value, 'available': number_value is not None,
                                 'unit': 'bytes', 'reason': None if number_value is not None else 'STALE_OR_MISSING'}
    proc = mapping(value.get('process'))
    available = fresh and proc.get('available') is True and number(proc.get('started_at_unix')) is not None
    result['process'] = {'pid': number(proc.get('pid')) if available else None,
                         'started_at_unix': number(proc.get('started_at_unix')) if available else None,
                         'available': available, 'source': 'psutil', 'unit': 'unix_seconds'}
    result['model_process'] = {'available': False, 'reason': 'SEPARATE_PROCESS_NOT_OBSERVED'}
    return result


def clean_event(row, wanted):
    if (not isinstance(row, dict) or row.get('identity') != wanted
            or not isinstance(row.get('stage'), str) or row['stage'] not in STAGES):
        raise ValueError('DIAGNOSTIC_IDENTITY_MISMATCH')
    if timestamp(row.get('at')) is None or not isinstance(row.get('instance_id'), str) or not re.fullmatch('[0-9a-f]{32}', row['instance_id']):
        raise ValueError('DIAGNOSTIC_EVIDENCE_INVALID')
    event = {k: row[k] for k in ('stage', 'at', 'instance_id')}
    event.update(identity=wanted, monotonic_s=number(row.get('monotonic_s')))
    if isinstance(row.get('error_code'), str) and row['error_code'] in ERROR_CODES:
        event['error_code'] = row['error_code']
    if 'resources' in row:
        event['resources'] = clean_resources(row['resources'])
    return event


class DiagnosticJournal:
    """One bounded internal writer thread; no per-token writes, fsync or control action.

    At most (files + 1) * max_bytes on disk, capacity * 4096 queued bytes.
    Reads scan those fixed files only. Every query reports possible unflushed loss.
    """
    def __init__(self, directory: Path, *, capacity=256, max_bytes=1024*1024, files=3, on_tick=None):
        self.directory = Path(directory)
        self.capacity = max(1, min(int(capacity), 256))
        self.max_bytes = max(4096, min(int(max_bytes), 1024*1024))
        self.files = max(1, min(int(files), 3))
        self.instance_id = uuid.uuid4().hex
        self.queue = queue.Queue(maxsize=self.capacity)
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()
        self.dropped = 0
        self.write_errors = 0
        self.rotations = 0
        self._active: dict[str, str] = {}
        self._active_overflow = False
        self._writer_unavailable = False
        self._prior_writer = False
        self._closed_events = 0
        self.on_tick = on_tick

    def start(self):
        if self._thread is None and not self._stop.is_set():
            try:
                self._thread = threading.Thread(target=self._run, name='request-diagnostic-writer', daemon=True)
                self._thread.start()
            except RuntimeError:
                self._writer_unavailable = True
                self._thread = None

    def emit(self, request_identity, stage, *, owner=None, resources=None, error_code=None):
        try:
            if self._stop.is_set():
                with self._lock:
                    self._closed_events += 1
                return
            rid = identity(request_identity)
            if stage not in STAGES:
                return
            record = {'schema_version': 1, 'identity': rid, 'stage': stage,
                      'at': utc_now(), 'monotonic_s': time.monotonic(),
                      'instance_id': self.instance_id, 'owner_key': owner_key(owner)}
            if isinstance(error_code, str) and error_code in ERROR_CODES:
                record['error_code'] = error_code
            with self._lock:
                record['gaps'] = (["BUFFER_LOSS"] if self.dropped else []) + (["WRITE_FAILURE"] if self.write_errors else [])
            # Resources must be produced locally, never copied from an HTTP payload.
            if resources is not None:
                record['resources'] = resource_observation(resources)
            encoded = json.dumps(record, separators=(',', ':')).encode() + b'\n'
            if len(encoded) > 4096:
                with self._lock: self.dropped += 1
                return
            with self._lock:
                key = rid['diagnostic_id']
                if stage in {'stream_closed', 'completed', 'timeout', 'transport_error', 'stream_incomplete', 'worker_error'}:
                    self._active.pop(key, None)
                elif (stage in {'submitted', 'received'} and len(self._active) < 1024) or key in self._active:
                    self._active[key] = stage
                elif stage in {'submitted', 'received'}:
                    self._active_overflow = True
            try:
                self.queue.put_nowait(encoded)
            except queue.Full:
                with self._lock: self.dropped += 1
        except Exception:
            # This optional recorder must never fail inference or mandatory journals.
            with self._lock: self.dropped += 1

    def _write(self, encoded):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.directory / 'events.jsonl'
        if path.is_symlink():
            raise OSError('diagnostic symlink')
        if path.exists() and path.stat().st_size + len(encoded) > self.max_bytes:
            for index in range(self.files, 0, -1):
                src = path if index == 1 else self.directory / f'events.{index-1}.jsonl'
                dst = self.directory / f'events.{index}.jsonl'
                if src.exists(): os.replace(src, dst)
            self.rotations += 1
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            remaining = memoryview(encoded)
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError("DIAGNOSTIC_SHORT_WRITE")
                remaining = remaining[written:]
        finally:
            os.close(fd)

    def _run(self):
        # One writer per fixed journal directory, including across processes.
        # Failure to acquire this optional writer never fails inference.
        fd = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._prior_writer = (self.directory / '.writer.lock').exists()
            fd = os.open(self.directory / '.writer.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._writer_unavailable = True
            if fd is not None:
                os.close(fd)
            return
        try:
            while not self._stop.is_set() or not self.queue.empty():
                if self.on_tick is not None:
                    try:
                        self.on_tick()
                    except Exception:
                        with self._lock:
                            self.dropped += 1
                try:
                    encoded = self.queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                try:
                    with self._io_lock:
                        self._write(encoded)
                except (OSError, ValueError):
                    with self._lock:
                        self.write_errors += 1
                finally:
                    self.queue.task_done()
        finally:
            os.close(fd)

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def status(self):
        with self._lock:
            stages = list(self._active.values())
            return {'schema_version': 1, 'mode': 'lifecycle', 'instance_id': self.instance_id,
                    'observed_at': utc_now(), 'source': 'diagnostic_journal', 'scope': 'correlated_requests_only',
                    'queue_capacity': self.capacity, 'max_event_bytes': 4096,
                    'max_disk_bytes': (self.files+1)*self.max_bytes,
                    'pending_events': self.queue.qsize(), 'dropped_events': self.dropped,
                    'write_errors': self.write_errors, 'rotations': self.rotations,
                    'writer_unavailable': self._writer_unavailable, 'closed_events': self._closed_events,
                    'active_requests': len(stages) if not self._active_overflow else None,
                    'observed_lock_waiters': stages.count('lock_wait') if not self._active_overflow else None,
                    'active_counts_complete': not self._active_overflow}

    def read(self, request_identity, *, owner=None):
        wanted = identity(request_identity)
        rows, gaps = [], {'UNFLUSHED_CRASH_LOSS_POSSIBLE'}
        with self._io_lock:
            for index in range(self.files, -1, -1):
                path = self.directory / (f'events.{index}.jsonl' if index else 'events.jsonl')
                if path.is_symlink():
                    gaps.add('UNREADABLE_SEGMENT'); continue
                try:
                    with path.open('rb') as stream:
                        data = stream.read(self.max_bytes + 1)
                    if len(data) > self.max_bytes:
                        gaps.add('OVERSIZED_SEGMENT'); continue
                    for line in data.splitlines():
                        try: row = json.loads(line)
                        except (ValueError, UnicodeError):
                            gaps.add('TRUNCATED_RECORD'); continue
                        if not isinstance(row, dict) or not isinstance(row.get('identity'), dict):
                            gaps.add('TRUNCATED_RECORD'); continue
                        if row.get('identity', {}).get('diagnostic_id') != wanted['diagnostic_id']:
                            continue
                        if row.get('identity') != wanted or row.get('owner_key') != owner_key(owner):
                            raise ValueError('DIAGNOSTIC_IDENTITY_MISMATCH')
                        row.pop('owner_key', None)
                        try:
                            rows.append(clean_event(row, wanted))
                        except ValueError:
                            gaps.add('TRUNCATED_RECORD')
                            continue
                        raw_gaps = row.get('gaps')
                        if isinstance(raw_gaps, list):
                            gaps.update(g for g in raw_gaps if isinstance(g, str) and g in GAPS)
                        if len(rows) > 32:
                            rows.pop(0)
                            gaps.add('EVENT_LIMIT')
                except FileNotFoundError: pass
                except OSError: gaps.add('UNREADABLE_SEGMENT')
        state = self.status()
        if state['dropped_events']: gaps.add('BUFFER_LOSS')
        if state['write_errors']: gaps.add('WRITE_FAILURE')
        if state['rotations'] or (self.directory / 'events.1.jsonl').exists(): gaps.add('RETENTION_ROTATED')
        if self._prior_writer or any(row.get('instance_id') != self.instance_id for row in rows):
            gaps.add('PRIOR_PROCESS_GAPS_UNKNOWN')
        if (self._thread is not None and any(row.get('instance_id') != self.instance_id for row in rows)) or len({row.get('instance_id') for row in rows}) > 1:
            gaps.add('PROCESS_RESTART')
        if state['writer_unavailable']: gaps.add('WRITER_UNAVAILABLE')
        if not state['active_counts_complete']: gaps.add('TRACKING_LIMIT')
        if state['closed_events']: gaps.add('CLOSED_JOURNAL')
        if state['pending_events']: gaps.add('PENDING_FLUSH')
        if not rows: gaps.add('WORKER_RECORD_NOT_ACQUIRED')
        return {'schema_version': 1, 'identity': wanted, 'events': rows[-32:],
                'gaps': sorted(set(gaps)), 'journal': state}
