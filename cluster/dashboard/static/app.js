const state = {
  token: "",
  nodes: [],
  status: [],
  models: [],
  selectedNodes: new Set(),
  runs: [],
  actions: [],
  activeExperiment: null,
  onboarding: {},
  eventSource: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function getToken() {
  const fromUrl = new URLSearchParams(location.search).get("token");
  if (fromUrl) {
    sessionStorage.setItem("clusterToken", fromUrl);
    const clean = new URL(location.href);
    clean.searchParams.delete("token");
    history.replaceState({}, "", clean.pathname + clean.search + clean.hash);
  }
  return fromUrl || sessionStorage.getItem("clusterToken") || "";
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}), "X-Cluster-Token": state.token };
  if (options.body && typeof options.body !== "string") {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

function toast(title, message = "", kind = "success") {
  const item = document.createElement("div");
  item.className = `toast ${kind === "error" ? "error" : ""}`;
  item.innerHTML = `<strong>${escapeHtml(title)}</strong>${message ? `<span>${escapeHtml(message)}</span>` : ""}`;
  $("#toastStack").append(item);
  setTimeout(() => item.remove(), 4300);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

const fmt = (value, digits = 1, fallback = "—") => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : fallback;
const pct = value => `${fmt(value, 0)}%`;

function statusFor(nodeName) {
  return state.status.find(item => item.name === nodeName) || {};
}

function initializeSelection() {
  if (state.selectedNodes.size) return;
  state.nodes.filter(node => node.enabled).forEach(node => state.selectedNodes.add(node.name));
}

function renderNodes() {
  initializeSelection();
  const grid = $("#nodeGrid");
  if (!state.nodes.length) {
    grid.innerHTML = `<div class="empty-result"><strong>등록된 노드가 없습니다.</strong></div>`;
    return;
  }
  grid.innerHTML = state.nodes.map(node => {
    const live = statusFor(node.name);
    const online = Boolean(live.api);
    const metrics = live.metrics || {};
    const model = live.current?.model_id || "모델 로드 안 됨";
    const selected = state.selectedNodes.has(node.name);
    const roleLabel = node.role === "head" ? "HEAD · CONTROL + INFERENCE" : "WORKER · INFERENCE";
    const error = live.error && live.error !== "disabled" ? live.error : "";
    return `
      <article class="node-card ${selected ? "selected" : ""} ${node.enabled ? "" : "disabled"}" data-node-card="${escapeHtml(node.name)}">
        <div class="node-card-head">
          <label class="node-select" title="실험 참여 여부">
            <input type="checkbox" data-node-select="${escapeHtml(node.name)}" ${selected ? "checked" : ""} ${node.enabled ? "" : "disabled"}>
            <span></span>
          </label>
          <div class="node-title"><strong>${escapeHtml(node.name)}</strong><small>${roleLabel}<br>${escapeHtml(node.host)}:${node.api_port}</small></div>
          <span class="status-pill ${online ? "online" : ""}"><i></i>${online ? "ONLINE" : node.enabled ? "OFFLINE" : "DISABLED"}</span>
        </div>
        <div class="node-model"><span>ACTIVE MODEL · ${live.model_count || 0} AVAILABLE</span><strong title="${escapeHtml(model)}">${escapeHtml(model)}</strong></div>
        <div class="node-metrics">
          <div><small>GPU</small><strong>${pct(metrics.gpu_pct)}</strong></div>
          <div><small>RAM</small><strong>${pct(metrics.ram_pct)}</strong></div>
          <div><small>POWER</small><strong>${fmt(metrics.power_w)}W</strong></div>
          <div><small>TEMP</small><strong>${fmt(metrics.gpu_temp_c, 0)}°</strong></div>
        </div>
        ${error ? `<button class="node-card-menu" type="button" title="${escapeHtml(error)}">!</button>` : ""}
      </article>`;
  }).join("");

  $$('[data-node-select]').forEach(input => input.addEventListener("change", event => {
    const name = event.currentTarget.dataset.nodeSelect;
    if (event.currentTarget.checked) state.selectedNodes.add(name); else state.selectedNodes.delete(name);
    if (!state.selectedNodes.size) {
      event.currentTarget.checked = true;
      state.selectedNodes.add(name);
      toast("노드 선택 필요", "실험에는 최소 한 대가 필요합니다.", "error");
    }
    renderNodes();
    updateSummary();
  }));
  updateSummary();
}

function updateSummary() {
  const enabled = state.nodes.filter(node => node.enabled);
  const online = enabled.filter(node => statusFor(node.name).api);
  const selected = [...state.selectedNodes];
  const powers = online.map(node => Number(statusFor(node.name).metrics?.power_w)).filter(Number.isFinite);
  $("#onlineCount").textContent = online.length;
  $("#enabledCount").textContent = enabled.length;
  $("#selectedCount").textContent = selected.length;
  $("#runNodes").textContent = selected.length;
  $("#selectionSummary").textContent = `선택 노드 ${selected.length}대 · ${selected.join(", ")}`;
  $("#modelCount").textContent = state.models.length || "—";
  $("#averagePower").textContent = powers.length ? fmt(powers.reduce((a, b) => a + b, 0) / powers.length) : "—";
  const head = enabled.find(node => node.role === "head");
  $("#headStatus").textContent = head && statusFor(head.name).api ? "ONLINE" : "OFFLINE";
  $$('.satellite').forEach((element, index) => {
    const worker = enabled.filter(node => node.role === "worker")[index];
    element.classList.toggle("online", Boolean(worker && statusFor(worker.name).api));
  });
  const latest = state.runs.find(run => run.status === "completed");
  $("#recentThroughput").textContent = latest ? fmt(latest.cluster_tokens_per_s) : "—";
  updateModelAvailability();
}

function updateModelAvailability() {
  const modelId = $("#modelSelect")?.value;
  if (!modelId) return;
  const missing = [...state.selectedNodes].filter(name => {
    const live = statusFor(name);
    return live.api && Array.isArray(live.model_ids) && !live.model_ids.includes(modelId);
  });
  const hint = $("#modelHint");
  if (missing.length) {
    hint.textContent = `${missing.join(", ")}에 모델이 없습니다. 실행 전에 모델 동기화가 필요합니다.`;
    hint.style.color = "var(--orange)";
  } else {
    const model = state.models.find(item => item.id === modelId);
    hint.textContent = model ? `${model.size_gb} GB · 선택 노드 모델 상태 정상` : "head 노드에 설치된 GGUF 모델";
    hint.style.color = "";
  }
}

function renderModels(defaults = {}) {
  const select = $("#modelSelect");
  const current = select.value || defaults.model_id;
  select.innerHTML = state.models.map(model => `<option value="${escapeHtml(model.id)}">${escapeHtml(model.id)} · ${model.size_gb} GB</option>`).join("");
  if (state.models.some(model => model.id === current)) select.value = current;
  updateModelAvailability();
}

function applyDefaults(defaults) {
  const mapping = {
    name: "#experimentName", requests: "#requestsInput", concurrency: "#concurrencyInput",
    max_tokens: "#maxTokensInput", n_ctx: "#contextInput", n_gpu_layers: "#layersInput",
    warmup_requests: "#warmupInput", temperature: "#temperatureInput", top_p: "#topPInput",
    seed: "#seedInput", prompt: "#promptInput",
  };
  Object.entries(mapping).forEach(([key, selector]) => { if (defaults[key] !== undefined) $(selector).value = defaults[key]; });
  $("#uniformInput").checked = defaults.require_uniform_config !== false;
  renderModels(defaults);
  updateFormMirrors();
}

function updateFormMirrors() {
  $("#temperatureValue").textContent = Number($("#temperatureInput").value).toFixed(1);
  $("#topPValue").textContent = Number($("#topPInput").value).toFixed(2).replace(/0$/, "");
  $("#promptLength").textContent = $("#promptInput").value.length;
  $("#runRequests").textContent = $("#requestsInput").value;
  $("#runConcurrency").textContent = $("#concurrencyInput").value;
  updateModelAvailability();
}

function renderRuns() {
  const runs = state.runs || [];
  const table = $("#runsTable");
  if (!runs.length) {
    table.innerHTML = `<tr><td colspan="7" class="empty-cell">실행 기록 없음</td></tr>`;
    return;
  }
  table.innerHTML = runs.slice(0, 20).map(run => `
    <tr>
      <td><strong>${escapeHtml(run.name || run.run_id)}</strong><br><small>${escapeHtml(run.run_id || "")}</small></td>
      <td>${Array.isArray(run.nodes) ? run.nodes.length : "—"}</td>
      <td>${run.success_rate !== undefined ? pct(Number(run.success_rate) * 100) : "—"}</td>
      <td>${run.ttft_p50_s != null ? `${fmt(run.ttft_p50_s, 2)}s` : "—"}</td>
      <td>${run.e2e_p95_s != null ? `${fmt(run.e2e_p95_s, 2)}s` : "—"}</td>
      <td>${run.cluster_tokens_per_s != null ? `${fmt(run.cluster_tokens_per_s)} tok/s` : "—"}</td>
      <td><span class="run-status ${run.status === "failed" ? "failed" : ""}">${escapeHtml((run.status || "unknown").toUpperCase())}</span></td>
    </tr>`).join("");
  const latest = runs.find(run => run.status === "completed") || runs[0];
  if (latest && latest.cluster_tokens_per_s != null) {
    $("#resultHighlight").innerHTML = `<div class="result-cards">
      <article class="result-card primary-result"><span>CLUSTER THROUGHPUT</span><strong>${fmt(latest.cluster_tokens_per_s)}<small> tok/s</small></strong><small>${escapeHtml(latest.name || latest.run_id)}</small></article>
      <article class="result-card"><span>TTFT · P50</span><strong>${fmt(latest.ttft_p50_s, 2)}s</strong><small>첫 토큰 지연</small></article>
      <article class="result-card"><span>E2E · P95</span><strong>${fmt(latest.e2e_p95_s, 2)}s</strong><small>요청 완료 지연</small></article>
      <article class="result-card"><span>SUCCESS</span><strong>${pct(Number(latest.success_rate) * 100)}</strong><small>${latest.successful || 0} / ${latest.requests || 0} requests</small></article>
    </div>`;
  }
  updateSummary();
}

function setRunState(active) {
  state.activeExperiment = active;
  const running = active && ["queued", "running"].includes(active.status);
  $("#runButton").classList.toggle("hidden", running);
  $("#cancelButton").classList.toggle("hidden", !running);
  $("#runStateDot").classList.toggle("running", running);
  if (!active) return;
  const total = Number(active.total || 0);
  const completed = Number(active.completed || 0);
  const progress = total ? Math.min(100, Math.round(completed / total * 100)) : 0;
  $("#runProgressBar").style.width = `${progress}%`;
  $("#runProgressText").textContent = `${progress}%`;
  $("#runPhase").textContent = ({ queued: "실행 준비", loading_model: "모델 로드", warmup: "워밍업", measurement: "부하 측정", cancelling: "취소 요청" })[active.phase] || active.status || "실행 중";
  if (active.error) logLine("ERROR", active.error);
}

function logLine(label, message) {
  const log = $("#consoleLog");
  const line = document.createElement("p");
  line.innerHTML = `<time>${escapeHtml(label)}</time>${escapeHtml(message)}`;
  log.append(line);
  while (log.children.length > 80) log.firstElementChild.remove();
  log.scrollTop = log.scrollHeight;
}

async function bootstrap() {
  try {
    const data = await api("/api/bootstrap");
    state.nodes = data.nodes || [];
    state.status = data.status || [];
    state.models = data.models || [];
    state.runs = data.runs || [];
    state.actions = data.actions || [];
    state.onboarding = data.onboarding || {};
    applyDefaults(data.defaults || {});
    renderNodes();
    renderRuns();
    setRunState(data.active_experiment);
    $("#publicKey").textContent = state.onboarding.public_key || "키가 아직 생성되지 않았습니다.";
    connectEvents();
    if (!location.hash) requestAnimationFrame(() => window.scrollTo({ top: 0, left: 0, behavior: "instant" }));
  } catch (error) {
    if (/401|token|invalid|missing/i.test(error.message)) {
      $("#authDialog").showModal();
    } else {
      toast("대시보드 초기화 실패", error.message, "error");
    }
  }
}

function connectEvents() {
  if (state.eventSource) state.eventSource.close();
  state.eventSource = new EventSource(`/api/events?token=${encodeURIComponent(state.token)}`);
  state.eventSource.onopen = () => {
    $(".live-indicator").classList.add("connected");
    $("#streamLabel").textContent = "실시간 연결됨";
  };
  state.eventSource.onerror = () => {
    $(".live-indicator").classList.remove("connected");
    $("#streamLabel").textContent = "재연결 중";
  };
  state.eventSource.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.type === "cluster_status") {
      state.status = message.nodes || [];
      $("#lastUpdated").textContent = `UPDATED ${new Date(message.at).toLocaleTimeString("ko-KR")}`;
      renderNodes();
    } else if (message.type === "inventory_changed") {
      state.nodes = message.nodes || state.nodes;
      renderNodes();
    } else if (message.type === "action_log") {
      logLine("TASK", message.line);
    } else if (message.type === "action_finished") {
      const action = message.action;
      toast(action.status === "completed" ? "작업 완료" : "작업 실패", `${action.action} · ${action.nodes.join(", ")}`, action.status === "completed" ? "success" : "error");
    } else if (message.type === "experiment_event") {
      const inner = message.event || {};
      setRunState(message.active);
      if (inner.type === "phase") logLine("PHASE", inner.message || inner.phase);
      if (inner.type === "node_model_loaded") logLine("MODEL", `${inner.node} · ${inner.actual?.model_id || "loaded"}`);
      if (inner.type === "request_completed") logLine("RUN", `${inner.completed}/${inner.total} · ${inner.result?.node} · ${inner.result?.ok ? "OK" : "FAIL"}`);
      if (inner.type === "warning") logLine("WARN", inner.message);
      if (inner.type === "run_finished") {
        state.runs.unshift(inner.summary);
        renderRuns();
        toast("벤치마크 완료", `${fmt(inner.summary.cluster_tokens_per_s)} tok/s · ${inner.summary.nodes.length} nodes`);
      }
    } else if (message.type === "experiment_failed") {
      setRunState(message.active);
      toast("벤치마크 실패", message.message, "error");
    }
  };
}

