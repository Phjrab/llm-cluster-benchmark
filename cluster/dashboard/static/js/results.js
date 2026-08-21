/* Durable result inspection reads the Phase 12 response endpoint; it never reconstructs answers. */
(() => {
  const dashboard = window.ClusterDashboard || (window.ClusterDashboard = {});
  const selected = { runId: "", responses: [] };

  function responseGroups(responses) {
    const groups = new Map();
    (responses || []).forEach((response, index) => {
      const requestId = String(response.logical_request_id ?? response.request_id ?? index + 1);
      const scenarioId = String(response.scenario_id || "");
      const key = scenarioId ? `${scenarioId}\u0000${requestId}` : requestId;
      const list = groups.get(key) || [];
      list.push(response);
      groups.set(key, list);
    });
    return [...groups.entries()];
  }

  function errorRecords(run, responses) {
    const fromRun = [run?.failure, ...(run?.failures || []), ...(run?.errors || []), ...(run?.suite_model_errors || [])].filter(Boolean);
    const fromResponses = (responses || []).flatMap(response => [response.failure, response.error_record, response.error]).filter(Boolean);
    return [...fromRun, ...fromResponses].map(value => typeof value === "string" ? { message: value, code: "UNKNOWN" } : value);
  }

  function renderFailureCards(run, responses) {
    const records = errorRecords(run, responses);
    if (!records.length) return "";
    return `<section class="failure-list" aria-label="구조화된 실패 정보"><h4>FAILURE DIAGNOSIS</h4>${records.map((record, index) => {
      const status = dashboard.utils.statusPresentation("failed");
      const evidence = record.evidence && typeof record.evidence === "object" ? JSON.stringify(record.evidence, null, 2) : "";
      const solutions = Array.isArray(record.solutions) ? record.solutions : [];
      const raw = record.raw_log || record.log || record.message || record.error || "";
      return `<article class="failure-card ${status.tone}"><div><span class="status-symbol">${status.icon}</span><strong>${dashboard.escapeHtml(record.code || "UNKNOWN")}</strong><small>${dashboard.escapeHtml(record.stage || "result")}</small></div><p>${dashboard.escapeHtml(record.message || record.error || "실행 중 오류가 기록되었습니다.")}</p><dl><div><dt>NODE</dt><dd>${dashboard.escapeHtml(record.node || run?.nodes?.join(", ") || "—")}</dd></div><div><dt>MODEL</dt><dd>${dashboard.escapeHtml(record.model_id || dashboard.runModelId?.(run) || "—")}</dd></div></dl>${evidence ? `<details><summary>evidence</summary><pre>${dashboard.escapeHtml(evidence)}</pre></details>` : ""}${solutions.length ? `<ul>${solutions.map(solution => `<li>${dashboard.escapeHtml(solution)}</li>`).join("")}</ul>` : ""}<details><summary>raw log</summary><pre>${dashboard.escapeHtml(raw)}</pre></details></article>`;
    }).join("")}</section>`;
  }

  function renderResponses(run, responses) {
    const inspector = dashboard.$?.("#resultInspector");
    const label = dashboard.$?.("#resultInspectorStatus");
    if (!inspector || !label) return;
    const groups = responseGroups(responses);
    const strategy = dashboard.runStrategy?.(run);
    label.className = `inspector-status ${dashboard.utils.statusPresentation(run?.status).tone}`;
    label.textContent = `${dashboard.utils.statusPresentation(run?.status).icon} ${dashboard.utils.statusPresentation(run?.status).label} · ${responses.length} response records`;
    const content = groups.length ? groups.map(([groupKey, group]) => {
      const keyParts = String(groupKey).split("\u0000");
      const scenarioId = keyParts.length > 1 ? keyParts[0] : "";
      const requestId = keyParts.length > 1 ? keyParts[1] : keyParts[0];
      const prompt = group.find(item => item.prompt !== undefined)?.prompt || "Prompt was not persisted for this run.";
      const hashes = new Set(group.map(item => item.output_sha256).filter(Boolean));
      const broadcast = strategy === "broadcast_compare" && group.length > 1;
      const agreement = broadcast ? (hashes.size === 1 ? "EXACT HASH AGREEMENT" : hashes.size ? "HASH MISMATCH" : "HASH UNAVAILABLE") : "SINGLE RESPONSE";
      const requestLabel = strategy === "node_sweep" && scenarioId
        ? `SCENARIO ${scenarioId} · LOGICAL REQUEST ${requestId}`
        : `LOGICAL REQUEST ${requestId}`;
      return `<article class="response-group"><div class="response-group-head"><div><span>${dashboard.escapeHtml(requestLabel)}</span><strong>${broadcast ? `${group.length} worker responses` : `${group.length} response`}</strong></div><b class="hash-agreement ${hashes.size === 1 ? "match" : hashes.size > 1 ? "mismatch" : "unknown"}">${agreement}</b></div><div class="prompt-block"><span>PROMPT</span><pre>${dashboard.escapeHtml(prompt)}</pre></div><div class="response-grid">${group.map(response => {
        const status = dashboard.utils.statusPresentation(response.ok === false || response.error ? "failed" : "completed");
        const output = response.response ?? response.output ?? response.text ?? response.error ?? "No generated response was persisted.";
        const metrics = [
          Number.isFinite(Number(response.ttft_s)) ? `TTFT ${Number(response.ttft_s).toFixed(2)}s` : "",
          Number.isFinite(Number(response.e2e_s)) ? `E2E ${Number(response.e2e_s).toFixed(2)}s` : "",
          Number.isFinite(Number(response.generated_tokens)) ? `${response.generated_tokens} tokens` : "",
          Number.isFinite(Number(response.tokens_per_s)) ? `${Number(response.tokens_per_s).toFixed(2)} tok/s` : "",
        ].filter(Boolean).join(" · ");
        return `<article class="response-card ${status.tone}"><header><div><span>${status.icon} ${status.label}</span><strong>${dashboard.escapeHtml(response.node || "worker")}</strong><small>${dashboard.escapeHtml(response.model_id || dashboard.runModelId?.(run) || "unknown model")}</small></div><code title="${dashboard.escapeHtml(response.output_sha256 || "")}">${dashboard.escapeHtml(response.output_sha256 ? response.output_sha256.slice(0, 16) : "hash N/A")}</code></header><pre>${dashboard.escapeHtml(output)}</pre><footer>${dashboard.escapeHtml(metrics || "No request-level metrics")}</footer></article>`;
      }).join("")}</div></article>`;
    }).join("") : `<div class="empty-result"><strong>저장된 응답이 없습니다.</strong><span>이전 형식의 결과이거나 prompt/response persistence가 꺼진 실행일 수 있습니다.</span></div>`;
    const environment = dashboard.power?.resultEnvironmentHtml?.(run) || "";
    inspector.innerHTML = `${environment}${content}${renderFailureCards(run, responses)}`;
  }

  function clear() {
    selected.runId = "";
    selected.responses = [];
    const inspector = dashboard.$?.("#resultInspector");
    const label = dashboard.$?.("#resultInspectorStatus");
    if (label) {
      label.className = "inspector-status";
      label.textContent = "실행을 선택하세요";
    }
    if (inspector) {
      inspector.innerHTML = `<div class="empty-result"><strong>아직 선택한 실행이 없습니다.</strong><span>표의 <em>응답 보기</em>를 눌러 프롬프트, 모델별 생성 결과, 구조화된 실패를 확인하세요.</span></div>`;
    }
  }

  async function show(runId) {
    const run = dashboard.state.runs.find(item => item.run_id === runId);
    const inspector = dashboard.$?.("#resultInspector");
    const label = dashboard.$?.("#resultInspectorStatus");
    if (!run || !inspector || !label) return;
    selected.runId = runId;
    label.className = "inspector-status running";
    label.textContent = "● LOADING RESPONSES";
    inspector.innerHTML = `<div class="empty-result"><strong>저장된 응답을 읽는 중입니다.</strong><span>결과 파일을 변경하지 않고 조회만 수행합니다.</span></div>`;
    try {
      const payload = await dashboard.api(`/api/runs/${encodeURIComponent(runId)}/responses`);
      selected.responses = payload.responses || [];
      renderResponses(run, selected.responses);
      inspector.closest(".result-inspector")?.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      label.className = "inspector-status failed";
      label.textContent = "× RESPONSE LOAD FAILED";
      inspector.innerHTML = `<div class="empty-result"><strong>응답을 불러오지 못했습니다.</strong><span>${dashboard.escapeHtml(error.message)}</span>${renderFailureCards(run, [])}</div>`;
    }
  }

  dashboard.results = { show, clear, renderResponses, responseGroups, errorRecords };

  document.addEventListener("DOMContentLoaded", () => {
    document.addEventListener("click", event => {
      const button = event.target.closest("[data-view-run]");
      if (!button) return;
      event.preventDefault();
      event.stopPropagation();
      show(button.dataset.viewRun);
    });
  });
})();
