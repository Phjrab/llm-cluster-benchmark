"""WS-MED: offline fixtures only; no native runtime, remote Worker or real dataset."""
import copy
import dataclasses
import hashlib
import io
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from cluster.medical.bank import question_bank, compose, sha, long_document_variants, model_mapping
from cluster.medical.planning import prepare, BLOCKS, inventory_workers
from cluster.medical.datasets import import_subset, SOURCES
from cluster.medical.evaluator import rubrics, evaluate
from cluster.medical.results import index_runs, blinded_export, report
from cluster.domain.sweep import WorkerReference, PromptVariant, SweepSpec
from cluster.application.sweep_models import preview_catalog_sweep, build_cell_config
from cluster.tests.test_sweep_models import fixtures
from cluster.tests.test_context_runtime import FakeLlama, preparer, TEMPLATE, SHA
from cluster.tests.test_worker_runtime import FakeTelemetry
from cluster.worker.inference import LlamaCppInferenceBackend
from cluster.worker.routes import mount_worker_routes, WorkerRuntimeInfo
from cluster.benchmark.core import BenchmarkRunner
from cluster.benchmark.executor import ScenarioExecutor
from cluster.benchmark.runner import _load_model
from cluster.benchmark.transport import stream_worker_request


def workers():
    return [WorkerReference(f'j{i}', f'physical-j{i}', 'jetson') for i in range(1,5)]


class MedicalPreparationTests(unittest.TestCase):
    def test_bank_hash_pairs_input_only_and_40_distinct_payloads(self):
        bank = question_bank()
        self.assertEqual(len(bank),48)
        self.assertEqual(len({v['paired_prompt_id'] for v in bank.values() if v.get('paired_prompt_id')}),8)
        bundle = prepare('B1',workers(),models=['M01'])
        self.assertEqual(bundle['counts']['runs'],160)
        self.assertEqual(bundle['unique_question_groups'],40)
        prompts = bundle['packages'][0]['request']['prompts']
        self.assertEqual(len({p['text'] for p in prompts}),40)
        self.assertEqual({p['ref'] for p in prompts},{k for k,v in bank.items() if v['language']=='ko'})
        for p in prompts:
            self.assertEqual(p['text'],compose(bank[p['ref']]))
            self.assertEqual(sha(p['text']),bundle['packages'][0]['questions'][p['ref']]['prompt_sha256'])
        self.assertNotIn('required_points',json.dumps(bundle))
        self.assertNotIn('reference_answer_structured',json.dumps(bundle))

    def test_blocks_counts_budget_and_sequential_policy(self):
        expected = {'B0':4,'B1':640,'B2':0,'B3':96,'B4a':18,'B4b':54,'B5':72,'B6-smoke':1,'B6':12,'B7-language':32,'B7-reasoning':1,'B7-quantization':0}
        for block in BLOCKS:
            b=prepare(block,workers())
            self.assertEqual(b['counts']['runs'],expected[block],block)
            for p in b['packages']:
                self.assertLessEqual(p['counts']['trials'],500)
                self.assertEqual(p['request']['spec']['execution']['max_parallel_jobs'],1)
                self.assertTrue(all(c['max_tokens']<=1024 for c in p['request']['spec']['explicit']))
        scaling=prepare('B3',workers())
        self.assertEqual(scaling['counts']['logical_requests'],1920)
        self.assertEqual(scaling['counts']['warmup_calls'],240)
        self.assertEqual(prepare('B7-language',workers())['unique_question_groups'],8)
        self.assertTrue(all(q['input_tokens'] is None for q in long_document_variants().values()))

    def test_invalid_mappings_rejected_and_privacy_preserved(self):
        for block,models in [('B1',['R01']),('B6',['R01','M01']),('B1',['M07']),('B1',['missing'])]:
            with self.assertRaises(ValueError): prepare(block,workers(),models=models)
        with self.assertRaises(ValueError): prepare('B0',[workers()[0]]*2)
        with self.assertRaises(ValueError): prepare('B0',[WorkerReference('mac','mac','unknown')])
        p=prepare('B0',workers(),persist_prompt=False,response_storage_mode='hash_only')['packages'][0]
        self.assertTrue(all(not c['persist_prompt'] and c['response_storage_mode']=='hash_only' for c in p['request']['spec']['explicit']))

    def test_soft_switch_only_qwen_and_reasoning_separate(self):
        q=prepare('B1',workers(),models=['M01','M03'],prompt_ids=['DOC02'])
        self.assertFalse(q['packages'][0]['request']['prompts'][0]['text'].endswith('/no_think'))
        self.assertTrue(q['packages'][1]['request']['prompts'][0]['text'].endswith('/no_think'))
        c=prepare('B7-reasoning',workers())['packages'][0]['request']['spec']['base']
        self.assertEqual((c['temperature'],c['max_tokens']),(0.6,1024))


