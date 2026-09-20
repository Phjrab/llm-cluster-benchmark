"use strict";

(() => {
  const dashboard = window.ClusterDashboard || (window.ClusterDashboard = {});
  const { state } = dashboard;
  const byId = id => document.getElementById(id);
  const terminalStates = new Set(["completed", "partial", "failed", "cancelled"]);
  let refreshTimer = null;

  function idempotencyKey(operation, sweepId) {
    const random = globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2);
    return `${operation}-${sweepId}-${random}`.slice(0, 160);
  }

  function statusMeta(status) {
    const values = {
      draft: ["○", "DRAFT", "unknown"], queued: ["◷", "QUEUED", "unknown"],
      running: ["▶", "RUNNING", "running"], pausing: ["◫", "PAUSING", "unknown"],
      paused: ["Ⅱ", "PAUSED", "paused"], cancelling: ["◫", "CANCELLING", "unknown"],
      completed: ["✓", "COMPLETED", "valid"], partial: ["△", "PARTIAL", "unknown"],
      failed: ["!", "FAILED", "blocked"], cancelled: ["×", "CANCELLED", "blocked"],
    };
    return values[status] || ["?", String(status || "UNKNOWN").toUpperCase(), "unknown"];
  }

  function planFor(detail) {
    return detail?.sweep?.plan_snapshot || null;
  }

  function currentTrial(detail) {
    const trials = detail?.sweep?.trials || [];
    return trials.find(item => ["running", "claimed", "queued"].includes(item.status)) || trials.find(item => item.status === "pending") || null;
  }

  function currentCell(detail) {
    const trial = currentTrial(detail);
    return (planFor(detail)?.cells || []).find(cell => cell.cell_id === trial?.cell_id) || null;
  }

  function progress(detail) {
    const trials = detail?.sweep?.trials || [];
    if (!trials.length) return { done: 0, total: planFor(detail)?.counts?.trials || 0, pct: 0 };
    const done = trials.filter(item => terminalStates.has(item.status) || ["succeeded", "skipped", "blocked"].includes(item.status)).length;
    return { done, total: trials.length, pct: trials.length ? Math.round(done / trials.length * 100) : 0 };
  }

  function waitingReason(detail) {
    const sweep = detail?.sweep || {};
    const trial = currentTrial(detail) || {};
    return sweep.waiting_reason || sweep.blocking_reason || trial.waiting_reason || trial.failure_code || (sweep.status === "paused" ? sweep.pause_reason || "safe boundary에서 일시정지" : "");
  }

  function approvalValid(draft) {
    return sessionStorage.getItem(`sweepApproval:${draft.sweep_id}`) === draft.plan_sha256;
  }

  function renderCard(detail) {
    const draft = detail.draft || {};
    const sweep = detail.sweep;
    const status = sweep?.status || draft.status || "draft";
    const meta = statusMeta(status);
    const p = progress(detail);
    const cell = currentCell(detail);
    const condition = cell?.condition || {};
    const model = cell?.model?.model_id || condition.model_ref || "대기";
    const workers = condition.worker_ids?.length ? condition.worker_ids : (cell?.rpc_profile?.worker_ids || []);
    const reason = waitingReason(detail);
    const startAllowed = !sweep && approvalValid(draft);
    const error = detail.controlError;
    const actions = [];
    if (!sweep) actions.push(`<button type="button" class="button primary compact" data-sweep-action="start" ${startAllowed ? "" : "disabled title=\"현재 plan hash를 Builder에서 다시 승인하세요\""}>Start</button>`);
    if (["running", "queued"].includes(status)) actions.push(`<button type="button" class="button ghost compact" data-sweep-action="pause">Pause</button>`);
    if (status === "paused") actions.push(`<button type="button" class="button primary compact" data-sweep-action="resume">Resume</button>`);
    if (!terminalStates.has(status) && sweep) actions.push(`<button type="button" class="button ghost compact danger-text" data-sweep-action="cancel">Cancel</button>`);
    actions.push(`<button type="button" class="button ghost compact" data-sweep-select>상세</button>`);
    return `<article class="active-sweep-card ${meta[2]} ${state.selectedSweepId === draft.sweep_id ? "selected" : ""}" data-sweep-card="${dashboard.escapeHtml(draft.sweep_id)}" tabindex="0">
      <header><div><span class="sweep-status-icon" aria-hidden="true">${meta[0]}</span><div><strong>${dashboard.escapeHtml(draft.sweep_id)}</strong><small>revision ${dashboard.escapeHtml(draft.plan_revision)} · ${dashboard.escapeHtml(String(draft.plan_sha256 || "").slice(0, 12))}</small></div></div><span class="sweep-status-text ${meta[2]}">${meta[1]}</span></header>
      <div class="sweep-progress"><div><span>${p.done} / ${p.total} trials</span><strong>${p.pct}%</strong></div><i><b style="width:${p.pct}%"></b></i></div>
      <dl><div><dt>MODEL</dt><dd>${dashboard.escapeHtml(model)}</dd></div><div><dt>CONTEXT</dt><dd>${condition.n_ctx || "—"}</dd></div><div><dt>RPC PROFILE</dt><dd>${dashboard.escapeHtml(condition.rpc_profile_ref || "—")}</dd></div><div><dt>WORKERS</dt><dd>${workers.length ? workers.map(dashboard.escapeHtml).join(", ") : "대기"}</dd></div></dl>
      ${reason ? `<p class="sweep-waiting"><strong>대기/실패 이유</strong>${dashboard.escapeHtml(reason)}</p>` : ""}
      ${error ? `<p class="sweep-control-error"><strong>최근 제어 실패</strong>${dashboard.escapeHtml(error)}</p>` : ""}
      ${!sweep && !startAllowed ? `<p class="sweep-start-disabled">Start 비활성 · Builder에서 동일 preview hash를 다시 확인하세요.</p>` : ""}
      <div class="sweep-card-actions">${actions.join("")}</div>
    </article>`;
  }

  function render() {
    const root = byId("activeSweepGrid");
    if (!root) return;
    root.innerHTML = state.sweeps.length ? state.sweeps.map(renderCard).join("") : `<div class="empty-result"><strong>저장된 sweep 없음</strong><span>Builder에서 preview를 승인하고 draft를 저장하세요.</span></div>`;
    renderSelectedLog();
    dashboard.sweepResults?.renderSelected?.();
  }

  async function refresh() {
    if (!byId("activeSweepGrid")) return;
    try {
      const listing = await dashboard.api("/api/sweeps?offset=0&limit=100");
      const previousErrors = new Map(state.sweeps.map(item => [item.draft?.sweep_id, item.controlError]));
      const details = await Promise.all((listing.sweeps || []).map(item => dashboard.api(`/api/sweeps/${encodeURIComponent(item.sweep_id)}`).catch(error => ({ draft: item, sweep: null, controlError: error.message }))));
      details.forEach(item => { if (!item.controlError) item.controlError = previousErrors.get(item.draft?.sweep_id) || ""; });
      state.sweeps = details;
      state.sweepDetails = new Map(details.map(item => [item.draft.sweep_id, item]));
      if (!state.selectedSweepId && details.length) state.selectedSweepId = details[0].draft.sweep_id;
      if (state.selectedSweepId && !state.sweepDetails.has(state.selectedSweepId)) state.selectedSweepId = details[0]?.draft.sweep_id || "";
      render();
      const streamable = new Set(details.filter(item => item.sweep && !terminalStates.has(item.sweep.status)).map(item => item.draft.sweep_id));
      state.sweepStreams.forEach((stream, sweepId) => {
        if (streamable.has(sweepId)) return;
        clearTimeout(stream.retryTimer); stream.controller.abort(); state.sweepStreams.delete(sweepId);
      });
      details.filter(item => streamable.has(item.draft.sweep_id)).forEach(item => connectStream(item.draft.sweep_id));
      return details;
    } catch (error) {
      rootError(error);
      return [];
    }
  }

  function rootError(error) {
    const root = byId("activeSweepGrid");
    const message = ["Not Found", "HTTP 404"].includes(error.message)
      ? "현재 Controller에서 스윕 API를 찾을 수 없습니다. 최신 코드가 적용된 Controller인지 확인하세요."
      : error.message;
    if (root) root.innerHTML = `<div class="empty-result"><strong>Sweep 상태를 불러오지 못했습니다.</strong><span>${dashboard.escapeHtml(message)}</span></div>`;
  }

  async function lifecycle(sweepId, operation) {
    const detail = state.sweepDetails.get(sweepId);
    if (!detail?.draft) throw new Error("Sweep draft 상태를 다시 불러오세요.");
    const body = {
      idempotency_key: idempotencyKey(operation, sweepId),
      plan_revision: detail.draft.plan_revision,
      plan_sha256: detail.draft.plan_sha256,
    };
    if (["pause", "cancel"].includes(operation)) body.reason = `${operation} requested from Dashboard`;
    try {
      const response = await dashboard.api(`/api/sweeps/${encodeURIComponent(sweepId)}/${operation}`, { method: "POST", body });
      detail.controlError = "";
      dashboard.toast?.(`Sweep ${operation}`, `${sweepId} · ${response.sweep?.status || "accepted"}`);
    } catch (error) {
      detail.controlError = error.message;
      appendEvent(sweepId, { type: "control_error", operation, message: error.message, at: new Date().toISOString() });
      render();
      throw error;
    }
    await refresh();
  }

  function appendEvent(sweepId, event) {
    const rows = state.sweepEvents.get(sweepId) || [];
    rows.push(event);
    if (rows.length > 500) rows.splice(0, rows.length - 500);
    state.sweepEvents.set(sweepId, rows);
    if (state.selectedSweepId === sweepId) renderSelectedLog();
    if ((event.status || /^(sweep|trial|attempt)_/.test(String(event.type || ""))) && event.type !== "sweep_status_replayed") {
      clearTimeout(refreshTimer);
      refreshTimer = setTimeout(() => refresh(), 150);
    }
  }

  function renderSelectedLog() {
    const root = byId("sweepEventLog");
    const title = byId("sweepLogTitle");
    if (!root || !title) return;
    const sweepId = state.selectedSweepId;
    title.textContent = sweepId || "선택 없음";
    const rows = state.sweepEvents.get(sweepId) || [];
    root.innerHTML = rows.length ? rows.slice(-100).map(event => `<p><time>${dashboard.escapeHtml(event.at || event.timestamp || event.type || "EVENT")}</time><span>${dashboard.escapeHtml(event.message || event.code || event.status || JSON.stringify(event))}</span></p>`).join("") : `<p>이 sweep의 event journal을 기다리는 중입니다.</p>`;
    root.scrollTop = root.scrollHeight;
  }

  async function replayEvents(sweepId, cursor) {
    const headers = {};
    if (state.token) headers["X-Cluster-Token"] = state.token;
    const response = await fetch(`/api/sweeps/${encodeURIComponent(sweepId)}/events?cursor=${cursor}&limit=200`, { headers, cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (response.status === 409 && data.detail?.code === "EVENT_CURSOR_AHEAD") return { events: [], next_cursor: data.detail.recovery_cursor || 0 };
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    (data.events || []).forEach(event => appendEvent(sweepId, event));
    return data;
  }

  function parseSseBlock(block) {
    const id = block.split(/\r?\n/).find(line => line.startsWith("id:"))?.slice(3).trim();
    const data = block.split(/\r?\n/).filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
    return { id: Number(id), data };
  }

  function connectStream(sweepId) {
    if (state.sweepStreams.has(sweepId)) return;
    const controller = new AbortController();
    const stream = { controller, cursor: Number(sessionStorage.getItem(`sweepCursor:${sweepId}`) || 0), retryTimer: null };
    state.sweepStreams.set(sweepId, stream);

    const pump = async () => {
      try {
        const replay = await replayEvents(sweepId, stream.cursor);
        stream.cursor = Number(replay.next_cursor ?? stream.cursor);
        sessionStorage.setItem(`sweepCursor:${sweepId}`, String(stream.cursor));
        const headers = { Accept: "text/event-stream", "Last-Event-ID": String(stream.cursor) };
        if (state.token) headers["X-Cluster-Token"] = state.token;
        const response = await fetch(`/api/sweeps/${encodeURIComponent(sweepId)}/events/stream`, { headers, signal: controller.signal, cache: "no-store" });
        if (!response.ok || !response.body) throw new Error(`SSE HTTP ${response.status}`);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (!controller.signal.aborted) {
          const { value, done } = await reader.read();
          buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
          const blocks = buffer.split(/\r?\n\r?\n/);
          buffer = blocks.pop() || "";
          for (const block of blocks) {
            const parsed = parseSseBlock(block);
            if (parsed.data) {
              appendEvent(sweepId, JSON.parse(parsed.data));
              if (Number.isFinite(parsed.id)) stream.cursor = parsed.id;
              else stream.cursor += 1;
              sessionStorage.setItem(`sweepCursor:${sweepId}`, String(stream.cursor));
            }
          }
          if (done) break;
        }
        if (!controller.signal.aborted) stream.retryTimer = setTimeout(pump, 1500);
      } catch (error) {
        if (controller.signal.aborted) return;
        appendEvent(sweepId, { type: "stream_reconnect", message: "event stream 재연결 중", at: new Date().toISOString() });
        stream.retryTimer = setTimeout(pump, 2000);
      }
    };
    pump();
  }

  function select(sweepId) {
    state.selectedSweepId = sweepId;
    render();
    dashboard.sweepResults?.load?.(sweepId);
  }

  function bind() {
    byId("refreshSweepsButton")?.addEventListener("click", async () => { await refresh(); dashboard.toast?.("Sweep 상태 복원", "서버의 durable manifest와 draft를 다시 읽었습니다."); });
    byId("activeSweepGrid")?.addEventListener("click", async event => {
      const card = event.target.closest("[data-sweep-card]");
      if (!card) return;
      const sweepId = card.dataset.sweepCard;
      if (event.target.closest("[data-sweep-select]")) return select(sweepId);
      const button = event.target.closest("[data-sweep-action]");
      if (!button || button.disabled) return;
      try { await lifecycle(sweepId, button.dataset.sweepAction); }
      catch (error) { dashboard.toast?.("Sweep 제어 실패", error.message, "error"); }
    });
  }

  window.addEventListener("beforeunload", () => {
    clearTimeout(refreshTimer);
    state.sweepStreams.forEach(stream => { clearTimeout(stream.retryTimer); stream.controller.abort(); });
    state.sweepStreams.clear();
  });
  dashboard.sweepControl = { refresh, render, select, lifecycle, appendEvent };
  document.addEventListener("DOMContentLoaded", bind);
})();
