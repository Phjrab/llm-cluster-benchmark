"""F03 isolated observation tests. No hardware, execution recovery or polling."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cluster.dashboard.service_layers.progress_observation import ProgressObservation
from cluster.dashboard.service_layers.diagnostic_views import run_diagnostics
from cluster.dashboard.service_layers.research_service import ResearchService
from cluster.dashboard.service_layers.result_service import ResultService
from cluster.infrastructure.storage import FilesystemRunRepository
from cluster.infrastructure.request_diagnostics import DiagnosticJournal
from cluster.tests.test_request_diagnostics import request_identity, OWNER, sample


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.clock = 1.0
        self.jobs = {'job_a': {'id': 'suite_a', 'job_id': 'job_a', 'suite_id': 'suite_a',
                              'status': 'running', 'phase': 'measurement', 'nodes': ['pi-a']}}
        self.view = ProgressObservation(read_job=lambda key: self.jobs[key], monotonic=lambda: self.clock)
        self.key = ('sweep', 'one')
        self.manifest = {'status': 'running', 'trials': [
            {'trial_id': 't_a', 'cell_id': 'c_a', 'status': 'running', 'attempts': [
                {'attempt_id': 'a_a', 'backend_job_id': 'job_a', 'suite_id': 'suite_a',
                 'status': 'running', 'started_at': '2026-10-23T01:00:00+00:00'}]}]}
        self.view.started(self.key)
        self.view.tick(self.key)
        self.view.success(self.key)

    def test_long_child_and_cooldown_are_not_faults(self):
        self.clock += 800
        self.view.tick(self.key)
        self.view.success(self.key)
        value = self.view.snapshot(self.key, self.manifest)
        self.assertEqual(value['display_state'], 'CHILD_ACTIVE')
        self.assertEqual(value['request_progress'], 'UNKNOWN')
        self.assertEqual(value['children'][0]['process_liveness'], 'UNKNOWN')
        self.jobs['job_a']['phase'] = 'resource_cooldown'
        self.assertEqual(self.view.snapshot(self.key, self.manifest)['display_state'], 'COOLDOWN')
        self.jobs['job_a']['phase'] = 'model_cleanup'
        self.assertEqual(self.view.snapshot(self.key, self.manifest)['display_state'], 'CLEANUP')
        self.jobs['job_a']['status'] = 'waiting_resources'
        self.jobs['job_a']['phase'] = 'waiting_resources'
        self.assertEqual(self.view.snapshot(self.key, self.manifest)['display_state'], 'RESOURCE_WAIT')

    def test_stale_and_new_instance_unknown_without_mutation(self):
        before = copy.deepcopy(self.manifest)
        jobs = copy.deepcopy(self.jobs)
        self.clock += 60
        for _ in range(10):
            value = self.view.snapshot(self.key, self.manifest)
            self.assertEqual(value['display_state'], 'UNKNOWN')
            self.assertEqual(value['freshness'], 'STALE')
        restarted = ProgressObservation()
        value = restarted.snapshot(self.key, self.manifest)
        self.assertIsNone(value['loop_instance_id'])
        self.assertNotEqual(value['serving_instance_id'], self.view.instance_id)
        self.assertEqual(value['owner_evidence'], 'UNKNOWN')
        self.assertEqual(jobs, self.jobs)
        self.assertEqual(before, self.manifest)

    def test_error_retained_after_thread_exit_and_read_is_not_restart(self):
        self.view.failed(self.key)
        self.view.stopped(self.key)
        value = self.view.snapshot(self.key, self.manifest)
        self.assertEqual(value['display_state'], 'LOOP_ERROR')
        self.assertFalse(value['loop_active'])
        self.assertEqual(value['last_error_code'], 'PROGRESS_LOOP_ERROR')
        self.assertEqual(value['children'][0]['status'], 'running')

    def test_disjoint_children_and_owner_mismatch(self):
        self.jobs['job_b'] = {**self.jobs['job_a'], 'job_id': 'job_b', 'suite_id': 'suite_b',
                              'nodes': ['pi-b'], 'status': 'cancelling', 'phase': 'model_cleanup'}
        other = copy.deepcopy(self.manifest['trials'][0])
        other.update(trial_id='t_b', cell_id='c_b')
        other['attempts'] = [{**other['attempts'][0], 'attempt_id': 'a_b', 'backend_job_id': 'job_b', 'suite_id': 'suite_b'}]
        self.manifest['trials'].append(other)
        rows = self.view.snapshot(self.key, self.manifest)['children']
        self.assertEqual([(r['job_id'], r['nodes'], r['status']) for r in rows],
                         [('job_a', ['pi-a'], 'running'), ('job_b', ['pi-b'], 'cancelling')])
        self.jobs['job_b']['suite_id'] = 'wrong_suite'
        self.assertEqual(self.view.snapshot(self.key, self.manifest)['children'][1]['source'], 'durable_manifest')

    def test_external_runner_and_observation_capacity(self):
        self.assertEqual(self.view.snapshot(self.key, self.manifest, external=True)['owner_evidence'], 'EXTERNAL_RUNNER')
        self.assertEqual(self.view.snapshot(self.key, self.manifest, external=True)['display_state'], 'UNKNOWN')
        view = ProgressObservation(capacity=2)
        for i in range(10):
            view.started(('sweep', str(i)))
        self.assertEqual(len(view._loops), 2)
        self.assertEqual(view.snapshot(('sweep', '9'))['loop_state'], 'UNKNOWN')
        view.stopped(('sweep', '0'))
        view.started(('sweep', '9'))
        self.assertEqual(len(view._loops), 2)

    def test_campaign_loop_error_and_get_read_only(self):
        repository = mock.Mock()
        manifest = {'campaign_id': 'campaign_one', 'status': 'running', 'cells': []}
        repository.read.return_value = manifest
        repository.read_events.return_value = []
        runner = mock.Mock()
        runner.tick.side_effect = RuntimeError('SECRET /private/path?token=SECRET')
        service = ResearchService(campaigns_dir=Path('/unused'), read_runs=lambda **_: [],
            read_research_document=lambda _: {'execution_gate': {'formal_execution_allowed': True}},
            status_snapshot=lambda: [], read_environment=lambda: [], controller_commit=lambda: 'test',
            repository_factory=lambda _: repository, runner_factory=lambda _: runner)
        service.observation.started(('campaign', 'campaign_one'))
        service._drive('campaign_one', runner)
        for _ in range(3):
            value = service.campaign('campaign_one')['progress_observation']
            self.assertEqual(value['display_state'], 'LOOP_ERROR')
            self.assertNotIn('SECRET', json.dumps(value))
        runner.tick.assert_called_once()

    def test_worker_cache_projection_never_probes_and_stale_is_unknown(self):
        from cluster.dashboard.services import StatusMonitor
        monitor = StatusMonitor()
        monitor._snapshot = [{"name": "pi-a", "api": True, "token": "SECRET"}]
        with mock.patch("cluster.dashboard.services.probe_node", side_effect=AssertionError("probe forbidden")), \
             mock.patch("cluster.dashboard.services.time.monotonic", return_value=100.0):
            self.assertEqual(monitor.observation()["nodes"][0]["response"], "UNKNOWN")
            monitor._sample_monotonic = 99.0
            self.assertEqual(monitor.observation()["nodes"][0]["response"], "RESPONDING")
            monitor._sample_monotonic = 20.0
            value = monitor.observation()
            self.assertEqual(value["freshness"], "STALE")
            self.assertEqual(value["nodes"][0]["response"], "UNKNOWN")
            self.assertNotIn("SECRET", json.dumps(value))

    def test_campaign_cooldown_and_closed_gate_evidence(self):
        view = ProgressObservation(now=lambda: '2026-10-23T00:00:00+00:00')
        key = ('campaign', 'one')
        view.started(key); view.tick(key); view.success(key)
        manifest = {'status': 'running', 'cooldown_not_before': '2026-10-23T00:10:00+00:00'}
        self.assertEqual(view.snapshot(key, manifest)['display_state'], 'COOLDOWN')
        view.success(key, gate_blocked=True)
        self.assertEqual(view.snapshot(key, manifest)['display_state'], 'GATE_BLOCKED')


class DiagnosticViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = FilesystemRunRepository(Path(self.temp.name))
        self.config = {'suite_id': 'suite_1', 'sweep': {'attempt_id': 'attempt_1'}}
        self.root = self.repo.create('run_test', self.config)
        self.job = {'id': 'suite_1', 'job_id': 'job_test', 'suite_id': 'suite_1', 'nodes': ['pi-test'],
                    'resource_reservation': {**OWNER, 'owner_job_id': 'job_test', 'status': 'quarantined'}}
        self.journal = DiagnosticJournal(self.root / 'diagnostics' / 'controller')
        self.journal.start()
        self.addCleanup(self.journal.close)
        self.wanted = request_identity()

    def emit(self, value=None, stage='timeout'):
        self.journal.emit(value or self.wanted, stage, owner=OWNER)
        self.journal.queue.join()

    def view(self, **kw):
        return run_diagnostics(self.root, read_job=lambda _: self.job, **kw)

    def test_interrupted_request_without_response_or_summary_and_read_only(self):
        self.emit(stage='submitted'); self.emit(stage='transport_started'); self.emit()
        self.journal.close()
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        for _ in range(3):
            value = self.view()
            record = value['requests'][0]
            self.assertEqual(record['controller']['terminal_stage'], 'timeout')
            self.assertEqual(record['worker']['last_stage'], 'UNKNOWN')
            self.assertEqual(record['cleanup']['status'], 'UNCERTAIN')
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertFalse((self.root / 'summary.json').exists())

    def test_late_worker_completion_keeps_failure_and_sanitizes_collection(self):
        self.emit()
        worker = DiagnosticJournal(Path(self.temp.name) / 'worker')
        worker.start()
        try:
            worker.emit(self.wanted, 'generation_completed', owner=OWNER, resources=sample())
            worker.queue.join()
            evidence = worker.read(self.wanted, owner=OWNER)
        finally:
            worker.close()
        folder = self.root / 'diagnostics' / 'collections'; folder.mkdir()
        (folder / '000.json').write_text(json.dumps({'identity': self.wanted, 'status': 'ACQUIRED', 'evidence': evidence,
            'summary': {'prompt': 'SECRET', 'failure': '/private/path?token=SECRET'}}))
        value = self.view()
        record = value['requests'][0]
        self.assertTrue(record['worker_completion_found_after_controller_failure'])
        self.assertEqual(record['controller']['terminal_stage'], 'timeout')
        self.assertTrue(record['resources']['values']['worker_rss_bytes']['available'])
        self.assertNotIn('SECRET', json.dumps(value))
        self.assertNotIn(OWNER['lease_id'], json.dumps(value))

    def test_wrong_attempt_run_replica_owner_rejected(self):
        self.emit()
        path = self.root / 'diagnostics/controller/events.jsonl'
        original = path.read_text()
        for change in ({'attempt_id': 'other'}, {'run_id': 'run_other'}, {'replica_index': 1}):
            row = json.loads(original)
            row['identity'].update(change)
            path.write_text(original + json.dumps(row) + '\n')
            value = self.view()
            self.assertEqual(value['requests'], [])
            self.assertIn('INVALID_OR_UNBOUND_CONTROLLER_RECORD', value['gaps'])
        row = json.loads(original); row['owner_key'] = 'wrong'
        path.write_text(json.dumps(row)+'\n')
        self.assertEqual(self.view()['requests'], [])

    def test_legacy_and_unreadable_symlink_gap(self):
        self.assertEqual(self.view()['availability'], 'NO_DIAGNOSTIC_RECORD')
        self.journal.close()
        path = self.root / 'diagnostics/controller/events.jsonl'
        if path.exists(): path.unlink()
        secret = Path(self.temp.name) / 'secret'; secret.write_text('SECRET')
        path.symlink_to(secret)
        value = self.view()
        self.assertIn('UNREADABLE_SEGMENT', value['gaps'])
        self.assertNotIn('SECRET', json.dumps(value))

    def test_pagination_size_and_duplicate_physical_id(self):
        for i in range(23):
            self.emit(request_identity(request_id=i, logical_request_id=i))
        first = self.view()
        self.assertEqual(len(first['requests']), 20)
        self.assertEqual(first['next_offset'], 20)
        self.assertEqual(len(self.view(offset=20)['requests']), 3)
        with self.assertRaises(ValueError): self.view(limit=21)
        self.emit(stage='submitted'); self.emit(stage='submitted')
        self.assertIn('AMBIGUOUS_REQUEST_REJECTED', self.view()['gaps'])

    def test_result_service_legacy_and_interrupted_endpoint(self):
        service = ResultService(run_repository=lambda: self.repo, suite_repository=lambda: None,
            read_suites=lambda **_: [], with_suite_metadata=lambda value, _: value,
            active_experiment=lambda: None, publish_event=lambda **_: None, utc_now=lambda: '',
            read_job=lambda _: self.job)
        self.emit()
        self.assertEqual(service.diagnostics('run_test')['requests'][0]['controller']['terminal_stage'], 'timeout')
        self.repo.write_summary('run_test', {'run_id': 'run_test', 'response_storage_mode': 'none'})
        value = service.responses('run_test')
        self.assertEqual(value['response_storage_status'], 'not_persisted')
        self.assertEqual(value['responses'], [])
        self.assertEqual(value['diagnostics']['run_id'], 'run_test')


if __name__ == '__main__':
    unittest.main()