class MedicalEvaluatorTests(unittest.TestCase):
    def score(self,pid,text,**extra):
        return evaluate(rubrics()[pid],dict(ok=True,response_storage_mode='full',response=text,**extra))

    def test_structured_correct_and_boolean_null_missing_distinct(self):
        answer='{"cough":true,"fever":false,"chest_pain":false,"dyspnea":null}'
        self.assertTrue(self.score('DOC02',answer)['correct'])
        for text in [answer.replace('true','1'), answer.replace('null','false'),answer.replace(',"dyspnea":null',''),answer[:-1]+',"extra":0}']:
            self.assertFalse(self.score('DOC02',text)['correct'])
        for text in ['```json\n'+answer+'\n```','{"cough":true,"cough":false}','{"cough":NaN}']:
            self.assertEqual(self.score('DOC02',text)['status'],'parse_failure')

    def test_document_aliases_dates_and_injection(self):
        key=rubrics()['DOC01'];answer=copy.deepcopy(key['reference_answer_structured']);answer['history']=['2형 당뇨병'];answer['laterality']='좌안'
        self.assertTrue(self.score('DOC01',json.dumps(answer))['correct'])
        dates=rubrics()['DOC03']['reference_answer_structured']
        self.assertTrue(self.score('DOC03',json.dumps(dates))['correct'])
        self.assertFalse(self.score('DOC03',json.dumps(list(reversed(dates))))['correct'])
        dates[0]['date']='2026-09-28'
        self.assertFalse(self.score('DOC03',json.dumps(dates))['correct'])
        self.assertFalse(self.score('DOC08','{"participants":999}')['correct'])

    def test_numeric_units_tolerance_and_partial_reason(self):
        self.assertTrue(self.score('EVID04','{"sensitivity":0.9,"specificity":0.9,"ppv":0.32143}')['correct'])
        self.assertFalse(self.score('EVID04','{"sensitivity":90,"specificity":90,"ppv":32.14}')['correct'])
        self.assertTrue(self.score('EVID01','{"difference_pp":20,"ratio":1.33}')['correct'])
        self.assertFalse(self.score('EVID01','{"difference_pp":0.2,"ratio":1.3333}')['correct'])
        partial=self.score('EVID08','{"supported":false,"evidence_ids":["S3"]}')
        self.assertFalse(partial['schema_valid']);self.assertIsNone(partial['correct'])

    def test_failure_privacy_refusal_and_manual_safety(self):
        key=rubrics()['DOC08']
        r=evaluate(key,dict(ok=False,error_code='TIMEOUT',response_storage_mode='full',response='{"participants":24}'))
        self.assertFalse(r['correct']);self.assertEqual(r['status'],'transport_failure')
        self.assertEqual(evaluate(key,dict(ok=True,response_storage_mode='hash_only'))['status'],'unscorable_privacy')
        self.assertEqual(self.score('DOC08','')['status'],'no_response')
        self.assertEqual(self.score('DOC08','refused',refusal=True)['status'],'refusal')
        manual=self.score('DOC06','text [N1] [N2] [N3] [BAD]')
        self.assertIsNone(manual['correct']);self.assertIsNone(manual['serious_safety_error'])
        self.assertEqual(manual['source_id_checks']['unexpected'],['BAD'])

    def test_think_wrapper_and_missing_final_answer(self):
        r=self.score('DOC08','<think>reasoning only')
        self.assertEqual(r['status'],'no_final_answer')
        self.assertFalse(r['correct'])
        r=self.score('DOC08','<think></think>{"participants":24}')
        self.assertTrue(r['correct']);self.assertFalse(r['thinking_output_observed'])

    def test_mcqa_strict_labels_and_all_failure_denominator(self):
        key={'prompt_id':'fake','scoring_kind':'mcqa','reference_answer_structured':{'answer':'C'},'allowed_labels':list('ABCDE')}
        for text,correct in [('{"answer":"C"}',True),('{"answer":"A"}',False),('{"answer":"C or A"}',False),('C',False)]:
            self.assertEqual(evaluate(key,dict(ok=True,response_storage_mode='full',response=text))['correct'],correct)


