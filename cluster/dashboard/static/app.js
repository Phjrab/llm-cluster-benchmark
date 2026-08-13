const state = {
  token: "",
  nodes: [],
  status: [],
  models: [],
  selectedNodes: new Set(),
  runs: [],
  experimentGroups: [],
  actions: [],
  activeExperiment: null,
  onboarding: {},
  settings: { worker_api_auth: false },
  onboardingProbe: null,
  devices: [],
  eventSource: null,
  metricHistory: new Map(),
  detailNode: "",
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
  setTimeout(() => item.remove(), 4800);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

function finite(value) {
  return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
}

const fmt = (value, digits = 1, fallback = "—") => finite(value) ? Number(value).toFixed(digits) : fallback;
const pct = value => finite(value) ? `${fmt(value, 0)}%` : "—";
const platformName = value => ({ jetson: "NVIDIA Jetson", "raspberry-pi": "Raspberry Pi 5", auto: "자동 감지", "generic-linux": "Linux" }[value] || value || "미확인");
const STRATEGIES = {
  single_node: { label: "단일 노드 기준선", short: "SINGLE" },
  replicated_round_robin: { label: "복제 · 요청 분산", short: "ROUND ROBIN" },
  broadcast_compare: { label: "전체 동시 전송", short: "BROADCAST" },
  node_sweep: { label: "노드 수 스윕", short: "NODE SWEEP" },
  model_parallel_rpc: { label: "모델 분할 · RPC", short: "MODEL RPC", experimental: true },
  legacy: { label: "이전 방식 · 복제 요청 분산", short: "LEGACY RR" },
};

function selectedStrategy() {
  return $('input[name="execution_strategy"]:checked')?.value || "replicated_round_robin";
}

function plannedNodeNames() {
  return [...state.selectedNodes];
}

function runStrategy(run) {
  return run.execution_strategy || run.strategy || run.config?.execution_strategy || run.definition?.default_config?.execution_strategy || "legacy";
}

function strategyMeta(value) {
  return STRATEGIES[value] || { label: value || "알 수 없음", short: String(value || "UNKNOWN").toUpperCase() };
}

function parseRpcTensorSplit(strict = false) {
  const raw = $("#rpcTensorSplitInput").value.trim();
  if (!raw) {
    if (strict && $("#rpcSplitPolicySelect").value === "custom") throw new Error("직접 분할을 선택했다면 노드별 비율을 입력하세요.");
    return [];
  }
  let values;
  try {
    values = raw.startsWith("[") ? JSON.parse(raw) : raw.split(",").map(value => Number(value.trim()));
  } catch (_error) {
    if (strict) throw new Error("노드별 비율은 1, 1, 2 같은 숫자 목록이어야 합니다.");
    return [];
  }
  const valid = Array.isArray(values) && values.length && values.every(value => Number.isFinite(Number(value)) && Number(value) > 0);
  if (!valid) {
    if (strict) throw new Error("노드별 분할 비율에는 0보다 큰 숫자만 사용할 수 있습니다.");
    return [];
  }
  const normalized = values.map(Number);
  if (strict && normalized.length !== state.selectedNodes.size) throw new Error(`선택 노드 ${state.selectedNodes.size}대와 같은 개수의 분할 비율이 필요합니다.`);
  return normalized;
}

function formatUptime(seconds) {
  if (!finite(seconds)) return "—";
  const total = Number(seconds);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return `${days ? `${days}일 ` : ""}${hours}시간 ${minutes}분`;
}

function statusFor(nodeName) {
  return state.status.find(item => item.name === nodeName) || {};
}

function actualPlatform(node) {
  return statusFor(node.name).profile?.platform_kind || statusFor(node.name).node_info?.platform_kind || node.platform || "auto";
}

function runExperimentId(run) {
  if (run.experiment_id) return run.experiment_id;
  const slug = String(run.name || "unnamed").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "experiment";
  return `legacy-${slug}`;
}

function ingestStatus(items) {
  items.forEach(item => {
    const metrics = item.metrics || {};
    if (!metrics.sampled_at) return;
    const history = state.metricHistory.get(item.name) || [];
    if (history.at(-1)?.sampled_at === metrics.sampled_at) return;
    history.push({
      sampled_at: metrics.sampled_at,
      cpu: finite(metrics.cpu_pct) ? Number(metrics.cpu_pct) : null,
      gpu: finite(metrics.gpu_pct) ? Number(metrics.gpu_pct) : null,
      ram: finite(metrics.ram_pct) ? Number(metrics.ram_pct) : null,
      power: finite(metrics.power_w) ? Number(metrics.power_w) : null,
      temperature: finite(metrics.gpu_temp_c) ? Number(metrics.gpu_temp_c) : finite(metrics.cpu_temp_c) ? Number(metrics.cpu_temp_c) : null,
    });
    if (history.length > 120) history.splice(0, history.length - 120);
    state.metricHistory.set(item.name, history);
  });
}

function initializeSelection() {
  if (state.selectedNodes.size) return;
  state.nodes.filter(node => node.enabled).slice(0, 4).forEach(node => state.selectedNodes.add(node.name));
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
    const kind = actualPlatform(node);
    const roleLabel = node.role === "head" ? "HEAD · CONTROL + INFERENCE" : `WORKER · ${platformName(kind).toUpperCase()}`;
    const error = live.error && live.error !== "disabled" ? live.error : "";
    return `
      <article class="node-card ${selected ? "selected" : ""} ${node.enabled ? "" : "disabled"}" data-node-card="${escapeHtml(node.name)}">
        <div class="node-card-head">
          <label class="node-select" title="실험 참여 여부">
            <input type="checkbox" data-node-select="${escapeHtml(node.name)}" ${selected ? "checked" : ""} ${node.enabled ? "" : "disabled"}>
            <span></span>
          </label>
          <div class="node-title"><strong>${escapeHtml(node.name)}</strong><small>${escapeHtml(roleLabel)}<br>${escapeHtml(node.host)}:${node.api_port}</small></div>
          <span class="status-pill ${online ? "online" : ""}"><i></i>${online ? "ONLINE" : node.enabled ? "OFFLINE" : "DISABLED"}</span>
        </div>
        <div class="node-model"><span>ACTIVE MODEL · ${live.model_count || 0} AVAILABLE</span><strong title="${escapeHtml(model)}">${escapeHtml(model)}</strong></div>
        <div class="node-metrics">
          <div><small>CPU</small><strong>${pct(metrics.cpu_pct)}</strong></div>
          <div><small>${kind === "jetson" ? "GPU" : "RAM"}</small><strong>${kind === "jetson" ? pct(metrics.gpu_pct) : pct(metrics.ram_pct)}</strong></div>
          <div><small>POWER</small><strong>${finite(metrics.power_w) ? `${fmt(metrics.power_w)}W` : "N/A"}</strong></div>
          <div><small>TEMP</small><strong>${finite(metrics.gpu_temp_c ?? metrics.cpu_temp_c) ? `${fmt(metrics.gpu_temp_c ?? metrics.cpu_temp_c, 0)}°` : "—"}</strong></div>
        </div>
        <button class="node-detail-button" type="button" data-node-detail="${escapeHtml(node.name)}">상세 상태</button>
        ${error ? `<span class="node-card-menu" title="${escapeHtml(error)}">!</span>` : ""}
      </article>`;
  }).join("");

  $$('[data-node-select]').forEach(input => input.addEventListener("change", event => {
    const name = event.currentTarget.dataset.nodeSelect;
    if (event.currentTarget.checked) {
      if (state.selectedNodes.size >= 4) {
        event.currentTarget.checked = false;
        return toast("최대 4대", "한 실험에는 최대 네 대까지 참여할 수 있습니다.", "error");
      }
      state.selectedNodes.add(name);
    } else {
      state.selectedNodes.delete(name);
    }
    if (!state.selectedNodes.size) {
      event.currentTarget.checked = true;
      state.selectedNodes.add(name);
      toast("노드 선택 필요", "실험에는 최소 한 대가 필요합니다.", "error");
    }
    renderNodes();
  }));
  $$('[data-node-detail]').forEach(button => button.addEventListener("click", () => openNodeDetail(button.dataset.nodeDetail)));
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
  $("#averagePower").textContent = powers.length ? fmt(powers.reduce((a, b) => a + b, 0)) : "—";
  const head = enabled.find(node => node.role === "head");
  $("#headStatus").textContent = head && statusFor(head.name).api ? "ONLINE" : "OFFLINE";
  $$('.satellite').forEach((element, index) => {
    const worker = enabled.filter(node => node.role === "worker")[index];
    element.classList.toggle("online", Boolean(worker && statusFor(worker.name).api));
  });
  const latest = state.runs.find(run => run.status === "completed");
  $("#recentThroughput").textContent = latest ? fmt(latest.cluster_tokens_per_s) : "—";
  updateStrategyGuidance();
  updatePlatformGuidance();
  updateModelAvailability();
}

