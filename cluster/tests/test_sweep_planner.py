"""Offline S01 compiler tests: only synthetic identities and pure functions."""
from __future__ import annotations

import ast
import copy
import dataclasses
import inspect
import json
import unittest
from unittest import mock

from cluster.application import sweep_planner
from cluster.application.sweep_planner import compile_plan, verify_plan
from cluster.benchmark.planner import build_strategy_scenarios
from cluster.domain import sweep
from cluster.domain.errors import DomainValidationError
from cluster.domain.experiment import ExperimentConfig
from cluster.domain.sweep import ResolutionContext, RpcProfile, SweepSpec


def context_data():
    return {
        "models": [dict(ref=ref, catalog_id=ref, model_id=f"synthetic/{ref}.gguf",
                        artifact_sha256=char * 64, source_revision=char * 40,
                        quantization="Q4_K_M", template_sha256=char * 64,
                        installed_workers=["j1", "j2", "j3"], availability="valid",
                        runtime_compatibility="valid") for ref, char in (("model-a", "a"), ("model-b", "b"))],
        "prompts": [dict(ref="same-text", model_ref=ref, text_sha256="c" * 64,
                         template_sha256=char * 64, rendered_input_tokens=tokens,
                         input_token_source="synthetic-template", input_tokens_exact=True)
                    for ref, char, tokens in (("model-a", "a", 64), ("model-b", "b", 80))],
        "workers": [dict(worker_id=node, endpoint_identity=f"fake-physical-{node}", platform="jetson",
                         availability="valid", rpc_layer="valid", rpc_row="unknown")
                    for node in ("j1", "j2", "j3")],
    }


def spec_data():
    return {"base": {"model_ref": "model-a", "prompt_ref": "same-text", "worker_ids": ["j1"]},
            "axes": [{"name": "model_ref", "values": ["model-a", "model-b"]},
                     {"name": "n_ctx", "values": [1024, 2048, 4096]},
                     {"name": "concurrency", "values": [1, 3, 6]},
                     {"name": "max_tokens", "values": [64, 128]}],
            "repeat_count": 3}


def rpc_spec():
    return {"base": {"model_ref": "model-a", "prompt_ref": "same-text",
                     "execution_strategy": "model_parallel_rpc", "requests": 2},
            "rpc_profiles": [
                {"profile_id": "two-auto", "worker_ids": ["j1", "j2"], "coordinator_id": "j1"},
                {"profile_id": "two-custom", "worker_ids": ["j1", "j2"], "coordinator_id": "j1",
                 "split_policy": "custom", "weights_by_worker": {"j1": 2, "j2": 1}},
                {"profile_id": "three-equal", "worker_ids": ["j1", "j2", "j3"], "coordinator_id": "j1",
                 "split_policy": "equal"}],
            "axes": [{"name": "model_ref", "values": ["model-a", "model-b"]},
                     {"name": "rpc_profile_ref", "values": ["two-auto", "two-custom", "three-equal"]},
                     {"name": "n_ctx", "values": [1024, 2048]}], "repeat_count": 3}


def plan(raw=None, context=None):
    return compile_plan(SweepSpec.from_dict(raw if raw is not None else spec_data()),
                        ResolutionContext.from_dict(context if context is not None else context_data()))


