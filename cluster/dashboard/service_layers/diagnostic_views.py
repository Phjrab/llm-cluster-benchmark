"""Bounded offline F02 projection. No Worker calls or execution-side effects."""
from __future__ import annotations

import json
from pathlib import Path

from cluster.benchmark.diagnostics import job_context, owner_from_headers, summarize_request, validate_evidence
from cluster.infrastructure.request_diagnostics import GAPS, clean_event, identity, mapping, owner_key


def _read(path, root, limit):
    # Only fixed internal filenames; reject symlinked files and directories.
    if any(p.is_symlink() for p in (path, *path.parents) if p == root or root in p.parents):
        raise ValueError('UNSAFE_DIAGNOSTIC_PATH')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('OVERSIZED_DIAGNOSTIC_FILE')
    return raw


def run_diagnostics(run_dir, *, read_job=None, offset=0, limit=20):
    """Scan at most 4 MiB Controller + 32 MiB collections, return <=20 summaries.

    No original event/collection/config document is exposed. Interrupted requests
    are discovered in the Controller journal even without a final response row.
    """
    if type(offset) is not int or not 0 <= offset <= 1024 or type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError('DIAGNOSTIC_PAGE_INVALID')
    root = Path(run_dir)
    gaps = set()
    groups, invalid, jobs = {}, set(), {}
    try:
        config = json.loads(_read(root / 'config.json', root, 1024 * 1024))
        if not isinstance(config, dict):
            raise ValueError('INVALID_CONFIG')
    except (OSError, ValueError):
        config = {}
        gaps.add('RUN_CONFIG_UNAVAILABLE')
    for index in range(3, -1, -1):
        path = root / 'diagnostics' / 'controller' / (f'events.{index}.jsonl' if index else 'events.jsonl')
        try:
            raw = _read(path, root, 1024 * 1024)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            gaps.add('UNREADABLE_SEGMENT')
            continue
        if index:
            gaps.add('RETENTION_ROTATED')
        for line in raw.splitlines():
            wanted = None
            try:
                row = json.loads(line)
                wanted = identity(mapping(row).get('identity'))
                if wanted['run_id'] != root.name:
                    raise ValueError('DIAGNOSTIC_IDENTITY_MISMATCH')
                key = wanted['diagnostic_id']
                if key in invalid:
                    continue
                if key not in groups:
                    if len(groups) >= 1024:
                        gaps.add('REQUEST_LIMIT')
                        continue
                    headers, cleanup = {}, None
                    if wanted['job_id']:
                        if not read_job:
                            raise ValueError('JOB_EVIDENCE_UNAVAILABLE')
                        if wanted['job_id'] not in jobs:
                            jobs[wanted['job_id']] = read_job(wanted['job_id'])
                        headers, cleanup = job_context(wanted, config, jobs[wanted['job_id']])
                    groups[key] = {'identity': wanted, 'events': [], 'gaps': set(),
                                   'owner': owner_key(owner_from_headers(headers)), 'cleanup': cleanup}
                group = groups[key]
                if group['identity'] != wanted or row.get('owner_key') != group['owner']:
                    raise ValueError('DIAGNOSTIC_IDENTITY_MISMATCH')
                group['events'].append(clean_event(row, wanted))
                if len(group['events']) > 32:
                    group['events'].pop(0)
                    group['gaps'].add('EVENT_LIMIT')
                group['gaps'].update(g for g in row.get('gaps', []) if isinstance(g, str) and g in GAPS)
            except (OSError, ValueError, KeyError, TypeError):
                gaps.add('INVALID_OR_UNBOUND_CONTROLLER_RECORD')
                if wanted is not None:
                    invalid.add(wanted['diagnostic_id'])
    keys = [k for k in groups if k not in invalid and groups[k]['events']]
    # Most recently retained request first; pagination stays bounded.
    selected = list(reversed(keys))[offset:offset + limit]
    workers, collection_gaps = {}, {key: set() for key in selected}
    if selected:
        for slot in range(256):
            try:
                raw = _read(root / 'diagnostics' / 'collections' / f'{slot:03d}.json', root, 128 * 1024)
                collection = json.loads(raw)
                key = mapping(mapping(collection).get('identity')).get('diagnostic_id')
                if key not in selected:
                    continue
                if collection.get('identity') != groups[key]['identity']:
                    collection_gaps[key].add('INVALID_OR_MISMATCHED_WORKER_EVIDENCE')
                    continue
                if collection.get('status') == 'ACQUIRED':
                    workers[key] = validate_evidence(collection.get('evidence'), groups[key]['identity'])
                else:
                    collection_gaps[key].add('COLLECTION_NOT_ACQUIRED')
            except FileNotFoundError:
                continue
            except (OSError, ValueError, TypeError):
                gaps.add('UNREADABLE_OR_INVALID_COLLECTION')
    summaries = []
    for key in selected:
        group = groups[key]
        evidence = {'schema_version': 1, 'identity': group['identity'], 'events': group['events'],
                    'gaps': sorted(group['gaps'] | (gaps & GAPS) | {'UNFLUSHED_CRASH_LOSS_POSSIBLE', 'PRIOR_PROCESS_GAPS_UNKNOWN'})}
        if len({r['instance_id'] for r in group['events']}) > 1:
            evidence['gaps'].append('PROCESS_RESTART')
        try:
            value = summarize_request(evidence, workers.get(key), cleanup=group['cleanup'])
            value['missing_evidence'] = sorted(set(value['missing_evidence']) | collection_gaps[key])
            summaries.append(value)
        except ValueError:
            gaps.add('AMBIGUOUS_REQUEST_REJECTED')
    return {'schema_version': 1, 'run_id': root.name, 'source': 'saved_local_artifacts',
            'availability': 'RECORDED' if summaries else 'UNAVAILABLE' if gaps - {'RUN_CONFIG_UNAVAILABLE'} else 'NO_DIAGNOSTIC_RECORD',
            'requests': summaries, 'gaps': sorted(gaps), 'offset': offset, 'limit': limit,
            'retained_requests': len(keys), 'next_offset': offset + limit if offset + limit < len(keys) else None,
            'recovery': 'EXPLICIT_POST_JOB_CLI_ONLY', 'metrics_modified': False}
