/* Model Library: catalog evidence and Worker filesystem facts remain distinct. */
(() => {
  const dashboard = window.ClusterDashboard || (window.ClusterDashboard = {});
  const progressByModel = new Map();
  let activeFilter = "all";
  let activeView = "models";
  let activeVendor = "";
  let activePackId = "";
  let modelPageSize = 10;
  let modelCurrentPage = 1;
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

  function tagsFor(model) {
    const catalog = model.catalog || {}; const parameters = Number(catalog.parameters_total_b || catalog.parameter_count_b || 0);
    const tags = new Set((catalog.capability_tags || []).map(value => String(value).toLowerCase()));
    if ((catalog.recommended_platforms || []).includes("raspberry-pi")) tags.add("edge");
    if ((catalog.recommended_platforms || []).includes("jetson")) tags.add("jetson");
    if ((catalog.benchmark_roles || []).includes("model_parallel_rpc")) tags.add("rpc");
    if (parameters > 8) tags.add("large");
    if ((model.installed_nodes || []).length) tags.add("installed");
    if (catalog.download_eligibility?.eligible) tags.add("downloadable");
    if (catalog.gated || catalog.license_review_required || catalog.download_policy === "gated_manual") tags.add("gated");
    return tags;
  }

  function vendorName(model) {
    const catalog = model?.catalog || {};
    return String(catalog.vendor || catalog.organization || "기타 / Community").trim() || "기타 / Community";
  }

  function groupByVendor(models = []) {
    const grouped = new Map();
    for (const model of models) {
      const vendor = vendorName(model);
      if (!grouped.has(vendor)) grouped.set(vendor, []);
      grouped.get(vendor).push(model);
    }
    return [...grouped.entries()].map(([vendor, entries]) => {
      const families = [...new Set(entries.map(model => model.catalog?.family).filter(Boolean))].sort((a, b) => a.localeCompare(b));
      const installed = entries.filter(model => (model.installed_nodes || []).length).length;
      const supported = entries.filter(model => ["recommended", "compatible", "verified"].includes(recommendationSummary(model.id).status)).length;
      const platforms = [...new Set(entries.flatMap(model => model.catalog?.recommended_platforms || []))].sort((a, b) => a.localeCompare(b));
      return { vendor, models: entries, families, installed, supported, platforms };
    }).sort((a, b) => b.models.length - a.models.length || a.vendor.localeCompare(b.vendor));
  }

  function renderVendorGroups(groups) {
    return groups.map(group => `<article class="vendor-overview-card" data-vendor-card="${dashboard.escapeHtml(group.vendor)}">
      <header><div><span>COMPANY / ORGANIZATION</span><h3>${dashboard.escapeHtml(group.vendor)}</h3></div><strong>${group.models.length}<small>MODELS</small></strong></header>
      <div class="vendor-overview-stats"><span><b>${group.installed}</b> 설치됨</span><span><b>${group.supported}</b> 권장·호환</span><span><b>${group.families.length}</b> 패밀리</span></div>
      <p>${group.families.length ? group.families.map(value => dashboard.escapeHtml(value)).join(" · ") : "분류되지 않은 모델군"}</p>
      <div class="vendor-model-names">${group.models.map(model => `<span>${dashboard.escapeHtml(model.catalog?.display_name || model.filename || model.id)}</span>`).join("")}</div>
      <footer><small>${group.platforms.length ? group.platforms.map(value => dashboard.escapeHtml(value)).join(" · ") : "플랫폼 검증 대기"}</small><button type="button" class="button ghost compact" data-vendor-open="${dashboard.escapeHtml(group.vendor)}">모델 상세 보기</button></footer>
    </article>`).join("");
  }

  function specialBadges(catalog) {
    const parameters = Number(catalog.parameters_total_b || catalog.parameter_count_b || 0); const values = [];
    if (parameters >= 70 || catalog.size_class === "rpc_extreme") values.push(["RPC EXTREME", "rpc"]);
    else if (parameters > 8 || catalog.size_class === "rpc_large") values.push(["RPC LARGE", "rpc"]);
    if (catalog.download_policy === "catalog_only" || catalog.download_policy === "multipart_unsupported") values.push(["CATALOG ONLY", ""]);
    if (catalog.gated || catalog.download_policy === "gated_manual") values.push(["GATED", "gated"]);
    if (catalog.provenance_status === "community_review") values.push(["COMMUNITY GGUF", "community"]);
    return `<div class="model-special-badges">${values.map(([label,tone]) => `<span class="model-special-badge ${tone}">${label}</span>`).join("")}</div>`;
  }

  function modelApiPath(modelId) { return String(modelId).split("/").map(encodeURIComponent).join("/"); }

  function licenseConsentHtml(model) {
    const catalog = model.catalog || {}; const status = catalog.license_acceptance || {};
    if (!status.required) return "";
    const sourceLink = status.terms_url ? `<a href="${dashboard.escapeHtml(status.terms_url)}" target="_blank" rel="noopener noreferrer">현재 약관과 원본 repository 열기</a>` : "약관 링크 없음";
    const artifactLink = status.artifact_url && status.artifact_url !== status.terms_url ? `<a href="${dashboard.escapeHtml(status.artifact_url)}" target="_blank" rel="noopener noreferrer">실제 GGUF repository 접근 확인</a>` : "";
    const terms = [sourceLink, artifactLink].filter(Boolean).join(" · ");
    if (status.accepted) return `<div class="license-consent accepted"><strong>LICENSE ACCEPTED · THIS PROJECT</strong><small>${dashboard.escapeHtml(catalog.license || "license")} · ${dashboard.escapeHtml(status.accepted_at || "accepted")}</small>${status.gated && !status.access_ready ? `<small>약관 동의는 완료되었습니다. 위 HUGGING FACE ACCOUNT의 로그인 명령을 복사해 Controller 터미널에서 실행하세요.</small>` : ""}<div class="license-consent-actions">${terms}<button type="button" class="button ghost compact danger-text" data-license-revoke="${dashboard.escapeHtml(model.id)}">동의 철회</button></div></div>`;
    return `<div class="license-consent"><strong>LICENSE REVIEW REQUIRED</strong><small>${dashboard.escapeHtml(catalog.license || "별도 약관")} · 동의는 현재 model/source revision에만 적용되며 토큰을 저장하지 않습니다.</small><div>${terms}</div><label><input type="checkbox" data-license-check="${dashboard.escapeHtml(model.id)}"> 약관과 모델 사용 조건을 확인했고 이 프로젝트에서 사용하는 데 동의합니다.</label><button type="button" class="button ghost compact" data-license-accept="${dashboard.escapeHtml(model.id)}" disabled>동의 저장</button></div>`;
  }

  function packPreviewData(pack, models = dashboard.state.models || []) {
    const byId = new Map(models.map(model => [model.id, model]));
    const entries = (pack?.model_ids || []).map((id, index) => {
      const model = byId.get(id);
      const catalog = model?.catalog || {};
      return {
        id, index, model,
        name: catalog.display_name || model?.filename || id,
        vendor: vendorName(model),
        family: catalog.family || "Unclassified",
        parameters: catalog.parameters_effective_b || catalog.parameters_total_b || catalog.parameter_count_b || null,
        installed: Boolean((model?.installed_nodes || []).length),
        installedNodes: model?.installed_nodes || [],
      };
    });
    return { entries, available: entries.filter(entry => entry.installed).map(entry => entry.id) };
  }

  function renderPackPreview(pack) {
    const preview = dashboard.$?.("#modelStarterPackPreview");
    if (!preview) return;
    if (!pack) { preview.hidden = true; preview.innerHTML = ""; return; }
    const data = packPreviewData(pack);
    preview.hidden = false;
    preview.innerHTML = `<header><div><span>SELECTED EXPERIMENT PACK</span><h3>${dashboard.escapeHtml(pack.label_ko || pack.id)}</h3><p>팩에 포함된 모델을 확인한 뒤 설치된 모델만 실험에 적용합니다.</p></div><button type="button" class="icon-button" data-pack-close aria-label="실험 팩 미리보기 닫기">×</button></header>
      <div class="model-pack-preview-list">${data.entries.map(entry => `<article class="${entry.installed ? "installed" : "catalog-only"}">
        <b>${entry.index + 1}</b><div><strong>${dashboard.escapeHtml(entry.name)}</strong><small>${dashboard.escapeHtml(entry.vendor)} · ${dashboard.escapeHtml(entry.family)}${entry.parameters ? ` · ${dashboard.escapeHtml(entry.parameters)}B` : ""}</small></div><span>${entry.installed ? `설치됨 · ${entry.installedNodes.length}대` : "카탈로그만 등록"}</span>
      </article>`).join("")}</div>
      <footer><small>총 ${data.entries.length}개 · 설치됨 ${data.available.length}개 · 미설치 ${data.entries.length - data.available.length}개</small><button type="button" class="button primary compact" data-pack-apply ${data.available.length ? "" : "disabled"}>설치된 ${data.available.length}개 모델을 실험에 적용</button></footer>`;
    preview.querySelector("[data-pack-close]")?.addEventListener("click", () => { activePackId = ""; renderStarterPacks(); });
    preview.querySelector("[data-pack-apply]")?.addEventListener("click", () => {
      if (!data.available.length) return dashboard.toast?.("설치된 모델 없음", "이 실험 팩의 GGUF가 아직 어떤 Worker에도 설치되어 있지 않습니다.", "error");
      dashboard.setSelectedModels?.(data.available);
      location.hash = "experiment";
      dashboard.toast?.("실험 팩 적용", `${data.available.length}개 설치된 모델을 선택 순서대로 실험에 반영했습니다.`);
    });
  }

  function renderModelPagination(meta) {
    const info = dashboard.$?.("#modelPageInfo"); const buttons = dashboard.$?.("#modelPageButtons");
    if (!info || !buttons) return;
    info.textContent = meta.totalItems ? `${meta.startIndex + 1}–${meta.endIndex} / ${meta.totalItems}개` : "0개";
    buttons.innerHTML = `<button type="button" data-model-page="${meta.page - 1}" ${meta.page <= 1 ? "disabled" : ""} aria-label="이전 페이지">‹</button>${dashboard.paginationPages(meta.page, meta.totalPages).map(value => value === "…" ? `<span>…</span>` : `<button type="button" data-model-page="${value}" class="${value === meta.page ? "active" : ""}" aria-current="${value === meta.page ? "page" : "false"}">${value}</button>`).join("")}<button type="button" data-model-page="${meta.page + 1}" ${meta.page >= meta.totalPages ? "disabled" : ""} aria-label="다음 페이지">›</button>`;
  }

  function renderStarterPacks() {
    const root = dashboard.$?.("#modelStarterPacks");
    if (!root) return;
    const packs = dashboard.state.modelStarterPacks || [];
    if (activePackId && !packs.some(pack => pack.id === activePackId)) activePackId = "";
    root.innerHTML = packs.map(pack => `<button type="button" class="model-pack ${pack.id === activePackId ? "active" : ""}" data-model-pack="${dashboard.escapeHtml(pack.id)}" aria-expanded="${pack.id === activePackId}"><strong>${dashboard.escapeHtml(pack.label_ko || pack.id)}</strong><small>${(pack.model_ids || []).length} models · 눌러서 구성 확인</small></button>`).join("");
    root.querySelectorAll("[data-model-pack]").forEach(button => button.addEventListener("click", () => {
      const pack = packs.find(item => item.id === button.dataset.modelPack);
      activePackId = activePackId === pack?.id ? "" : (pack?.id || "");
      renderStarterPacks();
    }));
    renderPackPreview(packs.find(pack => pack.id === activePackId));
  }

  function render(models = dashboard.state.models) {
    const root = dashboard.$?.("#modelLibrary");
    const summary = dashboard.$?.("#modelLibrarySummary");
    if (!root || !summary) return;
    const query = (dashboard.$("#libraryModelSearch")?.value || "").trim().toLowerCase();
    const filteredRows = (models || []).filter(model => {
      const catalog = model.catalog || {};
      const matchesQuery = !query || [model.id, model.filename, model.quantization, catalog.display_name, catalog.vendor, catalog.family, catalog.license, catalog.recommendation_tier, ...(catalog.capability_tags || [])].join(" ").toLowerCase().includes(query);
      return matchesQuery && (activeFilter === "all" || tagsFor(model).has(activeFilter));
    });
    const rows = activeVendor && activeView === "models" ? filteredRows.filter(model => vendorName(model) === activeVendor) : filteredRows;
    const installed = rows.filter(model => (model.installed_nodes || []).length).length;
    const recommended = rows.filter(model => recommendationSummary(model.id).status === "recommended").length;
    const groups = groupByVendor(filteredRows);
    summary.innerHTML = `<span><strong>${rows.length}</strong> catalog records</span><span><strong>${groups.length}</strong> companies / organizations</span><span><strong>${installed}</strong> installed</span><span><strong>${recommended}</strong> smoke-verified recommendations</span>${activeVendor ? `<button type="button" class="vendor-filter-clear" id="clearVendorFilter">${dashboard.escapeHtml(activeVendor)} 필터 해제 ×</button>` : ""}`;
    renderStarterPacks();
    root.classList.toggle("vendor-view", activeView === "vendors");
    if (activeView === "vendors") {
      const page = dashboard.paginateItems(groups, modelCurrentPage, modelPageSize); modelCurrentPage = page.page; renderModelPagination(page);
      root.innerHTML = groups.length ? renderVendorGroups(page.items) : `<div class="model-library-empty">검색 조건에 맞는 회사 또는 모델이 없습니다.</div>`;
      root.querySelectorAll("[data-vendor-open]").forEach(button => button.addEventListener("click", () => {
        activeVendor = button.dataset.vendorOpen || "";
        activeView = "models";
        modelCurrentPage = 1;
        dashboard.$("#modelLibraryViewModes")?.querySelectorAll("[data-model-view]").forEach(item => { const active = item.dataset.modelView === activeView; item.classList.toggle("active", active); item.setAttribute("aria-pressed", String(active)); });
        render();
        root.scrollIntoView({ behavior: "smooth", block: "start" });
      }));
      return;
    }
    dashboard.$?.("#clearVendorFilter")?.addEventListener("click", () => { activeVendor = ""; modelCurrentPage = 1; render(); });
    const page = dashboard.paginateItems(rows, modelCurrentPage, modelPageSize); modelCurrentPage = page.page; renderModelPagination(page);
    if (!rows.length) { root.innerHTML = `<div class="model-library-empty">검색 조건에 맞는 모델이 없습니다.</div>`; return; }
    root.innerHTML = page.items.map(model => {
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
      const eligibility = catalog.download_eligibility || { eligible: false, reason_ko: "SOURCE LOCK REQUIRED" };
      const canDirect = Boolean(eligibility.eligible && targets.length);
      const installLabel = catalog.download_mode === "controller_authenticated" ? "Controller 인증 다운로드" : "Worker 직접 다운로드";
      const large = Number(catalog.parameters_total_b || catalog.parameter_count_b || 0) >= 14;
      const sourceRows = [["원본 모델", catalog.source_model_repo || "—"], ["GGUF source", catalog.gguf_repo || catalog.hf_repo || "—"], ["Revision", catalog.gguf_revision || catalog.hf_revision || "LOCK REQUIRED"], ["Artifact", catalog.gguf_filename || "LOCK REQUIRED"], ["SHA-256", catalog.sha256 || "LOCK REQUIRED"], ["License", catalog.license || "REVIEW REQUIRED"], ["Quantizer / converter", [catalog.quantized_by, catalog.converter].filter(Boolean).join(" / ") || "—"], ["Aggregate memory", catalog.minimum_aggregate_memory_mb ? `estimated ${Number(catalog.minimum_aggregate_memory_mb).toLocaleString()} MB` : "—"]];
      return `<article class="library-model-card" data-library-model="${dashboard.escapeHtml(model.id)}">
        <div class="library-model-head"><div><div class="model-badge-stack"><div class="model-status-row">${badge(recommendation.status)}<span class="model-tier">${dashboard.escapeHtml(String(catalog.recommendation_tier || "unclassified").toUpperCase())}</span></div>${specialBadges(catalog)}</div><h3 title="${dashboard.escapeHtml(model.id)}">${dashboard.escapeHtml(catalog.display_name || model.filename || model.id)}</h3><p>${dashboard.escapeHtml(catalog.summary_ko || catalog.description || "이 모델의 추가 설명이 카탈로그에 없습니다.")}</p></div><strong>${dashboard.utils.bytes(model.size_bytes || catalog.size_bytes || 0)}</strong></div>
        <div class="library-facts">${facts.map(item => `<span>${dashboard.escapeHtml(item)}</span>`).join("")}</div>
        <div class="library-runtime">${runtime.map(item => `<span>${dashboard.escapeHtml(item)}</span>`).join("")}</div>
        <div class="library-placement"><strong>WORKER INSTALLATION</strong><p>${installedNodes.length ? installedNodes.map(name => `<span class="worker-install">${dashboard.escapeHtml(name)}</span>`).join("") : "아직 설치된 Worker 없음"}</p><small>${dashboard.escapeHtml(fitLabel(recommendation.memory))} · ${recommendation.workers.length ? `적합 Worker ${recommendation.workers.join(", ")}` : "Worker smoke 확인 필요"}</small></div>
        ${detailList("추천 이유", recommendation.reasons_ko, "reasons")}${detailList("주의사항", recommendation.cautions_ko, "cautions")}
        <details class="library-source-details"><summary>상세 정보 · source / license / identity</summary><dl>${sourceRows.map(([key,value]) => `<dt>${dashboard.escapeHtml(key)}</dt><dd>${dashboard.escapeHtml(value)}</dd>`).join("")}</dl>${large ? `<p class="download-disabled-reason">대형 모델은 선택 Worker 전체에 복제하지 않습니다. intended RPC coordinator 한 대에 먼저 설치하세요. Estimated fit은 실행 보장이 아닙니다.</p>` : ""}</details>
        ${licenseConsentHtml(model)}
        ${progress ? `<div class="library-progress"><span>${dashboard.escapeHtml(progress.node || "worker")} · ${dashboard.escapeHtml(progress.state || "working")}</span><i><b style="width:${Math.max(0, Math.min(100, Number(progress.percent) || 0))}%"></b></i><strong>${Number.isFinite(Number(progress.percent)) ? `${Number(progress.percent).toFixed(1)}%` : "…"}</strong></div>` : ""}
        <div class="library-actions"><button type="button" class="button primary compact" data-model-install="${dashboard.escapeHtml(model.id)}" ${canDirect ? "" : "disabled"}>${installLabel}</button><button type="button" class="button ghost compact" data-model-sync="${dashboard.escapeHtml(model.id)}" ${canSync ? "" : "disabled"}>${canSync ? `Controller cache에서 ${targetMissing.length}대 동기화` : installedNodes.length ? "선택 Worker에 설치됨" : "Controller cache 없음"}</button><button type="button" class="button ghost compact danger-text" data-model-delete="${dashboard.escapeHtml(model.id)}" ${canDelete ? "" : "disabled"}>Worker에서 삭제</button></div>
        ${canDirect ? "" : `<small class="download-disabled-reason">${dashboard.escapeHtml(eligibility.reason_ko || "Exact source identity가 없어 직접 다운로드할 수 없습니다.")}</small>`}
      </article>`;
    }).join("");
    root.querySelectorAll("[data-model-sync]").forEach(button => button.addEventListener("click", () => sync(button.dataset.modelSync)));
    root.querySelectorAll("[data-model-install]").forEach(button => button.addEventListener("click", () => install(button.dataset.modelInstall)));
    root.querySelectorAll("[data-model-delete]").forEach(button => button.addEventListener("click", () => remove(button.dataset.modelDelete)));
    root.querySelectorAll("[data-license-check]").forEach(checkbox => checkbox.addEventListener("change", () => {
      const button = [...root.querySelectorAll("[data-license-accept]")].find(item => item.dataset.licenseAccept === checkbox.dataset.licenseCheck); if (button) button.disabled = !checkbox.checked;
    }));
    root.querySelectorAll("[data-license-accept]").forEach(button => button.addEventListener("click", () => acceptLicense(button.dataset.licenseAccept)));
    root.querySelectorAll("[data-license-revoke]").forEach(button => button.addEventListener("click", () => revokeLicense(button.dataset.licenseRevoke)));
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

  async function install(modelId) {
    const model = dashboard.state.models.find(item => item.id === modelId); const targets = workerTargets().filter(name => !((model?.installed_nodes || []).includes(name)));
    if (!targets.length) return dashboard.toast?.("다운로드 대상 없음", "설치할 선택 Worker가 없거나 이미 설치되어 있습니다.", "error");
    const large = Number(model?.catalog?.parameters_total_b || 0) >= 14;
    if (large && targets.length !== 1) return dashboard.toast?.("RPC coordinator 선택 필요", "14B 이상 모델은 intended coordinator Worker 한 대만 선택하세요.", "error");
    const authenticated = model?.catalog?.download_mode === "controller_authenticated";
    const flow = authenticated ? "Controller의 Hugging Face 계정으로 cache에 다운로드한 뒤 선택 Worker로 검증 동기화" : "선택 Worker에서 직접 다운로드";
    if (!confirm(`${targets.join(", ")}에 고정된 GGUF를 ${flow}하고 SHA-256을 검증합니다. 다운로드는 benchmark와 분리됩니다. 계속할까요?`)) return;
    const response = await dashboard.api(`/api/models/${modelApiPath(modelId)}/install`, { method: "POST", body: JSON.stringify({ nodes: targets, source: "direct", confirmed: true }) });
    dashboard.toast?.("모델 다운로드 시작", `${response.nodes?.length || targets.length}개 Worker에서 검증형 다운로드를 시작했습니다.`);
  }

  async function acceptLicense(modelId) {
    const model = dashboard.state.models.find(item => item.id === modelId); const status = model?.catalog?.license_acceptance || {};
    if (!status.required || !status.fingerprint) return dashboard.toast?.("약관 정보 없음", "현재 모델의 약관 identity를 다시 불러오세요.", "error");
    await dashboard.api(`/api/models/${modelApiPath(modelId)}/license-acceptance`, { method: "POST", body: JSON.stringify({ accepted: true, confirmed: true, license_fingerprint: status.fingerprint }) });
    dashboard.toast?.("모델 약관 동의 저장", "현재 라이선스와 source revision에 대한 동의를 이 프로젝트에 기록했습니다.");
    await refresh();
  }

  async function revokeLicense(modelId) {
    if (!confirm("이 프로젝트의 모델 라이선스 동의를 철회할까요? 이미 설치된 파일은 자동 삭제되지 않습니다.")) return;
    await dashboard.api(`/api/models/${modelApiPath(modelId)}/license-acceptance`, { method: "DELETE" });
    dashboard.toast?.("모델 약관 동의 철회", "새 설치는 다시 동의할 때까지 차단됩니다.");
    await refresh();
  }

  async function refreshHuggingFaceStatus() {
    const target = dashboard.$?.("#huggingfaceAccessStatus"); if (!target) return;
    const commandTarget = dashboard.$?.("#huggingFaceLoginCommand");
    target.textContent = "접근 권한 확인 중…";
    try {
      const status = await dashboard.api("/api/huggingface/status");
      if (commandTarget && status.login_command) commandTarget.textContent = status.login_command;
      target.textContent = status.verified ? `연결됨 · ${status.account || "authenticated account"}` : status.installed ? "로그인 필요 · 오른쪽 명령을 복사해 Controller 터미널에서 실행" : "Controller 패키지 설치 필요 · setup-controller 재실행";
    } catch (error) { target.textContent = `접근 확인 실패 · ${error.message}`; }
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
  dashboard.modelLibrary = { refresh, render, sync, install, remove, acceptLicense, revokeLicense, refreshHuggingFaceStatus, recordProgress, tagsFor, specialBadges, vendorName, groupByVendor, packPreviewData };
  document.addEventListener("DOMContentLoaded", () => {
    dashboard.$?.("#libraryModelSearch")?.addEventListener("input", () => { modelCurrentPage = 1; render(); });
    dashboard.$?.("#modelLibraryFilters")?.querySelectorAll("[data-model-filter]").forEach(button => button.addEventListener("click", () => { activeFilter = button.dataset.modelFilter || "all"; modelCurrentPage = 1; dashboard.$("#modelLibraryFilters").querySelectorAll("[data-model-filter]").forEach(item => item.classList.toggle("active", item === button)); render(); }));
    dashboard.$?.("#modelLibraryViewModes")?.querySelectorAll("[data-model-view]").forEach(button => button.addEventListener("click", () => {
      activeView = button.dataset.modelView || "models";
      if (activeView === "vendors") activeVendor = "";
      modelCurrentPage = 1;
      dashboard.$("#modelLibraryViewModes").querySelectorAll("[data-model-view]").forEach(item => { const active = item === button; item.classList.toggle("active", active); item.setAttribute("aria-pressed", String(active)); });
      render();
    }));
    dashboard.$?.("#modelPageSize")?.addEventListener("change", event => { modelPageSize = Number(event.currentTarget.value) || 10; modelCurrentPage = 1; render(); });
    dashboard.$?.("#modelPageButtons")?.addEventListener("click", event => {
      const button = event.target.closest("[data-model-page]"); if (!button || button.disabled) return;
      modelCurrentPage = Number(button.dataset.modelPage) || 1; render();
      dashboard.$?.("#modelLibrary")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    dashboard.$?.("#refreshModelsButton")?.addEventListener("click", async () => {
      try { await refresh(); dashboard.toast?.("모델 상태 갱신", "카탈로그와 Worker 인벤토리를 새로 읽었습니다."); }
      catch (error) { dashboard.toast?.("모델 상태 갱신 실패", error.message, "error"); }
    });
    dashboard.$?.("#refreshHuggingFaceButton")?.addEventListener("click", refreshHuggingFaceStatus);
    dashboard.$?.("#copyHuggingFaceLoginButton")?.addEventListener("click", async () => {
      const commandTarget = dashboard.$?.("#huggingFaceLoginCommand");
      const command = commandTarget?.textContent?.trim() || "";
      try {
        if (!command || command.includes("불러오는 중")) throw new Error("로그인 명령을 아직 불러오지 못했습니다. 접근 권한 확인을 먼저 눌러주세요.");
        if (typeof dashboard.copyText !== "function") throw new Error("복사 기능을 불러오지 못했습니다. 페이지를 새로고침하세요.");
        await dashboard.copyText(command, commandTarget, "Hugging Face 로그인 명령 복사됨");
      }
      catch (error) { dashboard.toast?.("명령 복사 실패", error.message, "error"); }
    });
    refreshHuggingFaceStatus();
  });
})();