function updatePlatformGuidance() {
  const selectedKinds = plannedNodeNames().map(name => {
    const node = state.nodes.find(item => item.name === name);
    return node ? actualPlatform(node) : "auto";
  });
  const hasPi = selectedKinds.includes("raspberry-pi");
  const layers = $("#layersInput");
  const rpc = selectedStrategy() === "model_parallel_rpc";
  layers.max = hasPi && !rpc ? "0" : "120";
  if (hasPi && !rpc && Number(layers.value) !== 0) layers.value = "0";
  layers.disabled = rpc;
  layers.title = rpc ? "RPC 모델 분할은 coordinator와 원격 장치의 전체 가속 가능 레이어를 사용합니다." : "";
  $("#uniformInput").disabled = rpc;
  $("#configValidity").textContent = rpc
    ? ($("#rpcAcknowledgeInput").checked ? "RPC 실험 준비됨" : "RPC 위험 확인 필요")
    : hasPi ? "Pi 포함 · CPU 모드" : "설정 준비됨";
}

function updateStrategyGuidance() {
  const strategy = selectedStrategy();
  const meta = strategyMeta(strategy);
  const nodeCount = Math.max(1, state.selectedNodes.size);
  const requests = Math.max(0, Number($("#requestsInput").value) || 0);
  const sweepMode = $("#sweepModeSelect").value;
  const splitPolicy = $("#rpcSplitPolicySelect").value;
  $("#sweepOptions").hidden = strategy !== "node_sweep";
  $("#rpcOptions").hidden = strategy !== "model_parallel_rpc";
  $("#rpcTensorField").hidden = splitPolicy !== "custom";
  $$(".strategy-card").forEach(card => card.classList.toggle("selected", card.querySelector("input")?.checked));
  $("#strategyFormulaTitle").textContent = meta.label;
  $("#runStrategyBadge").textContent = meta.label;
  $("#runNodes").textContent = strategy === "single_node" ? 1 : nodeCount;

  let modelCopies = nodeCount;
  let nodesPerAnswer = 1;
  let logicalRequests = requests;
  let physicalCalls = requests;
  let explanation = "모델을 노드마다 복제하고 사용자 요청은 한 노드씩 분배합니다.";
  if (strategy === "single_node") {
    modelCopies = 1;
    explanation = nodeCount === 1
      ? `선택한 노드(${[...state.selectedNodes][0] || "미선택"}) 하나에서 기준 성능을 측정합니다.`
      : "정확한 기준선을 위해 노드 한 대만 선택해야 합니다.";
  } else if (strategy === "broadcast_compare") {
    physicalCalls = requests * nodeCount;
    explanation = `논리 요청 ${requests}개를 ${nodeCount}대 모두에 보내 서로 독립된 답변 ${physicalCalls}개를 비교합니다.`;
  } else if (strategy === "node_sweep") {
    physicalCalls = requests * nodeCount;
    logicalRequests = physicalCalls;
    explanation = sweepMode === "cumulative"
      ? `1대부터 ${nodeCount}대까지 ${nodeCount}개 단계에서 요청 ${requests}개씩 실행합니다. 각 단계 안에서는 선택된 노드가 요청을 나눕니다.`
      : `${nodeCount}대를 하나씩 분리해 각 노드에 요청 ${requests}개를 실행합니다.`;
  } else if (strategy === "model_parallel_rpc") {
    modelCopies = "1 · 분할";
    nodesPerAnswer = nodeCount;
    explanation = `모델 하나를 ${nodeCount}대에 나누고 한 답변 계산에 모두 참여시킵니다. 물리 호출 수에는 내부 텐서 RPC 통신을 포함하지 않습니다.`;
  }
  $("#formulaModelCopies").textContent = modelCopies;
  $("#formulaNodesPerAnswer").textContent = nodesPerAnswer;
  $("#formulaLogicalRequests").textContent = logicalRequests;
  $("#formulaPhysicalCalls").textContent = physicalCalls;
  $("#strategyFormulaExplanation").textContent = explanation;
}