class SweepCombinationTests(unittest.TestCase):
    def test_grid_108_trials_and_independent_repeat_ids(self):
        result = plan()
        self.assertEqual((result.counts.candidate_cells, result.counts.unique_cells, result.counts.trials), (36, 36, 108))
        self.assertEqual(result.counts.valid_cells, 36)
        self.assertEqual(len({trial.trial_id for trial in result.trials}), 108)
        self.assertEqual([t.sweep_repeat_index for t in result.trials[:3]], [1, 2, 3])
        self.assertEqual({t.cell_id for t in result.trials[:3]}, {result.cells[0].cell_id})
        self.assertTrue(result.executable)
        self.assertEqual(result.capabilities[0].code, "DURABLE_SWEEP_EXECUTION_AVAILABLE")
        self.assertNotIn('"campaign_id"', result.to_json())
        self.assertNotIn('"repeat_index"', result.to_json())

    def test_rpc_36_trials_atomic_profiles_and_coordinator_only_model(self):
        ctx = context_data()
        for model in ctx['models']:
            model['installed_workers'] = ['j1']
        result = plan(rpc_spec(), ctx)
        self.assertEqual((result.counts.unique_cells, result.counts.trials), (12, 36))
        self.assertEqual(result.counts.valid_cells, 12)
        self.assertEqual(result.counts.workload.warmup_calls, 36)
        self.assertEqual(result.counts.workload.model_loads, 36)
        self.assertEqual(result.cells[0].rpc_profile.split_policy, 'auto')
        self.assertEqual(result.cells[0].rpc_profile.weights_by_worker, ())
        self.assertEqual(result.cells[4].condition.worker_ids, ('j1', 'j2', 'j3'))

    def test_one_at_a_time_baseline_once_with_duplicate_rows_retained(self):
        raw = {"base": {"model_ref": "model-a", "prompt_ref": "same-text", "worker_ids": ["j1"],
                         "n_ctx": 1024, "concurrency": 1},
               "combination": "one_at_a_time", "axes": [{"name": "n_ctx", "values": [1024, 2048]},
                                                         {"name": "concurrency", "values": [1, 2]}]}
        result = plan(raw)
        self.assertEqual((result.counts.candidate_cells, result.counts.unique_cells, result.counts.duplicate_cells), (5, 3, 2))
        self.assertEqual(len(result.trials), 3)
        self.assertEqual([c.duplicate_of for c in result.cells], [None, 0, None, 0, None])
        self.assertEqual([(c.condition.n_ctx, c.condition.concurrency) for c in result.cells],
                         [(1024, 1), (1024, 1), (2048, 1), (1024, 1), (1024, 2)])

    def test_explicit_worker_sets_and_order_preserved(self):
        raw = {"base": spec_data()['base'], "combination": "explicit",
               "explicit": [dict(spec_data()['base'], worker_ids=['j2', 'j1']),
                            dict(spec_data()['base'], worker_ids=['j1', 'j2'])]}
        result = plan(raw)
        self.assertEqual(result.counts.unique_cells, 2)
        self.assertEqual(result.cells[0].condition.worker_ids, ('j2', 'j1'))

    def test_equivalent_rpc_ratios_dedupe_but_preserve_original_input(self):
        raw = rpc_spec()
        raw['rpc_profiles'] = [dict(profile_id='p1', worker_ids=['j1', 'j2'], coordinator_id='j1',
                                   split_policy='custom', weights_by_worker={'j1': 1, 'j2': 1}),
                               dict(profile_id='p2', worker_ids=['j1', 'j2'], coordinator_id='j1',
                                    split_policy='custom', weights_by_worker={'j1': 50, 'j2': 50})]
        raw['axes'] = [dict(name='rpc_profile_ref', values=['p1', 'p2'])]
        raw['repeat_count'] = 1
        saved = copy.deepcopy(raw)
        result = plan(raw)
        self.assertEqual(result.counts.duplicate_cells, 1)
        self.assertEqual(len(result.trials), 1)
        self.assertEqual(dict(result.cells[1].rpc_profile.weights_by_worker), {'j1': 50, 'j2': 50})
        self.assertEqual(raw, saved)

    def test_huge_grid_rejected_before_product_or_cell_materialization(self):
        raw = spec_data()
        raw['axes'] = [dict(name='n_ctx', values=list(range(128, 192))),
                       dict(name='concurrency', values=list(range(1, 65)))]
        with mock.patch.object(sweep_planner, '_conditions', side_effect=AssertionError('materialized')):
            with self.assertRaisesRegex(DomainValidationError, 'before materialization'):
                plan(raw)

    def test_exclusions_change_revision_hash_and_keep_reason(self):
        original = plan()
        raw = spec_data()
        raw.update(revision=2, exclusions=[dict(cell_id=original.cells[0].cell_id, reason='operator excluded')])
        revised = plan(raw)
        self.assertEqual((revised.counts.candidate_cells, revised.counts.excluded_cells, revised.counts.trials), (36, 1, 105))
        self.assertEqual(revised.cells[0].exclusion_reason, 'operator excluded')
        self.assertNotEqual(original.plan_sha256, revised.plan_sha256)
        self.assertEqual(original.cells[0].cell_id, revised.cells[0].cell_id)
        self.assertEqual(original.counts.trials, 108)
        raw['exclusions'][0]['cell_id'] = 'cell_' + '0'*64
        with self.assertRaises(DomainValidationError):
            plan(raw)

    def test_exclusion_requires_reason_and_new_revision(self):
        raw = spec_data()
        raw['exclusions'] = [dict(cell_id=plan().cells[0].cell_id, reason='')]
        with self.assertRaises(DomainValidationError): plan(raw)
        raw['exclusions'][0]['reason'] = 'excluded'
        with self.assertRaises(DomainValidationError): plan(raw)

    def test_seeded_order_deterministic_and_separate_from_generation(self):
        raw = spec_data()
        raw['execution'] = {'order': 'seeded_randomized', 'order_seed': 71}
        a, b = plan(raw), plan(raw)
        self.assertEqual(a, b)
        self.assertEqual([t.generation_order_index for t in a.trials], list(range(108)))
        self.assertEqual(sorted(t.execution_order_index for t in a.trials), list(range(108)))
        self.assertNotEqual([t.execution_order_index for t in a.trials], list(range(108)))
        raw['execution']['order_seed'] = 72
        self.assertNotEqual(a.plan_sha256, plan(raw).plan_sha256)