async function runAction(action, options = {}) {
  const selected = [...state.selectedNodes];
  if (!selected.length) return toast("노드 선택 필요", "하나 이상의 노드를 선택하세요.", "error");
  try {
    const result = await api("/api/actions", { method: "POST", body: { action, node_names: selected, options } });
    logLine("TASK", `${action} 시작 · ${result.action.nodes.join(", ")}`);
    toast("작업 시작", action);
  } catch (error) {
    toast("작업 시작 실패", error.message, "error");
  }
}

function experimentPayload() {
  return {
    name: $("#experimentName").value.trim(),
    node_names: [...state.selectedNodes],
    model_id: $("#modelSelect").value,
    requests: Number($("#requestsInput").value),
    concurrency: Number($("#concurrencyInput").value),
    max_tokens: Number($("#maxTokensInput").value),
    n_ctx: Number($("#contextInput").value),
    n_gpu_layers: Number($("#layersInput").value),
    warmup_requests: Number($("#warmupInput").value),
    temperature: Number($("#temperatureInput").value),
    top_p: Number($("#topPInput").value),
    seed: Number($("#seedInput").value),
    require_uniform_config: $("#uniformInput").checked,
    prompt: $("#promptInput").value,
  };
}

function bindEvents() {
  $("#authForm").addEventListener("submit", event => {
    event.preventDefault();
    state.token = $("#tokenInput").value.trim();
    sessionStorage.setItem("clusterToken", state.token);
    $("#authDialog").close();
    bootstrap();
  });
  $$('[data-close-dialog]').forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
  $("#addNodeButton").addEventListener("click", () => $("#nodeDialog").showModal());
  $("#nodeForm").addEventListener("submit", async event => {
    event.preventDefault();
    const payload = {
      name: $("#nodeName").value.trim(), role: "worker", host: $("#nodeHost").value.trim(),
      user: $("#nodeUser").value.trim(), ssh_port: Number($("#nodeSshPort").value), api_port: Number($("#nodeApiPort").value),
      project_dir: $("#nodeProjectDir").value.trim(), enabled: true, identity_file: "",
    };
    try {
      const result = await api("/api/nodes", { method: "POST", body: payload });
      const index = state.nodes.findIndex(node => node.name === result.node.name);
      if (index >= 0) state.nodes[index] = result.node; else state.nodes.push(result.node);
      state.selectedNodes.add(result.node.name);
      renderNodes();
      $("#nodeDialog").close();
      event.currentTarget.reset();
      $("#nodeUser").value = "jetson_orin_nano";
      $("#nodeProjectDir").value = "/home/jetson_orin_nano/project/llm/local_llm_bench";
      toast("워커 등록 완료", "SSH 키 배포 후 ‘선택 워커 준비’를 실행하세요.");
    } catch (error) { toast("워커 등록 실패", error.message, "error"); }
  });
  $("#copyKeyButton").addEventListener("click", async () => {
    if (!state.onboarding.public_key) return toast("SSH 키 없음", "head에서 키 생성 스크립트를 실행하세요.", "error");
    await navigator.clipboard.writeText(state.onboarding.public_key);
    toast("SSH 공개 키 복사됨");
  });
  $("#refreshButton").addEventListener("click", () => api("/api/status/refresh", { method: "POST" }).catch(error => toast("새로고침 실패", error.message, "error")));
  $("#quickHealthButton").addEventListener("click", () => runAction("doctor"));
  $("#prepareButton").addEventListener("click", () => {
    const workers = [...state.selectedNodes].filter(name => state.nodes.find(node => node.name === name)?.role === "worker");
    if (!workers.length) return toast("워커 선택 필요", "준비할 worker 노드를 선택하세요.", "error");
    if (confirm("선택한 워커에 코드와 모델을 동기화하고 Python/CUDA 환경을 구성한 뒤 API 서버를 시작합니다. 계속할까요?")) {
      runAction("prepare", { confirmed: true, models: [$("#modelSelect").value] });
    }
  });
  $$('[data-cluster-action]').forEach(button => button.addEventListener("click", () => {
    const action = button.dataset.clusterAction;
    const options = action === "sync-models" ? { models: [$("#modelSelect").value] } : {};
    runAction(action, options);
  }));
  $$('.segmented button').forEach(button => button.addEventListener("click", () => {
    const count = Number(button.dataset.preset);
    const candidates = state.nodes.filter(node => node.enabled).slice(0, count);
    state.selectedNodes = new Set(candidates.map(node => node.name));
    $$('.segmented button').forEach(item => item.classList.toggle("active", item === button));
    renderNodes();
  }));
  $("#experimentForm").addEventListener("input", updateFormMirrors);
  $("#modelSelect").addEventListener("change", updateModelAvailability);
  $("#experimentForm").addEventListener("submit", async event => {
    event.preventDefault();
    const missingOffline = [...state.selectedNodes].filter(name => !statusFor(name).api);
    if (missingOffline.length) return toast("노드 API 오프라인", `${missingOffline.join(", ")} 서버를 먼저 시작하세요.`, "error");
    try {
      const data = await api("/api/experiments", { method: "POST", body: experimentPayload() });
      $("#consoleLog").innerHTML = "";
      logLine("START", `${data.experiment.name} · ${data.experiment.nodes.join(", ")}`);
      setRunState(data.experiment);
    } catch (error) { toast("실험 시작 실패", error.message, "error"); }
  });
  $("#cancelButton").addEventListener("click", async () => {
    try { const data = await api("/api/experiments/cancel", { method: "POST" }); setRunState(data.experiment); }
    catch (error) { toast("취소 실패", error.message, "error"); }
  });
  const observer = new IntersectionObserver(entries => entries.forEach(entry => {
    if (entry.isIntersecting) $$('.nav-link').forEach(link => link.classList.toggle("active", link.dataset.section === entry.target.id));
  }), { rootMargin: "-30% 0px -60%" });
  $$('.section').forEach(section => observer.observe(section));
}

document.addEventListener("DOMContentLoaded", () => {
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";
  state.token = getToken();
  bindEvents();
  if (!state.token) $("#authDialog").showModal(); else bootstrap();
});
