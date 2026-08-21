/* Model Library: catalog evidence and Worker filesystem facts remain distinct. */
(() => {
  const dashboard = window.ClusterDashboard || (window.ClusterDashboard = {});
  const progressByModel = new Map();
  const STATUS = {
    recommended: ["RECOMMENDED", "ready"], compatible: ["COMPATIBLE", "compatible"],
    candidate: ["CANDIDATE", "candidate"], verified: ["VERIFIED", "verified"],
    stress_test: ["STRESS TEST", "stress"], rpc_only: ["RPC ONLY", "rpc"],
    unsupported: ["UNSUPPORTED", "unsupported"], deprecated: ["DEPRECATED", "deprecated"],
  };

  function workerTargets() {
    const state = dashboard.state;
    return [...state.selectedNodes].filter(name => state.nodes.some(node => node.name === name && node.role === "worker" && node.enabled));
  }

  function modelRecommendations(modelId, recommendations = dashboard.state.modelRecommendations || {}) {
    return Object.entries(recommendations).flatMap(([node, values]) => (values || [])
      .filter(item => item?.id === modelId).map(item => ({ node, ...item })));
  }

  function recommendationSummary(modelId) {
    const values = modelRecommendations(modelId);
    const rank = { recommended: 0, compatible: 1, verified: 1, candidate: 2, stress_test: 3, rpc_only: 4, unsupported: 5, deprecated: 6 };
    values.sort((a, b) => (rank[a.status] ?? 9) - (rank[b.status] ?? 9) || a.node.localeCompare(b.node));
    const primary = values[0] || { status: "candidate", reasons_ko: ["현재 Worker capability를 아직 읽지 못했습니다."], cautions_ko: [] };
    const targets = values.filter(item => ["recommended", "compatible", "verified"].includes(item.status)).map(item => item.node);
    return { ...primary, workers: targets, all: values };
  }

  function badge(status) {
    const [label, tone] = STATUS[String(status || "candidate").toLowerCase()] || ["UNVERIFIED", "candidate"];
    return `<span class="model-status-badge ${dashboard.escapeHtml(tone)}">${dashboard.escapeHtml(label)}</span>`;
  }

  function detailList(label, values, tone = "") {
    const rows = Array.isArray(values) ? values.filter(Boolean) : [];
    return rows.length ? `<div class="library-detail ${tone}"><strong>${dashboard.escapeHtml(label)}</strong><ul>${rows.map(value => `<li>${dashboard.escapeHtml(value)}</li>`).join("")}</ul></div>` : "";
  }

  function fitLabel(memory) {
    if (!memory || memory.fits === null || memory.fits === undefined) return "FIT PENDING";
    const required = Number(memory.required_mb); const safe = Number(memory.safe_available_mb);
    if (!Number.isFinite(required) || !Number.isFinite(safe)) return "FIT PENDING";
    return memory.fits ? `FIT ${required} / ${safe} MB` : `MEMORY ${required} / ${safe} MB`;
  }

  function renderStarterPacks() {
    const root = dashboard.$?.("#modelStarterPacks");
    if (!root) return;
    const packs = dashboard.state.modelStarterPacks || [];
    root.innerHTML = packs.map(pack => `<button type="button" class="model-pack" data-model-pack="${dashboard.escapeHtml(pack.id)}"><strong>${dashboard.escapeHtml(pack.label_ko || pack.id)}</strong><small>${(pack.model_ids || []).length} models · 설치된 항목만 실험 선택</small></button>`).join("");
    root.querySelectorAll("[data-model-pack]").forEach(button => button.addEventListener("click", () => {
      const pack = packs.find(item => item.id === button.dataset.modelPack);
      const available = (pack?.model_ids || []).filter(id => dashboard.state.models.some(model => model.id === id && (model.installed_nodes || []).length));
      if (!available.length) return dashboard.toast?.("설치된 모델 없음", "이 starter pack의 GGUF가 아직 어떤 Worker에도 설치되어 있지 않습니다.", "error");
      dashboard.setSelectedModels?.(available);
      location.hash = "experiment";
      dashboard.toast?.("Starter pack 적용", `${available.length}개 설치된 모델을 실험 선택에 반영했습니다.`);
    }));
  }

  function render(models = dashboard.state.models) {
    const root = dashboard.$?.("#modelLibrary");
    const summary = dashboard.$?.("#modelLibrarySummary");
    if (!root || !summary) return;
    const query = (dashboard.$("#libraryModelSearch")?.value || "").trim().toLowerCase();
    const rows = (models || []).filter(model => {
      const catalog = model.catalog || {};
      return !query || [model.id, model.filename, model.quantization, catalog.display_name, catalog.vendor, catalog.family, catalog.license, catalog.recommendation_tier].join(" ").toLowerCase().includes(query);
    });
    const installed = rows.filter(model => (model.installed_nodes || []).length).length;
    const recommended = rows.filter(model => recommendationSummary(model.id).status === "recommended").length;
    summary.innerHTML = `<span><strong>${rows.length}</strong> catalog records</span><span><strong>${installed}</strong> installed</span><span><strong>${recommended}</strong> smoke-verified recommendations</span><span>Controller는 추론 대상이 아님</span>`;
    renderStarterPacks();
    if (!rows.length) { root.innerHTML = `<div class="model-library-empty">검색 조건에 맞는 모델이 없습니다.</div>`; return; }
    root.innerHTML = rows.map(model => {
      const catalog = model.catalog || {};
      const installedNodes = model.installed_nodes || [];
      const targets = workerTargets();
      const targetMissing = targets.filter(name => !installedNodes.includes(name));
      const recommendation = recommendationSummary(model.id);
      const progress = progressByModel.get(model.id);
      const identity = catalog.identity_locked ? "PINNED IDENTITY" : "SOURCE LOCK REQUIRED";
      const provenance = catalog.official_gguf ? "OFFICIAL GGUF" : "COMMUNITY / REVIEW";
      const parameters = catalog.parameters_effective_b ? `${catalog.parameters_effective_b}B effective · ${catalog.parameters_total_b || catalog.parameter_count_b || "—"}B total` : `${catalog.parameters_total_b || catalog.parameter_count_b || "—"}B parameters`;
      const facts = [catalog.vendor || "Unknown vendor", catalog.family || "Unclassified", parameters, model.quantization || catalog.quantization || "quantization N/A"];
      const runtime = [catalog.context_length_advertised ? `ctx ${Number(catalog.context_length_advertised).toLocaleString()} · default ${catalog.default_context || 4096}` : "context N/A", catalog.license || "license review required", provenance, identity];
      const canSync = Boolean(targetMissing.length && installedNodes.length);
      const canDelete = Boolean(installedNodes.length);
      return `<article class="library-model-card" data-library-model="${dashboard.escapeHtml(model.id)}">
        <div class="library-model-head"><div><div class="model-status-row">${badge(recommendation.status)}<span class="model-tier">${dashboard.escapeHtml(String(catalog.recommendation_tier || "unclassified").toUpperCase())}</span></div><h3 title="${dashboard.escapeHtml(model.id)}">${dashboard.escapeHtml(catalog.display_name || model.filename || model.id)}</h3><p>${dashboard.escapeHtml(catalog.summary_ko || catalog.description || "이 모델의 추가 설명이 카탈로그에 없습니다.")}</p></div><strong>${dashboard.utils.bytes(model.size_bytes || catalog.size_bytes || 0)}</strong></div>
        <div class="library-facts">${facts.map(item => `<span>${dashboard.escapeHtml(item)}</span>`).join("")}</div>
        <div class="library-runtime">${runtime.map(item => `<span>${dashboard.escapeHtml(item)}</span>`).join("")}</div>
        <div class="library-placement"><strong>WORKER INSTALLATION</strong><p>${installedNodes.length ? installedNodes.map(name => `<span class="worker-install">${dashboard.escapeHtml(name)}</span>`).join("") : "아직 설치된 Worker 없음"}</p><small>${dashboard.escapeHtml(fitLabel(recommendation.memory))} · ${recommendation.workers.length ? `적합 Worker ${recommendation.workers.join(", ")}` : "Worker smoke 확인 필요"}</small></div>
        ${detailList("추천 이유", recommendation.reasons_ko, "reasons")}${detailList("주의사항", recommendation.cautions_ko, "cautions")}
        ${progress ? `<div class="library-progress"><span>${dashboard.escapeHtml(progress.node || "worker")} · ${dashboard.escapeHtml(progress.state || "working")}</span><i><b style="width:${Math.max(0, Math.min(100, Number(progress.percent) || 0))}%"></b></i><strong>${Number.isFinite(Number(progress.percent)) ? `${Number(progress.percent).toFixed(1)}%` : "…"}</strong></div>` : ""}
        <div class="library-actions"><button type="button" class="button ghost compact" data-model-sync="${dashboard.escapeHtml(model.id)}" ${canSync ? "" : "disabled"}>${canSync ? `선택 Worker ${targetMissing.length}대에 동기화` : installedNodes.length ? "선택 Worker에 설치됨" : "GGUF source 확인 필요"}</button><button type="button" class="button ghost compact danger-text" data-model-delete="${dashboard.escapeHtml(model.id)}" ${canDelete ? "" : "disabled"}>Worker에서 삭제</button></div>
      </article>`;
    }).join("");
    root.querySelectorAll("[data-model-sync]").forEach(button => button.addEventListener("click", () => sync(button.dataset.modelSync)));
    root.querySelectorAll("[data-model-delete]").forEach(button => button.addEventListener("click", () => remove(button.dataset.modelDelete)));
  }

  async function refresh() {
    const response = await dashboard.api("/api/models");
    dashboard.state.models = response.models || [];
    dashboard.state.modelCatalog = response.catalog || [];
    dashboard.state.modelRecommendations = response.recommendations || {};
    dashboard.state.modelStarterPacks = response.starter_packs || [];
    dashboard.state.modelCatalogPolicy = response.catalog_policy || {};
    dashboard.renderModels?.({}); render(); return response;
  }

  async function sync(modelId) {
    const targets = workerTargets().filter(name => !((dashboard.state.models.find(item => item.id === modelId)?.installed_nodes || []).includes(name)));
    if (!targets.length) return dashboard.toast?.("동기화 대상 없음", "선택한 Worker에는 이미 모델이 설치되어 있습니다.", "error");
    if (!confirm(`선택한 Worker ${targets.join(", ")}에 ${modelId}을(를) 동기화합니다. Controller cache의 검증된 GGUF만 전송하며 benchmark 시간에는 포함하지 않습니다. 계속할까요?`)) return;
    await dashboard.runActionOnNodes?.("sync-models", targets, { models: [modelId], confirmed: true });
  }

  function recordProgress(progress) { if (progress?.model_id) { progressByModel.set(progress.model_id, { ...progress }); render(); } }

  async function remove(modelId) {
    const installed = dashboard.state.models.find(item => item.id === modelId)?.installed_nodes || [];
    const targets = workerTargets().filter(name => installed.includes(name));
    if (!targets.length) return dashboard.toast?.("삭제 대상 없음", "선택한 Worker에 이 모델이 없습니다.", "error");
    if (!confirm(`${targets.join(", ")}에서 ${modelId}을(를) 삭제합니다. 실행 중인 실험에는 사용할 수 없습니다. 계속할까요?`)) return;
    await dashboard.runActionOnNodes?.("delete-models", targets, { models: [modelId], confirmed: true });
  }

  dashboard.renderModelLibrary = render;
  dashboard.modelLibrary = { refresh, render, sync, remove, recordProgress };
  document.addEventListener("DOMContentLoaded", () => {
    dashboard.$?.("#libraryModelSearch")?.addEventListener("input", () => render());
    dashboard.$?.("#refreshModelsButton")?.addEventListener("click", async () => {
      try { await refresh(); dashboard.toast?.("모델 상태 갱신", "카탈로그와 Worker 인벤토리를 새로 읽었습니다."); }
      catch (error) { dashboard.toast?.("모델 상태 갱신 실패", error.message, "error"); }
    });
  });
})();