class SweepIdentityTests(unittest.TestCase):
    def test_strict_spec_and_plan_roundtrip(self):
        spec = SweepSpec.from_dict(spec_data())
        self.assertEqual(spec, SweepSpec.from_json(spec.to_json()))
        result = plan()
        self.assertEqual(result, verify_plan(result.to_json()))

    def test_immutable_defensive_copy(self):
        raw, ctx = spec_data(), context_data()
        result = plan(raw, ctx)
        serialized = result.to_json()
        raw['axes'][0]['values'].clear()
        ctx['models'][0]['installed_workers'].clear()
        exported = result.to_dict()
        exported['cells'].clear()
        self.assertEqual(serialized, result.to_json())
        with self.assertRaises(dataclasses.FrozenInstanceError): result.executable = True
        self.assertIsInstance(result.spec.base.worker_ids, tuple)

    def test_mapping_order_and_numeric_spellings_canonical_axis_order_semantic(self):
        a = spec_data()
        a['base']['temperature'] = -0.0
        b = dict(reversed(list(a.items())))
        b['base'] = dict(a['base'], temperature=0)
        self.assertEqual(plan(a).plan_sha256, plan(b).plan_sha256)
        b['axes'] = list(reversed(a['axes']))
        self.assertNotEqual(plan(a).plan_sha256, plan(b).plan_sha256)

    def test_model_binary_quantization_template_and_revision_are_semantic(self):
        original = plan()
        for field, value in [('artifact_sha256', 'd'*64), ('quantization', 'Q8_0'),
                             ('source_revision', 'd'*40), ('template_sha256', 'e'*64)]:
            with self.subTest(field=field):
                ctx = context_data(); ctx['models'][0][field] = value
                changed = plan(context=ctx)
                self.assertNotEqual(changed.cells[0].cell_id, original.cells[0].cell_id)
                self.assertNotEqual(changed.plan_sha256, original.plan_sha256)

    def test_observed_status_does_not_change_semantic_plan_hash(self):
        ctx = context_data(); ctx['workers'][0]['availability'] = 'blocked'
        result = plan(context=ctx)
        self.assertEqual(result.plan_sha256, plan().plan_sha256)
        self.assertEqual(result.counts.blocked_cells, 36)

    def test_plan_tamper_detects_hash_ids_counts_config_and_approval(self):
        result = plan().to_dict()
        changes = [lambda r: r.update(plan_sha256='0'*64),
                   lambda r: r['cells'][0].update(cell_id='cell_'+'0'*64),
                   lambda r: r['trials'][0].update(sweep_repeat_index=2),
                   lambda r: r['counts'].update(trials=1),
                   lambda r: r.update(executable=False),
                   lambda r: r['spec']['base'].update(n_ctx=8192),
                   lambda r: r.update(unexpected=True)]
        for change in changes:
            raw = copy.deepcopy(result); change(raw)
            with self.assertRaisesRegex(DomainValidationError, 'integrity mismatch'):
                verify_plan(json.dumps(raw))

    def test_duplicate_json_keys_including_rpc_weight_keys_rejected(self):
        for value in ['{"base":{},"base":{}}', '{"weights_by_worker":{"j1":1,"j1":2}}']:
            with self.assertRaises(DomainValidationError): SweepSpec.from_json(value)

    def test_no_raw_prompt_in_public_plan_and_no_formal_namespace(self):
        result = plan()
        self.assertNotIn('sweep validation only', result.to_json())
        for field in ('prompt', 'campaign_id', 'repeat_index', 'formal_execution_allowed', 'ignored_config_keys'):
            raw = spec_data(); raw['base'][field] = 'secret-test-input'
            with self.assertRaises(DomainValidationError): plan(raw)