function updateModelAvailability() {
  const modelId = $("#modelSelect")?.value;
  if (!modelId) return;
  const plannedNames = plannedNodeNames();
  const placementNames = selectedStrategy() === "model_parallel_rpc"
    ? plannedNames.filter(name => state.nodes.find(node => node.name === name)?.role === "head")
    : plannedNames;
  const missing = placementNames.filter(name => {
    const live = statusFor(name);
    return live.api && Array.isArray(live.model_ids) && !live.model_ids.includes(modelId);
  });
  const selectedKinds = plannedNames.map(name => {
    const node = state.nodes.find(item => item.name === name);
    return node ? actualPlatform(node) : "auto";
  });
  const hint = $("#modelHint");
  if (missing.length) {
    hint.textContent = `${missing.join(", ")}에 모델이 없습니다. 실행 전에 모델 동기화가 필요합니다.`;
    hint.style.color = "var(--orange)";
  } else {
    const model = state.models.find(item => item.id === modelId);
    const piNote = selectedKinds.includes("raspberry-pi")
      ? selectedStrategy() === "model_parallel_rpc" ? " · Pi는 RPC CPU 장치로 참여" : " · Pi CPU/OpenBLAS · GPU 레이어 0"
      : "";
    const placementNote = selectedStrategy() === "model_parallel_rpc"
      ? " · GGUF는 head에만 필요, worker는 텐서 수신"
      : " · 선택 노드 모델 상태 정상";
    hint.textContent = model ? `${model.size_gb} GB${placementNote}${piNote}` : "head 노드에 설치된 GGUF 모델";
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

function renderSettings() {
  const enabled = Boolean(state.settings.worker_api_auth);
  $("#workerAuthInput").checked = enabled;
  const notice = $("#workerAuthNotice");
  notice.classList.toggle("enabled", enabled);
  notice.textContent = enabled
    ? "현재 켜짐 · worker API 토큰 인증 모드"
    : "현재 꺼짐 · 신뢰 LAN 전용 모드";
}

function applyConfig(defaults, includeName = true) {
  const mapping = {
    requests: "#requestsInput", concurrency: "#concurrencyInput", max_tokens: "#maxTokensInput",
    n_ctx: "#contextInput", n_gpu_layers: "#layersInput", warmup_requests: "#warmupInput",
    temperature: "#temperatureInput", top_p: "#topPInput", seed: "#seedInput", prompt: "#promptInput",
  };
  if (includeName && defaults.name !== undefined) $("#experimentName").value = defaults.name;
  Object.entries(mapping).forEach(([key, selector]) => { if (defaults[key] !== undefined) $(selector).value = defaults[key]; });
  if (defaults.require_uniform_config !== undefined) $("#uniformInput").checked = defaults.require_uniform_config !== false;
  const strategy = defaults.execution_strategy || defaults.strategy;
  const strategyInput = strategy ? $$('input[name="execution_strategy"]').find(input => input.value === strategy) : null;
  if (strategyInput) strategyInput.checked = true;
  if (defaults.sweep_mode !== undefined) $("#sweepModeSelect").value = defaults.sweep_mode;
  if (defaults.rpc_split_mode !== undefined) $("#rpcSplitModeSelect").value = defaults.rpc_split_mode;
  if (defaults.rpc_split_policy !== undefined) $("#rpcSplitPolicySelect").value = defaults.rpc_split_policy;
  if (Array.isArray(defaults.rpc_tensor_split)) $("#rpcTensorSplitInput").value = defaults.rpc_tensor_split.join(", ");
  if (defaults.acknowledge_experimental_rpc !== undefined) $("#rpcAcknowledgeInput").checked = Boolean(defaults.acknowledge_experimental_rpc);
  if (defaults.model_id && state.models.some(model => model.id === defaults.model_id)) $("#modelSelect").value = defaults.model_id;
  if (Array.isArray(defaults.node_names)) {
    const available = defaults.node_names.filter(name => state.nodes.some(node => node.name === name && node.enabled));
    if (available.length) state.selectedNodes = new Set(available.slice(0, 4));
  }
  updateFormMirrors();
  renderNodes();
}

function applyDefaults(defaults) {
  $("#experimentName").value = defaults.name || "cluster-load-test";
  renderModels(defaults);
  applyConfig({ ...defaults, require_uniform_config: defaults.require_uniform_config !== false });
}

function updateFormMirrors() {
  $("#temperatureValue").textContent = Number($("#temperatureInput").value).toFixed(1);
  $("#topPValue").textContent = Number($("#topPInput").value).toFixed(2).replace(/0$/, "");
  $("#promptLength").textContent = $("#promptInput").value.length;
  $("#runRequests").textContent = $("#requestsInput").value;
  $("#runConcurrency").textContent = $("#concurrencyInput").value;
  updateStrategyGuidance();
  updatePlatformGuidance();
  updateModelAvailability();
}

function renderExperimentGroups() {
  const formSelect = $("#experimentGroupSelect");
  const resultSelect = $("#resultExperimentFilter");
  const currentForm = formSelect.value;
  const currentResult = resultSelect.value || "all";
  const options = state.experimentGroups.map(group => {
    const strategy = group.default_config?.execution_strategy;
    const strategySuffix = strategy ? ` · ${strategyMeta(strategy).label}` : "";
    return `<option value="${escapeHtml(group.experiment_id)}">${escapeHtml(group.name)} · ${group.run_count || 0}회${escapeHtml(strategySuffix)}${group.legacy ? " · 이전 기록" : ""}</option>`;
  }).join("");
  formSelect.innerHTML = `<option value="">+ 새 실험 만들기</option>${options}`;
  resultSelect.innerHTML = `<option value="all">전체 실험</option>${options}`;
  if ([...formSelect.options].some(option => option.value === currentForm)) formSelect.value = currentForm;
  if ([...resultSelect.options].some(option => option.value === currentResult)) resultSelect.value = currentResult;
}

function filteredRuns() {
  const filter = $("#resultExperimentFilter").value;
  if (filter === "all") return state.runs;
  const group = state.experimentGroups.find(item => item.experiment_id === filter);
  return group?.runs || state.runs.filter(run => runExperimentId(run) === filter);
}

function renderRuns() {
  const runs = filteredRuns();
  const filter = $("#resultExperimentFilter").value;
  const group = state.experimentGroups.find(item => item.experiment_id === filter);
  const completedStrategies = new Set(runs.filter(run => run.status === "completed" && finite(run.cluster_tokens_per_s)).map(runStrategy));
  const mixedAllStrategies = completedStrategies.size > 1;
  const baseContext = group
    ? `${group.name} · ${group.run_count || 0}회 실행 · ${strategyMeta(group.default_config?.execution_strategy || runStrategy(runs[0] || {})).label} · experiment_id ${group.experiment_id}`
    : `모든 실험 ${state.experimentGroups.length}개 · 실행 ${state.runs.length}회`;
  $("#experimentContext").textContent = mixedAllStrategies
    ? `${baseContext} · 서로 다른 실행 방식이 섞여 있어 그래프를 숨겼습니다. 방식별 실험 묶음을 따로 만들어 비교하세요.`
    : baseContext;
  const table = $("#runsTable");
  if (!runs.length) {
    table.innerHTML = `<tr><td colspan="8" class="empty-cell">이 실험의 실행 기록 없음</td></tr>`;
    $("#resultHighlight").innerHTML = `<div class="empty-result"><strong>연결된 벤치마크 결과가 없습니다.</strong><span>이 실험을 실행하면 결과가 같은 experiment_id에 누적됩니다.</span></div>`;
    $("#chartGrid").hidden = true;
    updateSummary();
    return;
  }
  table.innerHTML = runs.slice(0, 30).map(run => `
    <tr data-run-experiment="${escapeHtml(runExperimentId(run))}">
      <td><strong>${escapeHtml(run.name || run.run_id)}</strong><br><small>${escapeHtml(run.run_id || "")}</small></td>
      <td><span class="strategy-badge ${strategyMeta(runStrategy(run)).experimental ? "experimental" : ""}">${escapeHtml(strategyMeta(runStrategy(run)).label)}</span></td>
      <td>${Array.isArray(run.nodes) ? run.nodes.length : "—"}</td>
      <td>${run.success_rate !== undefined ? pct(Number(run.success_rate) * 100) : "—"}</td>
      <td>${run.ttft_p50_s != null ? `${fmt(run.ttft_p50_s, 2)}s` : "—"}</td>
      <td>${run.e2e_p95_s != null ? `${fmt(run.e2e_p95_s, 2)}s` : "—"}</td>
      <td>${run.cluster_tokens_per_s != null ? `${fmt(run.cluster_tokens_per_s)} tok/s` : "—"}</td>
      <td><span class="run-status ${run.status === "failed" ? "failed" : ""}">${escapeHtml((run.status || "unknown").toUpperCase())}</span></td>
    </tr>`).join("");
  const latest = runs.find(run => run.status === "completed") || runs[0];
  if (latest && latest.cluster_tokens_per_s != null) {
    const latestStrategy = strategyMeta(runStrategy(latest));
    const throughputTitle = runStrategy(latest) === "model_parallel_rpc" ? "SHARDED MODEL THROUGHPUT" : "CLUSTER THROUGHPUT";
    const successDetail = runStrategy(latest) === "broadcast_compare" && latest.answer_agreement_rate != null
      ? `답변 일치 ${pct(Number(latest.answer_agreement_rate) * 100)}`
      : `${latest.successful || 0} / ${latest.requests || 0} physical calls`;
    $("#resultHighlight").innerHTML = `<div class="result-cards">
      <article class="result-card primary-result"><span>${throughputTitle}</span><strong>${fmt(latest.cluster_tokens_per_s)}<small> tok/s</small></strong><small>${escapeHtml(latest.name || latest.run_id)}</small><i class="result-strategy">${escapeHtml(latestStrategy.label)}</i></article>
      <article class="result-card"><span>TTFT · P50</span><strong>${fmt(latest.ttft_p50_s, 2)}s</strong><small>첫 토큰 지연</small></article>
      <article class="result-card"><span>E2E · P95</span><strong>${fmt(latest.e2e_p95_s, 2)}s</strong><small>요청 완료 지연</small></article>
      <article class="result-card"><span>SUCCESS</span><strong>${pct(Number(latest.success_rate) * 100)}</strong><small>${successDetail}</small></article>
    </div>`;
  } else {
    $("#resultHighlight").innerHTML = `<div class="empty-result"><strong>완료된 측정값이 없습니다.</strong><span>최근 실행 상태: ${escapeHtml((latest?.status || "unknown").toUpperCase())}${latest?.error ? ` · ${escapeHtml(latest.error)}` : ""}</span></div>`;
  }
  const hasCompletedMetrics = runs.some(run => run.status === "completed" && finite(run.cluster_tokens_per_s));
  $("#chartGrid").hidden = !hasCompletedMetrics || mixedAllStrategies;
  if (hasCompletedMetrics && !mixedAllStrategies) requestAnimationFrame(() => drawResultCharts(runs));
  $$('[data-run-experiment]').forEach(row => row.addEventListener("click", () => {
    $("#resultExperimentFilter").value = row.dataset.runExperiment;
    renderRuns();
  }));
  updateSummary();
}

function setupCanvas(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(canvas.clientWidth, 280);
  const height = Number(canvas.getAttribute("height")) || 240;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  return { context, width, height };
}

function drawEmptyChart(canvas, message) {
  const { context, width, height } = setupCanvas(canvas);
  context.fillStyle = "#92968f";
  context.font = "11px ui-monospace, monospace";
  context.textAlign = "center";
  context.fillText(message, width / 2, height / 2);
}

function drawLineChart(canvas, values, labels, color = "#718f17", suffix = "") {
  if (!values.length || !values.some(finite)) return drawEmptyChart(canvas, "표시할 측정값 없음");
  const { context: ctx, width, height } = setupCanvas(canvas);
  const pad = { left: 42, right: 16, top: 18, bottom: 34 };
  const numeric = values.filter(finite).map(Number);
  const max = Math.max(...numeric, 1) * 1.12;
  ctx.strokeStyle = "#d9d6ca";
  ctx.fillStyle = "#858980";
  ctx.font = "9px ui-monospace, monospace";
  ctx.lineWidth = 1;
  for (let index = 0; index <= 4; index += 1) {
    const y = pad.top + (height - pad.top - pad.bottom) * index / 4;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(width - pad.right, y); ctx.stroke();
    ctx.fillText(`${fmt(max * (4 - index) / 4, 1)}${suffix}`, 2, y + 3);
  }
  const step = values.length > 1 ? (width - pad.left - pad.right) / (values.length - 1) : 0;
  ctx.strokeStyle = color; ctx.lineWidth = 2.5; ctx.beginPath();
  values.forEach((value, index) => {
    if (!finite(value)) return;
    const x = values.length > 1 ? pad.left + step * index : width / 2;
    const y = height - pad.bottom - Number(value) / max * (height - pad.top - pad.bottom);
    if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
  values.forEach((value, index) => {
    if (!finite(value)) return;
    const x = values.length > 1 ? pad.left + step * index : width / 2;
    const y = height - pad.bottom - Number(value) / max * (height - pad.top - pad.bottom);
    ctx.fillStyle = color; ctx.beginPath(); ctx.arc(x, y, 3.5, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#858980"; ctx.textAlign = "center";
    ctx.fillText(String(labels[index] || "").slice(0, 9), x, height - 12);
  });
  ctx.textAlign = "left";
}

function drawBarChart(canvas, groups, series) {
  if (!groups.length || !series.some(item => item.values.some(finite))) return drawEmptyChart(canvas, "표시할 측정값 없음");
  const { context: ctx, width, height } = setupCanvas(canvas);
  const pad = { left: 38, right: 12, top: 25, bottom: 42 };
  const all = series.flatMap(item => item.values).filter(finite).map(Number);
  const max = Math.max(...all, 1) * 1.15;
  const plotWidth = width - pad.left - pad.right;
  const groupWidth = plotWidth / groups.length;
  const barWidth = Math.min(26, groupWidth * 0.72 / series.length);
  ctx.strokeStyle = "#d9d6ca"; ctx.fillStyle = "#858980"; ctx.font = "9px ui-monospace, monospace";
  for (let index = 0; index <= 4; index += 1) {
    const y = pad.top + (height - pad.top - pad.bottom) * index / 4;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(width - pad.right, y); ctx.stroke();
    ctx.fillText(fmt(max * (4 - index) / 4, 1), 2, y + 3);
  }
  series.forEach((item, seriesIndex) => {
    item.values.forEach((value, groupIndex) => {
      if (!finite(value)) return;
      const x = pad.left + groupWidth * groupIndex + groupWidth / 2 + (seriesIndex - (series.length - 1) / 2) * barWidth;
      const barHeight = Number(value) / max * (height - pad.top - pad.bottom);
      ctx.fillStyle = item.color;
      ctx.fillRect(x - barWidth * 0.42, height - pad.bottom - barHeight, barWidth * 0.84, barHeight);
    });
  });
  ctx.textAlign = "center"; ctx.fillStyle = "#858980";
  groups.forEach((label, index) => ctx.fillText(String(label).slice(0, 10), pad.left + groupWidth * index + groupWidth / 2, height - 18));
  ctx.textAlign = "left";
  series.forEach((item, index) => {
    const x = pad.left + index * 86;
    ctx.fillStyle = item.color; ctx.fillRect(x, 8, 9, 9);
    ctx.fillStyle = "#5f645d"; ctx.fillText(item.label, x + 13, 16);
  });
}

function drawResultCharts(runs) {
  const completed = runs.filter(run => run.status === "completed").slice(0, 10).reverse();
  drawLineChart(
    $("#throughputChart"),
    completed.map(run => run.cluster_tokens_per_s),
    completed.map(run => String(run.run_id || "").slice(9, 15)),
    "#718f17",
  );
  const latencyRuns = completed.slice(-6);
  drawBarChart(
    $("#latencyChart"),
    latencyRuns.map(run => String(run.run_id || "").slice(9, 15)),
    [
      { label: "TTFT p50", color: "#718f17", values: latencyRuns.map(run => run.ttft_p50_s) },
      { label: "E2E p95", color: "#e57c38", values: latencyRuns.map(run => run.e2e_p95_s) },
    ],
  );
  const latest = [...completed].reverse().find(run => run.per_node && Object.keys(run.per_node).length);
  if (!latest) return drawEmptyChart($("#nodeChart"), "노드별 지표가 있는 새 실행이 필요합니다");
  if (runStrategy(latest) === "model_parallel_rpc") {
    $("#nodeChartTitle").textContent = "분할 모델 공동 처리량";
    return drawEmptyChart($("#nodeChart"), "RPC 분할은 워커별 tok/s를 계산하지 않습니다");
  }
  if (runStrategy(latest) === "node_sweep" && Array.isArray(latest.scenario_summaries)) {
    $("#nodeChartTitle").textContent = "스윕 단계별 처리량";
    return drawBarChart(
      $("#nodeChart"),
      latest.scenario_summaries.map(item => item.label || item.scenario_id),
      [{ label: "cluster tok/s", color: "#163126", values: latest.scenario_summaries.map(item => item.cluster_tokens_per_s) }],
    );
  }
  $("#nodeChartTitle").textContent = "노드별 기여도";
  const nodeNames = Object.keys(latest.per_node);
  drawBarChart(
    $("#nodeChart"),
    nodeNames,
    [{ label: "effective tok/s", color: "#163126", values: nodeNames.map(name => latest.per_node[name].effective_tokens_per_s) }],
  );
}

function metricBar(label, value, detail = "") {
  const safe = finite(value) ? Math.min(100, Math.max(0, Number(value))) : 0;
  return `<div class="telemetry-bar"><div><span>${escapeHtml(label)}</span><strong>${finite(value) ? pct(value) : "N/A"}</strong></div><i><b style="width:${safe}%"></b></i>${detail ? `<small>${escapeHtml(detail)}</small>` : ""}</div>`;
}

function renderNodeDetail() {
  if (!state.detailNode || !$("#nodeDetailDialog").open) return;
  const node = state.nodes.find(item => item.name === state.detailNode);
  if (!node) return;
  const live = statusFor(node.name);
  const metrics = live.metrics || {};
  const profile = live.profile || {};
  const kind = profile.platform_kind || node.platform;
  $("#detailNodeName").textContent = node.name;
  const cores = metrics.cpu?.cores_pct || [];
  const temperatures = metrics.temperatures_c || {};
  const rails = metrics.power?.rails_w || {};
  const engines = metrics.accelerator?.engines || {};
  const fans = metrics.fans || {};
  $("#nodeDetailContent").innerHTML = `
    <div class="detail-identity">
      <div><span>PLATFORM</span><strong>${escapeHtml(platformName(kind))}</strong><small>${escapeHtml(profile.board_model || "미확인")}</small></div>
      <div><span>OS / KERNEL</span><strong>${escapeHtml(profile.os || "—")}</strong><small>${escapeHtml(profile.l4t || profile.kernel || "")}</small></div>
      <div><span>BACKEND</span><strong>${escapeHtml(profile.runtime_backend?.kind || "—")}</strong><small>${profile.runtime_backend?.verified ? `검증됨 · ${escapeHtml(profile.cuda || "native")}` : "검증 안 됨"}</small></div>
      <div><span>UPTIME</span><strong>${formatUptime(metrics.uptime_s)}</strong><small>${escapeHtml(metrics.power?.mode || "")}</small></div>
    </div>
    <div class="detail-grid">
      <section class="telemetry-panel"><div class="telemetry-title"><span>CPU</span><strong>${pct(metrics.cpu_pct)}</strong></div>
        <div class="core-grid">${cores.length ? cores.map((value, index) => `<div><span>C${index}</span><i><b style="height:${Math.min(100, Number(value) || 0)}%"></b></i><small>${fmt(value, 0)}%</small></div>`).join("") : "<small>코어 데이터 없음</small>"}</div>
        <p>${fmt(metrics.cpu?.frequency_mhz, 0)} MHz · load ${fmt(metrics.cpu?.load_1m, 2)} / ${fmt(metrics.cpu?.load_5m, 2)} / ${fmt(metrics.cpu?.load_15m, 2)}</p>
      </section>
      <section class="telemetry-panel"><div class="telemetry-title"><span>MEMORY / STORAGE</span><strong>${pct(metrics.ram_pct)}</strong></div>
        ${metricBar("RAM", metrics.memory?.percent, `${fmt(metrics.memory?.used_mb, 0)} / ${fmt(metrics.memory?.total_mb, 0)} MB`)}
        ${metricBar("SWAP", metrics.swap?.percent, `${fmt(metrics.swap?.used_mb, 0)} / ${fmt(metrics.swap?.total_mb, 0)} MB`)}
        ${metricBar("DISK", metrics.disk?.percent, `${fmt(metrics.disk?.free_gb)} GB free`)}
      </section>
      <section class="telemetry-panel"><div class="telemetry-title"><span>ACCELERATOR / ENGINES</span><strong>${finite(metrics.gpu_pct) ? pct(metrics.gpu_pct) : "CPU ONLY"}</strong></div>
        <div class="tag-metrics">${Object.keys(engines).length ? Object.entries(engines).map(([name, value]) => `<span>${escapeHtml(name)} <strong>${finite(value) ? pct(value) : "—"}</strong></span>`).join("") : "<span>전용 가속기 지표 없음</span>"}</div>
      </section>
      <section class="telemetry-panel"><div class="telemetry-title"><span>THERMAL / POWER</span><strong>${finite(metrics.power_w) ? `${fmt(metrics.power_w, 2)} W` : "N/A"}</strong></div>
        <div class="tag-metrics">${Object.entries(temperatures).map(([name, value]) => `<span>${escapeHtml(name)} <strong>${fmt(value, 1)}°C</strong></span>`).join("") || "<span>온도 센서 없음</span>"}</div>
        <div class="rail-list">${Object.entries(rails).map(([name, value]) => `<div><span>${escapeHtml(name)}</span><strong>${fmt(value, 2)} W</strong></div>`).join("") || "<small>전력 레일 데이터 없음</small>"}</div>
        <p>FAN ${Object.entries(fans).map(([name, value]) => `${name} ${fmt(value, 0)}%`).join(" · ") || "N/A"} · RX ${fmt((metrics.network?.receive_bytes_s || 0) / 1024, 1)} KB/s · TX ${fmt((metrics.network?.send_bytes_s || 0) / 1024, 1)} KB/s</p>
      </section>
    </div>
    <section class="telemetry-history"><div><span>LIVE HISTORY</span><strong>최근 ${state.metricHistory.get(node.name)?.length || 0}개 표본 · CPU / GPU / RAM</strong></div><canvas id="telemetryChart" height="230" aria-label="노드 CPU GPU RAM 실시간 사용률 그래프"></canvas></section>
    ${metrics.sampler_error ? `<div class="telemetry-warning">${escapeHtml(metrics.sampler_error)}</div>` : ""}`;
  requestAnimationFrame(drawTelemetryChart);
}

function drawTelemetryChart() {
  const canvas = $("#telemetryChart");
  if (!canvas || !state.detailNode) return;
  const history = (state.metricHistory.get(state.detailNode) || []).slice(-60);
  if (!history.length) return drawEmptyChart(canvas, "표본 수집 중");
  const { context: ctx, width, height } = setupCanvas(canvas);
  const pad = { left: 34, right: 12, top: 30, bottom: 24 };
  ctx.strokeStyle = "#d9d6ca"; ctx.fillStyle = "#858980"; ctx.font = "9px ui-monospace, monospace";
  [0, 25, 50, 75, 100].forEach(value => {
    const y = height - pad.bottom - value / 100 * (height - pad.top - pad.bottom);
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(width - pad.right, y); ctx.stroke();
    ctx.fillText(`${value}`, 4, y + 3);
  });
  const series = [
    { key: "cpu", label: "CPU", color: "#718f17" },
    { key: "gpu", label: "GPU", color: "#e57c38" },
    { key: "ram", label: "RAM", color: "#163126" },
  ];
  const step = history.length > 1 ? (width - pad.left - pad.right) / (history.length - 1) : 0;
  series.forEach((item, seriesIndex) => {
    ctx.strokeStyle = item.color; ctx.lineWidth = 2; ctx.beginPath(); let started = false;
    history.forEach((sample, index) => {
      if (!finite(sample[item.key])) return;
      const x = pad.left + step * index;
      const y = height - pad.bottom - Number(sample[item.key]) / 100 * (height - pad.top - pad.bottom);
      if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.fillStyle = item.color; ctx.fillRect(pad.left + seriesIndex * 64, 8, 9, 9);
    ctx.fillStyle = "#5f645d"; ctx.fillText(item.label, pad.left + 13 + seriesIndex * 64, 16);
  });
}

function openNodeDetail(nodeName) {
  state.detailNode = nodeName;
  $("#nodeDetailDialog").showModal();
  renderNodeDetail();
}

function setRunState(active) {
  state.activeExperiment = active;
  const running = active && ["queued", "running"].includes(active.status);
  $("#runButton").classList.toggle("hidden", running);
  $("#cancelButton").classList.toggle("hidden", !running);
  $("#runStateDot").classList.toggle("running", running);
  if (!active) return;
  if (active.execution_strategy || active.strategy) $("#runStrategyBadge").textContent = strategyMeta(active.execution_strategy || active.strategy).label;
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
    state.experimentGroups = data.experiment_groups || [];
    state.actions = data.actions || [];
    state.onboarding = data.onboarding || {};
    state.settings = data.settings || { worker_api_auth: false };
    ingestStatus(state.status);
    applyDefaults(data.defaults || {});
    renderExperimentGroups();
    renderNodes();
    renderRuns();
    setRunState(data.active_experiment);
    $("#publicKey").textContent = state.onboarding.public_key || "키가 아직 생성되지 않았습니다.";
    renderSettings();
    connectEvents();
    if (!location.hash) requestAnimationFrame(() => window.scrollTo({ top: 0, left: 0, behavior: "instant" }));
  } catch (error) {
    if (/401|token|invalid|missing/i.test(error.message)) $("#authDialog").showModal();
    else toast("대시보드 초기화 실패", error.message, "error");
  }
}

async function refreshExperimentData() {
  const data = await api("/api/experiments");
  state.runs = data.runs || [];
  state.experimentGroups = data.experiment_groups || [];
  renderExperimentGroups();
  renderRuns();
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
      ingestStatus(state.status);
      $("#lastUpdated").textContent = `UPDATED ${new Date(message.at).toLocaleTimeString("ko-KR")}`;
      renderNodes();
      renderNodeDetail();
    } else if (message.type === "inventory_changed") {
      state.nodes = message.nodes || state.nodes;
      renderNodes();
    } else if (message.type === "settings_changed") {
      state.settings = message.settings || state.settings;
      renderSettings();
      toast("보안 설정 적용 중", "연결된 worker API를 재시작합니다.");
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
        refreshExperimentData().catch(error => toast("결과 갱신 실패", error.message, "error"));
        toast("벤치마크 완료", `${fmt(inner.summary.cluster_tokens_per_s)} tok/s · ${inner.summary.nodes.length} nodes`);
      }
    } else if (message.type === "experiment_failed") {
      setRunState(message.active);
      refreshExperimentData().catch(() => {});
      toast("벤치마크 실패", message.message, "error");
    }
  };
}

async function runActionOnNodes(action, nodeNames, options = {}) {
  if (!nodeNames.length) return toast("노드 선택 필요", "하나 이상의 노드를 선택하세요.", "error");
  try {
    const result = await api("/api/actions", { method: "POST", body: { action, node_names: nodeNames, options } });
    logLine("TASK", `${action} 시작 · ${result.action.nodes.join(", ")}`);
    toast("작업 시작", action);
    return result;
  } catch (error) {
    toast("작업 시작 실패", error.message, "error");
    return null;
  }
}

async function runAction(action, options = {}) {
  return runActionOnNodes(action, [...state.selectedNodes], options);
}

function experimentPayload() {
  const executionStrategy = selectedStrategy();
  const selectedNodeNames = [...state.selectedNodes];
  if (executionStrategy === "single_node" && selectedNodeNames.length !== 1) {
    throw new Error("단일 노드 기준선은 정확히 한 대만 선택해야 합니다.");
  }
  if (["broadcast_compare", "node_sweep"].includes(executionStrategy) && selectedNodeNames.length < 2) {
    throw new Error("선택한 실행 방식에는 최소 두 대의 노드가 필요합니다.");
  }
  if (executionStrategy === "model_parallel_rpc") {
    const selectedNodes = selectedNodeNames.map(name => state.nodes.find(node => node.name === name)).filter(Boolean);
    if (selectedNodes.filter(node => node.role === "head").length !== 1 || !selectedNodes.some(node => node.role === "worker")) {
      throw new Error("모델 분할 RPC에는 coordinator인 head 1대와 worker 1대 이상을 선택해야 합니다.");
    }
    if (!$("#rpcAcknowledgeInput").checked) throw new Error("모델 분할 RPC의 실험적 특성과 위험을 먼저 확인하세요.");
  }
  const tensorSplit = executionStrategy === "model_parallel_rpc" && $("#rpcSplitPolicySelect").value === "custom" ? parseRpcTensorSplit(true) : [];
  return {
    experiment_id: $("#experimentGroupSelect").value,
    name: $("#experimentName").value.trim(),
    node_names: plannedNodeNames(),
    model_id: $("#modelSelect").value,
    execution_strategy: executionStrategy,
    sweep_mode: $("#sweepModeSelect").value,
    rpc_split_mode: $("#rpcSplitModeSelect").value,
    rpc_split_policy: $("#rpcSplitPolicySelect").value,
    rpc_tensor_split: tensorSplit,
    acknowledge_experimental_rpc: $("#rpcAcknowledgeInput").checked,
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

function candidatePayload() {
  return {
    name: $("#nodeName").value.trim(), role: "worker", host: $("#nodeHost").value.trim(),
    user: $("#nodeUser").value.trim(), ssh_port: Number($("#nodeSshPort").value), api_port: Number($("#nodeApiPort").value),
    project_dir: $("#nodeProjectDir").value.trim(), enabled: true, identity_file: "", platform: $("#nodePlatform").value,
  };
}

function renderDevices(scan) {
  state.devices = scan.devices || [];
  const networks = (scan.networks || []).map(item => `${item.interface} · ${item.network}`).join(", ");
  $("#scanStatus").textContent = `${networks || "사설 LAN 없음"} · SSH 기기 ${state.devices.length}대`;
  const list = $("#deviceList");
  if (!state.devices.length) {
    list.innerHTML = `<div class="device-empty">SSH 포트가 열린 기기를 찾지 못했습니다. 워커의 SSH 서비스를 확인하세요.</div>`;
    return;
  }
  list.innerHTML = state.devices.map(device => `
    <button type="button" class="device-card ${device.is_head ? "head-device" : ""}" data-device-host="${escapeHtml(device.host)}" ${device.is_head ? "disabled" : ""}>
      <i></i><span><strong>${escapeHtml(device.known_node || device.host)}</strong><small>${escapeHtml(device.host)} · SSH ${device.ssh_port}${device.is_head ? " · HEAD" : device.known_node ? " · 등록됨" : " · 새 기기"}</small><code>${escapeHtml(device.fingerprint || "fingerprint 확인 불가")}</code></span><b>선택</b>
    </button>`).join("");
  $$('[data-device-host]').forEach(button => button.addEventListener("click", () => {
    const device = state.devices.find(item => item.host === button.dataset.deviceHost);
    $("#nodeHost").value = device.host;
    if (device.known_node) {
      const known = state.nodes.find(node => node.name === device.known_node);
      $("#nodeName").value = device.known_node;
      if (known) {
        $("#nodeUser").value = known.user;
        $("#nodeSshPort").value = known.ssh_port;
        $("#nodeApiPort").value = known.api_port;
        $("#nodeProjectDir").value = known.project_dir;
        $("#nodePlatform").value = known.platform || "auto";
      }
    }
    else if (!$("#nodeName").value) {
      const used = new Set(state.nodes.map(node => node.name));
      const available = [1, 2, 3].find(index => !used.has(`edge-worker-0${index}`)) || state.nodes.length;
      $("#nodeName").value = `edge-worker-0${available}`;
    }
    $$('.device-card').forEach(item => item.classList.toggle("selected", item === button));
    state.onboardingProbe = null;
    $("#probeResult").hidden = true;
  }));
}

async function scanNetwork(force = false) {
  $("#scanStatus").textContent = "사설 LAN에서 SSH 기기를 검색하는 중…";
  $("#deviceList").innerHTML = `<div class="device-empty scanning">최대 /24 범위 · SSH 포트만 확인합니다.</div>`;
  try {
    const result = await api(`/api/network/scan?force=${force ? "true" : "false"}`, { method: "POST" });
    renderDevices(result);
  } catch (error) {
    $("#scanStatus").textContent = "검색 실패";
    $("#deviceList").innerHTML = `<div class="device-empty">${escapeHtml(error.message)}</div>`;
  }
}

function renderProbe(result) {
  state.onboardingProbe = result;
  const panel = $("#probeResult");
  panel.hidden = false;
  const discovery = result.discovery || {};
  if (!result.ok) {
    const paired = result.ssh_ok;
    panel.className = "probe-result failed";
    panel.innerHTML = `<strong>${paired ? "지원하지 않는 환경" : "SSH 공개 키 인증 필요"}</strong><span>${paired ? "Jetson 또는 Raspberry Pi의 64-bit ARM OS가 필요합니다." : "Head 공개 키를 선택한 기기의 authorized_keys에 등록한 뒤 다시 확인하세요."}</span><small>${escapeHtml((result.warnings || []).join(" · ") || discovery.error || "연결할 수 없음")}</small>`;
    return;
  }
  const missing = discovery.missing_packages || [];
  const manual = missing.length && !discovery.sudo_nopasswd;
  panel.className = `probe-result ${manual ? "warning" : "ready"}`;
  panel.innerHTML = `
    <strong>${manual ? "수동 sudo 1회 필요" : "자동 준비 가능"}</strong>
    <span>${escapeHtml(platformName(discovery.platform_kind))} · ${escapeHtml(discovery.board_model || "")} · ${escapeHtml(discovery.architecture || "")} · ${escapeHtml(discovery.os || "")}</span>
    <small>프로젝트 ${discovery.project ? "있음" : "신규 설치"} · 디스크 ${fmt(discovery.disk_free_gb)} GB · NTP ${escapeHtml(discovery.ntp_synchronized || "미확인")}</small>
    ${missing.length ? `<code>sudo apt-get update &amp;&amp; sudo apt-get install -y ${escapeHtml(missing.join(" "))}</code>` : ""}
    ${(result.warnings || []).map(warning => `<em>${escapeHtml(warning)}</em>`).join("")}`;
  if (["jetson", "raspberry-pi"].includes(discovery.platform_kind)) $("#nodePlatform").value = discovery.platform_kind;
}

async function probeCandidate() {
  const payload = candidatePayload();
  if (!payload.host || !payload.name || !payload.user) throw new Error("기기와 SSH 계정 정보를 먼저 입력하세요.");
  $("#probeResult").hidden = false;
  $("#probeResult").className = "probe-result";
  $("#probeResult").innerHTML = `<strong>SSH 및 환경 확인 중…</strong>`;
  const result = await api("/api/nodes/probe", { method: "POST", body: payload });
  renderProbe(result);
  return result;
}

function resetNodeForm() {
  $("#nodeForm").reset();
  $("#nodeUser").value = "jetson_orin_nano";
  $("#nodeProjectDir").value = "/home/jetson_orin_nano/project/llm/local_llm_bench";
  $("#nodePlatform").value = "auto";
  $("#probeResult").hidden = true;
  state.onboardingProbe = null;
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
  $("#addNodeButton").addEventListener("click", () => {
    $("#nodeDialog").showModal();
    scanNetwork();
  });
  $("#settingsButton").addEventListener("click", () => {
    renderSettings();
    $("#settingsDialog").showModal();
  });
  $("#workerAuthInput").addEventListener("change", event => {
    $("#workerAuthNotice").textContent = event.currentTarget.checked
      ? "저장 후 켜짐 · 연결 노드 재시작 필요"
      : "저장 후 꺼짐 · 신뢰 LAN 전용 모드";
  });
  $("#settingsForm").addEventListener("submit", async event => {
    event.preventDefault();
    try {
      const data = await api("/api/settings", {
        method: "PUT",
        body: { worker_api_auth: $("#workerAuthInput").checked },
      });
      state.settings = data.settings;
      renderSettings();
      $("#settingsDialog").close();
      toast("설정 저장 완료", data.action ? "노드 API 재시작 작업을 시작했습니다." : "변경 사항이 없습니다.");
    } catch (error) {
      renderSettings();
      toast("설정 적용 실패", error.message, "error");
    }
  });
  $("#scanNetworkButton").addEventListener("click", () => scanNetwork(true));
  $("#probeNodeButton").addEventListener("click", async () => {
    try { await probeCandidate(); }
    catch (error) { toast("환경 확인 실패", error.message, "error"); }
  });
  $("#nodeForm").addEventListener("submit", async event => {
    event.preventDefault();
    try {
      const probe = await probeCandidate();
      if (!probe.ok) return toast("SSH 키 등록 필요", "공개 키를 워커에 등록한 뒤 다시 확인하세요.", "error");
      const discovery = probe.discovery || {};
      if ((discovery.missing_packages || []).length && !discovery.sudo_nopasswd) {
        return toast("수동 패키지 설치 필요", "표시된 sudo 명령을 워커에서 한 번 실행한 뒤 다시 확인하세요.", "error");
      }
      const payload = candidatePayload();
      const result = await api("/api/nodes", { method: "POST", body: payload });
      const index = state.nodes.findIndex(node => node.name === result.node.name);
      if (index >= 0) state.nodes[index] = result.node; else state.nodes.push(result.node);
      state.selectedNodes.add(result.node.name);
      renderNodes();
      $("#nodeDialog").close();
      resetNodeForm();
      await runActionOnNodes("prepare", [result.node.name], { confirmed: true, models: [$("#modelSelect").value] });
      toast("워커 등록 완료", "환경 구성, 모델 동기화와 API 시작 작업을 진행합니다.");
    } catch (error) { toast("워커 등록 실패", error.message, "error"); }
  });
  $("#copyKeyButton").addEventListener("click", async () => {
    if (!state.onboarding.public_key) return toast("SSH 키 없음", "head에서 키 생성 스크립트를 실행하세요.", "error");
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(state.onboarding.public_key);
      else {
        const helper = document.createElement("textarea");
        helper.value = state.onboarding.public_key;
        helper.style.position = "fixed"; helper.style.opacity = "0";
        document.body.append(helper); helper.select();
        if (!document.execCommand("copy")) throw new Error("copy unsupported");
        helper.remove();
      }
      toast("SSH 공개 키 복사됨");
    } catch (_error) {
      const range = document.createRange();
      range.selectNodeContents($("#publicKey"));
      const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
      toast("키를 선택했습니다", "복사가 차단되어 있습니다. 선택된 키를 직접 복사하세요.", "error");
    }
  });
  $("#refreshButton").addEventListener("click", () => api("/api/status/refresh", { method: "POST" }).catch(error => toast("새로고침 실패", error.message, "error")));
  $("#quickHealthButton").addEventListener("click", () => runAction("doctor"));
  $("#prepareButton").addEventListener("click", () => {
    const workers = [...state.selectedNodes].filter(name => state.nodes.find(node => node.name === name)?.role === "worker");
    if (!workers.length) return toast("워커 선택 필요", "준비할 worker 노드를 선택하세요.", "error");
    if (confirm("선택한 워커의 플랫폼을 감지하고 의존성, 코드, 선택 모델과 API를 준비합니다. 계속할까요?")) {
      runActionOnNodes("prepare", workers, { confirmed: true, models: [$("#modelSelect").value] });
    }
  });
  $("#prepareRpcButton").addEventListener("click", () => {
    const nodes = [...state.selectedNodes];
    const records = nodes.map(name => state.nodes.find(node => node.name === name)).filter(Boolean);
    if (records.filter(node => node.role === "head").length !== 1 || !records.some(node => node.role === "worker")) {
      return toast("노드 구성 확인", "RPC coordinator인 head 1대와 worker 1대 이상을 선택하세요.", "error");
    }
    if (confirm(`선택한 ${nodes.length}대에 모델 분할 RPC 실행 환경을 준비합니다. 실제 성능은 네트워크 상태에 따라 저하될 수 있습니다. 계속할까요?`)) {
      runActionOnNodes("prepare-rpc", nodes, { confirmed: true });
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
  $("#experimentGroupSelect").addEventListener("change", event => {
    const group = state.experimentGroups.find(item => item.experiment_id === event.currentTarget.value);
    if (!group) return;
    $("#experimentName").value = group.name;
    applyConfig(group.default_config || {}, false);
    $("#resultExperimentFilter").value = group.experiment_id;
    renderRuns();
  });
  $("#resultExperimentFilter").addEventListener("change", renderRuns);
  $("#experimentForm").addEventListener("input", updateFormMirrors);
  $("#modelSelect").addEventListener("change", updateModelAvailability);
  $("#experimentForm").addEventListener("submit", async event => {
    event.preventDefault();
    const participatingNodes = plannedNodeNames();
    const missingOffline = participatingNodes.filter(name => !statusFor(name).api);
    if (missingOffline.length) return toast("노드 API 오프라인", `${missingOffline.join(", ")} 서버를 먼저 시작하세요.`, "error");
    try {
      const data = await api("/api/experiments", { method: "POST", body: experimentPayload() });
      $("#consoleLog").innerHTML = "";
      logLine("START", `${data.experiment.name} · ${data.experiment.nodes.join(", ")}`);
      setRunState(data.experiment);
      await refreshExperimentData();
      $("#experimentGroupSelect").value = data.definition.experiment_id;
      $("#resultExperimentFilter").value = data.definition.experiment_id;
      renderRuns();
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
  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { renderRuns(); renderNodeDetail(); }, 140);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";
  state.token = getToken();
  bindEvents();
  if (!state.token) $("#authDialog").showModal(); else bootstrap();
});
