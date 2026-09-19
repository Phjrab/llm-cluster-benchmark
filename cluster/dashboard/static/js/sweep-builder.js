"use strict";

(() => {
  const dashboard = window.ClusterDashboard || (window.ClusterDashboard = {});
  const { state } = dashboard;
  const builder = state.sweepBuilder;
  const byId = id => document.getElementById(id);

  function uniqueNumbers(id, { integer = true, min, max } = {}) {
    const raw = String(byId(id)?.value || "").split(",").map(value => value.trim()).filter(Boolean);
    if (!raw.length) throw new Error(`${byId(id)?.labels?.[0]?.textContent || id} 목록이 비어 있습니다.`);
    const values = raw.map(value => Number(value));
    if (values.some(value => !Number.isFinite(value) || (integer && !Number.isInteger(value)) || value < min || value > max)) {
      throw new Error(`${byId(id)?.labels?.[0]?.textContent || id} 범위를 확인하세요.`);
    }
    return [...new Set(values)];
  }

  function integerValue(id, min, max, optional = false) {
    const raw = String(byId(id)?.value || "").trim();
    if (optional && !raw) return null;
    const value = Number(raw);
    if (!Number.isInteger(value) || value < min || value > max) throw new Error(`${byId(id)?.labels?.[0]?.textContent || id} 범위를 확인하세요.`);
    return value;
  }

  function numberValue(id, min, max) {
    const value = Number(byId(id)?.value);
    if (!Number.isFinite(value) || value < min || value > max) throw new Error(`${byId(id)?.labels?.[0]?.textContent || id} 범위를 확인하세요.`);
    return value;
  }

  function statusForWorker(name) {
    return state.status.find(item => item.name === name) || {};
  }

  function nodeLabel(node) {
    const status = statusForWorker(node.name);
    const platform = status.profile?.platform_kind || node.platform || "unknown";
    const online = status.api === true;
    return { platform, online, label: `${node.name} · ${dashboard.platformName?.(platform) || platform}` };
  }

  function installedOn(model, worker) {
    return (model.installed_nodes || []).includes(worker);
  }

  function recommendationFor(modelId, worker) {
    const rows = state.modelRecommendations?.[worker] || [];
    return rows.find(item => item.id === modelId) || {};
  }

  function modelEligibility(model) {
    const workers = [...builder.selectedWorkers];
    const strategy = byId("sweepStrategySelect")?.value || "replicated_round_robin";
    const installed = workers.filter(worker => installedOn(model, worker));
    const enough = strategy === "model_parallel_rpc" ? installed.length > 0 : workers.length > 0 && installed.length === workers.length;
    let reason = "";
    if (!workers.length) reason = "Worker를 먼저 선택하세요.";
    else if (!enough) reason = strategy === "model_parallel_rpc" ? "선택 Worker의 coordinator 후보에 설치되지 않음" : "선택 Worker 전체에 설치되지 않음";
    return { enabled: enough, installed, reason };
  }

  function modelStatus(model) {
    const catalog = model.catalog || {};
    const workers = [...builder.selectedWorkers];
    const recommendations = workers.map(worker => recommendationFor(model.id, worker)).filter(item => item.status);
    const runtime = recommendations.length && recommendations.every(item => ["recommended", "compatible"].includes(item.status));
    const downloadable = Boolean(catalog.download_eligibility?.eligible || catalog.download_eligible || catalog.source_url || catalog.artifact_url || catalog.official_gguf);
    const formal = Boolean(catalog.formal_approved || catalog.formal_identity_approved);
    return { runtime, downloadable, formal };
  }

  function renderWorkers() {
    const root = byId("sweepWorkerPicker");
    if (!root) return;
    const workers = state.nodes.filter(node => node.role === "worker" && node.enabled);
    root.innerHTML = workers.length ? workers.map(node => {
      const meta = nodeLabel(node);
      const checked = builder.selectedWorkers.has(node.name);
      return `<label class="sweep-choice ${checked ? "selected" : ""}"><input type="checkbox" data-sweep-worker="${dashboard.escapeHtml(node.name)}" ${checked ? "checked" : ""}><span><strong>${dashboard.escapeHtml(meta.label)}</strong><small>${meta.online ? "● ONLINE" : "○ OFFLINE"} · stable ID</small></span></label>`;
    }).join("") : `<div class="empty-result"><strong>등록된 Worker 없음</strong></div>`;
  }

  function renderModels() {
    const root = byId("sweepModelPicker");
    if (!root) return;
    const rows = state.models || [];
    root.innerHTML = rows.length ? rows.map(model => {
      const eligibility = modelEligibility(model);
      const catalog = model.catalog || {};
      const status = modelStatus(model);
      const selected = builder.selectedModels.has(model.id) && eligibility.enabled;
      const installedText = eligibility.installed.length ? `${eligibility.installed.length}/${builder.selectedWorkers.size || 0} Worker 설치` : "미설치";
      const badges = [
        `<span class="${eligibility.installed.length ? "valid" : "blocked"}">${dashboard.escapeHtml(installedText)}</span>`,
        `<span class="${status.downloadable ? "valid" : "unknown"}">${status.downloadable ? "다운로드 가능" : "source 불명"}</span>`,
        `<span class="${status.runtime ? "valid" : "unknown"}">${status.runtime ? "runtime 검증" : "실행 검증 없음"}</span>`,
        `<span class="${status.formal ? "valid" : "neutral"}">${status.formal ? "formal 승인" : "formal 별도"}</span>`,
      ].join("");
      return `<label class="sweep-model-option ${selected ? "selected" : ""} ${eligibility.enabled ? "" : "disabled"}">
        <input type="checkbox" data-sweep-model="${dashboard.escapeHtml(model.id)}" ${selected ? "checked" : ""} ${eligibility.enabled ? "" : "disabled"} aria-describedby="sweep-model-reason-${dashboard.escapeHtml(model.id.replace(/[^A-Za-z0-9_-]/g, "-"))}">
        <span class="sweep-model-main"><strong>${dashboard.escapeHtml(catalog.display_name || model.filename || model.id)}</strong><small>${dashboard.escapeHtml(catalog.vendor || "unknown vendor")} · ${dashboard.escapeHtml(catalog.family || "unknown family")} · ${dashboard.escapeHtml(model.quantization || catalog.quantization || "unknown quant")} · ${dashboard.escapeHtml(String(model.size_gb || "?"))} GB</small><span class="sweep-model-badges">${badges}</span></span>
        ${eligibility.enabled ? "" : `<span class="sweep-disabled-reason" id="sweep-model-reason-${dashboard.escapeHtml(model.id.replace(/[^A-Za-z0-9_-]/g, "-"))}">${dashboard.escapeHtml(eligibility.reason)} · <a href="#models">모델 화면에서 준비</a></span>`}
      </label>`;
    }).join("") : `<div class="empty-result"><strong>서버 model catalog가 비어 있습니다.</strong><span><a href="#models">모델 화면</a>에서 설치 상태를 확인하세요.</span></div>`;
    for (const modelId of [...builder.selectedModels]) {
      const model = rows.find(item => item.id === modelId);
      if (!model || !modelEligibility(model).enabled) builder.selectedModels.delete(modelId);
    }
  }

  function renderPromptVariants() {
    const root = byId("sweepPromptVariants");
    if (!root) return;
    root.innerHTML = builder.promptVariants.map((variant, index) => `<article class="sweep-prompt-variant" data-prompt-index="${index}">
      <header><label><span>Prompt ref</span><input data-prompt-field="ref" value="${dashboard.escapeHtml(variant.ref)}" maxlength="80"></label><button type="button" data-prompt-remove ${builder.promptVariants.length === 1 ? "disabled" : ""} aria-label="${dashboard.escapeHtml(variant.ref)} 삭제">×</button></header>
      <textarea data-prompt-field="text" rows="3" maxlength="20000">${dashboard.escapeHtml(variant.text)}</textarea>
      <div class="sweep-inline-fields"><label><span>방식</span><select data-prompt-field="mode"><option value="same_text" ${variant.mode === "same_text" ? "selected" : ""}>모델별 같은 원문</option><option value="token_length_profile" ${variant.mode === "token_length_profile" ? "selected" : ""}>목표 actual input length</option></select></label><label ${variant.mode === "token_length_profile" ? "" : "hidden"}><span>목표 input tokens</span><input data-prompt-field="target_input_tokens" type="number" min="1" max="16384" value="${dashboard.escapeHtml(variant.target_input_tokens || 128)}"></label></div>
    </article>`).join("");
  }

  function profileCapability(profile) {
    const field = profile.split_mode === "row" ? "rpc_row_v1" : "rpc_layer_v1";
    const states = profile.worker_ids.map(worker => statusForWorker(worker).capabilities?.[field]);
    if (states.some(value => value === false)) return { tone: "blocked", text: `${profile.split_mode} unsupported` };
    if (states.every(value => value === true)) return { tone: "valid", text: `${profile.split_mode} supported` };
    return { tone: "unknown", text: `${profile.split_mode} capability unknown` };
  }

  function profileMemory(profile) {
    const modelIds = [...builder.selectedModels];
    const figures = [];
    for (const worker of profile.worker_ids) {
      for (const modelId of modelIds) {
        const memory = recommendationFor(modelId, worker).memory || {};
        if (Number.isFinite(Number(memory.safe_available_mb))) figures.push(Number(memory.safe_available_mb));
      }
    }
    return figures.length ? `safe memory 최소 ${Math.min(...figures)} MiB` : "safe memory estimate unknown";
  }

  function renderRpcProfiles() {
    const editor = byId("sweepRpcEditor");
    const strategy = byId("sweepStrategySelect")?.value;
    if (!editor) return;
    editor.hidden = strategy !== "model_parallel_rpc";
    if (editor.hidden) return;
    const root = byId("sweepRpcProfiles");
    const selectedWorkers = [...builder.selectedWorkers];
    root.innerHTML = builder.rpcProfiles.length ? builder.rpcProfiles.map((profile, index) => {
      const capability = profileCapability(profile);
      const coordinatorModels = [...builder.selectedModels].filter(id => installedOn(state.models.find(item => item.id === id) || {}, profile.coordinator_id));
      const context = String(byId("sweepContextInput")?.value || "unknown");
      const workerChecks = selectedWorkers.map(worker => `<label><input type="checkbox" data-rpc-worker="${dashboard.escapeHtml(worker)}" ${profile.worker_ids.includes(worker) ? "checked" : ""}>${dashboard.escapeHtml(worker)}</label>`).join("");
      const coordinatorOptions = profile.worker_ids.map(worker => `<option value="${dashboard.escapeHtml(worker)}" ${profile.coordinator_id === worker ? "selected" : ""}>${dashboard.escapeHtml(worker)}</option>`).join("");
      const weights = profile.split_policy === "custom" ? `<div class="rpc-weight-grid">${profile.worker_ids.map(worker => `<label><span>${dashboard.escapeHtml(worker)}</span><input data-rpc-weight="${dashboard.escapeHtml(worker)}" type="number" min="0.001" step="0.1" value="${dashboard.escapeHtml(profile.weights_by_worker[worker] ?? 1)}"></label>`).join("")}</div>` : "";
      return `<article class="sweep-rpc-profile" data-rpc-profile-index="${index}">
        <header><label><span>Profile ID</span><input data-rpc-field="profile_id" value="${dashboard.escapeHtml(profile.profile_id)}" maxlength="80"></label><button type="button" data-rpc-remove aria-label="${dashboard.escapeHtml(profile.profile_id)} 삭제">×</button></header>
        <div class="rpc-profile-workers"><span>Worker set</span>${workerChecks}</div>
        <div class="sweep-form-grid compact-grid">
          <label class="field"><span>Coordinator</span><select data-rpc-field="coordinator_id">${coordinatorOptions}</select></label>
          <label class="field"><span>Split mode</span><select data-rpc-field="split_mode"><option value="layer" ${profile.split_mode === "layer" ? "selected" : ""}>layer</option><option value="row" ${profile.split_mode === "row" ? "selected" : ""}>row</option></select></label>
          <label class="field"><span>Split policy</span><select data-rpc-field="split_policy"><option value="auto" ${profile.split_policy === "auto" ? "selected" : ""}>auto</option><option value="equal" ${profile.split_policy === "equal" ? "selected" : ""}>equal</option><option value="custom" ${profile.split_policy === "custom" ? "selected" : ""}>custom</option></select></label>
          <label class="field"><span>rpc_gpu_layers</span><input data-rpc-field="rpc_gpu_layers" value="${dashboard.escapeHtml(profile.rpc_gpu_layers)}" inputmode="numeric"></label>
        </div>${weights}
        <div class="rpc-profile-evidence"><span class="${capability.tone}">${dashboard.escapeHtml(capability.text)}</span><span class="${coordinatorModels.length === builder.selectedModels.size && coordinatorModels.length ? "valid" : "blocked"}">coordinator model ${coordinatorModels.length}/${builder.selectedModels.size}</span><span>${dashboard.escapeHtml(profileMemory(profile))}</span><span>n_ctx ${dashboard.escapeHtml(context)}</span></div>
      </article>`;
    }).join("") : `<div class="empty-result"><strong>RPC profile이 없습니다.</strong><span>선택 Worker 2대 이상으로 profile을 추가하세요.</span></div>`;
  }

  function dirty(message = "입력 변경 · preview 필요") {
    if (!builder.dirty && builder.savedHash && builder.preview?.plan?.plan_sha256 === builder.savedHash) {
      builder.revision = Math.max(builder.revision + 1, Number(builder.preview.plan.spec?.revision || 1) + 1);
      message = "저장 후 변경 · 새 revision과 새 Sweep ID가 필요합니다";
    }
    builder.dirty = true;
    builder.approvedHash = "";
    const check = byId("sweepApprovalCheck");
    if (check) check.checked = false;
    const save = byId("sweepSaveButton");
    if (save) save.disabled = true;
    const status = byId("sweepBuilderStatus");
    if (status) status.textContent = message;
  }

  function resetRpcForWorkerChange() {
    if (builder.rpcProfiles.length) {
      builder.rpcProfiles = [];
      const notice = byId("sweepRpcNotice");
      if (notice) notice.textContent = "Worker 집합이 바뀌어 이전 coordinator와 custom 비율을 폐기했습니다. Profile을 다시 구성하세요.";
    }
    renderRpcProfiles();
  }

  function addRpcProfile() {
    const workers = [...builder.selectedWorkers];
    if (workers.length < 2) throw new Error("RPC profile에는 선택 Worker가 2대 이상 필요합니다.");
    const index = builder.rpcProfiles.length + 1;
    builder.rpcProfiles.push({
      profile_id: `rpc_${index}`,
      worker_ids: [...workers],
      coordinator_id: workers[0],
      split_mode: "layer",
      split_policy: "equal",
      weights_by_worker: Object.fromEntries(workers.map(worker => [worker, 1])),
      rpc_gpu_layers: "all",
    });
    dirty();
    renderRpcProfiles();
  }

  function normalizeProfile(profile) {
    if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(profile.profile_id || "")) throw new Error("RPC Profile ID 형식을 확인하세요.");
    if (profile.worker_ids.length < 2 || !profile.worker_ids.includes(profile.coordinator_id)) throw new Error(`${profile.profile_id}: Worker 2대 이상과 coordinator가 필요합니다.`);
    const gpu = String(profile.rpc_gpu_layers).trim();
    if (gpu !== "all" && (!/^\d+$/.test(gpu) || Number(gpu) > 999)) throw new Error(`${profile.profile_id}: rpc_gpu_layers는 all 또는 0..999입니다.`);
    const value = {
      profile_id: profile.profile_id,
      worker_ids: [...profile.worker_ids],
      coordinator_id: profile.coordinator_id,
      split_mode: profile.split_mode,
      split_policy: profile.split_policy,
      weights_by_worker: {},
      rpc_gpu_layers: gpu === "all" ? "all" : Number(gpu),
    };
    if (profile.split_policy === "custom") {
      for (const worker of profile.worker_ids) {
        const weight = Number(profile.weights_by_worker[worker]);
        if (!Number.isFinite(weight) || weight <= 0) throw new Error(`${profile.profile_id}: ${worker} 비율은 양수여야 합니다.`);
        value.weights_by_worker[worker] = weight;
      }
    }
    return value;
  }

  function buildRequest() {
    const workers = [...builder.selectedWorkers];
    const modelIds = [...builder.selectedModels];
    const strategy = byId("sweepStrategySelect").value;
    if (!workers.length) throw new Error("대상 Worker를 선택하세요.");
    if (!modelIds.length) throw new Error("실행 가능한 설치 모델을 선택하세요.");
    if (strategy === "single_node" && workers.length !== 1) throw new Error("단일 노드는 Worker를 정확히 1대 선택해야 합니다.");
    if (strategy === "model_parallel_rpc" && workers.length < 2) throw new Error("RPC는 Worker가 2대 이상 필요합니다.");

    const contexts = uniqueNumbers("sweepContextInput", { min: 128, max: 16384 });
    const concurrency = uniqueNumbers("sweepConcurrencyInput", { min: 1, max: 256 });
    const outputs = uniqueNumbers("sweepMaxTokensInput", { min: 1, max: 1024 });
    const modelRefs = modelIds.map((_, index) => `model_${index + 1}`);
    const rpcProfiles = strategy === "model_parallel_rpc" ? builder.rpcProfiles.map(normalizeProfile) : [];
    if (strategy === "model_parallel_rpc" && !rpcProfiles.length) throw new Error("RPC profile을 하나 이상 추가하세요.");
    const prompts = builder.promptVariants.map(variant => {
      if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(variant.ref || "")) throw new Error("Prompt ref 형식을 확인하세요.");
      if (!String(variant.text || "").trim()) throw new Error(`${variant.ref}: prompt 원문을 입력하세요.`);
      const prompt = { ref: variant.ref, text: variant.text, mode: variant.mode };
      if (variant.mode === "token_length_profile") {
        const target = Number(variant.target_input_tokens);
        if (!Number.isInteger(target) || target < 1 || target > 16384) throw new Error(`${variant.ref}: 목표 input tokens 범위를 확인하세요.`);
        prompt.target_input_tokens = target;
      }
      return prompt;
    });
    if (new Set(prompts.map(item => item.ref)).size !== prompts.length) throw new Error("Prompt ref는 중복될 수 없습니다.");
    const base = {
      model_ref: modelRefs[0], prompt_ref: prompts[0].ref,
      worker_ids: strategy === "model_parallel_rpc" ? [] : workers,
      execution_strategy: strategy,
      sweep_mode: "cumulative",
      rpc_profile_ref: strategy === "model_parallel_rpc" ? rpcProfiles[0].profile_id : null,
      n_ctx: contexts[0], concurrency: concurrency[0], max_tokens: outputs[0],
      n_gpu_layers: integerValue("sweepGpuLayersInput", 0, 120),
      temperature: numberValue("sweepTemperatureInput", 0, 2),
      top_p: numberValue("sweepTopPInput", 0, 1),
      seed: integerValue("sweepSeedInput", -1, 2147483647),
      requests: integerValue("sweepRequestsInput", 1, 10000),
      warmup_requests: integerValue("sweepWarmupInput", 0, 10),
      request_timeout_s: 600,
      persist_prompt: byId("sweepPersistPromptInput").checked,
      response_storage_mode: byId("sweepStorageSelect").value,
    };
    const threads = integerValue("sweepThreadsInput", 1, 1024, true);
    const batch = integerValue("sweepBatchInput", 1, 16384, true);
    if (threads !== null) base.n_threads = threads;
    if (batch !== null) base.n_batch = batch;
    if (base.rpc_profile_ref === null) delete base.rpc_profile_ref;

    const axes = [];
    const addAxis = (name, values) => { if (values.length > 1) axes.push({ name, values }); };
    addAxis("model_ref", modelRefs);
    addAxis("prompt_ref", prompts.map(item => item.ref));
    addAxis("n_ctx", contexts);
    addAxis("concurrency", concurrency);
    addAxis("max_tokens", outputs);
    if (rpcProfiles.length > 1) axes.push({ name: "rpc_profile_ref", values: rpcProfiles.map(item => item.profile_id) });
    const executionMode = byId("sweepExecutionModeSelect").value;
    const maxParallel = integerValue("sweepParallelInput", 1, 2);
    if (executionMode === "sequential" && maxParallel !== 1) throw new Error("순차 dispatch의 max_parallel_jobs는 1이어야 합니다.");
    const revision = builder.exclusions.length ? Math.max(2, builder.revision) : builder.revision;
    return {
      spec: {
        base, schema_version: 1, artifact_type: "sweep_spec", mode: "exploratory",
        revision, combination: byId("sweepCombinationSelect").value, axes, explicit: [],
        rpc_profiles: rpcProfiles,
        repeat_count: integerValue("sweepRepeatsInput", 1, 500),
        execution: {
          mode: executionMode, max_parallel_jobs: maxParallel,
          order: byId("sweepOrderSelect").value, order_seed: Math.max(0, integerValue("sweepSeedInput", -1, 2147483647)),
          backfill_policy: executionMode === "sequential" ? "strict" : "bounded", backfill_window: 8,
          failure_policy: byId("sweepFailureSelect").value,
          cleanup_policy: "quarantine_on_uncertainty", cooldown_s: numberValue("sweepCooldownInput", 0, 86400),
          model_cache_policy: "reload_per_cell", rpc_session_policy: "new_session_per_cell", retry_policy: "manual",
        },
        budget: { max_trials: integerValue("sweepMaxTrialsInput", 1, 500), max_physical_requests: integerValue("sweepMaxRequestsInput", 1, 1000000) },
        exclusions: builder.exclusions,
      },
      model_selections: Object.fromEntries(modelRefs.map((ref, index) => [ref, modelIds[index]])),
      prompts,
    };
  }

  function capabilityLabel(code) {
    return String(code || "UNKNOWN").replaceAll("_", " ").toLowerCase();
  }

  function renderPreview(result) {
    const plan = result.plan || {};
    const counts = plan.counts || {};
    const workload = counts.workload || {};
    byId("sweepPreviewEmpty").hidden = true;
    byId("sweepPreviewContent").hidden = false;
    byId("sweepEvidenceMode").textContent = String(result.evidence_mode || "cached").toUpperCase();
    byId("sweepPlanHash").textContent = plan.plan_sha256 || "—";
    const fields = [
      ["BASE CELLS", counts.candidate_cells], ["TRIALS", counts.trials],
      ["PHYSICAL", workload.physical_requests], ["WARMUP", workload.warmup_calls],
      ["VALID", counts.valid_cells], ["BLOCKED", counts.blocked_cells],
      ["UNKNOWN", counts.unknown_cells], ["DUPLICATE", counts.duplicate_cells],
      ["EXCLUDED", counts.excluded_cells],
    ];
    byId("sweepCountGrid").innerHTML = fields.map(([label, value]) => `<div><small>${label}</small><strong>${dashboard.escapeHtml(value ?? 0)}</strong></div>`).join("");
    const execution = plan.spec?.execution || {};
    byId("sweepBudgetNote").innerHTML = `<strong>${plan.executable ? "실행 가능한 plan" : "실행 전 blocker 해결 필요"}</strong><span>trial ${counts.trials || 0}/${plan.spec?.budget?.max_trials || 0} · physical+warmup ${(workload.physical_requests || 0) + (workload.warmup_calls || 0)}/${plan.spec?.budget?.max_physical_requests || 0} · max_parallel_jobs ${execution.max_parallel_jobs || 1}</span><small>같은 Worker pool은 resource lease가 직렬화합니다. Pi 과거 전원 경고는 정보이며 단독 blocker가 아닙니다.</small>`;
    const cells = plan.cells || [];
    const review = cells.slice(0, 80).map(cell => {
      const reasons = (cell.capabilities || []).filter(item => item.status !== "valid").map(item => `${item.status}: ${capabilityLabel(item.code)}${item.subject ? ` · ${item.subject}` : ""}`);
      if (cell.exclusion_reason) reasons.push(`excluded: ${cell.exclusion_reason}`);
      return `<article class="sweep-cell-row ${dashboard.escapeHtml(cell.status || "unknown")}" data-preview-cell="${dashboard.escapeHtml(cell.cell_id)}"><label><input type="checkbox" data-exclude-cell="${dashboard.escapeHtml(cell.cell_id)}" ${cell.exclusion_reason ? "checked" : ""}><span><strong>${dashboard.escapeHtml(cell.condition?.model_ref || "model")} · ctx ${dashboard.escapeHtml(cell.condition?.n_ctx)} · c${dashboard.escapeHtml(cell.condition?.concurrency)}${cell.condition?.rpc_profile_ref ? ` · ${dashboard.escapeHtml(cell.condition.rpc_profile_ref)}` : ""}</strong><small>${dashboard.escapeHtml(cell.status || "unknown")} · workers ${(cell.condition?.worker_ids || []).map(dashboard.escapeHtml).join(", ") || "profile-bound"}</small></span></label><input data-exclusion-reason="${dashboard.escapeHtml(cell.cell_id)}" maxlength="512" value="${dashboard.escapeHtml(cell.exclusion_reason || "")}" placeholder="제외 사유를 기록하세요" ${cell.exclusion_reason ? "" : "disabled"}><p>${reasons.length ? reasons.map(dashboard.escapeHtml).join(" · ") : "capability valid"}</p></article>`;
    }).join("");
    byId("sweepCellReview").innerHTML = review || `<div class="empty-result"><strong>계산된 cell 없음</strong></div>`;
    byId("sweepApplyExclusionsButton").hidden = !cells.length;
    byId("sweepBuilderStatus").textContent = `${counts.trials || 0} trials · ${plan.executable ? "실행 가능" : "blocker 있음"}`;
  }

  async function preview(refresh = false) {
    const request = buildRequest();
    const result = await dashboard.api(refresh ? "/api/sweeps/readiness/refresh" : "/api/sweeps/preview", { method: "POST", body: request });
    builder.request = request;
    builder.preview = result;
    builder.dirty = false;
    builder.approvedHash = "";
    byId("sweepApprovalCheck").checked = false;
    byId("sweepSaveButton").disabled = true;
    renderPreview(result);
    return result;
  }

  function applyExclusions() {
    const exclusions = [];
    document.querySelectorAll("[data-exclude-cell]:checked").forEach(input => {
      const cellId = input.dataset.excludeCell;
      const reason = document.querySelector(`[data-exclusion-reason="${CSS.escape(cellId)}"]`)?.value.trim();
      if (!reason) throw new Error(`${cellId.slice(0, 18)}… 제외 사유가 필요합니다.`);
      exclusions.push({ cell_id: cellId, reason });
    });
    builder.exclusions = exclusions;
    builder.revision = Math.max(2, builder.revision + 1);
    dirty("제외 조건 적용 · 새 revision preview 필요");
    return preview(false);
  }

  async function save() {
    if (builder.dirty || !builder.preview || !builder.request) throw new Error("현재 입력으로 preview를 다시 실행하세요.");
    const hash = builder.preview.plan?.plan_sha256 || "";
    if (!byId("sweepApprovalCheck").checked || builder.approvedHash !== hash) throw new Error("현재 preview hash를 확인하고 승인하세요.");
    const sweepId = byId("sweepIdInput").value.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(sweepId)) throw new Error("Sweep ID 형식을 확인하세요.");
    if (builder.savedSweepId === sweepId && builder.savedHash && builder.savedHash !== hash) throw new Error("저장한 plan을 수정했습니다. 과거 draft를 덮어쓰지 않도록 새 Sweep ID를 사용하세요.");
    const saved = await dashboard.api("/api/sweeps/drafts", { method: "POST", body: { sweep_id: sweepId, ...builder.request } });
    builder.savedHash = hash;
    builder.savedSweepId = sweepId;
    if (builder.preview.plan?.executable) sessionStorage.setItem(`sweepApproval:${sweepId}`, hash);
    else sessionStorage.removeItem(`sweepApproval:${sweepId}`);
    dashboard.toast?.("Sweep draft 저장", `${sweepId} · revision ${saved.plan_revision}`);
    await dashboard.sweepControl?.refresh?.();
    return saved;
  }

  function syncInventory() {
    const availableWorkers = state.nodes.filter(node => node.role === "worker" && node.enabled).map(node => node.name);
    if (!builder.selectedWorkers.size) availableWorkers.forEach(worker => builder.selectedWorkers.add(worker));
    else [...builder.selectedWorkers].forEach(worker => { if (!availableWorkers.includes(worker)) builder.selectedWorkers.delete(worker); });
    renderWorkers();
    renderModels();
    renderPromptVariants();
    renderRpcProfiles();
  }

  function bind() {
    const form = byId("sweepBuilderForm");
    if (!form) return;
    form.addEventListener("submit", async event => {
      event.preventDefault();
      try { await preview(false); }
      catch (error) { dashboard.toast?.("Sweep preview 실패", error.message, "error"); }
    });
    form.addEventListener("input", event => {
      if (event.target.matches("[data-rpc-field], [data-rpc-weight], [data-rpc-worker], [data-sweep-worker], [data-sweep-model]")) return;
      dirty();
      if (["sweepContextInput", "sweepStrategySelect"].includes(event.target.id)) {
        renderModels();
        renderRpcProfiles();
      }
    });
    byId("sweepWorkerPicker").addEventListener("change", event => {
      const input = event.target.closest("[data-sweep-worker]");
      if (!input) return;
      input.checked ? builder.selectedWorkers.add(input.dataset.sweepWorker) : builder.selectedWorkers.delete(input.dataset.sweepWorker);
      resetRpcForWorkerChange();
      renderWorkers(); renderModels(); dirty("Worker 변경 · RPC profile 재확인 필요");
    });
    byId("sweepModelPicker").addEventListener("change", event => {
      const input = event.target.closest("[data-sweep-model]");
      if (!input || input.disabled) return;
      input.checked ? builder.selectedModels.add(input.dataset.sweepModel) : builder.selectedModels.delete(input.dataset.sweepModel);
      renderModels(); renderRpcProfiles(); dirty();
    });
    byId("sweepStrategySelect").addEventListener("change", () => { renderModels(); renderRpcProfiles(); dirty(); });
    byId("addSweepPromptButton").addEventListener("click", () => {
      builder.promptVariants.push({ ref: `prompt_${builder.promptVariants.length + 1}`, text: "", mode: "same_text", target_input_tokens: null });
      renderPromptVariants(); dirty();
    });
    byId("sweepPromptVariants").addEventListener("input", event => {
      const row = event.target.closest("[data-prompt-index]");
      const field = event.target.dataset.promptField;
      if (!row || !field) return;
      builder.promptVariants[Number(row.dataset.promptIndex)][field] = field === "target_input_tokens" ? Number(event.target.value) : event.target.value;
      dirty();
    });
    byId("sweepPromptVariants").addEventListener("change", event => {
      if (event.target.dataset.promptField === "mode") renderPromptVariants();
    });
    byId("sweepPromptVariants").addEventListener("click", event => {
      const button = event.target.closest("[data-prompt-remove]");
      if (!button || button.disabled) return;
      builder.promptVariants.splice(Number(button.closest("[data-prompt-index]").dataset.promptIndex), 1);
      renderPromptVariants(); dirty();
    });
    byId("sweepExecutionModeSelect").addEventListener("change", event => {
      if (event.target.value === "sequential") byId("sweepParallelInput").value = "1";
      dirty();
    });
    byId("addRpcProfileButton").addEventListener("click", () => { try { addRpcProfile(); } catch (error) { dashboard.toast?.("RPC profile 추가 실패", error.message, "error"); } });
    byId("sweepRpcProfiles").addEventListener("change", event => {
      const card = event.target.closest("[data-rpc-profile-index]");
      if (!card) return;
      const profile = builder.rpcProfiles[Number(card.dataset.rpcProfileIndex)];
      if (event.target.matches("[data-rpc-worker]")) {
        const worker = event.target.dataset.rpcWorker;
        profile.worker_ids = event.target.checked ? [...profile.worker_ids, worker] : profile.worker_ids.filter(item => item !== worker);
        profile.worker_ids = [...builder.selectedWorkers].filter(item => profile.worker_ids.includes(item));
        if (!profile.worker_ids.includes(profile.coordinator_id)) profile.coordinator_id = profile.worker_ids[0] || "";
      } else if (event.target.matches("[data-rpc-weight]")) {
        profile.weights_by_worker[event.target.dataset.rpcWeight] = Number(event.target.value);
      } else if (event.target.matches("[data-rpc-field]")) {
        profile[event.target.dataset.rpcField] = event.target.value;
      }
      dirty(); renderRpcProfiles();
    });
    byId("sweepRpcProfiles").addEventListener("click", event => {
      const button = event.target.closest("[data-rpc-remove]");
      if (!button) return;
      const card = button.closest("[data-rpc-profile-index]");
      builder.rpcProfiles.splice(Number(card.dataset.rpcProfileIndex), 1);
      dirty(); renderRpcProfiles();
    });
    byId("sweepCellReview").addEventListener("change", event => {
      const input = event.target.closest("[data-exclude-cell]");
      if (!input) return;
      const reason = document.querySelector(`[data-exclusion-reason="${CSS.escape(input.dataset.excludeCell)}"]`);
      reason.disabled = !input.checked;
      if (!input.checked) reason.value = "";
    });
    byId("sweepApplyExclusionsButton").addEventListener("click", async () => { try { await applyExclusions(); } catch (error) { dashboard.toast?.("제외 적용 실패", error.message, "error"); } });
    byId("sweepRefreshReadinessButton").addEventListener("click", async () => { try { await preview(true); dashboard.toast?.("Readiness 확인 완료", "서버가 Worker/inventory evidence를 읽기 전용으로 갱신했습니다."); } catch (error) { dashboard.toast?.("Readiness 확인 실패", error.message, "error"); } });
    byId("sweepApprovalCheck").addEventListener("change", event => {
      const hash = builder.preview?.plan?.plan_sha256 || "";
      builder.approvedHash = event.target.checked && !builder.dirty ? hash : "";
      byId("sweepSaveButton").disabled = !builder.approvedHash;
    });
    byId("sweepSaveButton").addEventListener("click", async () => { try { await save(); } catch (error) { dashboard.toast?.("Draft 저장 실패", error.message, "error"); } });
    byId("sweepResetButton").addEventListener("click", () => {
      builder.rpcProfiles = []; builder.promptVariants = [{ ref: "prompt_main", text: "엣지 환경에서 LLM을 운영할 때의 장점을 설명해줘.", mode: "same_text", target_input_tokens: null }]; builder.exclusions = []; builder.revision += 1; builder.preview = null; builder.request = null;
      byId("sweepBuilderForm").reset(); dirty("초기화됨 · preview 필요"); syncInventory();
      byId("sweepPreviewEmpty").hidden = false; byId("sweepPreviewContent").hidden = true;
    });
  }

  async function loadCapabilities() {
    try {
      builder.capabilities = await dashboard.api("/api/sweeps/capabilities");
      byId("sweepBuilderStatus").textContent = `API 준비 · 최대 ${builder.capabilities.max_trials || 500} trials`;
    } catch (error) {
      byId("sweepBuilderStatus").textContent = "Sweep API 확인 실패";
    }
  }

  dashboard.sweepBuilder = { syncInventory, preview, buildRequest, renderRpcProfiles };
  document.addEventListener("DOMContentLoaded", () => { bind(); syncInventory(); loadCapabilities(); });
})();