class SweepCapabilityTests(unittest.TestCase):
    def codes(self, result):
        return {check.code for cell in result.cells for check in cell.capabilities}

    def test_future_runtime_axes_retained_and_blocked(self):
        for axis in ('n_threads', 'n_batch'):
            raw = spec_data(); raw['axes'] = [dict(name=axis, values=[2, 4])]
            result = plan(raw)
            self.assertEqual(result.counts.blocked_cells, 2)
            self.assertEqual([getattr(c.condition, axis) for c in result.cells], [2, 4])
            self.assertIn('WORKER_LOAD_PROFILE_UNVERIFIED', self.codes(result))

    def test_rpc_ordinary_gpu_is_blocked_across_combination_modes(self):
        for mode in ('grid', 'one_at_a_time', 'explicit'):
            for values in ([0, 120], [30, 120]):
                with self.subTest(mode=mode, values=values):
                    raw = rpc_spec()
                    raw.update(combination=mode, repeat_count=1)
                    raw['base']['rpc_profile_ref'] = 'two-auto'
                    raw['axes'] = [dict(name='n_gpu_layers', values=values)]
                    if mode == 'explicit':
                        raw.update(axes=[], explicit=[dict(raw['base'], n_gpu_layers=n) for n in values])
                    result = plan(raw)
                    self.assertEqual(result.counts.valid_cells, 0)
                    self.assertTrue(all(c.status == 'blocked' for c in result.cells))
                    self.assertTrue(all(t.status == 'blocked' for t in result.trials))
                    self.assertIn('ORDINARY_GPU_AXIS_NOT_APPLICABLE_TO_RPC', self.codes(result))
                    self.assertEqual(verify_plan(result.to_json()), result)

    def test_rpc_fixed_gpu_rejected_but_omission_and_ordinary_runs_preserved(self):
        raw = rpc_spec()
        raw.update(axes=[], repeat_count=1)
        raw['base']['rpc_profile_ref'] = 'two-auto'
        self.assertEqual(plan(raw).counts.valid_cells, 1)
        for value in (0, 120):
            raw['base']['n_gpu_layers'] = value
            self.assertEqual(plan(raw).counts.blocked_cells, 1)
        ordinary = dict(spec_data()['base'], n_gpu_layers=0)
        raw.update(combination='explicit', explicit=[ordinary, dict(ordinary, n_gpu_layers=120)])
        self.assertEqual(plan(raw).counts.valid_cells, 2)
        raw['explicit'].append(dict(raw['base']))
        result = plan(raw)
        self.assertEqual([c.status for c in result.cells], ['valid', 'valid', 'blocked'])

    def test_rpc_integer_gpu_and_row_uncertainty_not_rewritten(self):
        raw = rpc_spec(); raw['rpc_profiles'][0].update(rpc_gpu_layers=0, split_mode='row')
        result = plan(raw)
        self.assertNotIn('RPC_GPU_POLICY_NOT_IMPLEMENTED', self.codes(result))
        self.assertEqual(result.cells[0].rpc_profile.split_mode, 'row')
        self.assertEqual(result.cells[0].rpc_profile.rpc_gpu_layers, 0)
        self.assertTrue(any(c.status == 'unknown' and c.code == 'RPC_MODE_CAPABILITY' for c in result.cells[0].capabilities))

    def test_invalid_runtime_mode_blocks_with_evidence(self):
        ctx = context_data(); ctx['workers'][0]['rpc_layer'] = 'blocked'
        result = plan(rpc_spec(), ctx)
        self.assertEqual(result.counts.blocked_cells, 12)

    def test_missing_model_worker_prompt_and_unknown_are_not_removed(self):
        ctx = context_data(); ctx['models'] = []; ctx['workers'] = []; ctx['prompts'] = []
        result = plan(context=ctx)
        self.assertEqual((len(result.cells), len(result.trials)), (36, 108))
        self.assertTrue({'MODEL_REFERENCE_UNRESOLVED', 'WORKER_REFERENCE_UNRESOLVED', 'PROMPT_VARIANT_UNRESOLVED'} <= self.codes(result))
        ctx = context_data(); ctx['prompts'][0].update(input_tokens_exact=False, rendered_input_tokens=None, input_token_source='unavailable')
        result = plan(context=ctx)
        self.assertEqual((result.counts.valid_cells, result.counts.unknown_cells), (18, 18))
        self.assertIn('TOKEN_BUDGET_UNKNOWN', self.codes(result))

    def test_context_overflow_including_output_reserve_and_token_target(self):
        raw = spec_data(); raw['axes'] = [dict(name='n_ctx', values=[128, 1024])]
        result = plan(raw)
        self.assertEqual([c.status for c in result.cells], ['blocked', 'valid'])
        ctx = context_data(); ctx['prompts'] = [ctx['prompts'][0]]
        ctx['prompts'][0].update(mode='token_length_profile', target_input_tokens=65)
        result = plan(raw, ctx)
        self.assertIn('TOKEN_PROFILE_TARGET_MISMATCH', self.codes(result))

    def test_different_model_templates_and_token_counts_preserved(self):
        result = plan()
        self.assertEqual(result.cells[0].prompt.rendered_input_tokens, 64)
        self.assertEqual(result.cells[18].prompt.rendered_input_tokens, 80)
        self.assertNotEqual(result.cells[0].prompt.template_sha256, result.cells[18].prompt.template_sha256)
        self.assertEqual(result.cells[0].prompt.text_sha256, result.cells[18].prompt.text_sha256)

    def test_pi_layers_and_alias_duplicate_rejected_as_blocked_rows(self):
        ctx = context_data(); ctx['workers'][0]['platform'] = 'raspberry-pi'
        self.assertIn('PI_REQUIRES_CPU_ONLY', self.codes(plan(context=ctx)))
        raw = spec_data(); raw['base']['n_gpu_layers'] = 0
        self.assertNotIn('PI_REQUIRES_CPU_ONLY', self.codes(plan(raw, ctx)))
        raw['base']['worker_ids'] = ['j1', 'j2']
        ctx['workers'][1]['endpoint_identity'] = ctx['workers'][0]['endpoint_identity']
        self.assertIn('DUPLICATE_PHYSICAL_WORKER', self.codes(plan(raw, ctx)))

    def test_multipart_only_blocks_that_model(self):
        ctx = context_data(); ctx['models'][1]['artifact_kind'] = 'artifact_set'
        result = plan(context=ctx)
        self.assertEqual((result.counts.valid_cells, result.counts.blocked_cells), (18, 18))

    def test_parallel_declares_durable_resource_coordination(self):
        raw = spec_data(); raw['execution'] = {'mode': 'disjoint_parallel', 'max_parallel_jobs': 2}
        result = plan(raw)
        self.assertTrue(result.executable)
        self.assertEqual(result.spec.execution.max_parallel_jobs, 2)
        self.assertIn('DURABLE_RESOURCE_RESERVATIONS_AVAILABLE', [c.code for c in result.capabilities])


