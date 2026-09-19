"""S02: synthetic four-byte artifacts and injected factories; no native runtime."""
import dataclasses
import hashlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from cluster.domain.experiment import ExperimentConfig, normalized_config_identity
from cluster.domain.runtime_profile import require_applied_profile
from cluster.worker.inference import LlamaCppInferenceBackend
from cluster.worker.prompt_preparation import capture_chat_input, PreparationError
from cluster.worker.routes import mount_worker_routes, WorkerRuntimeInfo
from cluster.dashboard.schemas import ExperimentPayload
from cluster.benchmark.runner import _load_model
from cluster.benchmark.persistence import RunPersistence
from cluster.tests.test_worker_runtime import FakeTelemetry, FakeInferenceBackend
from cluster.tests.test_sweep_planner import plan, context_data, spec_data


TEXT = '  private test prompt  '
SHA = hashlib.sha256(b'gguf').hexdigest()
TEMPLATE = hashlib.sha256(b'fake-special-template').hexdigest()


def trace():
    return dict(sweep_id='sweep-test', plan_sha256='a'*64, cell_id='cell-test',
                trial_id='trial-test', attempt_id='attempt-test', sweep_repeat_index=1,
                model_sha256=SHA, template_sha256=TEMPLATE,
                prompt_sha256=hashlib.sha256(TEXT.encode()).hexdigest(), prompt_mode='same_text')


class FakeLlama:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.n_batch = kwargs['n_batch']
        self.n_threads = kwargs['n_threads']
        self.calls = []

    def n_ctx(self):
        return self.kwargs['n_ctx']

    def set_seed(self, seed):
        self.seed = seed

    def tokenize(self, text, **kwargs):
        return [1, 2]

    def create_completion(self, **kwargs):
        self.calls.append(kwargs)
        yield {'choices': [{'text': 'early', 'finish_reason': None}]}
        yield {'choices': [{'text': '', 'finish_reason': 'stop'}]}

    create_chat_completion = create_completion


def preparer(llm, messages):
    return dict(prompt=[1, 10, 11, 2], stop=['eos']), TEMPLATE


class ContextRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'tiny.gguf').write_bytes(b'gguf')
        self.instances = []
        def factory(**kwargs):
            instance = FakeLlama(**kwargs)
            self.instances.append(instance)
            return instance
        self.factory = factory
        self.backend = LlamaCppInferenceBackend(self.root, llama_factory=factory,
            torch_module=types.SimpleNamespace(), chat_preparer=preparer)

    def client(self, platform='jetson', backend=None):
        app = FastAPI()
        mount_worker_routes(app, backend=backend or self.backend, telemetry=FakeTelemetry(),
            runtime=WorkerRuntimeInfo('w1', 'worker', 'fake', platform, platform, None, {}, False))
        return TestClient(app)

    def config(self, **extra):
        return ExperimentConfig(node_names=['w1'], model_id='tiny.gguf', prompt=TEXT,
                                n_ctx=128, n_gpu_layers=0, **extra)

    def prepare_args(self, **extra):
        return dict(preparation_id='attempt-test', message=TEXT, history=[], max_tokens=16,
                    model_sha256=SHA, template_sha256=TEMPLATE,
                    prompt_sha256=hashlib.sha256(TEXT.encode()).hexdigest(), **extra)

    def test_api_config_runner_worker_factory_two_values(self):
        client = self.client()
        node = types.SimpleNamespace(name='w1', api_url='http://fake')
        for ctx, gpu, threads, batch in [(128, 0, 1, 32), (256, 4, 2, 64)]:
            with self.subTest(ctx=ctx):
                payload = ExperimentPayload(node_names=['w1'], model_id='tiny.gguf', prompt=TEXT, n_ctx=ctx,
                                            n_gpu_layers=gpu, n_threads=threads, n_batch=batch)
                cfg = ExperimentConfig.from_dict(payload.model_dump())
                cfg.validate()
                def request(url, method='GET', payload=None, **kwargs):
                    if method == 'POST':
                        response = client.post('/api/select-model', json=payload)
                        self.assertEqual(response.status_code, 200)
                        return response.json()
                    return {'profile': {}}
                with mock.patch('cluster.benchmark.runner.request_json', side_effect=request):
                    info = _load_model(node, cfg)
                self.assertEqual(info['factory_config'], dict(n_ctx=ctx, n_gpu_layers=gpu, n_threads=threads, n_batch=batch))
                self.assertIsNone(info['effective_config']['n_gpu_layers'])
                self.assertEqual(info['effective_config']['n_threads'], threads)
                self.assertEqual(self.instances[-1].kwargs['n_batch'], batch)

    def test_omission_preserves_three_argument_legacy_backend(self):
        backend = FakeInferenceBackend()
        response = self.client(backend=backend).post('/api/select-model', json={'model_id':'tiny.gguf', 'n_ctx':128,'n_gpu_layers':0})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(backend.load_calls, [('tiny.gguf',128,0)])
        identity = normalized_config_identity(self.config())
        self.assertTrue({'n_threads','n_batch','sweep'}.isdisjoint(identity))

    def test_cache_identity_covers_every_load_parameter(self):
        kwargs = dict(n_ctx=128, n_gpu_layers=0, n_threads=1, n_batch=32)
        self.backend.load_model('tiny.gguf', **kwargs)
        self.backend.load_model('tiny.gguf', **kwargs)
        self.assertEqual(len(self.instances), 1)
        for name, value in [('n_ctx',256),('n_gpu_layers',4),('n_threads',2),('n_batch',64)]:
            kwargs[name] = value
            self.backend.load_model('tiny.gguf', **kwargs)
        self.assertEqual(len(self.instances), 5)

    def test_same_path_binary_replacement_reloads_even_with_mtime_restored(self):
        self.backend.load_model('tiny.gguf',128,0)
        path=self.root/'tiny.gguf'; stat=path.stat()
        path.write_bytes(b'GGUF'); os.utime(path, ns=(stat.st_atime_ns,stat.st_mtime_ns))
        info=self.backend.load_model('tiny.gguf',128,0)
        self.assertEqual(len(self.instances),2)
        self.assertNotEqual(info['model_sha256'],SHA)

    def test_batch_clamp_disclosed_and_strict_caller_rejects(self):
        info=self.backend.load_model('tiny.gguf',128,0,n_batch=256)
        self.assertEqual(info['requested_config']['n_batch'],256)
        self.assertEqual(info['factory_config']['n_batch'],128)
        self.assertIn('n_batch',info['adjustment_reasons'])
        with self.assertRaisesRegex(ValueError,'MISMATCH'):
            require_applied_profile(info,self.config(n_batch=256,require_uniform_config=False))

    def test_unknown_effective_values_never_copied_from_request(self):
        self.backend._llama_factory=lambda **kw: object()
        info=self.backend.load_model('tiny.gguf',128,0,n_threads=2)
        self.assertIsNone(info['effective_config']['n_threads'])
        self.assertEqual(info['effective_sources']['n_threads'],'unavailable')
        with self.assertRaisesRegex(ValueError,'UNVERIFIED'):
            require_applied_profile(info,self.config(n_threads=2))

    def test_sweep_context_gpu_retry_not_accepted_even_uniform_false(self):
        def factory(**kw):
            if kw['n_ctx']>128 or kw['n_gpu_layers']>0: raise ValueError('fake OOM')
            return self.factory(**kw)
        self.backend._llama_factory=factory
        info=self.backend.load_model('tiny.gguf',256,4)
        self.assertEqual(info['factory_config']['n_ctx'],128)
        cfg=self.config(sweep=trace(),require_uniform_config=False)
        with self.assertRaisesRegex(ValueError,'SWEEP_CONDITION_MISMATCH'):
            require_applied_profile(info,cfg)

    def test_exact_preparation_special_tokens_budget_and_private_storage(self):
        self.backend.load_model('tiny.gguf',128,0)
        result=self.backend.prepare_input(**self.prepare_args())
        self.assertEqual(result['input_tokens'],4)
        self.assertTrue(result['input_tokens_exact'])
        self.assertNotIn(TEXT,json.dumps(result))
        self.assertNotIn('prompt',result)
        self.assertEqual(self.instances[-1].calls,[])
        args=self.prepare_args();args['max_tokens']=125
        with self.assertRaisesRegex(PreparationError,'BUDGET'):
            self.backend.prepare_input(**args)
        self.assertEqual(self.instances[-1].calls,[])

    def test_prepared_tokens_reused_and_early_eos_distinct_from_limit(self):
        self.backend.load_model('tiny.gguf',128,0)
        self.backend.prepare_input(**self.prepare_args())
        for limit,temp,top_p,seed in [(8,0.0,0.8,3),(16,0.7,0.9,4)]:
            out={}
            result=list(self.backend.stream_chat(message=TEXT,history=[],max_tokens=limit,
                temperature=temp,top_p=top_p,seed=seed,trace=out,prepared_input_id='attempt-test'))
            self.assertEqual(result,['early'])
            self.assertEqual(out['finish_reason'],'stop')
            self.assertEqual(out['effective_max_tokens'],limit)
            call=self.instances[-1].calls[-1]
            self.assertEqual(call['prompt'],[1,10,11,2])
            self.assertEqual((call['temperature'],call['top_p'],call['seed']),(temp,top_p,seed))

    def test_profile_target_model_template_and_prompt_mismatch_fail(self):
        self.backend.load_model('tiny.gguf',128,0)
        for change in [dict(target_input_tokens=5),dict(template_sha256='0'*64),
                       dict(model_sha256='0'*64),dict(prompt_sha256='0'*64)]:
            with self.subTest(change=change), self.assertRaises(PreparationError):
                self.backend.prepare_input(**{**self.prepare_args(),**change})
        result=self.backend.prepare_input(**self.prepare_args(target_input_tokens=4))
        self.assertEqual(result['input_tokens'],4)

    def test_different_models_use_different_template_and_token_count(self):
        self.backend.load_model('tiny.gguf',128,0)
        first=self.backend.prepare_input(**self.prepare_args())
        (self.root/'other.gguf').write_bytes(b'other')
        self.backend._chat_preparer=lambda llm,msg:(dict(prompt=[1,2,3,4,5]),'b'*64)
        info=self.backend.load_model('other.gguf',128,0)
        second=self.backend.prepare_input(**{**self.prepare_args(),'model_sha256':info['model_sha256'],'template_sha256':'b'*64})
        self.assertNotEqual(first['input_tokens'],second['input_tokens'])
        self.assertEqual(first['prompt_sha256'],second['prompt_sha256'])

    def test_prepared_binding_and_reload_invalidation(self):
        self.backend.load_model('tiny.gguf',128,0)
        self.backend.prepare_input(**self.prepare_args())
        params=dict(message=TEXT,history=[],max_tokens=16,temperature=0,top_p=1,prepared_input_id='attempt-test')
        with self.assertRaisesRegex(PreparationError,'MISMATCH'):
            list(self.backend.stream_chat(**{**params,'message':TEXT.strip()}))
        self.backend.load_model('tiny.gguf',256,0)
        with self.assertRaisesRegex(PreparationError,'STALE'):
            list(self.backend.stream_chat(**params))

    def test_prepare_and_stream_http_preserve_proof_and_privacy(self):
        self.backend.load_model('tiny.gguf',128,0)
        client=self.client()
        response=client.post('/cluster/input/prepare',json=self.prepare_args())
        self.assertEqual(response.status_code,200)
        self.assertNotIn(TEXT,response.text)
        response=client.post('/cluster/chat/stream',json=dict(message=TEXT,max_tokens=8,prepared_input_id='attempt-test'))
        events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        metrics=events[-1]['metrics']
        self.assertEqual(metrics['finish_reason'],'stop')
        self.assertEqual(metrics['input_tokens'],4)
        self.assertEqual(metrics['requested_max_tokens'],8)
        self.assertEqual(metrics['effective_n_ctx'],128)
        self.assertEqual(metrics['prompt_sha256'],trace()['prompt_sha256'])

    def test_pi_gpu_and_unknown_load_option_rejected(self):
        client=self.client('raspberry-pi')
        response=client.post('/api/select-model',json=dict(model_id='tiny.gguf',n_gpu_layers=4))
        self.assertNotEqual(response.status_code,200)
        response=client.post('/api/select-model',json=dict(model_id='tiny.gguf',n_gpu_layers=0,flash_attention=True))
        self.assertEqual(response.status_code,422)
        self.assertEqual(self.instances,[])

    def test_old_worker_cannot_silently_ignore_new_options(self):
        with self.assertRaisesRegex(ValueError,'MISMATCH'):
            require_applied_profile(dict(n_threads=2),self.config(n_threads=2))

    def test_trace_roundtrip_formal_separation_and_rpc_deferred(self):
        cfg=self.config(sweep=trace(),n_threads=2,n_batch=32)
        cfg.validate()
        restored=ExperimentConfig.from_dict(dataclasses.asdict(cfg),strict=True)
        self.assertEqual(restored,cfg)
        for change in [dict(campaign_id='campaign-1'),dict(pilot_id='pilot_1'),dict(experiment_type='formal'),dict(execution_strategy='model_parallel_rpc')]:
            with self.subTest(change=change),self.assertRaises(ValueError):
                dataclasses.replace(cfg,**change).validate()
        with self.assertRaises(ValueError):
            self.config(sweep={**trace(),'prompt':TEXT}).validate()
        for key in ('n_threads','n_batch'):
            with self.assertRaises(ValueError):self.config(**{key:True}).validate()

    def test_persistence_privacy_and_finish_reason_without_csv_change(self):
        for mode in ('full','hash_only','none'):
            cfg=self.config(sweep=trace(),persist_prompt=False,response_storage_mode=mode)
            store=RunPersistence(self.root,mode,cfg)
            raw=dict(response='early',finish_reason='stop',input_tokens=4,input_tokens_exact=True,
                     effective_n_ctx=128,requested_max_tokens=16,effective_max_tokens=16)
            saved=store._response_record(raw)
            self.assertEqual(saved['finish_reason'],'stop')
            self.assertEqual(saved['sweep'],trace())
            self.assertNotIn(TEXT,json.dumps(saved))
            if mode!='full': self.assertNotIn('response',saved)

    def test_fake_http_runner_prepares_before_warmup_and_persists_proof(self):
        import io
        from cluster.benchmark.core import BenchmarkRunner
        from cluster.benchmark.executor import ScenarioExecutor
        from cluster.benchmark.transport import stream_worker_request
        from cluster.domain.worker import WorkerNode
        client = self.client()
        node = WorkerNode('w1', '127.0.0.2', 'fake', 22, 8000, '/home/fake/project')
        calls = []
        def request(url, method='GET', payload=None, **kwargs):
            path = '/' + url.split('/', 3)[-1]
            calls.append(path)
            if method == 'POST':
                response = client.post(path, json=payload)
                if response.status_code != 200:
                    raise ValueError(response.json()['detail'])
                return response.json()
            return {'profile': {}}
        def open_stream(request, **kwargs):
            calls.append('/cluster/chat/stream')
            response = client.post('/cluster/chat/stream', json=json.loads(request.data))
            return io.BytesIO(response.content)
        runner = BenchmarkRunner(_load_model, lambda *a: [], lambda *a: None,
            ScenarioExecutor(stream_worker_request, mock.Mock(side_effect=AssertionError('native RPC'))),
            mock.Mock())
        cfg = self.config(sweep=trace(),max_tokens=16,requests=1,warmup_requests=1,
                          persist_prompt=False,response_storage_mode='hash_only')
        with mock.patch('cluster.benchmark.runner.request_json',side_effect=request), mock.patch(
                'cluster.benchmark.transport.urllib.request.urlopen',side_effect=open_stream):
            summary = runner.run(cfg,[node],self.root/'results')
        self.assertEqual(summary['status'],'completed')
        self.assertLess(calls.index('/cluster/input/prepare'),calls.index('/cluster/chat/stream'))
        self.assertEqual(len(self.instances[-1].calls),2)  # one warmup, one measured
        result_dir = Path(summary['result_dir'])
        responses = (result_dir/'responses.jsonl').read_text()
        self.assertIn('"finish_reason": "stop"',responses)
        self.assertNotIn(TEXT,responses)
        self.assertNotIn('"response":',responses)
        self.assertIn('"effective_max_tokens": 16',responses)

    def test_core_rejects_bad_proof_before_warmup_even_uniform_disabled(self):
        from cluster.benchmark.core import BenchmarkRunner
        from cluster.domain.worker import WorkerNode
        node = WorkerNode('w1','127.0.0.2','fake',22,8000,'/home/fake/project')
        cfg = self.config(sweep=trace(),max_tokens=16,require_uniform_config=False)
        info = self.backend.load_model('tiny.gguf',128,0)
        good = self.backend.prepare_input(**self.prepare_args())
        for bad in ({},dict(good,input_tokens=127),dict(good,model_sha256='0'*64)):
            executor=mock.Mock()
            runner=BenchmarkRunner(lambda *a:dict(info,input_preparation=bad),lambda *a:[],
                lambda *a:None,executor,mock.Mock())
            with self.assertRaisesRegex(ValueError, 'SWEEP_INPUT'):
                runner.run(cfg,[node],self.root/'rejected')
            executor.warmup.assert_not_called()
            executor.run.assert_not_called()

    def test_preparation_unsupported_has_safe_reason_and_no_generation(self):
        self.backend.load_model('tiny.gguf',128,0)
        self.backend._chat_preparer=mock.Mock(side_effect=RuntimeError(TEXT))
        response=self.client().post('/cluster/input/prepare',json=self.prepare_args())
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()['detail'],'EXACT_INPUT_UNAVAILABLE')
        self.assertNotIn(TEXT,response.text)
        self.assertEqual(self.instances[-1].calls,[])

    def test_default_batch_fallback_is_a_sweep_mismatch(self):
        def factory(**kw):
            if kw['n_batch']>64: raise ValueError('fake OOM')
            return self.factory(**kw)
        self.backend._llama_factory=factory
        info=self.backend.load_model('tiny.gguf',128,0)
        self.assertIn('n_batch',info['adjustment_reasons'])
        with self.assertRaisesRegex(ValueError,'SWEEP_CONDITION_MISMATCH'):
            require_applied_profile(info,self.config(sweep=trace()))

    def test_rpc_finish_reason_and_output_limit_remain_distinct(self):
        import io
        from cluster.benchmark.transport import stream_rpc_request
        from cluster.benchmark.models import RequestTask
        cfg=self.config(max_tokens=64)
        node=types.SimpleNamespace(name='w1',host='127.0.0.2')
        data = (b'data: {"choices":[{"delta":{"content":"early"},"finish_reason":null}]}\n'
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":4,"completion_tokens":1}}\n'
                b'data: [DONE]\n')
        with mock.patch('cluster.benchmark.transport.urllib.request.urlopen',return_value=io.BytesIO(data)):
            result=stream_rpc_request(node,'http://fake',cfg,RequestTask(1,1,'scenario','w1'))
        self.assertEqual(result['finish_reason'],'stop')
        self.assertEqual(result['generated_tokens'],1)
        self.assertEqual(result['requested_max_tokens'],64)
        self.assertIsNone(result['effective_max_tokens'])

    def test_template_metadata_change_invalidates_load_cache(self):
        self.backend.load_model('tiny.gguf',128,0)
        with mock.patch.object(self.backend,'_read_model_metadata',return_value={
                'tiny.gguf':{'chat_template_hash':'b'*64}}):
            self.backend.load_model('tiny.gguf',128,0)
        self.assertEqual(len(self.instances),2)

    def test_loader_retains_actual_metadata_for_rejected_conditions(self):
        cfg=self.config(sweep=trace(),max_tokens=16)
        node=types.SimpleNamespace(name='w1',api_url='http://fake')
        info=self.backend.load_model('tiny.gguf',128,0)
        bad=dict(info,adjustment_reasons=['n_ctx'])
        with mock.patch('cluster.benchmark.runner.request_json',return_value={'ok':True,'current':bad}) as request:
            result=_load_model(node,cfg)
            self.assertEqual(request.call_count,1)
            self.assertEqual(result['factory_config'],info['factory_config'])
            self.assertEqual(result['adjustment_reasons'],['n_ctx'])
        with mock.patch('cluster.benchmark.runner.request_json',side_effect=[
                {'ok':True,'current':info},ValueError('CONTEXT_BUDGET_EXCEEDED '+TEXT)]):
            result=_load_model(node,cfg)
            self.assertEqual(result['input_preparation_error'],'CONTEXT_BUDGET_EXCEEDED')
            self.assertNotIn(TEXT,json.dumps(result))
            self.assertEqual(result['effective_config']['n_ctx'],128)

    def test_planner_load_profile_capability_required(self):
        raw=spec_data();raw['axes']=[dict(name='n_threads',values=[1,2])]
        ctx=context_data()
        self.assertEqual(plan(raw,ctx).counts.blocked_cells,2)
        for worker in ctx['workers']:worker['load_profile']='valid'
        result=plan(raw,ctx)
        self.assertEqual(result.counts.valid_cells,2)
        self.assertFalse(result.executable)


