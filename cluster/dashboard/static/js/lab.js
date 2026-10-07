(function labPresentation(global) {
  'use strict';
  const METRICS = {
    throughput_tokens_s: { label: '물리 처리량', title: '조건별 물리 클러스터 처리량', unit: 'tok/s', better: 'higher' },
    effective_user_tokens_per_s: { label: '유효 사용자 처리량', title: '조건별 유효 사용자 처리량', unit: 'tok/s', better: 'higher' },
    logical_requests_per_s: { label: '논리 요청 처리량', title: '조건별 논리 요청 처리량', unit: 'req/s', better: 'higher' },
    ttft_p50_s: { label: 'TTFT p50', title: '조건별 첫 토큰 지연', unit: 's', better: 'lower' },
    e2e_p95_s: { label: 'E2E p95', title: '조건별 요청 완료 지연', unit: 's', better: 'lower' },
    success_rate: { label: '성공률', title: '조건별 요청 성공률', unit: '%', better: 'higher' },
    generated_tokens_per_j: { label: '생성 토큰 / 에너지', title: '조건별 생성 토큰 에너지 효율', unit: 'tok/J', better: 'higher' },
  };
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const known = value => value !== null && value !== undefined && value !== '' && !['unknown', 'unlocked', 'legacy', 'mixed', 'unobserved'].includes(String(value).toLowerCase());
  function metricValue(run, metric) {
    const raw = run?.metrics?.[metric];
    return finite(raw) ? (metric === 'success_rate' ? raw * 100 : raw) : null;
  }
  const CONDITION_FIELDS = [
    ['model_sha256', 'Model hash'], ['tokenizer_sha256', 'Tokenizer'], ['quantization', 'Quantization'],
    ['prompt_sha256', 'Prompt'], ['workload_fingerprint', 'Workload'], ['source_fingerprint', 'Source'],
    ['measurement_fingerprint', 'Measurement'], ['runtime_fingerprint', 'Runtime'], ['power_mode', 'Power mode'],
    ['platform', 'Platform'], ['strategy', 'Strategy'], ['node_count', 'Node count'],
    ['provenance', 'Provenance'], ['experiment_type', 'Research phase'],
  ];
  function comparisonEvidence(run, baseline, metric, controlled = []) {
    if (!baseline) return { comparable: false, reasons: ['Baseline을 선택하세요'], differences: [] };
    const reasons = [];
    if (run?.status !== 'completed' || baseline.status !== 'completed') reasons.push('완료 측정값만 ratio 비교 가능');
    if ([run, baseline].some(item => !['clean', 'warning'].includes(item?.measurement_quality))) reasons.push('측정 품질 unknown/degraded');
    const allowedAxes = new Set(['strategy', 'node_count', 'platform', 'power_mode', 'runtime_fingerprint']);
    const axes = new Set(controlled.filter(key => allowedAxes.has(key)));
    const differences = CONDITION_FIELDS.map(([key, label]) => {
      const current = run?.[key]; const base = baseline[key];
      const missing = !known(current) || !known(base);
      const changed = String(current) !== String(base);
      const independent = axes.has(key);
      if (missing) reasons.push(`${label} 근거 부족`);
      else if (changed && !independent) reasons.push(`${label} 불일치`);
      return { key, label, current, base, missing, changed, independent };
    });
    // Broadcast physical totals and replica latency have different observation units.
    const broadcastChanged = (run?.strategy === 'broadcast_compare') !== (baseline.strategy === 'broadcast_compare');
    if (broadcastChanged && ['throughput_tokens_s', 'ttft_p50_s', 'e2e_p95_s', 'success_rate', 'generated_tokens_per_j'].includes(metric)) reasons.push('Broadcast 복제 응답의 metric 의미가 다름');
    if ([run?.platform, baseline.platform].includes('mixed')) reasons.push('Mixed RPC는 별도 exploratory cohort');
    return { comparable: !reasons.length, reasons, differences };
  }
  function baselineRatio(run, baseline, metric, controlled = []) {
    if (metric === 'success_rate' || !comparisonEvidence(run, baseline, metric, controlled).comparable) return null;
    const current = metricValue(run, metric); const base = metricValue(baseline, metric);
    if (!finite(current) || !finite(base) || base <= 0 || current < 0) return null;
    if (METRICS[metric]?.better === 'lower') return current > 0 ? base / current : null;
    return current / base;
  }
  function baselineChange(run, baseline, metric, controlled = []) {
    if (!comparisonEvidence(run, baseline, metric, controlled).comparable) return null;
    const current = metricValue(run, metric); const base = metricValue(baseline, metric);
    if (!finite(current) || !finite(base)) return null;
    if (metric === 'success_rate') return { value: current - base, unit: 'pp' };
    if (METRICS[metric]?.better === 'lower' && base > 0) return { value: (current - base) / base * 100, unit: '%' };
    return null;
  }
  function powerSummary(statuses) {
    const values = (statuses || []).map(status => status.metrics?.power_w);
    const valid = values.filter(finite);
    return { total: values.length && valid.length === values.length ? valid.reduce((a, b) => a + b, 0) : null, observed: valid.length, expected: values.length };
  }
  function experimentMatrix(cells) {
    const rows = new Map(); const columns = new Map(); const groups = new Map();
    for (const cell of cells || []) {
      const row = JSON.stringify([cell.model_lock_key || cell.model?.model_id || 'UNKNOWN', cell.strategy || cell.condition?.execution_strategy || 'UNKNOWN', cell.prompt_id || cell.prompt?.ref || 'UNKNOWN']);
      const nodes = cell.node_set || cell.condition?.worker_ids || [];
      const column = JSON.stringify([cell.platform_cohort || 'UNKNOWN', nodes.join(', ')]);
      rows.set(row, { key: row, model: JSON.parse(row)[0], strategy: JSON.parse(row)[1], prompt: JSON.parse(row)[2] });
      columns.set(column, { key: column, platform: cell.platform_cohort || 'UNKNOWN', nodes, count: cell.node_count ?? nodes.length });
      const key = JSON.stringify([row, column]);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(cell);
    }
    return { rows: [...rows.values()], columns: [...columns.values()], groups };
  }
  function cellState(cell) {
    if (cell.status === 'completed' && !cell.run_id && !(cell.attempts || []).some(attempt => attempt.run_id)) return 'missing';
    return ['pending', 'planned', 'draft'].includes(cell.status) ? 'planned' : cell.status || 'unknown';
  }
  global.ClusterDashboard = Object.assign(global.ClusterDashboard || {}, { lab: { METRICS, CONDITION_FIELDS, metricValue, comparisonEvidence, baselineRatio, baselineChange, powerSummary, experimentMatrix, cellState } });
})(globalThis);
