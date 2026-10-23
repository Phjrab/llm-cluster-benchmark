"""F02: synthetic lifecycle/retention/privacy tests; never contacts hardware."""
from __future__ import annotations

import asyncio
import copy
import csv
import errno
import io
import json
import os
import tempfile
import threading
import tracemalloc
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from cluster.benchmark.diagnostics import (
    RunDiagnostics, collect_worker_evidence, find_identity,
    summarize_request, validate_evidence, job_context, latest_worker_evidence, main,
)
from cluster.benchmark.models import RequestTask
from cluster.benchmark.persistence import RunPersistence
from cluster.benchmark.transport import stream_worker_request, stream_rpc_request
from cluster.domain.experiment import ExperimentConfig
from cluster.infrastructure.request_diagnostics import (
    DiagnosticJournal, HEADER, identity, resource_observation, clean_resources, utc_now,
)
from cluster.tests.test_worker_runtime import FakeInferenceBackend, FakeTelemetry
from cluster.worker.app import create_app
from cluster.worker.inference import LlamaCppInferenceBackend
from cluster.worker.telemetry import TelemetryService

NODE = SimpleNamespace(name='pi-test', host='127.0.0.1', api_url='http://127.0.0.1:1')
OWNER = {'owner_id': 'job_test', 'lease_id': 'lease_' + 'a' * 32, 'fencing_epoch': 7}
HEADERS = {'X-Cluster-Resource-Owner': OWNER['owner_id'],
           'X-Cluster-Resource-Lease': OWNER['lease_id'], 'X-Cluster-Fencing-Epoch': '7'}


def request_identity(**updates):
    return identity(dict(diagnostic_id=uuid.uuid4().hex, run_id='run_test', attempt_id='attempt_1',
                         job_id='job_test', sweep_id='', campaign_id='', trial_id='', cell_id='',
                         node=NODE.name, scenario_id='single', request_id=1, logical_request_id=1,
                         replica_index=0, warmup=False, **updates)) if not updates else identity({**request_identity(), **updates})


def sample(**updates):
    return dict(sampled_at=utc_now(), telemetry_cache_age_s=0.1, telemetry_collection_interval_s=1,
                memory={'available_mb': 50, 'total_mb': 100}, swap={'used_mb': 0},
                worker_process={'pid': 123, 'started_at_unix': 1234, 'rss_bytes': 500}, **updates) if not updates else {**sample(), **updates}


