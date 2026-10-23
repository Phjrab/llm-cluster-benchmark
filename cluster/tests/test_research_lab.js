const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../dashboard/static/js/lab.js'), 'utf8'), context);
const lab = context.ClusterDashboard.lab;
const base = {
  status: 'completed', measurement_quality: 'clean', model_sha256: 'model-a', tokenizer_sha256: 'tokenizer-a',
  quantization: 'Q4', prompt_sha256: 'prompt-a', workload_fingerprint: 'requests-20', source_fingerprint: 'source-a',
  measurement_fingerprint: 'measure-a', runtime_fingerprint: 'runtime-a', power_mode: 'MAXN', platform: 'jetson',
  strategy: 'single_node', node_count: 1, provenance: 'saved_run', experiment_type: 'exploratory',
  metrics: { throughput_tokens_s: 4, ttft_p50_s: 2, success_rate: 1, generated_tokens_per_j: null },
};
const scaled = { ...base, strategy: 'replicated_round_robin', node_count: 2, metrics: { throughput_tokens_s: 8, ttft_p50_s: 1, success_rate: 0 } };
assert.equal(lab.baselineRatio(scaled, base, 'throughput_tokens_s'), null);
assert.equal(lab.baselineRatio(scaled, base, 'throughput_tokens_s', ['strategy', 'node_count']), 2);
assert.equal(lab.baselineRatio(scaled, base, 'ttft_p50_s', ['strategy', 'node_count']), 2);
assert.equal(lab.baselineChange(scaled, base, 'ttft_p50_s', ['strategy', 'node_count']).value, -50);
assert.equal(lab.baselineChange(scaled, base, 'success_rate', ['strategy', 'node_count']).value, -100);
assert.equal(lab.baselineRatio(scaled, base, 'success_rate', ['strategy', 'node_count']), null);
assert.equal(lab.metricValue(scaled, 'success_rate'), 0);
assert.equal(lab.metricValue(base, 'generated_tokens_per_j'), null);
assert.equal(lab.metricValue({ metrics: { throughput_tokens_s: null } }, 'throughput_tokens_s'), null);
assert.equal(lab.metricValue({ metrics: { throughput_tokens_s: NaN } }, 'throughput_tokens_s'), null);
assert.equal(lab.baselineRatio({ ...base, metrics: { throughput_tokens_s: 0 } }, base, 'throughput_tokens_s'), 0);
for (const metric of ['throughput_tokens_s', 'ttft_p50_s']) {
  assert.equal(lab.baselineRatio(base, { ...base, metrics: { [metric]: 0 } }, metric), null);
  assert.equal(lab.baselineRatio(base, { ...base, metrics: {} }, metric), null);
}
assert.equal(lab.baselineRatio({ ...base, metrics: { ttft_p50_s: 0 } }, base, 'ttft_p50_s'), null);
for (const field of ['model_sha256', 'tokenizer_sha256', 'prompt_sha256', 'quantization', 'workload_fingerprint', 'source_fingerprint', 'measurement_fingerprint', 'provenance', 'experiment_type']) {
  assert.equal(lab.baselineRatio({ ...base, [field]: 'different' }, base, 'throughput_tokens_s'), null, field);
  assert.equal(lab.baselineRatio({ ...base, [field]: 'unknown' }, base, 'throughput_tokens_s'), null, field);
}
for (const status of ['failed', 'cancelled', 'running', 'planned']) assert.equal(lab.baselineRatio({ ...base, status }, base, 'throughput_tokens_s'), null);
assert.equal(lab.baselineRatio({ ...base, strategy: 'broadcast_compare' }, base, 'throughput_tokens_s', ['strategy']), null);
assert.equal(lab.baselineRatio({ ...base, platform: 'mixed' }, base, 'throughput_tokens_s', ['platform']), null);
assert.equal(lab.powerSummary([{ metrics: { power_w: null } }]).total, null);
assert.equal(lab.powerSummary([{ metrics: { power_w: 5 } }, { metrics: {} }]).total, null);
assert.equal(lab.powerSummary([{ metrics: { power_w: 0 } }]).total, 0);
const cells = [
  { model_lock_key: 'a', strategy: 'single_node', prompt_id: 'p', node_set: ['pi-1'], platform_cohort: 'pi', status: 'pending' },
  { model_lock_key: 'a', strategy: 'single_node', prompt_id: 'p', node_set: ['pi-1'], platform_cohort: 'pi', status: 'completed', run_id: 'run-1' },
];
const matrix = lab.experimentMatrix(cells);
assert.equal(matrix.rows.length, 1); assert.equal(matrix.columns.length, 1);
assert.equal([...matrix.groups.values()][0].length, 2);
assert.equal(lab.cellState(cells[0]), 'planned');
assert.equal(lab.cellState({ status: 'completed' }), 'missing');
assert.equal(lab.experimentMatrix([]).rows.length, 0);
console.log('research lab conditions, zero/null, metric semantics, matrix fixtures: OK');