class PublicSubsetTests(unittest.TestCase):
    def test_local_kormed_subset_keys_disjoint_no_cot_and_stable(self):
        rows=[dict(subject='doctor',year=2020,period=1,q_number=i,question=f'question{i}',A='a',B='b',C='c',D='d',E='e',answer=1,cot='SECRET_KEY') for i in range(6)]
        kwargs=dict(dataset='kormedmcqa',config='doctor',split='test',revision=SOURCES['kormedmcqa'][1],answer_base=1,calibration=2,evaluation=4)
        a=import_subset(rows,**kwargs);b=import_subset(list(reversed(rows)),**kwargs)
        self.assertEqual(a,b)
        self.assertFalse(set(a['manifest']['calibration_ids']) & set(a['manifest']['evaluation_ids']))
        self.assertNotIn('SECRET_KEY',json.dumps(a))
        self.assertNotIn('answer',json.dumps(a['evaluation_questions']))
        bundle=prepare('B2',workers(),public_questions=a['evaluation_questions'])
        self.assertEqual(bundle['counts']['runs'],4)
        with self.assertRaises(ValueError):import_subset(rows,**{**kwargs,'answer_base':None})

    def test_pubmed_no_conclusion_or_predictions(self):
        row=dict(pubid=42,question='test?',context=dict(contexts=['permitted','SECRET_CONCLUSION'],labels=['RESULTS','CONCLUSIONS'],reasoning_required_pred='SECRET_PRED'),long_answer='SECRET_ANSWER',final_decision='maybe')
        result=import_subset([row],dataset='pubmedqa',config='pqa_labeled',split='train',revision=SOURCES['pubmedqa'][1],calibration=0,evaluation=1)
        self.assertNotIn('SECRET',json.dumps(result))
        self.assertEqual(result['evaluation_only_keys'][result['evaluation_questions'][0]['prompt_id']]['reference_answer_structured'],{'answer':'maybe'})


class MedicalPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.bundle=prepare('B0',workers()[:1]);self.package=self.bundle['packages'][0]
        data=fixtures();model_id=self.package['model_id'];text=self.package['request']['prompts'][0]['text']
        catalog=dataclasses.replace(data['catalog'][0],id=model_id,sha256=SHA,gguf_filename=Path(model_id).name,parameters_total_b=1.5)
        installed=dataclasses.replace(data['inventories'][0].models[0],id=model_id,filename=Path(model_id).name,sha256=SHA,chat_template_hash=TEMPLATE)
        self.worker=dataclasses.replace(data['workers'][0],load_profile='valid')
        inventory=dataclasses.replace(data['inventories'][0],models=(installed,))
        self.plan=preview_catalog_sweep(spec=SweepSpec.from_dict(self.package['request']['spec']),selections={'M01':model_id},catalog=[catalog],inventories=[inventory],workers=[self.worker],prompts=[PromptVariant('DOC02','M01',sha(text),TEMPLATE,rendered_input_tokens=4,input_token_source='fake-template',input_tokens_exact=True)]).plan
        self.assertTrue(self.plan.executable,self.plan.to_dict())
        self.config=build_cell_config(self.plan,trial_id=self.plan.trials[0].trial_id,sweep_id='medical-fake',attempt_id='attempt-fake',prompt_text=text)

    def run_fake(self,mode='full'):
        answer='{"cough":true,"fever":false,"chest_pain":false,"dyspnea":null}'
        class MedicalLlama(FakeLlama):
            def create_completion(self,**kw):
                self.calls.append(kw)
                yield {'choices':[{'text':answer,'finish_reason':None}]}
                yield {'choices':[{'text':'','finish_reason':'stop'}]}
        path=self.root/self.config.model_id;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'gguf')
        self.inputs=[]
        def capture(llm,messages):
            self.inputs.append(messages)
            return preparer(llm,messages)
        backend=LlamaCppInferenceBackend(self.root,llama_factory=MedicalLlama,torch_module=types.SimpleNamespace(),chat_preparer=capture)
        app=FastAPI();mount_worker_routes(app,backend=backend,telemetry=FakeTelemetry(),runtime=WorkerRuntimeInfo('j1','worker','fake','jetson','jetson',None,{},False))
        client=TestClient(app)
        def request(url,method='GET',payload=None,**kwargs):
            path='/'+url.split('/',3)[-1]
            if method=='POST':
                response=client.post(path,json=payload)
                if response.status_code!=200: raise ValueError(response.text)
                return response.json()
            return {'profile':{}}
        def stream(req,**kwargs):
            r=client.post('/cluster/chat/stream',json=json.loads(req.data))
            return io.BytesIO(r.content)
        from cluster.domain.worker import WorkerNode
        runner=BenchmarkRunner(_load_model,lambda *a:[],lambda *a:None,ScenarioExecutor(stream_worker_request,mock.Mock(side_effect=AssertionError('native RPC'))),mock.Mock())
        with mock.patch('cluster.benchmark.runner.request_json',side_effect=request),mock.patch('cluster.benchmark.transport.urllib.request.urlopen',side_effect=stream):
            summary=runner.run(self.config,[WorkerNode('j1','127.0.0.2','fake',22,8000,'/home/fake/project')],self.root/'results')
        self.assertEqual(summary['status'],'completed')
        self.run_dir=Path(summary['result_dir'])
        return index_runs(self.bundle,self.package['id'],self.plan.to_dict(),[self.run_dir])

    def test_preset_native_payload_persistence_evaluation_and_blind_export(self):
        index=self.run_fake()
        self.assertEqual(self.inputs[0][-1]['content'],self.config.prompt)
        self.assertEqual(len(index['requests']),1)
        row=index['requests'][0]
        self.assertTrue(row['evaluation']['correct'])
        self.assertEqual(row['actual_input_tokens'],4)
        self.assertEqual(row['rendered_prompt_sha256'],hashlib.sha256(b'[1,10,11,2]').hexdigest())
        self.assertEqual(row['rendered_prompt_hash_source'],'chat_template_token_ids_v1')
        self.assertNotIn('required_points',self.inputs[0][-1]['content'])
        reviews,mapping=blinded_export(index)
        self.assertNotIn('model_id',reviews[0]);self.assertIn('model_id',mapping[0])
        self.assertIn('completed',report(index))
        header=(self.run_dir/'requests.csv').read_text().splitlines()[0]
        self.assertEqual(len(header.split(',')),19)

    def test_compare_keeps_independent_runs_and_rejects_duplicate(self):
        from cluster.medical.results import combine_indexes
        index=self.run_fake()
        combined=combine_indexes([index])
        self.assertEqual(combined['comparison_groups'][0]['independent_jobs'],1)
        self.assertEqual(combined['comparison_groups'][0]['unique_question_groups'],1)
        with self.assertRaises(ValueError):combine_indexes([index,index])

    def test_cross_attempt_replica_run_and_package_mismatch_rejected(self):
        self.run_fake();path=self.run_dir/'responses.jsonl';original=path.read_text();row=json.loads(original)
        for mutate in ('attempt','node','diagnostic'):
            bad=copy.deepcopy(row)
            if mutate=='attempt':bad['sweep']['attempt_id']='another'
            elif mutate=='node':bad['node']='j2'
            else:bad['diagnostic_identity']['run_id']='another'
            path.write_text(json.dumps(bad)+'\n')
            with self.assertRaises(ValueError):index_runs(self.bundle,self.package['id'],self.plan.to_dict(),[self.run_dir])
        path.write_text(original+original)
        with self.assertRaises(ValueError):index_runs(self.bundle,self.package['id'],self.plan.to_dict(),[self.run_dir])
        path.write_text(original)
        with self.assertRaises(ValueError):index_runs(self.bundle,self.package['id'],self.plan.to_dict(),[self.run_dir,self.run_dir])
        bad=copy.deepcopy(self.bundle);bad['packages'][0]['request']['prompts'][0]['text']='changed'
        with self.assertRaises(ValueError):index_runs(bad,self.package['id'],self.plan.to_dict(),[])

    def test_legacy_missing_failed_and_private_evidence_not_promoted(self):
        self.run_fake();path=self.run_dir/'responses.jsonl';row=json.loads(path.read_text())
        row.update(ok=False,error_code='TIMEOUT',response_storage_mode='hash_only');row.pop('response');row.pop('rendered_prompt_sha256');row['effective_n_ctx']=1024
        path.write_text(json.dumps(row)+'\n')
        index=index_runs(self.bundle,self.package['id'],self.plan.to_dict(),[self.run_dir]);r=index['requests'][0]
        self.assertFalse(r['evaluation']['correct']);self.assertFalse(r['controlled_sample']);self.assertEqual(blinded_export(index),([],[]))
        path.write_text('')
        index=index_runs(self.bundle,self.package['id'],self.plan.to_dict(),[self.run_dir])
        self.assertEqual(index['runs'][0]['unobserved_requests'],1)
        empty=index_runs(self.bundle,self.package['id'],self.plan.to_dict(),[])
        self.assertEqual(empty['status'],'NOT_RUN');self.assertEqual(empty['remaining_trials'],1)