class Response(io.BytesIO):
    def __init__(self, value):
        super().__init__(value if isinstance(value, bytes) else json.dumps(value).encode())


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def journal(self, name='worker', **options):
        journal = DiagnosticJournal(self.root / name, **options)
        journal.start()
        self.addCleanup(journal.close)
        return journal

    def flush(self, journal):
        # No real 600s timers: bounded queue drain with a test deadline.
        done = threading.Event()
        thread = threading.Thread(target=lambda: (journal.queue.join(), done.set()), daemon=True)
        thread.start()
        self.assertTrue(done.wait(2), 'diagnostic queue did not drain')

    def evidence(self, journal, wanted):
        self.flush(journal)
        return journal.read(wanted, owner=OWNER)

    def config(self):
        config = ExperimentConfig(node_names=[NODE.name], requests=1, concurrency=1,
                                  warmup_requests=0, model_id='tiny.gguf', n_gpu_layers=0,
                                  execution_strategy='single_node', persist_prompt=False,
                                  response_storage_mode='hash_only', prompt='PRIVATE_PROMPT')
        self.run_dir = self.root / 'run_test'
        self.run_dir.mkdir(exist_ok=True)
        self.cancel = threading.Event()
        self.run = RunDiagnostics(self.run_dir, 'run_test', config, self.cancel)
        self.addCleanup(self.run.journal.close)
        config._diagnostic_run = self.run
        return config

    def transport(self, config, opener):
        with mock.patch('cluster.benchmark.transport.worker_resource_headers', return_value=HEADERS), \
             mock.patch('cluster.benchmark.transport.worker_auth_headers', return_value={'X-Cluster-Worker-Token': 'SECRET_TOKEN'}), \
             mock.patch('cluster.benchmark.transport.urllib.request.urlopen', side_effect=opener):
            return stream_worker_request(NODE, config, RequestTask(1, 1, 'single', NODE.name))

    def test_success_controller_worker_routes_and_owned_read(self):
        config = self.config()
        provider = FakeTelemetry()
        app = create_app(backend=FakeInferenceBackend(), telemetry=TelemetryService(provider),
                         project_root=self.root / 'app', environment={
                             'CLUSTER_NODE_NAME': NODE.name, 'CLUSTER_WORKER_AUTH': 'true',
                             'CLUSTER_API_TOKEN': 'SECRET_TOKEN', 'CLUSTER_PLATFORM': 'generic-linux'})
        with TestClient(app) as client:
            app.state.inference_backend.loaded = 'tiny.gguf'
            auth = {'X-Cluster-Worker-Token': 'SECRET_TOKEN'}
            self.assertEqual(client.post('/cluster/resource-ownership/acquire', headers=auth, json=OWNER).status_code, 200)

            def open_request(request, **_kwargs):
                response = client.post('/cluster/chat/stream', content=request.data, headers=dict(request.header_items()))
                self.assertEqual(response.status_code, 200)
                return Response(response.content)

            result = self.transport(config, open_request)
            wanted = result['diagnostic_identity']
            journal = app.state.request_diagnostics
            self.flush(journal)
            read_headers = {**auth, **HEADERS, HEADER: json.dumps(wanted)}
            response = client.get('/cluster/diagnostics/' + wanted['diagnostic_id'], headers=read_headers)
            self.assertEqual(response.status_code, 200)
            worker = validate_evidence(response.json(), wanted)
            summary = summarize_request(self.evidence(self.run.journal, wanted), worker)
            self.assertTrue(result['ok'])
            self.assertEqual(summary['worker']['observed_outcome'], 'generation_completed')
            self.assertEqual(summary['cleanup']['status'], 'UNKNOWN')
            self.assertEqual(client.get('/cluster/diagnostics/' + wanted['diagnostic_id'], headers=HEADERS).status_code, 401)
            wrong = {**read_headers, 'X-Cluster-Resource-Lease': 'wrong'}
            self.assertEqual(client.get('/cluster/diagnostics/' + wanted['diagnostic_id'], headers=wrong).status_code, 403)
            self.assertEqual(client.post('/cluster/resource-ownership/release', headers=auth,
                                         json={**OWNER, 'cleanup_verified': True}).status_code, 200)
            self.assertEqual(client.get('/cluster/diagnostics/' + wanted['diagnostic_id'], headers=read_headers).status_code, 200)
            # No raw request, response, token or lease in new journals.
            text = '\n'.join(path.read_text() for path in self.root.rglob('events*.jsonl'))
            for secret in ('PRIVATE_PROMPT', 'SECRET_TOKEN', OWNER['lease_id']):
                self.assertNotIn(secret, text)

    def _timeout_at_backend_stage(self, before_lock):
        config = self.config()
        worker = self.journal()
        backend = LlamaCppInferenceBackend(self.root / 'models', llama_factory=lambda **_: None)
        entered = threading.Event()
        release = threading.Event()
        backend.llm = SimpleNamespace(create_chat_completion=lambda **_: iter([{'choices': [{'delta': {'content': 'private_output'}}]}]))
        thread = None
        if before_lock:
            backend.lock.acquire()
        try:
            def opener(request, **_kwargs):
                nonlocal thread
                wanted = json.loads(request.get_header(HEADER.capitalize()))
                worker.emit(wanted, 'received', owner=OWNER)

                def note(stage):
                    worker.emit(wanted, stage, owner=OWNER, resources=sample())
                    if stage == ('lock_wait' if before_lock else 'inference_started'):
                        entered.set()
                        if not before_lock:
                            self.assertTrue(release.wait(2))

                def infer():
                    list(backend.stream_chat(message='private', history=[], max_tokens=1,
                                             temperature=0, top_p=1, trace={'_diagnostic_emit': note}))
                    worker.emit(wanted, 'generation_completed', owner=OWNER)
                thread = threading.Thread(target=infer)
                thread.start()
                self.assertTrue(entered.wait(2))
                raise TimeoutError('timed out PRIVATE_EXCEPTION')

            result = self.transport(config, opener)
            wanted = result['diagnostic_identity']
            early = summarize_request(self.evidence(self.run.journal, wanted), self.evidence(worker, wanted))
            self.assertEqual(early['controller']['terminal_stage'], 'timeout')
            self.assertEqual(early['worker']['last_stage'], 'lock_wait' if before_lock else 'inference_started')
            self.assertEqual(early['worker']['observed_outcome'], 'UNKNOWN')
            self.assertEqual(early['cause']['classification'], 'INSUFFICIENT_EVIDENCE')
        finally:
            if before_lock:
                backend.lock.release()
            release.set()
            if thread is not None:
                thread.join(2)
                self.assertFalse(thread.is_alive())
        return result, worker

    def test_timeout_before_lock_retains_last_observed_stage(self):
        self._timeout_at_backend_stage(True)

    def test_timeout_after_lock_before_first_token_and_late_completion(self):
        result, worker = self._timeout_at_backend_stage(False)
        original = copy.deepcopy(result)
        wanted = result['diagnostic_identity']
        summary = summarize_request(self.evidence(self.run.journal, wanted), self.evidence(worker, wanted))
        self.assertTrue(summary['worker_completion_found_after_controller_failure'])
        self.assertEqual(summary['controller']['failure_code'], 'REQUEST_TIMEOUT')
        self.assertEqual(result, original)
        self.assertFalse(result['ok'])

    def test_reset_during_stream_and_no_worker_exit_inference(self):
        config = self.config()
        class ResetResponse(Response):
            def __iter__(self):
                yield b'data: {"type":"token","text":"private_output"}\n\n'
                raise ConnectionResetError('connection reset /private/path?token=secret')
        result = self.transport(config, lambda *_, **__: ResetResponse(b''))
        evidence = self.evidence(self.run.journal, result['diagnostic_identity'])
        self.assertIn('first_token_received', [r['stage'] for r in evidence['events']])
        summary = summarize_request(evidence)
        self.assertEqual(summary['controller']['terminal_stage'], 'transport_error')
        self.assertEqual(summary['worker']['observed_outcome'], 'UNKNOWN')
        self.assertNotIn('private_output', json.dumps(evidence))
        self.assertNotIn('/private/path', json.dumps(evidence))

    def test_collection_failure_then_recovery_preserves_failure_and_timeout(self):
        config = self.config()
        result = self.transport(config, lambda *_, **__: (_ for _ in ()).throw(TimeoutError('timed out')))
        wanted = result['diagnostic_identity']
        self.flush(self.run.journal)
        worker = self.journal()
        worker.emit(wanted, 'received', owner=OWNER)
        worker.emit(wanted, 'generation_completed', owner=OWNER)
        evidence = self.evidence(worker, wanted)
        saved = json.dumps(result, sort_keys=True)
        timeout = lambda *_, **__: (_ for _ in ()).throw(ConnectionError('SECRET'))
        first = collect_worker_evidence(self.run_dir, wanted, NODE, HEADERS, opener=timeout)
        second = collect_worker_evidence(self.run_dir, wanted, NODE, HEADERS, opener=lambda *_, **__: Response(evidence))
        self.assertEqual(first['collection_error'], 'COLLECTION_TRANSPORT_ERROR')
        self.assertEqual(second['status'], 'ACQUIRED')
        self.assertEqual(len(list((self.run_dir / 'diagnostics/collections').glob('*.json'))), 2)
        self.assertEqual(latest_worker_evidence(self.run_dir, wanted)['events'][-1]['stage'], 'generation_completed')
        self.assertEqual(saved, json.dumps(result, sort_keys=True))
        self.assertNotIn('SECRET', json.dumps(first))

    def test_actual_stream_disconnect_retains_worker_journal_without_final_response(self):
        from starlette.requests import Request
        from cluster.worker.schemas import ClusterChatRequest
        wanted = request_identity()
        backend = FakeInferenceBackend()
        release = threading.Event()
        closed = threading.Event()

        def stream(**kwargs):
            note = kwargs['trace']['_diagnostic_emit']
            note('lock_wait')
            note('lock_acquired')
            note('inference_started')
            try:
                yield 'PRIVATE_RESPONSE'
                release.wait(2)
                yield 'second'
            finally:
                note('lock_released')
                closed.set()

        backend.stream_chat = stream
        telemetry = TelemetryService(FakeTelemetry())
        app = create_app(backend=backend, telemetry=telemetry, project_root=self.root / 'disconnect',
                         environment={'CLUSTER_NODE_NAME': NODE.name, 'CLUSTER_PLATFORM': 'generic-linux'})
        with TestClient(app):
            route = next(r for r in app.routes if getattr(r, 'path', '') == '/cluster/chat/stream')
            journal = app.state.request_diagnostics

            async def exercise():
                disconnected = asyncio.Event()
                scope = {'type': 'http', 'method': 'POST', 'path': '/cluster/chat/stream',
                         'asgi': {'version': '3.0', 'spec_version': '2.0'},
                         'headers': [(k.lower().encode(), v.encode()) for k, v in {**HEADERS, HEADER: json.dumps(wanted)}.items()]}
                response = await route.endpoint(ClusterChatRequest(message='private'), Request(scope))

                async def receive():
                    await disconnected.wait()
                    return {'type': 'http.disconnect'}

                async def send(message):
                    if message['type'] == 'http.response.body' and b'PRIVATE_RESPONSE' in message['body']:
                        disconnected.set()

                await asyncio.wait_for(response(scope, receive, send), timeout=2)

            try:
                asyncio.run(exercise())
                evidence = self.evidence(journal, wanted)
                stages = [r['stage'] for r in evidence['events']]
                self.assertIn('disconnect_observed', stages)
                self.assertNotIn('generation_completed', stages)
                self.assertNotIn('PRIVATE_RESPONSE', json.dumps(evidence))
            finally:
                release.set()
                self.assertTrue(closed.wait(2))

    def test_collection_limit_invalid_evidence_and_old_worker_are_preserved(self):
        import urllib.error
        config = self.config()
        result = self.transport(config, lambda *_, **__: (_ for _ in ()).throw(TimeoutError('timed out')))
        wanted = result['diagnostic_identity']
        self.flush(self.run.journal)
        invalid = {'schema_version': 1, 'identity': {**wanted, 'run_id': 'wrong'}, 'events': []}
        got = collect_worker_evidence(self.run_dir, wanted, NODE, HEADERS, opener=lambda *_, **__: Response(invalid))
        self.assertEqual(got['collection_error'], 'INVALID_OR_MISMATCHED_EVIDENCE')
        old = lambda *_, **__: (_ for _ in ()).throw(urllib.error.HTTPError(NODE.api_url, 404, 'not found', {}, None))
        got = collect_worker_evidence(self.run_dir, wanted, NODE, HEADERS, opener=old)
        self.assertEqual(got['status'], 'WORKER_RECORD_NOT_ACQUIRED')
        self.assertEqual(got['summary']['worker']['observed_outcome'], 'UNKNOWN')
        destination = self.run_dir / 'diagnostics/collections'
        for index in range(254):
            (destination / f'{index:03d}.json').touch(exist_ok=True)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(collect_worker_evidence, self.run_dir, wanted, NODE, HEADERS, opener=old) for _ in range(4)]
        self.assertEqual(sum(f.exception() is None for f in futures), 2)
        self.assertEqual(len(list(destination.glob('*.json'))), 256)
        self.assertTrue(all(p.stat().st_size <= 128 * 1024 for p in destination.glob('*.json')))

    def test_diagnostic_writer_and_cache_failure_do_not_fail_inference(self):
        app = create_app(backend=FakeInferenceBackend(), telemetry=TelemetryService(FakeTelemetry()),
                         project_root=self.root / 'fail-open', environment={'CLUSTER_NODE_NAME': NODE.name})
        wanted = request_identity()
        app.state.inference_backend.loaded = 'tiny.gguf'
        with TestClient(app) as client, \
             mock.patch.object(app.state.telemetry, 'cached_snapshot', side_effect=RuntimeError('PRIVATE')), \
             mock.patch.object(app.state.request_diagnostics, '_write', side_effect=OSError(errno.ENOSPC, 'PRIVATE')):
            result = client.post('/cluster/chat/stream', json={'message': 'test'},
                                 headers={**HEADERS, HEADER: json.dumps(wanted)})
            self.assertEqual(result.status_code, 200)
            self.assertIn('"type": "done"', result.text)
            self.flush(app.state.request_diagnostics)
            self.assertGreater(app.state.request_diagnostics.write_errors, 0)

    def test_durable_job_attempt_binding_and_offline_collection_reader(self):
        wanted = request_identity(sweep_id='sweep_1', trial_id='trial_1', cell_id='cell_1')
        config = {'suite_id': 'suite_1', 'sweep': {'sweep_id': 'sweep_1', 'trial_id': 'trial_1',
                  'cell_id': 'cell_1', 'attempt_id': 'attempt_1'}}
        job = {'id': 'job_test', 'suite_id': 'suite_1', 'nodes': [NODE.name],
               'resource_reservation': {'owner_job_id': 'job_test', 'lease_id': OWNER['lease_id'],
                 'fencing_epoch': 7, 'status': 'quarantined', 'cleanup_verified': False}}
        headers, cleanup = job_context(wanted, config, job)
        self.assertEqual(headers, HEADERS)
        self.assertEqual(cleanup['status'], 'UNCERTAIN')
        for changed in ({**job, 'id': 'other'}, {**job, 'suite_id': 'other'}, {**job, 'nodes': ['other']}):
            with self.assertRaises(ValueError):
                job_context(wanted, config, changed)
        with self.assertRaises(ValueError):
            job_context({**wanted, 'attempt_id': 'other'}, config, job)
        self.assertIsNone(latest_worker_evidence(self.root, wanted))

    def test_offline_cli_legacy_summary_never_reads_inventory_or_connects(self):
        directory = self.root / 'run_legacy'
        directory.mkdir()
        (directory / 'config.json').write_text('{}')
        args = ['diagnostics', '--results-root', str(self.root), '--run-id', 'run_legacy',
                '--diagnostic-id', uuid.uuid4().hex, '--inventory', str(self.root / 'missing.csv'),
                '--jobs-dir', str(self.root / 'missing'), '--summary-only']
        with mock.patch('sys.argv', args), mock.patch('sys.stdout', new_callable=io.StringIO) as output, \
             mock.patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
            main()
        self.assertEqual(json.loads(output.getvalue())['availability'], 'NO_DIAGNOSTIC_RECORD')

    def test_identity_rejects_run_attempt_replica_retry_mixing(self):
        journal = self.journal()
        wanted = request_identity()
        journal.emit(wanted, 'received', owner=OWNER)
        evidence = self.evidence(journal, wanted)
        for field, replacement in [('run_id', 'other_run'), ('attempt_id', 'retry_2'),
                                   ('replica_index', 1), ('node', 'pi-other'), ('logical_request_id', 2)]:
            bad = {**wanted, field: replacement}
            with self.subTest(field=field), self.assertRaises(ValueError):
                journal.read(bad, owner=OWNER)
            with self.assertRaises(ValueError):
                validate_evidence(evidence, bad)
        replay = copy.deepcopy(evidence)
        replay['events'].append(copy.deepcopy(replay['events'][0]))
        with self.assertRaises(ValueError):
            validate_evidence(replay, wanted)

    def test_broadcast_and_retry_get_fresh_physical_ids(self):
        config = self.config()
        a = self.run.begin(NODE, RequestTask(1, 1, 'broadcast', NODE.name, 0), False, HEADERS)
        b = self.run.begin(SimpleNamespace(name='pi-other'), RequestTask(2, 1, 'broadcast', 'pi-other', 1), False, HEADERS)
        retry = RunDiagnostics(self.root / 'other', 'run_retry', config)
        self.addCleanup(retry.journal.close)
        c = retry.begin(NODE, RequestTask(1, 1, 'broadcast', NODE.name, 0), False, HEADERS)
        self.assertEqual(len({x.identity['diagnostic_id'] for x in (a, b, c)}), 3)
        self.assertEqual(a.identity['logical_request_id'], b.identity['logical_request_id'])
        self.assertNotEqual(a.identity['attempt_id'], c.identity['attempt_id'])
        self.assertNotEqual(a.identity['replica_index'], b.identity['replica_index'])

    def test_missing_stale_and_unavailable_memory_never_zero(self):
        for raw in ({}, sample(telemetry_cache_age_s=99), sample(telemetry_cache_age_s=None)):
            with self.subTest(raw=raw):
                values = resource_observation(raw)['values']
                self.assertTrue(all(v['value'] is None and not v['available'] for v in values.values()))
        fresh = resource_observation(sample())
        self.assertEqual(fresh['values']['swap_used_bytes']['value'], 0)
        self.assertTrue(fresh['values']['swap_used_bytes']['available'])
        self.assertEqual(fresh['values']['system_available_bytes']['value'], 50 * 1024 * 1024)
        self.assertFalse(fresh['model_process']['available'])
        fresh['values']['worker_rss_bytes'] = {'value': None, 'available': True}
        self.assertFalse(clean_resources(fresh)['values']['worker_rss_bytes']['available'])

    def test_cache_age_is_measured_on_worker_monotonic_clock(self):
        service = TelemetryService(FakeTelemetry())
        service._snapshot_cache = sample()
        service._snapshot_monotonic = 10
        with mock.patch('cluster.worker.telemetry.time.perf_counter', return_value=14):
            value = service.cached_snapshot()
        self.assertEqual(value['telemetry_cache_age_s'], 4)
        self.assertFalse(resource_observation(value)['process']['available'])

    def test_restart_retains_evidence_and_reports_unobserved_gap(self):
        wanted = request_identity()
        first = self.journal()
        first.emit(wanted, 'received', owner=OWNER)
        first.emit(wanted, 'lock_wait', owner=OWNER)
        self.flush(first)
        first.close()
        second = self.journal()
        evidence = second.read(wanted, owner=OWNER)
        self.assertEqual(evidence['events'][-1]['stage'], 'lock_wait')
        self.assertIn('PRIOR_PROCESS_GAPS_UNKNOWN', evidence['gaps'])
        self.assertNotEqual(first.instance_id, second.instance_id)
        self.assertEqual(summarize_request(evidence)['cause']['classification'], 'INSUFFICIENT_EVIDENCE')

    def test_rotation_disk_and_queue_bounds_under_synthetic_load(self):
        wanted = request_identity()
        journal = DiagnosticJournal(self.root / 'bounded', capacity=8, max_bytes=4096, files=1)
        tracemalloc.start()
        try:
            for _ in range(3000):
                journal.emit(wanted, 'lock_wait', owner=OWNER, resources=sample())
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLessEqual(journal.queue.qsize(), 8)
        self.assertLess(peak, 2 * 1024 * 1024)
        self.assertGreater(journal.dropped, 0)
        journal.start()
        self.addCleanup(journal.close)
        self.flush(journal)
        evidence = journal.read(wanted, owner=OWNER)
        self.assertIn('BUFFER_LOSS', evidence['gaps'])
        self.assertIn('RETENTION_ROTATED', evidence['gaps'])
        self.assertLessEqual(sum(p.stat().st_size for p in journal.directory.glob('events*.jsonl')), 8192)
        self.assertTrue(all(len(line) + 1 <= 4096 for p in journal.directory.glob('events*.jsonl') for line in p.read_bytes().splitlines()))

    def test_disk_full_is_optional_and_recovery_marks_gap(self):
        journal = self.journal()
        wanted = request_identity()
        with mock.patch.object(journal, '_write', side_effect=OSError(errno.ENOSPC, 'PRIVATE_PATH')):
            journal.emit(wanted, 'received', owner=OWNER)
            self.flush(journal)
        journal.emit(wanted, 'lock_wait', owner=OWNER)
        evidence = self.evidence(journal, wanted)
        self.assertIn('WRITE_FAILURE', evidence['gaps'])
        self.assertNotIn('PRIVATE_PATH', json.dumps(evidence))
        journal.close()
        self.assertIn('WRITE_FAILURE', DiagnosticJournal(journal.directory).read(wanted, owner=OWNER)['gaps'])

    def test_short_os_write_is_completed(self):
        journal = self.journal()
        wanted = request_identity()
        real_write = os.write
        with mock.patch('cluster.infrastructure.request_diagnostics.os.write', side_effect=lambda fd, buf: real_write(fd, buf[:17])):
            journal.emit(wanted, 'received', owner=OWNER)
            self.flush(journal)
        self.assertEqual(len(journal.read(wanted, owner=OWNER)['events']), 1)

    def test_second_writer_cannot_rotate_first_writer_files(self):
        wanted = request_identity()
        first = self.journal()
        first.emit(wanted, 'received', owner=OWNER)
        self.flush(first)
        second = self.journal()
        second._thread.join(1)
        self.assertTrue(second.status()['writer_unavailable'])
        self.assertIn('WRITER_UNAVAILABLE', second.read(wanted, owner=OWNER)['gaps'])

    def test_writer_thread_start_failure_does_not_break_run_shutdown(self):
        journal = DiagnosticJournal(self.root / 'start-failed')
        with mock.patch.object(threading.Thread, 'start', side_effect=RuntimeError('thread limit')):
            journal.start()
        journal.emit(request_identity(), 'received', owner=OWNER)
        journal.close()
        self.assertTrue(journal.status()['writer_unavailable'])

    def test_malformed_segments_and_untrusted_evidence_are_bounded(self):
        journal = self.journal()
        wanted = request_identity()
        journal.emit(wanted, 'received', owner=OWNER)
        self.flush(journal)
        with (journal.directory / 'events.jsonl').open('ab') as stream:
            stream.write(b'[]\nnull\n{"partial":\n')
        evidence = journal.read(wanted, owner=OWNER)
        self.assertIn('TRUNCATED_RECORD', evidence['gaps'])
        for invalid in (None, [], {**evidence, 'events': [None]}, {**evidence, 'gaps': [dict(secret='a')]}):
            with self.assertRaises(ValueError):
                validate_evidence(invalid, wanted)
        private = copy.deepcopy(evidence)
        private['events'][0]['prompt'] = 'PRIVATE'
        private['events'][0]['exception'] = '/private/SECRET?token=secret'
        self.assertNotIn('PRIVATE', json.dumps(validate_evidence(private, wanted)))

    def test_lookup_rejects_paths_and_unknown_id_without_any_connection(self):
        for changes in ({'run_id': '../run'}, {'node': 'pi?token=secret'}, {'prompt': 'private'}, {'request_id': True}):
            with self.assertRaises(ValueError):
                identity({**request_identity(), **changes})
        with self.assertRaises(ValueError):
            find_identity(self.root, uuid.uuid4().hex)

    def test_legacy_and_off_mode_do_not_create_synthetic_evidence(self):
        self.assertEqual(summarize_request()['availability'], 'NO_DIAGNOSTIC_RECORD')
        config = ExperimentConfig(diagnostics_mode='off')
        result = self.transport(config, lambda *_, **__: Response(b'data: {"type":"done","metrics":{}}\n\n'))
        self.assertIsNone(result['diagnostic_identity'])
        self.assertFalse(list(self.root.rglob('events*.jsonl')))

    def test_disjoint_cancellation_does_not_change_other_trace_or_ownership(self):
        config = self.config()
        one = self.run.begin(NODE, RequestTask(1, 1, 'single', NODE.name), False, HEADERS)
        other = RunDiagnostics(self.root / 'other', 'run_other', config, threading.Event())
        self.addCleanup(other.journal.close)
        two = other.begin(SimpleNamespace(name='pi-other'), RequestTask(1, 1, 'single', 'pi-other'), False, HEADERS)
        self.cancel.set()
        self.run.observe_cancel()
        self.run.observe_cancel()
        other.observe_cancel()
        one_rows = self.evidence(self.run.journal, one.identity)['events']
        two_rows = self.evidence(other.journal, two.identity)['events']
        self.assertEqual(sum(r['stage'] == 'cancel_requested' for r in one_rows), 1)
        self.assertNotIn('cancel_requested', [r['stage'] for r in two_rows])
        self.assertFalse(list(self.root.rglob('*ownership*')))

    def test_cleanup_network_loss_remains_uncertain_after_worker_completion(self):
        journal = self.journal()
        wanted = request_identity()
        journal.emit(wanted, 'completed', owner=OWNER)
        controller = self.evidence(journal, wanted)
        cleanup = {**{k: wanted[k] for k in ('run_id', 'job_id', 'attempt_id')}, 'status': 'UNCERTAIN'}
        summary = summarize_request(controller, cleanup=cleanup)
        self.assertEqual(summary['cleanup']['status'], 'UNCERTAIN')
        self.assertIn('OWNERSHIP_CLEANUP_NOT_VERIFIED', summary['missing_evidence'])
        with self.assertRaises(ValueError):
            summarize_request(controller, cleanup={**cleanup, 'attempt_id': 'other'})

    def test_cross_instance_monotonic_values_are_never_subtracted(self):
        wanted = request_identity()
        journal = self.journal()
        journal.emit(wanted, 'transport_started', owner=OWNER)
        journal.emit(wanted, 'first_token_received', owner=OWNER)
        value = self.evidence(journal, wanted)
        value['events'][0]['monotonic_s'] = 10
        value['events'][1]['monotonic_s'] = 11
        summary = summarize_request(value)
        self.assertEqual(summary['durations']['controller_ttft']['seconds'], 1)
        value['events'][1]['instance_id'] = uuid.uuid4().hex
        summary = summarize_request(value)
        self.assertIsNone(summary['durations']['controller_ttft']['seconds'])
        self.assertIsNone(summary['durations']['cross_host_latency']['seconds'])

    def test_rpc_observes_controller_only_and_does_not_send_worker_identity(self):
        config = self.config()
        with mock.patch('cluster.benchmark.transport.urllib.request.urlopen', return_value=Response(b'data: [DONE]\n\n')) as opener, \
             mock.patch('cluster.benchmark.transport.worker_resource_headers', return_value=HEADERS):
            result = stream_rpc_request(NODE, NODE.api_url, config, RequestTask(1, 1, 'rpc', NODE.name))
        self.assertNotIn(HEADER.lower(), {k.lower() for k, _ in opener.call_args.args[0].header_items()})
        summary = summarize_request(self.evidence(self.run.journal, result['diagnostic_identity']))
        self.assertEqual(summary['worker']['last_stage'], 'UNKNOWN')

    def test_privacy_mode_csv_contract_and_required_journal_failure_unchanged(self):
        config = self.config()
        store = RunPersistence(self.root / 'results', 'run_persist', config)
        wanted = request_identity(run_id='run_persist')
        record = dict(diagnostic_identity=wanted, request_id=1, logical_request_id=1,
                      node=NODE.name, scenario_id='single', ok=False, error_code='REQUEST_TIMEOUT', response='PRIVATE_RESPONSE')
        store.emit('request_completed', result=record)
        response = store.repository.read_responses('run_persist')[0]
        self.assertEqual(response['diagnostic_identity'], wanted)
        self.assertNotIn('PRIVATE_RESPONSE', json.dumps(response))
        self.assertNotIn('PRIVATE_PROMPT', (store.run_dir / 'config.json').read_text())
        store.repository.write_requests('run_persist', [record])
        with (store.run_dir / 'requests.csv').open() as stream:
            self.assertEqual(len(next(csv.reader(stream))), 19)
        with mock.patch.object(store.repository, 'append_event', side_effect=OSError('required journal failure')):
            with self.assertRaises(OSError):
                store.emit('required_failure')


if __name__ == '__main__':
    unittest.main()