class SweepValidationTests(unittest.TestCase):
    def test_invalid_scalar_axis_values(self):
        cases = [('concurrency', True), ('n_ctx', 127), ('n_ctx', 16385), ('n_threads', 0),
                 ('n_batch', False), ('seed', 1.5), ('temperature', float('nan')),
                 ('top_p', float('inf')), ('max_tokens', -1), ('model_ref', '../escape'),
                 ('rpc_profile_ref', ['not-atomic']), ('n_gpu_layers', 121)]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                raw = spec_data(); raw['axes'] = [dict(name=key, values=[value])]
                with self.assertRaises(DomainValidationError): plan(raw)

    def test_unknown_empty_axes_duplicates_and_template_are_errors(self):
        for axes in ([dict(name='top_k', values=[1])], [dict(name='n_ctx', values=[])],
                     [dict(name='n_ctx', values=[1024])]*2):
            raw = spec_data(); raw['axes'] = axes
            with self.assertRaises(DomainValidationError): plan(raw)
        for key, value in [('artifact_type', 'sweep_spec_template'), ('schema_version', True),
                           ('repeat_count', True), ('mode', 'formal'), ('example_only', True),
                           ('fingerprint', '0'*64)]:
            raw = spec_data(); raw[key] = value
            with self.assertRaises(DomainValidationError): plan(raw)

    def test_negative_timeout_and_policy_limits(self):
        raw = spec_data(); raw['base']['request_timeout_s'] = -1
        with self.assertRaises(DomainValidationError): plan(raw)
        for policy in ({'max_parallel_jobs': 2}, {'mode': 'disjoint_parallel', 'max_parallel_jobs': 3},
                       {'cooldown_s': -1}, {'order_seed': True}, {'retry_policy': 'automatic'}):
            raw = spec_data(); raw['execution'] = policy
            with self.assertRaises(DomainValidationError): plan(raw)
        raw = spec_data(); raw['budget'] = {'max_trials': 501}
        with self.assertRaises(DomainValidationError): plan(raw)

    def test_profile_malformed_and_exact_weight_keys(self):
        valid = dict(profile_id='p', worker_ids=['j1', 'j2'], coordinator_id='j1', split_policy='custom', weights_by_worker={'j1': 1, 'j2': 2})
        cases = [dict(worker_ids=['j1', 'j1']), dict(worker_ids=['j1']), dict(coordinator_id='controller'),
                 dict(weights_by_worker={'j1': 1}), dict(weights_by_worker={'j1': 1, 'j2': 2, 'j3': 1}),
                 dict(weights_by_worker={'j1': 0, 'j2': 1}), dict(weights_by_worker={'j1': float('nan'), 'j2': 1}),
                 dict(weights_by_worker={'j1': True, 'j2': 1}), dict(rpc_gpu_layers=True),
                 dict(split_policy='auto'), dict(split_mode='tensor')]
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(DomainValidationError): RpcProfile.from_dict({**valid, **case})

    def test_profile_reference_and_worker_set_are_structural_errors(self):
        raw = rpc_spec(); raw['axes'][1]['values'] = ['missing']
        with self.assertRaises(DomainValidationError): plan(raw)
        raw = rpc_spec(); raw['base']['worker_ids'] = ['j1']
        with self.assertRaises(DomainValidationError): plan(raw)
        raw = spec_data(); raw['base']['worker_ids'] = ['j1', 'j1']
        with self.assertRaises(DomainValidationError): plan(raw)
        raw = spec_data(); raw['base']['execution_strategy'] = 'single_node'; raw['base']['worker_ids'] = ['j1', 'j2']
        with self.assertRaises(ValueError): plan(raw)

    def test_context_duplicate_ref_same_text_conflict_and_exact_without_source(self):
        for mutate in (lambda c: c['models'].append(c['models'][0]),
                       lambda c: c['prompts'][1].update(text_sha256='d'*64),
                       lambda c: c['prompts'][0].update(input_token_source='unavailable')):
            ctx = context_data(); mutate(ctx)
            with self.assertRaises(DomainValidationError): plan(context=ctx)

    def test_direct_construction_cannot_bypass_compile_validation(self):
        spec = SweepSpec.from_dict(spec_data())
        spec = dataclasses.replace(spec, repeat_count=True)
        with self.assertRaises(DomainValidationError): compile_plan(spec, ResolutionContext.from_dict(context_data()))

    def test_document_limit_and_nonfinite_json(self):
        for value in ('{"base": NaN}', '{"base": Infinity}', '{"x":"' + 'x'*1_048_576 + '"}', '[]'):
            with self.assertRaises(DomainValidationError): SweepSpec.from_json(value)


