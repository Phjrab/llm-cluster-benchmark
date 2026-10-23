"""Bounded read-only reuse of completed native-input jobs for exploratory preview.

No Worker calls, files supplied by clients, retries, or catalog/formal mutations.
Only the same Worker/source/runtime/model/template/prompt may reuse evidence.
"""
from dataclasses import replace
import heapq
import json
import os
from pathlib import Path

from cluster.application.sweep_planner import compile_plan
from cluster.domain.experiment import ExperimentConfig
from cluster.domain.runtime_profile import require_applied_profile, require_prepared_input
from cluster.domain.sweep import digest, ref

MAX_FILES = 128
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024


def _jobs(root):
    try:
        with os.scandir(root) as entries:
            recent = heapq.nlargest(MAX_FILES,
                ((e.stat(follow_symlinks=False).st_mtime_ns, e.name) for e in entries
                 if e.name.startswith('job_') and e.name.endswith('.json')
                 and e.is_file(follow_symlinks=False)))
    except OSError:
        return
    remaining = MAX_TOTAL_BYTES
    for _, name in recent:
        try:
            path = Path(root) / name
            with path.open('rb') as stream:
                if path.is_symlink() or path.stat().st_size > min(MAX_FILE_BYTES, remaining):
                    continue
                content = stream.read(min(MAX_FILE_BYTES, remaining) + 1)
            remaining -= len(content)
            if remaining < 0 or len(content) > MAX_FILE_BYTES:
                break
            job = json.loads(content)
            if isinstance(job, dict) and job.get('job_id') == name[:-5]:
                yield job
        except (OSError, ValueError):
            continue


def _identity(deployment, runtime, platform):
    if deployment.get('verified') is not True or deployment.get('working_tree_clean') is not True:
        raise ValueError('unverified source')
    digest(deployment.get('source_commit'), 40)
    digest(deployment.get('source_tree_sha256'))
    if runtime.get('verified') is not True or not all(runtime.get(k) for k in
            ('kind', 'llama_cpp_python', 'runtime_fingerprint')):
        raise ValueError('unverified runtime')
    return (deployment['source_commit'], deployment['source_tree_sha256'], platform,
            runtime['kind'], runtime['llama_cpp_python'], runtime['runtime_fingerprint'])


def observations(jobs_dir, status, *, fresh):
    if not fresh:
        return []
    live = {x.get('name'): x for x in status}
    output = []
    for job in _jobs(jobs_dir):
        try:
            lease = job.get('resource_reservation') or {}
            if (job.get('status') != 'completed' or lease.get('status') != 'released'
                    or lease.get('cleanup_verified') is not True
                    or lease.get('owner_job_id') != job['job_id']):
                continue
            cfg = ExperimentConfig.from_dict(job['config'], strict=True)
            cfg.validate()
            if cfg.input_binding is None or cfg.sweep is not None or cfg.campaign_id or cfg.pilot_id:
                continue
            summaries = job.get('summaries') or []
            if len(summaries) != 1:
                continue
            summary = summaries[0]
            ref(summary['run_id'])
            if (summary.get('status') != 'completed' or summary.get('model_id') != cfg.model_id
                    or summary.get('success_rate') != 1 or summary.get('failed') != 0
                    or not summary.get('successful')):
                continue
            participants = {p['name']: p for p in summary.get('participant_nodes', [])}
            actuals = {a['node']: a for a in summary.get('actual_model_config', [])}
            for evidence in summary.get('native_input_evidence', []):
                node = evidence['node']
                if node not in cfg.node_names or node not in lease.get('worker_ids', []):
                    continue
                now = live.get(node) or {}
                participant, actual = participants[node], actuals[node]
                if now.get('api') is not True or participant.get('capture_status') != 'captured':
                    continue
                profile = now.get('profile') or {}
                old_identity = _identity(participant.get('deployment') or {},
                                         participant.get('runtime_backend') or {}, participant.get('detected_platform'))
                new_identity = _identity(now.get('deployment') or {}, profile.get('runtime_backend') or {}, profile.get('platform_kind'))
                if old_identity != new_identity or participant.get('host') != now.get('host') or participant.get('api_port') != now.get('api_port'):
                    continue
                require_applied_profile(actual, cfg)
                preparation = actual.get('input_preparation') or {}
                require_prepared_input(preparation, cfg)
                if (actual.get('model_id') != cfg.model_id or evidence.get('successful_requests', 0) < 1
                        or any(evidence.get(k) != preparation.get(k) for k in evidence if k not in {'node', 'successful_requests'})):
                    continue
                required = ('model_sha256', 'template_hash', 'prompt_sha256', 'input_tokens',
                            'rendered_prompt_sha256', 'preparation_id', 'input_tokens_exact',
                            'input_token_source', 'rendered_prompt_hash_source')
                if not all(k in evidence for k in required):
                    continue
                output.append({'job_id': job['job_id'], 'run_id': summary['run_id'], 'node': node,
                               'model_id': cfg.model_id, **{k: evidence[k] for k in required}})
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return output


def enrich_preview(preview, evidence):
    if any(c.condition.execution_strategy == "model_parallel_rpc" for c in preview.plan.cells):
        return preview  # Native RPC keeps its separate runtime and placement gates.
    context = preview.plan.context
    checks = list(context.model_checks)
    models = {m.ref: m for m in context.models}
    matches = {}
    for alias, model in models.items():
        matches[alias] = [e for e in evidence if e['model_id'] == model.model_id
            and e['model_sha256'] == model.artifact_sha256 and e['template_hash'] == model.template_sha256
            and e['node'] in model.installed_workers]
    for i, check in enumerate(checks):
        if (check.kind == 'runtime' and check.status == 'unknown'
                and check.code == 'MODEL_RUNTIME_NOT_VERIFIED'
                and any(e['node'] == check.worker_id for e in matches.get(check.model_ref, []))):
            checks[i] = replace(check, status='valid', code='MODEL_RUNTIME_OBSERVED_RUN')
    prompts = []
    for prompt in context.prompts:
        found = [e for e in matches.get(prompt.model_ref, []) if e['prompt_sha256'] == prompt.text_sha256]
        # No cross-node inference of evidence: every selected node must have seen this exact input.
        required = {n for cell in preview.plan.cells if cell.condition.model_ref == prompt.model_ref
                    and cell.condition.prompt_ref == prompt.ref for n in cell.condition.worker_ids}
        counts = {(e['input_tokens'], e['rendered_prompt_sha256']) for e in found}
        if (not prompt.input_tokens_exact and required and required <= {e['node'] for e in found}
                and len(counts) == 1):
            prompt = replace(prompt, rendered_input_tokens=next(iter(counts))[0],
                             input_token_source='prior_native_run', input_tokens_exact=True)
        prompts.append(prompt)
    candidates = []
    for c in preview.candidates:
        nodes = {x.worker_id for x in checks if x.model_ref == c.model_ref and x.kind == 'runtime' and x.status == 'valid'}
        proofs = tuple(sorted({(e['job_id'], e['run_id'], e['node']) for e in matches.get(c.model_ref, [])}))
        candidates.append(replace(c, runtime_verified_workers=tuple(sorted(nodes & set(c.installed_workers))),
                                  observed_run_refs=proofs))
    new_context = replace(context, prompts=tuple(prompts), model_checks=tuple(checks))
    return replace(preview, plan=compile_plan(preview.plan.spec, new_context), candidates=tuple(candidates))
