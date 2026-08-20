/* Model Library: catalog metadata and worker facts are rendered together, never inferred. */
(() => {
  const dashboard = window.ClusterDashboard || (window.ClusterDashboard = {});
  const progressByModel = new Map();

  function workerTargets() {
    const state = dashboard.state;
    return [...state.selectedNodes].filter(name => state.nodes.some(node => node.name === name && node.role === "worker" && node.enabled));
  }

  function recommendationReason(model, recommendations) {
    const recommendedNodes = Object.entries(recommendations || {})
      .filter(([, values]) => (values || []).some(item => item.id === model.id))
      .map(([name]) => name);
    if (recommendedNodes.length) return `${recommendedNodes.join(", ")}의 플랫폼·메모리 규칙에 적합`;
    const platforms = model.catalog?.recommended_platforms || [];
    return platforms.length ? `${platforms.join(" / ")} 권장 모델` : "카탈로그 추천 정보 없음";
  }

  function render(models = dashboard.state.models, recommendations = dashboard.modelRecommendations || {}) {
    const root = dashboard.$?.("#modelLibrary");
    const summary = dashboard.$?.("#modelLibrarySummary");
    if (!root || !summary) return;
    const query = (dashboard.$("#libraryModelSearch")?.value || "").trim().toLowerCase();
    const rows = (models || []).filter(model => {
      const catalog = model.catalog || {};
      const text = [model.id, model.filename, model.quantization, catalog.vendor, catalog.family, catalog.license].join(" ").toLowerCase();
      return !query || text.includes(query);
    });
    const installed = rows.filter(model => (model.installed_nodes || []).length).length;
    summary.innerHTML = `<span><strong>${rows.length}</strong> catalog records</span><span><strong>${installed}</strong> installed on one or more workers</span><span><strong>${workerTargets().length}</strong> selected worker targets</span>`;
    if (!rows.length) {
      root.innerHTML = `<div class="model-library-empty">검색 조건에 맞는 모델이 없습니다.</div>`;
      return;
    }
    root.innerHTML = rows.map(model => {
      const catalog = model.catalog || {};
      const installedNodes = model.installed_nodes || [];
      const targets = workerTargets();
      const targetMissing = targets.filter(name => !installedNodes.includes(name));
      const canSync = Boolean(targetMissing.length);
      const canDelete = Boolean(installedNodes.length);
      const progress = progressByModel.get(model.id);
      const facts = [
        catalog.vendor || "Unknown vendor",
        catalog.family || "Unclassified",
        catalog.parameter_count_b ? `${catalog.parameter_count_b}B` : "parameter count N/A",
        model.quantization || catalog.quantization || "quantization N/A",
      ];
      const runtime = [
        catalog.context_length ? `ctx ${Number(catalog.context_length).toLocaleString()}` : "context N/A",
        catalog.license || "license N/A",
        catalog.estimated_memory_mb ? `est. ${(Number(catalog.estimated_memory_mb) / 1024).toFixed(1)} GB` : "memory estimate N/A",
      ];
      return `<article class="library-model-card" data-library-model="${dashboard.escapeHtml(model.id)}">
        <div class="library-model-head"><div><span class="model-availability ${installedNodes.length ? "installed" : "catalog-only"}"><i></i>${installedNodes.length ? "INSTALLED" : "CATALOG ONLY"}</span><h3 title="${dashboard.escapeHtml(model.id)}">${dashboard.escapeHtml(model.filename || model.id)}</h3><p>${dashboard.escapeHtml(catalog.description || "이 모델의 추가 설명이 카탈로그에 없습니다.")}</p></div><strong>${dashboard.utils.bytes(model.size_bytes || (Number(model.size_gb) * 1024 ** 3))}</strong></div>
        <div class="library-facts">${facts.map(item => `<span>${dashboard.escapeHtml(item)}</span>`).join("")}</div>
        <div class="library-runtime">${runtime.map(item => `<span>${dashboard.escapeHtml(item)}</span>`).join("")}</div>
        <div class="library-placement"><strong>WORKER INSTALLATION</strong><p>${installedNodes.length ? installedNodes.map(name => `<span class="worker-install">${dashboard.escapeHtml(name)}</span>`).join("") : "아직 설치된 워커 없음"}</p><small>추천 · ${dashboard.escapeHtml(recommendationReason(model, recommendations))}</small></div>
        ${progress ? `<div class="library-progress"><span>${dashboard.escapeHtml(progress.node || "worker")} · ${dashboard.escapeHtml(progress.state || "working")}</span><i><b style="width:${Math.max(0, Math.min(100, Number(progress.percent) || 0))}%"></b></i><strong>${Number.isFinite(Number(progress.percent)) ? `${Number(progress.percent).toFixed(1)}%` : "…"}</strong></div>` : ""}
        <div class="library-actions"><button type="button" class="button ghost compact" data-model-sync="${dashboard.escapeHtml(model.id)}" ${canSync ? "" : "disabled"}>${canSync ? `선택 워커 ${targetMissing.length}대에 설치` : "선택 워커에 설치됨"}</button><button type="button" class="button ghost compact danger-text" data-model-delete="${dashboard.escapeHtml(model.id)}" ${canDelete ? "" : "disabled"}>워커에서 삭제</button></div>
      </article>`;
    }).join("");
    root.querySelectorAll("[data-model-sync]").forEach(button => button.addEventListener("click", () => sync(button.dataset.modelSync)));
    root.querySelectorAll("[data-model-delete]").forEach(button => button.addEventListener("click", () => remove(button.dataset.modelDelete)));
  }

  async function refresh() {
    const response = await dashboard.api("/api/models");
    dashboard.state.models = response.models || [];
    dashboard.modelCatalog = response.catalog || [];
    dashboard.modelRecommendations = response.recommendations || {};
    dashboard.renderModels?.({});
    render();
    return response;
  }

  async function sync(modelId) {
    const targets = workerTargets().filter(name => !((dashboard.state.models.find(item => item.id === modelId)?.installed_nodes || []).includes(name)));
    if (!targets.length) return dashboard.toast?.("동기화 대상 없음", "선택한 워커에는 이미 모델이 설치되어 있습니다.", "error");
    if (!confirm(`선택 워커 ${targets.join(", ")}에 ${modelId}을(를) 설치합니다. Controller cache의 검증된 GGUF를 동기화하며 이 작업은 벤치마크 시간에 포함되지 않습니다. 계속할까요?`)) return;
    await dashboard.runActionOnNodes?.("sync-models", targets, { models: [modelId], confirmed: true });
  }

  function recordProgress(progress) {
    if (!progress?.model_id) return;
    progressByModel.set(progress.model_id, { ...progress });
    render();
  }

  async function remove(modelId) {
    const installed = dashboard.state.models.find(item => item.id === modelId)?.installed_nodes || [];
    const targets = workerTargets().filter(name => installed.includes(name));
    if (!targets.length) return dashboard.toast?.("삭제 대상 없음", "선택한 워커에 이 모델이 없습니다.", "error");
    if (!confirm(`${targets.join(", ")}에서 ${modelId}을(를) 삭제합니다. 실행 중인 실험에는 사용할 수 없습니다. 계속할까요?`)) return;
    await dashboard.runActionOnNodes?.("delete-models", targets, { models: [modelId], confirmed: true });
  }

  dashboard.renderModelLibrary = render;
  dashboard.modelLibrary = { refresh, render, sync, remove, recordProgress };

  document.addEventListener("DOMContentLoaded", () => {
    dashboard.$?.("#libraryModelSearch")?.addEventListener("input", () => render());
    dashboard.$?.("#refreshModelsButton")?.addEventListener("click", async () => {
      try { await refresh(); dashboard.toast?.("모델 상태 갱신", "카탈로그와 워커 인벤토리를 새로 읽었습니다."); }
      catch (error) { dashboard.toast?.("모델 상태 갱신 실패", error.message, "error"); }
    });
  });
})();