class PinnedPreparationAdapterTests(unittest.TestCase):
    def test_selected_formatter_special_token_call_without_evaluator(self):
        calls=[]
        chat_formatter=types.SimpleNamespace(template='synthetic-template')
        def handler(*,llama,messages,**kwargs):
            # A fake pinned formatter wrapper, including its captured template.
            rendered=chat_formatter.template+messages[-1]['content']
            tokens=llama.tokenize(rendered.encode(),add_bos=False,special=True)
            return llama.create_completion(prompt=tokens,stop=['eos'],repeat_penalty=kwargs['repeat_penalty'])
        handler.__module__='llama_cpp.llama_chat_format'
        handler.__qualname__='chat_formatter_to_chat_completion_handler.<locals>.chat_completion_handler'
        def tokenize(text,**kw):calls.append(kw);return [1,2,3]
        llm=types.SimpleNamespace(chat_handler=None,_chat_handlers={'chosen':handler},chat_format='chosen',tokenize=tokenize)
        module=types.ModuleType('llama_cpp');module.__version__='0.3.20';module.llama_chat_format=types.SimpleNamespace()
        with mock.patch.dict(sys.modules,{'llama_cpp':module}):
            args,sha=capture_chat_input(llm,[dict(role='user',content='text')])
            self.assertEqual(args['prompt'],[1,2,3]);self.assertEqual(args['repeat_penalty'],1.0)
            self.assertEqual(calls,[dict(add_bos=False,special=True)])
            self.assertEqual(sha,hashlib.sha256(b'synthetic-template').hexdigest())
            module.__version__='unverified'
            with self.assertRaisesRegex(PreparationError,'VERSION'):capture_chat_input(llm,[])
            module.__version__='0.3.20';llm.chat_handler=lambda **kw:None
            with self.assertRaisesRegex(PreparationError,'HANDLER'):capture_chat_input(llm,[])


if __name__=='__main__':unittest.main()
