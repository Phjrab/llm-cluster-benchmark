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

  function participantNodes(run) {
    if (Array.isArray(run?.participant_nodes) && run.participant_nodes.length) return run.participant_nodes;
    const configs = Array.isArray(run?.actual_model_config) ? run.actual_model_config : [];
    return (Array.isArray(run?.nodes) ? run.nodes : []).map(name => ({
      name,
      capture_status: "legacy",
      ...(configs.find(item => item?.node === name) || {}),
    }));
  }

  function renderParticipantNodes(run) {
    const participants = participantNodes(run);
    if (!participants.length) return "";
    const configs = Array.isArray(run?.actual_model_config) ? run.actual_model_config : [];
    const coordinator = run?.topology?.coordinator;
    const cards = participants.map(node => {
      const actual = configs.find(item => item?.node === node.name) || {};
      const backend = node.runtime_backend && typeof node.runtime_backend === "object"
        ? node.runtime_backend : { kind: node.runtime_backend };
      const platform = node.detected_platform || node.platform_kind || node.configured_platform || "unknown";
      const memoryGb = Number.isFinite(Number(node.memory_total_mb)) ? `${(Number(node.memory_total_mb) / 1024).toFixed(1)} GB` : "—";
      const endpoint = node.host ? `${node.host}${node.api_port ? `:${node.api_port}` : ""}` : "기록 없음";
      const modelRuntime = actual.placement === "sharded_participant"
        ? `RPC shard${actual.coordinator ? " · coordinator" : ""}`
        : [actual.n_ctx != null ? `ctx ${actual.n_ctx}` : "", actual.n_gpu_layers != null ? `GPU layers ${actual.n_gpu_layers}` : "", actual.n_batch != null ? `batch ${actual.n_batch}` : ""].filter(Boolean).join(" · ") || "기록 없음";
      const mode = node.power_mode || "—";
      const capture = node.capture_status === "captured" ? "SNAPSHOT" : node.capture_status === "unavailable" ? "SNAPSHOT FAILED" : "LEGACY";
      return `<article class="participant-card"><header><div><span>${dashboard.escapeHtml(String(platform).toUpperCase())}</span><strong>${dashboard.escapeHtml(node.name || "worker")}</strong><small>${dashboard.escapeHtml(node.hostname || endpoint)}</small></div><div><b>${dashboard.escapeHtml(capture)}</b>${coordinator === node.name ? `<em>COORDINATOR</em>` : ""}</div></header><dl>
        <div><dt>ENDPOINT</dt><dd>${dashboard.escapeHtml(endpoint)}</dd></div>
        <div><dt>BOARD / OS</dt><dd>${dashboard.escapeHtml([node.board_model, node.os].filter(Boolean).join(" · ") || "기록 없음")}</dd></div>
        <div><dt>CPU / MEMORY</dt><dd>${dashboard.escapeHtml([node.cpu_model, node.cpu_cores_logical ? `${node.cpu_cores_logical} threads` : "", memoryGb].filter(value => value && value !== "—").join(" · ") || "기록 없음")}</dd></div>
        <div><dt>INFERENCE BACKEND</dt><dd>${dashboard.escapeHtml([backend.kind, backend.llama_cpp_python ? `llama-cpp-python ${backend.llama_cpp_python}` : "", node.inference_threads ? `${node.inference_threads} inference threads` : ""].filter(Boolean).join(" · ") || "기록 없음")}</dd></div>
        <div><dt>MODEL RUNTIME</dt><dd>${dashboard.escapeHtml(modelRuntime)}</dd></div>
        <div><dt>POWER / REVISION</dt><dd>${dashboard.escapeHtml([mode, node.git_commit ? `git ${node.git_commit}` : ""].filter(Boolean).join(" · ") || "기록 없음")}</dd></div>
      </dl>${node.capture_error ? `<p>${dashboard.escapeHtml(node.capture_error)}</p>` : ""}</article>`;
    }).join("");
    return `<section class="participant-environment" aria-label="실험 참여 노드 정보"><div class="participant-environment-head"><div><span>PARTICIPANT NODE SNAPSHOT</span><h4>실험 참여 노드 · ${participants.length}대</h4></div><small>실험 시작 시점 기준</small></div><p>결과 재현을 위해 저장된 하드웨어와 런타임 정보입니다. SSH 키와 인증 정보는 포함하지 않습니다.</p><div class="participant-grid">${cards}</div></section>`;
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
    inspector.innerHTML = `${renderParticipantNodes(run)}${environment}${content}${renderFailureCards(run, responses)}`;
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

  async function remove(runId) {
    const run = dashboard.state.runs.find(item => item.run_id === runId);
    if (!run) return;
    const label = `${run.name || run.run_id}\n${dashboard.runModelId?.(run) || ""}`.trim();
    if (!confirm(`다음 실험 결과를 삭제할까요?\n\n${label}\n\n대시보드에서는 즉시 제거되며, 안전을 위해 private trash로 이동됩니다.`)) return;
    try {
      await dashboard.api(`/api/runs/${encodeURIComponent(runId)}`, { method: "DELETE" });
      if (selected.runId === runId) clear();
      await dashboard.refreshExperimentData?.();
      dashboard.toast?.("실험 결과 삭제 완료", `${runId} · private trash로 이동했습니다.`);
    } catch (error) {
      dashboard.toast?.("실험 결과 삭제 실패", error.message, "error");
    }
  }

  function formatDeletedAt(epochSeconds) {
    const value = Number(epochSeconds);
    if (!Number.isFinite(value)) return "삭제 시각 기록 없음";
    return new Date(value * 1000).toLocaleString("ko-KR");
  }

  function renderTrash(entries) {
    const list = dashboard.$?.("#resultTrashList");
    if (!list) return;
    list.innerHTML = entries.length ? entries.map(entry => {
      const protectedResult = entry.protected === true;
      return `<article class="result-trash-card ${protectedResult ? "protected" : ""}">
        <header><div><span>${protectedResult ? "RETENTION PROTECTED" : "RECOVERABLE RESULT"}</span><strong>${dashboard.escapeHtml(entry.run_id || "unknown run")}</strong><small>${dashboard.escapeHtml(formatDeletedAt(entry.deleted_at_epoch_s))}</small></div><b>${dashboard.escapeHtml(String(entry.status || "unknown").toUpperCase())}</b></header>
        <dl><div><dt>TRASH ID</dt><dd>${dashboard.escapeHtml(entry.trash_id || "—")}</dd></div><div><dt>CAMPAIGN / SUITE</dt><dd>${dashboard.escapeHtml(entry.campaign_id || entry.suite_id || "—")}</dd></div><div class="trash-checksum"><dt>CONTENT SHA-256</dt><dd title="${dashboard.escapeHtml(entry.archive_sha256 || "")}">${dashboard.escapeHtml(entry.archive_sha256 || "—")}</dd></div></dl>
        <footer><button type="button" class="button ghost compact" data-restore-trash="${dashboard.escapeHtml(entry.trash_id || "")}">복원</button><button type="button" class="button danger compact" data-purge-trash="${dashboard.escapeHtml(entry.trash_id || "")}" data-run-id="${dashboard.escapeHtml(entry.run_id || "")}" data-trash-sha="${dashboard.escapeHtml(entry.archive_sha256 || "")}" ${protectedResult ? "disabled title=\"정식 캠페인 결과는 영구 삭제할 수 없습니다.\"" : ""}>${protectedResult ? "보존 보호" : "영구 삭제"}</button></footer>
      </article>`;
    }).join("") : `<div class="empty-result"><strong>휴지통이 비어 있습니다.</strong><span>결과 표에서 삭제한 실행이 여기에 보관됩니다.</span></div>`;
  }

  async function loadTrash({ open = false } = {}) {
    const dialog = dashboard.$?.("#resultTrashDialog");
    const list = dashboard.$?.("#resultTrashList");
    if (!dialog || !list) return;
    if (open && !dialog.open) dialog.showModal();
    list.innerHTML = `<div class="empty-result"><strong>휴지통을 불러오는 중입니다.</strong><span>결과 파일의 체크섬과 보호 상태를 확인합니다.</span></div>`;
    try {
      const payload = await dashboard.api("/api/results/trash");
      renderTrash(Array.isArray(payload.trash) ? payload.trash : []);
    } catch (error) {
      list.innerHTML = `<div class="empty-result"><strong>휴지통을 불러오지 못했습니다.</strong><span>${dashboard.escapeHtml(error.message)}</span></div>`;
    }
  }

  async function restore(trashId) {
    if (!trashId) return;
    try {
      const restored = await dashboard.api(`/api/results/trash/${encodeURIComponent(trashId)}/restore`, { method: "POST" });
      await dashboard.refreshExperimentData?.();
      await loadTrash();
      dashboard.toast?.("실험 결과 복원 완료", restored.run_id || trashId);
    } catch (error) {
      dashboard.toast?.("실험 결과 복원 실패", error.message, "error");
    }
  }

  async function purge(button) {
    const trashId = button?.dataset?.purgeTrash || "";
    const runId = button?.dataset?.runId || "";
    const archiveSha256 = button?.dataset?.trashSha || "";
    if (!trashId || !runId || !/^[0-9a-f]{64}$/.test(archiveSha256)) return;
    const typed = prompt(`이 작업은 복구할 수 없습니다.\n\n영구 삭제하려면 실행 ID를 그대로 입력하세요.\n${runId}`);
    if (typed !== runId) {
      if (typed !== null) dashboard.toast?.("영구 삭제 취소", "실행 ID가 일치하지 않습니다.", "error");
      return;
    }
    if (!confirm(`${runId} 결과를 영구 삭제할까요?\n\n현재 휴지통 내용의 SHA-256 체크섬을 서버가 다시 검증합니다.`)) return;
    try {
      await dashboard.api(`/api/results/trash/${encodeURIComponent(trashId)}`, {
        method: "DELETE",
        body: { confirmed: true, archive_sha256: archiveSha256 },
      });
      await loadTrash();
      dashboard.toast?.("실험 결과 영구 삭제 완료", runId);
    } catch (error) {
      dashboard.toast?.("영구 삭제 실패", error.message, "error");
    }
  }

  dashboard.results = { show, clear, remove, renderResponses, responseGroups, errorRecords, participantNodes, renderParticipantNodes, renderTrash, loadTrash, restore };

  document.addEventListener("DOMContentLoaded", () => {
    document.addEventListener("click", event => {
      const button = event.target.closest("[data-view-run]");
      const deleteButton = event.target.closest("[data-delete-run]");
      const restoreButton = event.target.closest("[data-restore-trash]");
      const purgeButton = event.target.closest("[data-purge-trash]");
      if (!button && !deleteButton && !restoreButton && !purgeButton) return;
      event.preventDefault(); event.stopPropagation();
      if (deleteButton) remove(deleteButton.dataset.deleteRun);
      else if (restoreButton) restore(restoreButton.dataset.restoreTrash);
      else if (purgeButton) purge(purgeButton);
      else show(button.dataset.viewRun);
    });
    dashboard.$?.("#openResultTrashButton")?.addEventListener("click", () => loadTrash({ open: true }));
    dashboard.$?.("#refreshResultTrashButton")?.addEventListener("click", () => loadTrash());
  });
})();
