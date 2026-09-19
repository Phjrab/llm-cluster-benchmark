"use strict";

(() => {
  const dashboard = window.ClusterDashboard || (window.ClusterDashboard = {});
  const { state } = dashboard;
  const byId = id => document.getElementById(id);
  const cache = new Map();

  function idempotencyKey(sweepId, trialId) {
    const random = globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2);
    return `retry-${sweepId}-${trialId}-${random}`.slice(0, 160);
  }

  function planCells(sweepId) {
    const plan = state.sweepDetails.get(sweepId)?.sweep?.plan_snapshot;
    return new Map((plan?.cells || []).map(cell => [cell.cell_id, cell]));
  }

  function renderSelected() {
    const root = byId("sweepTrialResults");
    const title = byId("sweepTrialTitle");
    if (!root || !title) return;
    const sweepId = state.selectedSweepId;
    title.textContent = sweepId || "선택 없음";
    if (!sweepId) {
      root.innerHTML = "<p>실행할 sweep을 선택하세요.</p>";
      return;
    }
    const detail = state.sweepDetails.get(sweepId);
    if (!detail?.sweep) {
      root.innerHTML = `<div class="empty-result"><strong>아직 시작하지 않은 draft입니다.</strong><span>현재 preview hash 승인 후 Start할 수 있습니다.</span></div>`;
      return;
    }
    const result = cache.get(sweepId);
    if (!result) {
      root.innerHTML = `<div class="empty-result"><strong>Trial 결과를 불러오는 중입니다.</strong></div>`;
      return;
    }
    const cells = planCells(sweepId);
    const rows = result.trials || [];
    const counts = rows.reduce((value, trial) => { value[trial.status || "unknown"] = (value[trial.status || "unknown"] || 0) + 1; return value; }, {});
    const coverage = result.coverage || {};
    root.innerHTML = `<div class="sweep-trial-summary"><span><strong>${rows.length}</strong> total</span>${Object.entries(counts).map(([status, count]) => `<span class="${dashboard.escapeHtml(status)}"><strong>${count}</strong> ${dashboard.escapeHtml(status)}</span>`).join("")}<span><strong>${dashboard.escapeHtml(coverage.completed ?? coverage.succeeded ?? "—")}</strong> coverage</span></div>
      <div class="sweep-trial-table-wrap"><table><thead><tr><th>Trial</th><th>조건</th><th>상태</th><th>Attempt</th><th>Failure</th><th>제어</th></tr></thead><tbody>${rows.length ? rows.map(trial => {
        const cell = cells.get(trial.cell_id) || {};
        const condition = cell.condition || trial.failure?.condition || {};
        const attempt = (trial.attempts || []).find(item => item.attempt_id === trial.official_attempt_id) || (trial.attempts || []).at(-1) || {};
        const canRetry = ["failed", "cancelled", "blocked"].includes(trial.status);
        return `<tr data-sweep-trial="${dashboard.escapeHtml(trial.trial_id)}"><td><code>${dashboard.escapeHtml(String(trial.trial_id || "").slice(0, 18))}</code><small>${dashboard.escapeHtml(String(trial.cell_id || "").slice(0, 18))}</small></td><td>${dashboard.escapeHtml(cell.model?.model_id || trial.failure?.model_id || condition.model_ref || "unknown")}<small>ctx ${dashboard.escapeHtml(condition.n_ctx ?? "—")} · c${dashboard.escapeHtml(condition.concurrency ?? "—")} · ${dashboard.escapeHtml(condition.rpc_profile_ref || condition.execution_strategy || "")}</small></td><td><span class="trial-state ${dashboard.escapeHtml(trial.status || "unknown")}">${dashboard.escapeHtml(trial.status || "unknown")}</span></td><td>${dashboard.escapeHtml(attempt.attempt_id || "—")}<small>${dashboard.escapeHtml(attempt.cleanup_status || "")}</small></td><td>${dashboard.escapeHtml(trial.failure_code || "—")}<small>${dashboard.escapeHtml((trial.failure?.solutions || [])[0] || "")}</small></td><td>${canRetry ? `<button type="button" class="button ghost compact" data-retry-trial="${dashboard.escapeHtml(trial.trial_id)}">수동 Retry</button>` : "—"}</td></tr>`;
      }).join("") : `<tr><td colspan="6">Trial 없음</td></tr>`}</tbody></table></div>`;
  }

  async function load(sweepId) {
    const detail = state.sweepDetails.get(sweepId);
    if (!detail?.sweep) return renderSelected();
    try {
      cache.set(sweepId, await dashboard.api(`/api/sweeps/${encodeURIComponent(sweepId)}/results`));
    } catch (error) {
      cache.set(sweepId, { trials: [], coverage: {}, error: error.message });
    }
    renderSelected();
  }

  async function retry(sweepId, trialId) {
    const detail = state.sweepDetails.get(sweepId);
    if (!detail?.draft) throw new Error("Sweep 상태를 다시 불러오세요.");
    const body = {
      idempotency_key: idempotencyKey(sweepId, trialId),
      plan_revision: detail.draft.plan_revision,
      plan_sha256: detail.draft.plan_sha256,
      reason: "Manual retry requested from Dashboard",
    };
    await dashboard.api(`/api/sweeps/${encodeURIComponent(sweepId)}/trials/${encodeURIComponent(trialId)}/retry`, { method: "POST", body });
    dashboard.toast?.("Trial retry 등록", `${sweepId} · ${trialId}`);
    await dashboard.sweepControl?.refresh?.();
    await load(sweepId);
  }

  function bind() {
    byId("sweepTrialResults")?.addEventListener("click", async event => {
      const button = event.target.closest("[data-retry-trial]");
      if (!button || !state.selectedSweepId) return;
      try { await retry(state.selectedSweepId, button.dataset.retryTrial); }
      catch (error) { dashboard.toast?.("Trial retry 실패", error.message, "error"); }
    });
  }

  dashboard.sweepResults = { load, renderSelected, retry };
  document.addEventListener("DOMContentLoaded", bind);
})();