class SweepWorkloadTests(unittest.TestCase):
    def test_existing_strategy_planner_agreement_without_nested_double_count(self):
        for strategy, mode, expected in (
            ('replicated_round_robin', 'cumulative', (1, 5, 5)),
            ('broadcast_compare', 'cumulative', (1, 5, 15)),
            ('node_sweep', 'cumulative', (3, 15, 15)),
            ('node_sweep', 'individual', (3, 15, 15)),
        ):
            with self.subTest(strategy=strategy, mode=mode):
                raw = {'base': dict(spec_data()['base'], worker_ids=['j3', 'j1', 'j2'],
                                    execution_strategy=strategy, sweep_mode=mode, requests=5, warmup_requests=2),
                       'repeat_count': 2}
                result = plan(raw); work = result.cells[0].workload
                self.assertEqual((work.scenarios, work.logical_requests, work.physical_requests), expected)
                config = ExperimentConfig(node_names=['j3', 'j1', 'j2'], execution_strategy=strategy, sweep_mode=mode, requests=5)
                nodes = [sweep_planner._Participant(name) for name in config.node_names]
                scenarios = build_strategy_scenarios(config, nodes)
                self.assertEqual(sum(len(s.tasks) for s in scenarios), work.physical_requests)
                self.assertEqual(result.counts.workload.warmup_calls, 12)
                self.assertEqual(result.counts.workload.model_loads, 6)
                self.assertIsNone(work.duration_s); self.assertIsNone(work.storage_bytes)

    def test_physical_request_budget_includes_warmup_and_repeats(self):
        raw = {'base': dict(spec_data()['base'], requests=5, warmup_requests=1), 'repeat_count': 2,
               'budget': {'max_physical_requests': 11}}
        with self.assertRaisesRegex(DomainValidationError, 'including warmup'): plan(raw)
        raw['budget']['max_physical_requests'] = 12
        self.assertEqual(plan(raw).counts.workload.physical_requests, 10)

    def test_pure_module_boundary(self):
        forbidden = {'subprocess', 'socket', 'urllib', 'httpx', 'requests', 'fastapi', 'pydantic', 'os', 'pathlib'}
        for module in (sweep, sweep_planner):
            tree = ast.parse(inspect.getsource(module))
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import): imports.extend(alias.name for alias in node.names)
                if isinstance(node, ast.ImportFrom): imports.append(node.module or '')
            self.assertFalse(forbidden & {name.split('.')[0] for name in imports})
            self.assertFalse(any(name.startswith(('cluster.dashboard', 'cluster.worker', 'cluster.benchmark.runner')) for name in imports))


if __name__ == '__main__':
    unittest.main()
