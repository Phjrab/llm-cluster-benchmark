"use strict";

(() => {
  const dashboard = window.ClusterDashboard || (window.ClusterDashboard = {});
  const { state } = dashboard;
  const byId = id => document.getElementById(id);
  const cache = new Map();
  const esc = value => dashboard.escapeHtml(value == null ? "" : String(value));

  function idempotencyKey(sweepId, trialId) {
    const random = globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2);
    return `retry-${sweepId}-${trialId}-${random}`.slice(0, 160);
  }

  function representative(trial) {
    return (trial.attempts || []).find(item => item.representative || item.attempt_id === trial.representative_attempt_id || item.attempt_id === trial.official_attempt_id) || (trial.attempts || []).at(-1) || {};
  }

  function axisValue(trial, attempt, axis) {
    const condition = trial.condition || {};
    const evidence = attempt.request_evidence || {};
    const values = {
      context_capacity: condition.n_ctx,
      actual_prompt_tokens: (evidence.actual_input_tokens || []).join("/"),
      concurrency: condition.concurrency,
      model: trial.model_identity?.model_id,
      gpu_offload: condition.n_gpu_layers,
      rpc_profile: trial.rpc_profile?.profile_id || "none",
      execution_strategy: condition.execution_strategy,
      none: "all",
    };
    return values[axis] ?? "N/A";
  }

  function visibleAttempts(trial) {
    return byId("sweepAllAttempts")?.checked ? (trial.attempts || []) : [representative(trial)];
  }

  function syncFilter(result) {
    const select = byId("sweepResultFilter");
    if (!select) return;
    const axis = byId("sweepResultAxis")?.value || "context_capacity";
    const previousAxis = select.dataset.axis;
    const previous = previousAxis === axis ? select.value : "all";
    const values = [...new Set((result.trials || []).flatMap(trial => visibleAttempts(trial).map(attempt => String(axisValue(trial, attempt, axis)))))].sort();
    select.innerHTML = `<option value="all">전체</option>${values.map(value => `<option value="${esc(value)}">${esc(value)}</option>`).join("")}`;
    select.dataset.axis = axis;
    select.value = values.includes(previous) ? previous : "all";
  }

  function selectedTrials(result) {
    const axis = byId("sweepResultAxis")?.value || "context_capacity";
    const filter = byId("sweepResultFilter")?.value || "all";
    if (filter === "all") return result.trials || [];
    return (result.trials || []).filter(trial => visibleAttempts(trial).some(attempt => String(axisValue(trial, attempt, axis)) === filter));
  }

  function chart(result) {
    const axis = byId("sweepResultAxis")?.value || "context_capacity";
    const metric = byId("sweepResultMetric")?.value || "cluster_tokens_per_s";
    const series = byId("sweepResultSeries")?.value || "none";
    const rows = selectedTrials(result).flatMap(trial => visibleAttempts(trial).map(attempt => ({
      trial, attempt, x: axisValue(trial, attempt, axis), series: axisValue(trial, attempt, series),
      y: attempt.metrics?.[metric],
    }))).filter(row => Number.isFinite(Number(row.y)));
    if (!rows.length) return `<div class="sweep-na"><strong>N/A</strong><span>선택한 지표의 실제 run-level 값이 없습니다.</span></div>`;
    const max = Math.max(...rows.map(row => Number(row.y)), 0.000001);
    const fixedKeys = ["context_capacity", "concurrency", "model", "gpu_offload", "rpc_profile", "execution_strategy"].filter(key => key !== axis && key !== series);
    const mixed = fixedKeys.filter(key => new Set(rows.map(row => String(axisValue(row.trial, row.attempt, key)))).size > 1);
    const step = Math.max(34, Math.floor(560 / rows.length));
    const bars = rows.map((row, index) => {
      const height = Math.max(2, Number(row.y) / max * 112);
      const x = 16 + index * step;
      return `<g><rect x="${x}" y="${130 - height}" width="24" height="${height}" rx="3"></rect><title>${esc(row.x)} · ${esc(row.series)} · ${esc(row.y)}</title><text x="${x + 12}" y="148" text-anchor="middle">${esc(String(row.x).slice(0, 10))}</text></g>`;
    }).join("");
    return `${mixed.length ? `<div class="sweep-warning"><strong>고정 조건 혼합</strong><span>${esc(mixed.join(", "))}가 함께 섞였습니다. 이 그래프를 단일 조건 효과로 해석하지 마세요.</span></div>` : ""}<div class="sweep-chart"><strong>${esc(metric)} by ${esc(axis)} · series ${esc(series)}</strong><svg viewBox="0 0 600 160" role="img" aria-label="선택한 sweep run-level 비교 막대 그래프"><line x1="8" y1="130" x2="592" y2="130"></line>${bars}</svg></div>`;
  }

  function responseRows(attempt) {
    const rows = attempt.responses || [];
    if (!rows.length) return `<span class="na">N/A · 저장된 response evidence 없음</span>`;
    return `<details><summary>${rows.length} response record</summary><div class="sweep-response-list">${rows.map(row => `<article><b>#${esc(row.request_id)} · ${esc(row.finish_reason || "unknown")}</b><span>input ${esc(row.input_tokens ?? "N/A")} (${row.input_tokens_exact ? "exact" : "not exact"}) · output ${esc(row.generated_tokens ?? "N/A")} (${row.output_tokens_exact ? "exact" : "not exact"})</span><pre>${esc(row.response ?? `[${row.response_storage_status || "legacy_missing"}] raw response unavailable`)}</pre></article>`).join("")}</div></details>`;
  }

  function resultRows(result) {
    return selectedTrials(result).flatMap(trial => visibleAttempts(trial).map(attempt => ({ trial, attempt })));
  }

  function renderSelected() {
    const root = byId("sweepTrialResults");
    const title = byId("sweepTrialTitle");
    const toolbar = byId("sweepResultToolbar");
    if (!root || !title) return;
    const sweepId = state.selectedSweepId;
    title.textContent = sweepId || "선택 없음";
    if (!sweepId) { if (toolbar) toolbar.hidden = true; root.innerHTML = "<p>실행할 sweep을 선택하세요.</p>"; return; }
    const detail = state.sweepDetails.get(sweepId);
    if (!detail?.sweep) { if (toolbar) toolbar.hidden = true; root.innerHTML = `<div class="empty-result"><strong>아직 시작하지 않은 draft입니다.</strong><span>현재 preview hash 승인 후 Start할 수 있습니다.</span></div>`; return; }
    const result = cache.get(sweepId);
    if (!result) { root.innerHTML = `<div class="empty-result"><strong>Trial 결과를 불러오는 중입니다.</strong></div>`; return; }
    if (toolbar) toolbar.hidden = false;
    syncFilter(result);
    const rows = resultRows(result);
    const warnings = result.comparison_warnings || [];
    const aggregates = result.aggregates || [];
    root.innerHTML = `<div class="sweep-result-contract"><strong>Exploratory only · formal 승인 아님</strong><span>${esc(result.statistical_unit || "independent run per repeat")} · ${esc(result.selection_policy || "official/latest attempt")}</span></div>
      ${warnings.map(message => `<div class="sweep-warning"><strong>비교 주의</strong><span>${esc(message)}</span></div>`).join("")}
      ${chart(result)}
      <div class="sweep-trial-summary"><span><strong>${(result.trials || []).length}</strong> trials</span><span><strong>${rows.length}</strong> shown attempts</span><span><strong>${aggregates.reduce((n, row) => n + Number(row.independent_run_count || 0), 0)}</strong> independent runs</span></div>
      <div class="sweep-trial-table-wrap"><table><thead><tr><th>Repeat / attempt</th><th>조건·identity</th><th>요청 ↔ 적용</th><th>run 지표</th><th>응답·계측</th><th>상태·제어</th></tr></thead><tbody>${rows.length ? rows.map(({ trial, attempt }) => {
        const condition = trial.condition || {};
        const evidence = attempt.request_evidence || {};
        const energy = attempt.energy || {};
        const energyCoverage = energy.coverage || {};
        const energyCoverageLabel = Number.isFinite(Number(energyCoverage.coverage_ratio))
          ? `${Math.round(Number(energyCoverage.coverage_ratio) * 100)}% coverage`
          : "coverage unknown";
        const energyCoverageReasons = Object.values(energyCoverage.unavailable_nodes || {}).filter(Boolean);
        const energyReason = energyCoverageReasons.join(", ") || energy.reason;
        const parallel = attempt.parallel_context || {};
        const canRetry = ["failed", "cancelled", "blocked"].includes(trial.status);
        const metrics = attempt.metrics || {};
        return `<tr data-sweep-trial="${esc(trial.trial_id)}"><td><b>repeat ${esc(trial.repeat_index ?? "—")}</b><small>${esc(attempt.attempt_id || "unrun")}${attempt.representative ? " · representative" : " · preserved"}</small><small>${esc(attempt.run_id || "run N/A")}</small></td>
          <td>${esc(trial.model_identity?.model_id || "unknown model")}<small>sha ${esc((trial.model_identity?.artifact_sha256 || "N/A").slice(0, 12))} · template ${esc((trial.prompt_identity?.template_sha256 || "N/A").slice(0, 12))}</small><small>ctx ${esc(condition.n_ctx ?? "N/A")} · input ${esc((evidence.actual_input_tokens || []).join("/") || "N/A")} · c${esc(condition.concurrency ?? "N/A")} · gpu ${esc(condition.n_gpu_layers ?? "N/A")}</small><small>${esc(condition.execution_strategy || "N/A")} · ${esc(trial.rpc_profile?.profile_id || "no RPC")}</small></td>
          <td>${attempt.condition_mismatch ? `<span class="condition-mismatch">CONDITION MISMATCH</span>` : `<span class="condition-match">MATCH / N/A</span>`}<small>requested ${esc(JSON.stringify(attempt.requested_config || {}))}</small><small>effective ${esc(JSON.stringify(attempt.effective_config || {}))}</small></td>
          <td><b>${esc(metrics.cluster_tokens_per_s ?? "N/A")}</b> physical tok/s<small>${esc(metrics.effective_user_tokens_per_s ?? "N/A")} effective user tok/s</small><small>TTFT ${esc(metrics.ttft_p50_s ?? "N/A")} · E2E ${esc(metrics.e2e_p50_s ?? "N/A")}</small><small>energy ${esc(energy.generated_tokens_per_j ?? "N/A")} tok/J · ${esc(energy.quality || "unknown")}</small><small>${esc(energyCoverageLabel)}${energyReason ? ` · ${esc(energyReason)}` : ""}</small></td>
          <td>${responseRows(attempt)}<small>${esc(evidence.finish_reasons?.join(", ") || "finish N/A")} · early EOS ${esc(evidence.early_eos_count ?? "N/A")}</small><small>warmup ${esc(condition.warmup_requests ?? "N/A")} · cache ${esc(result.cache_policy || "N/A")} · measurements ${esc(attempt.measurement_count ?? "N/A")}</small><small>prefill ${esc(evidence.prefill_semantics || "N/A")} · RTT ${esc(evidence.rtt_semantics || "N/A")}</small>${attempt.measurements?.length ? `<details><summary>node × scenario measurements</summary><pre>${esc(JSON.stringify(attempt.measurements, null, 2))}</pre></details>` : ""}<small>${esc(parallel.label || "exploratory")} · overlap ${esc((parallel.overlapping_run_ids || []).join(", ") || "none observed")}</small></td>
          <td><span class="trial-state ${esc(attempt.status || trial.status || "unknown")}">${esc(attempt.status || trial.status || "unknown")}</span><small>${esc(attempt.failure_code || trial.failure_code || "no failure")}</small>${canRetry ? `<button type="button" class="button ghost compact" data-retry-trial="${esc(trial.trial_id)}">수동 Retry</button>` : ""}<button type="button" class="button ghost compact" data-clone-trial="${esc(trial.trial_id)}">이 조건 새 draft</button></td></tr>`;
      }).join("") : `<tr><td colspan="6">Trial 없음</td></tr>`}</tbody></table></div>`;
  }

  async function load(sweepId) {
    const detail = state.sweepDetails.get(sweepId);
    if (!detail?.sweep) return renderSelected();
    try { cache.set(sweepId, await dashboard.api(`/api/sweeps/${encodeURIComponent(sweepId)}/results`)); }
    catch (error) { cache.set(sweepId, { trials: [], coverage: {}, error: error.message }); }
    renderSelected();
  }

  async function retry(sweepId, trialId) {
    const detail = state.sweepDetails.get(sweepId);
    if (!detail?.draft) throw new Error("Sweep 상태를 다시 불러오세요.");
    const body = { idempotency_key: idempotencyKey(sweepId, trialId), plan_revision: detail.draft.plan_revision, plan_sha256: detail.draft.plan_sha256, reason: "Manual retry requested from Dashboard" };
    await dashboard.api(`/api/sweeps/${encodeURIComponent(sweepId)}/trials/${encodeURIComponent(trialId)}/retry`, { method: "POST", body });
    dashboard.toast?.("Trial retry 등록", `${sweepId} · ${trialId}`);
    await dashboard.sweepControl?.refresh?.(); await load(sweepId);
  }

  async function clone(sweepId, trialId) {
    const suffix = new Date().toISOString().replace(/\D/g, "").slice(0, 14);
    const newId = `${sweepId}-rerun-${suffix}`.slice(0, 128);
    const value = await dashboard.api(`/api/sweeps/${encodeURIComponent(sweepId)}/trials/${encodeURIComponent(trialId)}/clone-draft`, { method: "POST", body: { new_sweep_id: newId } });
    dashboard.toast?.("새 draft 생성", `${value.draft?.sweep_id || newId} · 실행되지 않음`);
    await dashboard.sweepControl?.refresh?.();
  }

  function download(format) {
    if (!state.selectedSweepId) return;
    const anchor = document.createElement("a");
    anchor.href = `/api/sweeps/${encodeURIComponent(state.selectedSweepId)}/export-results?format=${format}`;
    anchor.download = `sweep-results-${state.selectedSweepId}.${format}`;
    document.body.append(anchor); anchor.click(); anchor.remove();
  }

  function bind() {
    byId("sweepTrialResults")?.addEventListener("click", async event => {
      const retryButton = event.target.closest("[data-retry-trial]");
      const cloneButton = event.target.closest("[data-clone-trial]");
      if (!state.selectedSweepId || (!retryButton && !cloneButton)) return;
      try {
        if (retryButton) await retry(state.selectedSweepId, retryButton.dataset.retryTrial);
        if (cloneButton) await clone(state.selectedSweepId, cloneButton.dataset.cloneTrial);
      } catch (error) { dashboard.toast?.("Sweep 결과 작업 실패", error.message, "error"); }
    });
    ["sweepResultAxis", "sweepResultFilter", "sweepResultMetric", "sweepResultSeries", "sweepAllAttempts"].forEach(id => byId(id)?.addEventListener("change", renderSelected));
    byId("sweepExportJson")?.addEventListener("click", () => download("json"));
    byId("sweepExportCsv")?.addEventListener("click", () => download("csv"));
  }

  dashboard.sweepResults = { load, renderSelected, retry, clone };
  document.addEventListener("DOMContentLoaded", bind);
})();
