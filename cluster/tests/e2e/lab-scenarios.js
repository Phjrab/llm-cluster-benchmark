const { fixtureState, installApiFixture, sweepPlan } = require('./research-lab-fixture');

const FIXTURE_ID = 'research-lab-v1';
const DEMO_LABEL = 'UI DEMO / SYNTHETIC FIXTURE / 성능·임상·실장비 검증 아님';

function labFixture({ empty = false } = {}) {
  const fixture = fixtureState();
  fixture.nodes.forEach((node, index) => Object.assign(node, { host: `192.0.2.${index + 11}`, user: 'worker', project_dir: '/opt/llm-cluster' }));
  fixture.run.participant_nodes.forEach((node, index) => { node.host = `192.0.2.${index + 11}`; });
  const common = {
    campaign_id: 'lab-demo-plan', model_lock: 'qwen-demo-lock', model_id: fixture.model.id,
    model_sha256: 'a'.repeat(64), tokenizer_sha256: 'b'.repeat(64), quantization: 'Q4_K_M',
    prompt_sha256: 'c'.repeat(64), workload_fingerprint: 'demo-20req-c4-t128',
    runtime_fingerprint: 'demo-openblas-v1', source_fingerprint: 'demo-source-v1',
    measurement_fingerprint: 'demo-instrument-v1', power_mode: 'CPU default',
    platform: 'raspberry-pi', node_count: 1, nodes: ['pi-worker-02'],
    strategy: 'single_node', status: 'completed', measurement_quality: 'clean',
    experiment_type: 'exploratory', provenance: 'synthetic', legacy_fallback: false,
    finished_at: '2026-10-07T00:00:00Z',
  };
  fixture.compareRuns = [
    { ...common, run_id: 'pi-baseline', metrics: { throughput_tokens_s: 7.5, physical_cluster_tokens_per_s: 7.5, effective_user_tokens_per_s: 7.5, logical_requests_per_s: 0.8, ttft_p50_s: 0.7, e2e_p95_s: 4.2, success_rate: 1, generated_tokens_per_j: null } },
    { ...common, run_id: 'pi-round-robin', strategy: 'replicated_round_robin', node_count: 2, nodes: ['pi-worker-02', 'pi-worker-03'], measurement_quality: 'warning', metrics: { throughput_tokens_s: 14.2, physical_cluster_tokens_per_s: 14.2, effective_user_tokens_per_s: 14.2, ttft_p50_s: 0.8, e2e_p95_s: 3.8, success_rate: 0.95 } },
    { ...common, run_id: 'pi-broadcast', strategy: 'broadcast_compare', node_count: 2, metrics: { throughput_tokens_s: 15, physical_cluster_tokens_per_s: 15, effective_user_tokens_per_s: 7.2, ttft_p50_s: 0.75, e2e_p95_s: 4.4, success_rate: 1 } },
    { ...common, run_id: 'jetson-other', platform: 'jetson', runtime_fingerprint: 'demo-cuda-v1', model_id: 'other-model.gguf', model_sha256: 'd'.repeat(64), provenance: 'imported', metrics: { throughput_tokens_s: 28, physical_cluster_tokens_per_s: 28, ttft_p50_s: 0.3, e2e_p95_s: 2.1, success_rate: 1 } },
    { ...common, run_id: 'pi-failed', status: 'failed', measurement_quality: 'degraded', metrics: { throughput_tokens_s: null, success_rate: 0 } },
  ];
  const templates = [
    ['raspberry-pi', 'single_node', ['pi-worker-02'], 'completed', 'pi-baseline', 'clean'],
    ['raspberry-pi', 'single_node', ['pi-worker-02'], 'completed', 'pi-repeat', 'warning'],
    ['raspberry-pi', 'replicated_round_robin', ['pi-worker-02', 'pi-worker-03'], 'running', null, 'unknown'],
    ['raspberry-pi', 'broadcast_compare', ['pi-worker-02', 'pi-worker-03'], 'failed', 'pi-failed', 'degraded'],
    ['jetson', 'single_node', ['jetson-worker-01'], 'pending', null, 'unknown'],
    ['jetson', 'model_parallel_rpc', ['jetson-worker-01', 'jetson-worker-02'], 'completed', null, 'unknown'],
    ['jetson', 'node_sweep', ['jetson-worker-01', 'jetson-worker-02'], 'cancelled', null, 'unknown'],
  ];
  fixture.campaign = {
    campaign_id: 'lab-demo-plan', matrix_id: 'demo-matrix', status: 'paused', phase: 'exploratory', experiment_type: 'exploratory',
    updated_at: '2026-10-07T00:00:00Z', progress_pct: 57, formal_eligible: false,
    coverage: { planned: 7, completed: 3, pending: 1, running: 1, failed: 1, cancelled: 1 },
    result_coverage: { linked_runs: 3, planned_runs: 7 }, repeat_progress: { completed: 1, total: 2 },
    current_drift: [{ code: 'CURRENT_SOURCE_REVALIDATION_REQUIRED' }], estimated_remaining: { cells: 2 },
    cells: templates.map(([platform, strategy, nodes, status, run_id, quality], index) => ({
      campaign_cell_id: `demo-cell-${index + 1}`, order_index: index + 1, repeat_index: index === 1 ? 2 : 1,
      platform_cohort: platform, strategy, node_set: nodes, node_count: nodes.length,
      model_lock_key: 'qwen-demo-lock', prompt_id: 'prompt-demo', status, run_id,
      measurement_quality: quality, attempts: run_id ? [{ run_id, status }] : [],
    })),
  };
  fixture.readiness = {
    eligible: false, model_counts: { approved: 1, total: 2 }, approved_models: [{ model_key: 'qwen-demo-lock', display_name: 'Qwen demo identity', sha256: 'a'.repeat(64), verified_workers: ['pi-worker-02'] }],
    license_blockers: [{ model_key: 'restricted-demo', display_name: 'Unapproved demo model', license: 'acceptance required' }],
    controller_source: { status: 'unverified' },
    workers: fixture.nodes.map(node => ({ node: node.name, platform: node.platform, online: true, source_identity: 'drift', runtime_identity: 'unknown', power_identity: 'unknown', instrumentation_ready: false })),
    blocking_issues: [{ code: 'CURRENT_SOURCE_REVALIDATION_REQUIRED' }, { code: 'LICENSE_ACCEPTANCE_REQUIRED' }],
    execution_gate: { formal_execution_allowed: false, blocking_requirements: ['Current-source pilot 재검증', 'runtime/source re-lock'] },
  };
  if (empty) { fixture.nodes = []; fixture.status = []; fixture.models = []; fixture.compareRuns = []; fixture.campaign = null; fixture.readiness.workers = []; }
  fixture.requests = [];
  fixture.unexpected = [];
  fixture.networkRequests = [];
  fixture.sweepApi = async ({ request, path, json }) => {
    fixture.requests.push({ method: request.method(), path });
    if (path === '/api/sweeps/preview') {
      const plan = sweepPlan(request.postDataJSON(), 'c'.repeat(64), { capabilities: [{ status: 'blocked', code: 'WORKER_OFFLINE', subject: 'pi-worker-03' }] });
      plan.executable = false; plan.counts.valid_cells = 0; plan.counts.blocked_cells = 1;
      return json({ plan, candidates: [], evidence_mode: 'cached' }), true;
    }
    if (request.method() !== 'GET') { fixture.unexpected.push(path); return json({ detail: 'Synthetic fixture denies execution and management' }, 403), true; }
    if (path === '/api/campaigns') return json({ campaigns: fixture.campaign ? [fixture.campaign] : [] }), true;
    if (path === '/api/campaigns/lab-demo-plan') return json(fixture.campaign), true;
    if (path === '/api/research/compare') {
      const mapping = { campaigns: 'campaign_id', model_locks: 'model_lock', platforms: 'platform', node_counts: 'node_count', strategies: 'strategy', runtime_fingerprints: 'runtime_fingerprint', power_modes: 'power_mode', measurement_qualities: 'measurement_quality' };
      return json({ runs: fixture.compareRuns, filters: Object.fromEntries(Object.entries(mapping).map(([key, field]) => [key, [...new Set(fixture.compareRuns.map(run => run[field]))]])) }), true;
    }
    if (path === '/api/research/readiness') return json(fixture.readiness), true;
    if (path === '/api/sweeps/capabilities') return json({ max_trials: 500, max_parallel_jobs: 2, preview_uses_cached_evidence: true }), true;
    if (path === '/api/sweeps') return json({ sweeps: [] }), true;
    return false;
  };
  return fixture;
}

async function installLabFixture(page, fixture) {
  page.on("request", request => fixture.networkRequests.push({ method: request.method(), url: request.url() }));
  // Every browser request is either loopback static content or a synthetic API.
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.hostname !== '127.0.0.1') { fixture.unexpected.push(url.origin); return route.abort(); }
    return route.continue();
  });
  await installApiFixture(page, fixture);
  if (!fixture.nodes.length) {
    await page.route('**/api/bootstrap', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ nodes: [], status: [], models: [], runs: [], suites: [], experiment_groups: [], defaults: {}, settings: {} }) }));
  }
  await page.addInitScript(label => {
    document.addEventListener('DOMContentLoaded', () => {
      const badge = document.createElement('div'); badge.id = 'syntheticFixtureLabel'; badge.textContent = label;
      Object.assign(badge.style, { position: 'fixed', bottom: '0', left: '0', right: '0', zIndex: '2000', padding: '5px 12px', background: '#fff4cf', color: '#493400', font: '12px system-ui', borderTop: '1px solid #9a761b', pointerEvents: 'none' });
      document.body.append(badge);
    });
  }, DEMO_LABEL);
}

module.exports = { labFixture, installLabFixture, FIXTURE_ID, DEMO_LABEL };
