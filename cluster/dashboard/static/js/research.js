(function researchDashboardModule(global) {
  "use strict";

  const METRICS = {
    throughput_tokens_s: { label: "처리량", title: "조건별 클러스터 처리량", unit: "tok/s", better: "higher" },
    ttft_p50_s: { label: "TTFT p50", title: "조건별 첫 토큰 지연", unit: "s", better: "lower" },
    e2e_p95_s: { label: "E2E p95", title: "조건별 요청 완료 지연", unit: "s", better: "lower" },
    success_rate: { label: "성공률", title: "조건별 요청 성공률", unit: "%", better: "higher" },
  };
  const QUALITY_COLORS = { clean: "#718f17", warning: "#e57c38", degraded: "#e54b4b", unknown: "#8b908a" };
  const FILTER_KEYS = [
    "campaign_id", "model_lock", "platform", "node_count", "strategy",
    "runtime_fingerprint", "power_mode", "measurement_quality",
  ];
  const researchState = {
    campaigns: [], campaign: null, selectedCampaign: "",
    compare: { runs: [], filters: {}, legacy_run_count: 0 },
    readiness: null,
    filters: Object.fromEntries(FILTER_KEYS.map(key => [key, "all"])),
    metric: "throughput_tokens_s", baseline: "", loading: false, bound: false,
  };

  const apiSurface = () => global.ClusterDashboard || {};
  const esc = value => apiSurface().escapeHtml?.(value) ?? String(value ?? "");
  const finiteNumber = value => value !== null && value !== "" && Number.isFinite(Number(value));
  const short = (value, length = 16) => {
    const text = String(value || "—");
    return text.length > length ? `${text.slice(0, length - 1)}…` : text;
  };
  const timestamp = value => {
    const parsed = new Date(value || "");
    return Number.isNaN(parsed.getTime()) ? "시각 미확인" : parsed.toLocaleString("ko-KR");
  };
  const duration = seconds => {
    const value = Number(seconds || 0);
    if (!Number.isFinite(value) || value <= 0) return "—";
    if (value < 60) return `${Math.round(value)}초`;
    if (value < 3600) return `${Math.floor(value / 60)}분 ${Math.round(value % 60)}초`;
    return `${Math.floor(value / 3600)}시간 ${Math.round((value % 3600) / 60)}분`;
  };
  const bytes = value => {
    const number = Number(value || 0);
    if (!Number.isFinite(number) || number <= 0) return "—";
    return number >= 1024 ** 3 ? `${(number / 1024 ** 3).toFixed(1)} GiB` : `${(number / 1024 ** 2).toFixed(0)} MiB`;
  };
  const metricValue = (run, metric) => {
    const raw = run?.metrics?.[metric];
    if (!finiteNumber(raw)) return null;
    return metric === "success_rate" ? Number(raw) * 100 : Number(raw);
  };

  function filterCompareRuns(runs, filters) {
    return (runs || []).filter(run => FILTER_KEYS.every(key => {
      const selected = String(filters?.[key] ?? "all");
      return selected === "all" || String(run?.[key]) === selected;
    }));
  }

  function compareChartModel(runs, metric = "throughput_tokens_s", baselineId = "") {
    const config = METRICS[metric] || METRICS.throughput_tokens_s;
    const measurable = (runs || [])
      .filter(run => run.status === "completed" && finiteNumber(metricValue(run, metric)))
      .slice()
      .sort((left, right) => String(left.finished_at || left.started_at || "").localeCompare(String(right.finished_at || right.started_at || "")));
    const baseline = measurable.find(run => run.run_id === baselineId) || measurable[0] || null;
    const labels = measurable.map(run => short(run.run_id, 14));
    const qualities = ["clean", "warning", "degraded", "unknown"].filter(quality => measurable.some(run => run.measurement_quality === quality));
    const series = qualities.map(quality => ({
      label: quality.toUpperCase(),
      color: QUALITY_COLORS[quality],
      values: measurable.map(run => run.measurement_quality === quality ? metricValue(run, metric) : null),
      details: measurable.map(run => [
        run.model_lock !== "unlocked" ? run.model_lock : run.model_id,
        `${run.platform} · ${run.node_count} node`,
        run.strategy,
        run.runtime_fingerprint,
        run.power_mode,
        `${run.measurement_quality} · ${run.status}`,
      ].join(" · ")),
    }));
    return {
      type: "bar",
      title: config.title,
      subtitle: baseline ? `baseline ${baseline.run_id} · ${measurable.length} measured runs` : "표시할 완료 측정값 없음",
      xLabel: "Run",
      yLabel: config.label,
      unit: config.unit,
      strategy: measurable.length && new Set(measurable.map(run => run.strategy)).size === 1 ? measurable[0].strategy : "cross_run_compare",
      labels,
      series,
      runs: measurable,
      metric,
      baseline,
    };
  }

  function baselineRatio(run, baseline, metric) {
    const current = metricValue(run, metric);
    const base = metricValue(baseline, metric);
    if (!finiteNumber(current) || !finiteNumber(base) || Number(current) === 0 || Number(base) === 0) return null;
    return METRICS[metric]?.better === "lower" ? Number(base) / Number(current) : Number(current) / Number(base);
  }

  function spreadsheetCell(value) {
    let text = String(value ?? "");
    if (/^[=+\-@]/.test(text)) text = `'${text}`;
    return `"${text.replace(/"/g, '""')}"`;
  }

  function compareCsv(runs, metric, baselineId) {
    const baseline = (runs || []).find(run => run.run_id === baselineId) || (runs || []).find(run => run.status === "completed") || null;
    const header = ["run_id", "campaign_id", "model_lock", "model_id", "platform", "node_count", "strategy", "runtime_fingerprint", "power_mode", "measurement_quality", "status", metric, "baseline_ratio"];
    const rows = (runs || []).map(run => [
      run.run_id, run.campaign_id, run.model_lock, run.model_id, run.platform, run.node_count,
      run.strategy, run.runtime_fingerprint, run.power_mode, run.measurement_quality, run.status,
      metricValue(run, metric), baselineRatio(run, baseline, metric),
    ]);
    return [header, ...rows].map(row => row.map(spreadsheetCell).join(",")).join("\r\n") + "\r\n";
  }

  function renderCampaignSummary() {
    const target = document.querySelector("#campaignSummary");
    const list = document.querySelector("#campaignList");
    if (!target || !list) return;
    if (!researchState.campaigns.length) {
      target.innerHTML = `<div class="research-empty"><strong>아직 생성된 durable campaign이 없습니다.</strong><span>Phase 09 pilot이 반복 수와 실행 gate를 확정하면 이 화면에 cell과 결과 커버리지가 표시됩니다.</span></div>`;
      list.innerHTML = "";
      return;
    }
    const totals = researchState.campaigns.reduce((acc, campaign) => {
      acc.cells += campaign.coverage?.planned || 0;
      acc.completed += campaign.coverage?.completed || 0;
      acc.failed += campaign.coverage?.failed || 0;
      acc.running += campaign.coverage?.running || 0;
      return acc;
    }, { cells: 0, completed: 0, failed: 0, running: 0 });
    target.innerHTML = `<div><span>CAMPAIGNS</span><strong>${researchState.campaigns.length}</strong><small>durable manifests</small></div><div><span>TOTAL CELLS</span><strong>${totals.cells}</strong><small>planned</small></div><div><span>COMPLETED</span><strong>${totals.completed}</strong><small>linked results</small></div><div><span>RUNNING / FAILED</span><strong>${totals.running} / ${totals.failed}</strong><small>current outcomes</small></div>`;
    list.innerHTML = researchState.campaigns.map(campaign => `<button type="button" class="campaign-card ${researchState.selectedCampaign === campaign.campaign_id ? "active" : ""}" data-campaign-id="${esc(campaign.campaign_id)}"><span>${esc(campaign.status.toUpperCase())}</span><strong>${esc(campaign.campaign_id)}</strong><small>${esc(campaign.matrix_id)} · ${campaign.progress_pct}%</small><i><b style="width:${Math.max(0, Math.min(100, campaign.progress_pct))}%"></b></i><em>${campaign.coverage.completed}/${campaign.coverage.planned} cells · result ${campaign.result_coverage.linked_runs}/${campaign.result_coverage.planned_runs}</em></button>`).join("");
    list.querySelectorAll("[data-campaign-id]").forEach(button => button.addEventListener("click", () => selectCampaign(button.dataset.campaignId)));
  }

  function renderCampaignDetail() {
    const target = document.querySelector("#campaignDetail");
    if (!target) return;
    const campaign = researchState.campaign;
    if (!campaign) {
      target.innerHTML = `<div class="research-empty"><strong>캠페인을 선택하면 cell 커버리지와 drift를 표시합니다.</strong></div>`;
      return;
    }
    const coverage = campaign.coverage || {};
    const drift = campaign.current_drift || [];
    const cells = campaign.cells || [];
    target.innerHTML = `<div class="campaign-detail-head"><div><span>${esc(campaign.phase.toUpperCase())}</span><h3>${esc(campaign.campaign_id)}</h3><p>${timestamp(campaign.updated_at)} · 반복 ${campaign.repeat_progress.completed}/${campaign.repeat_progress.total}</p></div><b class="research-eligibility ${campaign.formal_eligible ? "ready" : "blocked"}">${campaign.formal_eligible ? "FORMAL ELIGIBLE" : "DRIFT BLOCKED"}</b></div>
      <div class="campaign-stat-grid"><div><span>PENDING</span><strong>${coverage.pending || 0}</strong></div><div><span>RUNNING</span><strong>${coverage.running || 0}</strong></div><div><span>FAILED</span><strong>${coverage.failed || 0}</strong></div><div><span>REMAINING</span><strong>${campaign.estimated_remaining?.cells || 0}</strong><small>${duration(campaign.estimated_remaining?.runtime_seconds)} · ${bytes(campaign.estimated_remaining?.storage_bytes)}</small></div></div>
      <div class="campaign-drift ${drift.length ? "warning" : "clean"}"><strong>CURRENT DRIFT · ${drift.length}</strong>${drift.length ? `<ul>${drift.map(item => `<li>${esc(item.code || "UNKNOWN_DRIFT")}${item.node ? ` · ${esc(item.node)}` : ""}</li>`).join("")}</ul>` : `<span>저장된 현재 drift 없음</span>`}</div>
      <div class="table-wrap campaign-cell-table"><table><thead><tr><th>#</th><th>REPEAT / CELL</th><th>MODEL / PROMPT</th><th>TOPOLOGY</th><th>QUALITY</th><th>STATUS / RUN</th></tr></thead><tbody>${cells.slice(0, 100).map(cell => `<tr><td>${cell.order_index || "—"}</td><td><strong>R${cell.repeat_index || "?"}</strong><br><small>${esc(short(cell.campaign_cell_id, 28))}</small></td><td>${esc(short(cell.model_lock_key, 26))}<br><small>${esc(cell.prompt_id || "—")}</small></td><td>${esc(cell.strategy || "—")}<br><small>${esc((cell.node_set || []).join(", "))}</small></td><td><span class="quality-badge ${esc(cell.measurement_quality || "unknown")}">${esc((cell.measurement_quality || "unknown").toUpperCase())}</span></td><td><span class="run-status ${esc(cell.status || "unknown")}">${esc((cell.status || "unknown").toUpperCase())}</span><br><small>${esc(cell.run_id || cell.failure_code || "—")}</small></td></tr>`).join("")}</tbody></table></div>${cells.length > 100 ? `<p class="research-limit-note">첫 100개 cell만 표시 · 전체 ${cells.length}개는 manifest에 보존</p>` : ""}`;
  }

  async function selectCampaign(campaignId) {
    if (!campaignId) return;
    researchState.selectedCampaign = campaignId;
    renderCampaignSummary();
    try {
      researchState.campaign = await apiSurface().api(`/api/campaigns/${encodeURIComponent(campaignId)}`);
      renderCampaignDetail();
    } catch (error) {
      apiSurface().toast?.("캠페인 조회 실패", error.message, "error");
    }
  }

  function populateCompareFilters() {
    const mapping = {
      campaign_id: "campaigns", model_lock: "model_locks", platform: "platforms",
      node_count: "node_counts", strategy: "strategies", runtime_fingerprint: "runtime_fingerprints",
      power_mode: "power_modes", measurement_quality: "measurement_qualities",
    };
    document.querySelectorAll("[data-compare-filter]").forEach(select => {
      const key = select.dataset.compareFilter;
      const values = researchState.compare.filters?.[mapping[key]] || [];
      const current = researchState.filters[key] || "all";
      select.innerHTML = `<option value="all">전체</option>${values.map(value => `<option value="${esc(value)}">${esc(value)}</option>`).join("")}`;
      select.value = [...select.options].some(option => option.value === current) ? current : "all";
      researchState.filters[key] = select.value;
    });
  }

  function renderCompare() {
    const runs = filterCompareRuns(researchState.compare.runs, researchState.filters);
    const metric = researchState.metric;
    const model = compareChartModel(runs, metric, researchState.baseline);
    const baselineSelect = document.querySelector("#compareBaseline");
    if (baselineSelect) {
      const previous = researchState.baseline;
      const candidates = runs.filter(run => run.status === "completed" && finiteNumber(metricValue(run, metric)));
      baselineSelect.innerHTML = candidates.map(run => `<option value="${esc(run.run_id)}">${esc(short(run.run_id, 22))} · ${esc(short(run.model_lock !== "unlocked" ? run.model_lock : run.model_id, 26))}</option>`).join("") || `<option value="">비교 가능한 run 없음</option>`;
      baselineSelect.value = candidates.some(run => run.run_id === previous) ? previous : candidates[0]?.run_id || "";
      researchState.baseline = baselineSelect.value;
    }
    const finalModel = compareChartModel(runs, metric, researchState.baseline);
    const canvas = document.querySelector("#researchCompareChart");
    if (canvas) {
      if (finalModel.runs.length) global.setChartModel?.("researchCompareChart", finalModel);
      else global.drawEmptyChart?.(canvas, "필터에 맞는 완료 측정값이 없습니다");
    }
    const config = METRICS[metric];
    const title = document.querySelector("#researchCompareTitle");
    if (title) title.textContent = config.title;
    const column = document.querySelector("#compareMetricColumn");
    if (column) column.textContent = config.label;
    const coverage = document.querySelector("#compareCoverage");
    if (coverage) coverage.textContent = `${runs.length} runs · ${finalModel.runs.length} measured`;
    const legacy = runs.filter(run => run.legacy_fallback).length;
    const note = document.querySelector("#compareLegacyNote");
    if (note) note.textContent = legacy ? `LEGACY FALLBACK ${legacy}` : "FORMAL IDENTITY AVAILABLE";
    const baseline = runs.find(run => run.run_id === researchState.baseline) || null;
    const table = document.querySelector("#compareRunsTable");
    if (table) table.innerHTML = runs.length ? runs.slice(0, 200).map(run => {
      const value = metricValue(run, metric);
      const ratio = baselineRatio(run, baseline, metric);
      return `<tr><td><strong>${esc(short(run.run_id, 20))}</strong><br><small>${esc(run.campaign_id)}${run.legacy_fallback ? " · LEGACY" : ""}</small></td><td>${esc(short(run.model_lock !== "unlocked" ? run.model_lock : run.model_id, 30))}</td><td>${esc(run.platform)} · ${run.node_count}<br><small>${esc((run.nodes || []).join(", "))}</small></td><td>${esc(run.strategy)}</td><td><small>${esc(short(run.runtime_fingerprint, 24))}<br>${esc(run.power_mode)}</small></td><td><span class="quality-badge ${esc(run.measurement_quality)}">${esc(run.measurement_quality.toUpperCase())}</span><br><span class="run-status ${esc(run.status)}">${esc(run.status.toUpperCase())}</span></td><td><strong>${finiteNumber(value) ? `${Number(value).toFixed(config.unit === "%" ? 1 : 2)} ${config.unit}` : "—"}</strong></td><td>${ratio ? `${ratio.toFixed(2)}×` : "—"}${run.run_id === researchState.baseline ? "<br><small>BASELINE</small>" : ""}</td></tr>`;
    }).join("") : `<tr><td colspan="8" class="empty-cell">필터에 맞는 실행 없음</td></tr>`;
  }

  function renderReadiness() {
    const value = researchState.readiness;
    const eligibility = document.querySelector("#researchEligibility");
    if (!value) {
      if (eligibility) eligibility.textContent = "UNAVAILABLE";
      return;
    }
    if (eligibility) {
      eligibility.className = `research-eligibility ${value.eligible ? "ready" : "blocked"}`;
      eligibility.textContent = value.eligible ? "FORMAL READY" : "FORMAL BLOCKED";
    }
    const workers = value.workers || [];
    const matches = key => workers.filter(worker => worker[key] === "match").length;
    const instrumentation = workers.filter(worker => worker.instrumentation_ready).length;
    const summary = document.querySelector("#readinessSummary");
    if (summary) summary.innerHTML = `<div><span>APPROVED MODELS</span><strong>${value.model_counts.approved} / ${value.model_counts.total}</strong><small>${value.license_blockers.length} license blocker</small></div><div><span>WORKER SOURCE</span><strong>${matches("source_identity")} / ${workers.length}</strong><small>locked commit match</small></div><div><span>RUNTIME</span><strong>${matches("runtime_identity")} / ${workers.length}</strong><small>fingerprint match</small></div><div><span>INSTRUMENTATION</span><strong>${instrumentation} / ${workers.length}</strong><small>API + backend + environment</small></div>`;
    const models = document.querySelector("#approvedModelList");
    if (models) models.innerHTML = `${(value.approved_models || []).map(model => `<article><span>APPROVED</span><strong>${esc(model.display_name || model.model_key)}</strong><code>${esc(short(model.sha256, 28))}</code><small>${model.verified_workers.length} workers verified</small></article>`).join("") || `<div class="research-empty"><strong>승인된 모델 없음</strong></div>`}${(value.license_blockers || []).map(model => `<article class="blocked"><span>LICENSE BLOCKER</span><strong>${esc(model.display_name || model.model_key)}</strong><small>${esc(model.license || "acceptance required")}</small></article>`).join("")}`;
    const workerList = document.querySelector("#researchWorkerList");
    if (workerList) workerList.innerHTML = workers.map(worker => `<article class="research-worker-card ${worker.instrumentation_ready ? "ready" : "blocked"}"><header><div><span>${esc(worker.platform)}</span><strong>${esc(worker.node)}</strong></div><b>${worker.online ? "ONLINE" : "OFFLINE"}</b></header><dl><div><dt>SOURCE</dt><dd class="${esc(worker.source_identity)}">${esc(worker.source_identity.toUpperCase())}<small>${esc(short(worker.observed_source_commit || "unobserved", 14))}</small></dd></div><div><dt>RUNTIME</dt><dd class="${esc(worker.runtime_identity)}">${esc(worker.runtime_identity.toUpperCase())}<small>${esc(short(worker.observed_runtime_fingerprint || "unobserved", 16))}</small></dd></div><div><dt>POWER</dt><dd class="${esc(worker.power_identity)}">${esc(worker.power_identity.toUpperCase())}<small>${esc(worker.observed_power_mode || "unobserved")}</small></dd></div><div><dt>MEASUREMENT</dt><dd class="${worker.instrumentation_ready ? "match" : "drift"}">${worker.instrumentation_ready ? "READY" : "NOT READY"}<small>backend ${worker.backend_verified ? "verified" : "unverified"}</small></dd></div></dl></article>`).join("");
    const blockers = document.querySelector("#researchBlockers");
    if (blockers) {
      const issues = value.blocking_issues || [];
      const executionAllowed = value.execution_gate?.formal_execution_allowed === true;
      const phaseBlockers = value.execution_gate?.blocking_phases || [];
      const requirementBlockers = value.execution_gate?.blocking_requirements || [];
      const gateDetail = requirementBlockers.length
        ? requirementBlockers.join(", ")
        : phaseBlockers.length
          ? `phase ${phaseBlockers.join(", ")}`
          : executionAllowed ? "open" : "closed";
      blockers.className = `research-blockers ${issues.length ? "blocked" : "ready"}`;
      blockers.innerHTML = `<div><span>${issues.length ? "FORMAL EXECUTION BLOCKERS" : "FORMAL GATE"}</span><strong>${issues.length ? `${issues.length}개 조치 필요` : "현재 blocker 없음"}</strong><small>Controller source ${esc(value.controller_source.status)} · execution ${executionAllowed ? "open" : "blocked"} · ${esc(gateDetail)}</small></div>${issues.length ? `<ul>${issues.map(issue => `<li><code>${esc(issue.code)}</code>${issue.node ? `<span>${esc(issue.node)}</span>` : ""}${issue.model_key ? `<span>${esc(issue.model_key)}</span>` : ""}</li>`).join("")}</ul>` : ""}`;
    }
  }

  async function load() {
    if (researchState.loading) return;
    researchState.loading = true;
    try {
      const [campaigns, compare, readiness] = await Promise.all([
        apiSurface().api("/api/campaigns"),
        apiSurface().api("/api/research/compare"),
        apiSurface().api("/api/research/readiness"),
      ]);
      researchState.campaigns = campaigns.campaigns || [];
      researchState.compare = compare || { runs: [], filters: {} };
      researchState.readiness = readiness;
      if (!researchState.selectedCampaign && researchState.campaigns.length) researchState.selectedCampaign = researchState.campaigns[0].campaign_id;
      populateCompareFilters();
      renderCampaignSummary();
      renderCompare();
      renderReadiness();
      if (researchState.selectedCampaign && researchState.campaign?.campaign_id !== researchState.selectedCampaign) await selectCampaign(researchState.selectedCampaign);
    } catch (error) {
      apiSurface().toast?.("연구 현황 조회 실패", error.message, "error");
    } finally {
      researchState.loading = false;
    }
  }

  function downloadCsv() {
    const runs = filterCompareRuns(researchState.compare.runs, researchState.filters);
    const content = compareCsv(runs, researchState.metric, researchState.baseline);
    const blob = new Blob(["\ufeff", content], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `llm-cluster-cross-run-${researchState.metric}.csv`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  function bind() {
    if (researchState.bound) return;
    researchState.bound = true;
    document.querySelector("#refreshResearchButton")?.addEventListener("click", load);
    document.querySelectorAll("[data-compare-filter]").forEach(select => select.addEventListener("change", event => {
      researchState.filters[event.currentTarget.dataset.compareFilter] = event.currentTarget.value;
      renderCompare();
    }));
    document.querySelector("#compareMetric")?.addEventListener("change", event => {
      researchState.metric = event.currentTarget.value;
      renderCompare();
    });
    document.querySelector("#compareBaseline")?.addEventListener("change", event => {
      researchState.baseline = event.currentTarget.value;
      renderCompare();
    });
    document.querySelector("#resetCompareFilters")?.addEventListener("click", () => {
      FILTER_KEYS.forEach(key => { researchState.filters[key] = "all"; });
      populateCompareFilters();
      renderCompare();
    });
    document.querySelector("#researchCompareCsv")?.addEventListener("click", downloadCsv);
  }

  global.ClusterDashboard = Object.assign(global.ClusterDashboard || {}, {
    research: {
      state: researchState,
      load,
      bind,
      renderCompare,
      filterCompareRuns,
      compareChartModel,
      compareCsv,
      baselineRatio,
    },
  });
})(globalThis);
