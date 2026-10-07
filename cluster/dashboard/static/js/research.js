(function researchDashboardModule(global) {
  "use strict";

  const lab = global.ClusterDashboard.lab;
  const METRICS = lab.METRICS;
  const QUALITY_COLORS = { clean: "#2458A6", warning: "#946200", degraded: "#AD3434", unknown: "#646F7B" };
  const FILTER_KEYS = [
    "campaign_id", "model_lock", "platform", "node_count", "strategy",
    "runtime_fingerprint", "power_mode", "measurement_quality",
  ];
  const researchState = {
    campaigns: [], campaign: null, selectedCampaign: "",
    compare: { runs: [], filters: {}, legacy_run_count: 0 },
    readiness: null,
    filters: Object.fromEntries(FILTER_KEYS.map(key => [key, "all"])),
    metric: "throughput_tokens_s", baseline: "", controlled: [], matrixGroups: [], selectionVersion: 0, error: "", loading: false, actionPending: false, bound: false,
  };

  const apiSurface = () => global.ClusterDashboard || {};
  const esc = value => apiSurface().escapeHtml?.(value) ?? String(value ?? "");
  const finiteNumber = value => typeof value === "number" && Number.isFinite(value);
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
  const metricValue = lab.metricValue;

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
    const baseline = measurable.find(run => run.run_id === baselineId) || null;
    const labels = measurable.map(run => `${run.run_id === baselineId ? "★ " : ""}${short(run.run_id, 14)}`);
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
        `Provenance ${run.provenance || "UNKNOWN"} · Phase ${run.experiment_type || "UNKNOWN"}`,
      ].join(" · ")),
    }));
    return {
      type: "bar",
      title: config.title,
      subtitle: `${baseline ? `baseline ${baseline.run_id}` : "baseline unselected"} · ${measurable.length} measured runs · descriptive values; comparability checked separately`,
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

  function baselineRatio(run, baseline, metric, controlled = researchState.controlled) {
    return lab.baselineRatio(run, baseline, metric, controlled);
  }

  function spreadsheetCell(value) {
    let text = String(value ?? "");
    if (/^[=+\-@]/.test(text)) text = `'${text}`;
    return `"${text.replace(/"/g, '""')}"`;
  }

  function compareCsv(runs, metric, baselineId) {
    const baseline = (runs || []).find(run => run.run_id === baselineId) || null;
    const header = ["run_id", "campaign_id", "model_lock", "model_id", "platform", "node_count", "strategy", "runtime_fingerprint", "power_mode", "measurement_quality", "status", metric, "baseline_ratio", "change", "change_unit", "comparability", "provenance", "research_phase"];
    const rows = (runs || []).map(run => [
      run.run_id, run.campaign_id, run.model_lock, run.model_id, run.platform, run.node_count,
      run.strategy, run.runtime_fingerprint, run.power_mode, run.measurement_quality, run.status,
      metricValue(run, metric), baselineRatio(run, baseline, metric), lab.baselineChange(run, baseline, metric, researchState.controlled)?.value, lab.baselineChange(run, baseline, metric, researchState.controlled)?.unit, lab.comparisonEvidence(run, baseline, metric, researchState.controlled).reasons.join("; "), run.provenance, run.experiment_type,
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
    const status = String(campaign.status || "unknown");
    const executionAllowed = researchState.readiness?.execution_gate?.formal_execution_allowed === true;
    const gateTitle = executionAllowed ? "" : "formal execution gate가 닫혀 있습니다";
    const disabled = researchState.actionPending ? "disabled" : "";
    const gatedDisabled = researchState.actionPending || !executionAllowed ? "disabled" : "";
    const controls = [];
    if (status === "ready") controls.push(`<button type="button" class="button primary compact" data-campaign-action="start" ${gatedDisabled} title="${esc(gateTitle)}">Start</button>`);
    if (status === "running") controls.push(`<button type="button" class="button ghost compact" data-campaign-action="pause" ${disabled}>Pause</button>`);
    if (status === "paused") controls.push(`<button type="button" class="button primary compact" data-campaign-action="resume" ${gatedDisabled} title="${esc(gateTitle)}">Resume</button>`);
    if (["ready", "running", "paused"].includes(status)) controls.push(`<button type="button" class="button ghost compact danger-text" data-campaign-action="cancel" ${disabled}>Cancel</button>`);
    target.innerHTML = `<div class="campaign-detail-head"><div><span>${esc(String(campaign.phase || "unknown").toUpperCase())}</span><h3>${esc(campaign.campaign_id)}</h3><p>${timestamp(campaign.updated_at)} · 반복 ${campaign.repeat_progress.completed}/${campaign.repeat_progress.total}</p></div><b class="research-eligibility ${campaign.formal_eligible ? "ready" : "blocked"}">${campaign.formal_eligible ? "DRIFT CLEAR · GATE 별도" : "DRIFT BLOCKED"}</b></div>
      <div class="campaign-controls"><div>${controls.join("") || `<span>종료된 campaign입니다.</span>`}</div><small>Start, Resume, Retry는 formal execution gate가 열려 있을 때만 가능합니다.</small></div>
      <div class="campaign-stat-grid"><div><span>PENDING</span><strong>${coverage.pending || 0}</strong></div><div><span>RUNNING</span><strong>${coverage.running || 0}</strong></div><div><span>FAILED</span><strong>${coverage.failed || 0}</strong></div><div><span>REMAINING</span><strong>${campaign.estimated_remaining?.cells || 0}</strong><small>${duration(campaign.estimated_remaining?.runtime_seconds)} · ${bytes(campaign.estimated_remaining?.storage_bytes)}</small></div></div>
      <div class="campaign-drift ${drift.length ? "warning" : "clean"}"><strong>CURRENT DRIFT · ${drift.length}</strong>${drift.length ? `<ul>${drift.map(item => `<li>${esc(item.code || "UNKNOWN_DRIFT")}${item.node ? ` · ${esc(item.node)}` : ""}</li>`).join("")}</ul>` : `<span>저장된 현재 drift 없음</span>`}</div>
      <div class="table-wrap campaign-cell-table"><table><thead><tr><th>#</th><th>REPEAT / CELL</th><th>MODEL / PROMPT</th><th>TOPOLOGY</th><th>QUALITY</th><th>STATUS / RUN</th><th>CONTROL</th></tr></thead><tbody>${cells.slice(0, 100).map(cell => `<tr><td>${cell.order_index || "—"}</td><td><strong>R${cell.repeat_index || "?"}</strong><br><small>${esc(short(cell.campaign_cell_id, 28))}</small></td><td>${esc(short(cell.model_lock_key, 26))}<br><small>${esc(cell.prompt_id || "—")}</small></td><td>${esc(cell.strategy || "—")}<br><small>${esc((cell.node_set || []).join(", "))}</small></td><td><span class="quality-badge ${esc(cell.measurement_quality || "unknown")}">${esc((cell.measurement_quality || "unknown").toUpperCase())}</span></td><td><span class="run-status ${esc(cell.status || "unknown")}">${esc((cell.status || "unknown").toUpperCase())}</span><br><small>${esc(cell.run_id || cell.failure_code || "—")}</small></td><td>${["failed", "cancelled"].includes(cell.status) ? `<button type="button" class="button ghost compact" data-campaign-action="retry" data-campaign-cell-id="${esc(cell.campaign_cell_id)}" ${gatedDisabled} title="${esc(gateTitle)}">Retry</button>` : "—"}</td></tr>`).join("")}</tbody></table></div>${cells.length > 100 ? `<p class="research-limit-note">첫 100개 cell만 표시 · 전체 ${cells.length}개는 manifest에 보존</p>` : ""}`;
  }

  async function controlCampaign(action, campaignCellId = "") {
    const campaignId = researchState.campaign?.campaign_id;
    if (!campaignId || researchState.actionPending) return;
    if (["start", "resume", "retry"].includes(action) && researchState.readiness?.execution_gate?.formal_execution_allowed !== true) return apiSurface().toast?.("Formal gate blocked", "연구 준비도와 gate를 확인하세요.", "error");
    let body = { confirmed: true };
    let path = `/api/campaigns/${encodeURIComponent(campaignId)}/${action}`;
    if (action === "retry") {
      const reason = prompt("이 cell을 다시 실행하는 이유를 입력하세요.");
      if (!reason?.trim()) return;
      body = { ...body, reason: reason.trim() };
      path = `/api/campaigns/${encodeURIComponent(campaignId)}/cells/${encodeURIComponent(campaignCellId)}/retry`;
    } else if (!confirm(`${campaignId} campaign에 ${action.toUpperCase()} 명령을 적용할까요?`)) {
      return;
    }
    const selectionVersion = researchState.selectionVersion;
    researchState.actionPending = true;
    renderCampaignDetail();
    try {
      const updated = await apiSurface().api(path, { method: "POST", body });
      if (researchState.selectedCampaign === campaignId && selectionVersion === researchState.selectionVersion) researchState.campaign = updated;
      apiSurface().toast?.("Campaign 제어 반영", `${campaignId} · ${action}`);
      renderCampaignDetail();
      const campaigns = await apiSurface().api("/api/campaigns");
      researchState.campaigns = campaigns.campaigns || [];
      renderCampaignSummary();
    } catch (error) {
      apiSurface().toast?.("Campaign 제어 실패", error.message, "error");
    } finally {
      researchState.actionPending = false;
      renderCampaignDetail();
    }
  }

  async function selectCampaign(campaignId) {
    if (!campaignId) return;
    researchState.selectedCampaign = campaignId;
    const version = ++researchState.selectionVersion;
    researchState.campaign = null;
    renderCampaignSummary();
    renderCampaignDetail();
    renderMatrix();
    try {
      const campaign = await apiSurface().api(`/api/campaigns/${encodeURIComponent(campaignId)}`);
      if (version !== researchState.selectionVersion) return;
      researchState.campaign = campaign;
      researchState.error = "";
      renderCampaignDetail();
      renderMatrix();
    } catch (error) {
      if (version !== researchState.selectionVersion) return;
      researchState.error = error.message;
      renderMatrix();
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

  function renderContext(run) {
    const target = document.querySelector("#labContext");
    if (!target) return;
    const fields = [["Model", run?.model_id || run?.model_lock], ["Hash", run?.model_sha256], ["Runtime", run?.runtime_fingerprint], ["Source", run?.source_fingerprint], ["Platform", run?.platform], ["Power", run?.power_mode], ["Strategy", run?.strategy], ["Provenance", run?.provenance], ["Phase", run?.experiment_type]];
    target.innerHTML = fields.map(([label, value]) => `<span title="${esc(value || "UNKNOWN")}"><b>${label}</b> ${esc(short(value || "UNKNOWN", 28))}</span>`).join("");
  }

  function renderMatrix() {
    const errorState = document.querySelector("#researchDataState");
    if (errorState) { errorState.hidden = !researchState.error; errorState.textContent = researchState.error; }
    const campaign = researchState.campaign;
    const selector = document.querySelector("#overviewCampaignSelect");
    if (selector) {
      selector.innerHTML = researchState.campaigns.length ? researchState.campaigns.map(item => `<option value="${esc(item.campaign_id)}">${esc(item.campaign_id)} · ${esc(item.status)}</option>`).join("") : '<option value="">저장된 campaign 없음</option>';
      selector.value = researchState.selectedCampaign;
    }
    const allowed = researchState.readiness?.execution_gate?.formal_execution_allowed;
    const gate = document.querySelector("#overviewGate");
    if (gate) { gate.textContent = allowed === true ? "FORMAL GATE OPEN" : allowed === false ? "FORMAL BLOCKED" : "GATE UNKNOWN"; gate.className = `research-eligibility ${allowed === true ? "ready" : "blocked"}`; }
    const targets = [document.querySelector("#overviewMatrix"), document.querySelector("#campaignMatrix")].filter(Boolean);
    if (!campaign) {
      const message = researchState.error ? `조회 실패 · ${esc(researchState.error)}` : researchState.selectedCampaign ? "저장된 plan을 불러오는 중입니다." : "저장된 campaign이 없습니다.";
      targets.forEach(target => { target.innerHTML = `<div class="research-empty"><strong>${message}</strong><span>계획 조건이 없으면 Matrix를 채우지 않습니다. Worker와 모델을 준비하고 실험을 설계하세요.</span><div><a class="button ghost compact" href="#nodes">Worker 준비</a> <a class="button primary compact" href="#experiment">실험 설계</a></div></div>`; });
      return;
    }
    const matrix = lab.experimentMatrix(campaign.cells);
    researchState.matrixGroups = [];
    const symbols = { completed: '✓', planned: '○', running: '▶', failed: '×', cancelled: '■', missing: '?', unknown: '?', excluded: '−' };
    const rows = matrix.rows.map(row => `<tr><th scope="row"><strong>${esc(row.model)}</strong><span>${esc(row.strategy)}</span><small>Prompt ${esc(row.prompt)}</small></th>${matrix.columns.map(column => {
      const cells = matrix.groups.get(JSON.stringify([row.key, column.key]));
      if (!cells) return '<td class="not-planned">—<small>Not planned</small></td>';
      const index = researchState.matrixGroups.push(cells) - 1;
      const counts = cells.reduce((acc, cell) => { const key = lab.cellState(cell); acc[key] = (acc[key] || 0) + 1; return acc; }, {});
      const state = ['failed', 'missing', 'running', 'cancelled', 'planned', 'completed'].find(key => counts[key]) || 'unknown';
      const qualities = [...new Set(cells.map(cell => cell.measurement_quality || 'unknown'))];
      return `<td><button type="button" class="matrix-cell ${state}" data-matrix-cell="${index}"><strong>${Object.entries(counts).map(([key, count]) => `${symbols[key] || '?'} ${esc(key)} ${count}`).join(' · ')}</strong><span>Repeat ${cells.length} · ${esc(campaign.experiment_type || campaign.phase || 'UNKNOWN')}</span><small>Quality ${esc(qualities.join(' / '))}</small></button></td>`;
    }).join('')}</tr>`).join('');
    const content = matrix.rows.length ? `<div class="matrix-caption"><strong>${esc(campaign.campaign_id)}</strong><span>${esc(campaign.status)} · Phase ${esc(campaign.experiment_type || campaign.phase || 'UNKNOWN')} · ${campaign.cells.length} planned repetitions · saved manifest</span></div><div class="table-wrap"><table class="matrix-table"><caption>Experiment Matrix · cell 선택은 조회만 수행합니다</caption><thead><tr><th scope="col">Model / strategy / prompt</th>${matrix.columns.map(column => `<th scope="col">${esc(column.platform)}<span>${column.count} Workers</span><small>${esc(column.nodes.join(', ') || 'UNKNOWN')}</small></th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div><div class="matrix-legend">✓ Completed · ○ Planned / not run · ▶ Running · × Failed · ■ Cancelled · ? Missing result · — Not planned</div>` : '<div class="research-empty"><strong>저장된 plan에 cell이 없습니다.</strong></div>';
    targets.forEach(target => { target.innerHTML = content; });
    if (location.hash !== '#compare') renderContext({ model_lock: new Set(matrix.rows.map(row => row.model)).size === 1 ? matrix.rows[0].model : 'Multiple models', experiment_type: campaign.experiment_type || campaign.phase, provenance: 'saved manifest' });
  }

  function inspectMatrix(index) {
    const cells = researchState.matrixGroups[index];
    const dialog = document.querySelector('#matrixInspectDialog');
    if (!cells || !dialog) return;
    const content = document.querySelector('#matrixInspectContent');
    content.innerHTML = cells.map(cell => {
      const ids = [...new Set([cell.run_id, ...(cell.attempts || []).map(attempt => attempt.run_id)].filter(Boolean))];
      const runs = ids.map(id => researchState.compare.runs?.find(run => run.run_id === id));
      return `<article class="matrix-inspect-cell"><h3>${esc(cell.campaign_cell_id)}</h3><dl><div><dt>조건</dt><dd>${esc(cell.platform_cohort)} · ${esc(cell.strategy)} · ${esc((cell.node_set || []).join(', '))}</dd></div><div><dt>모델 / 입력</dt><dd>${esc(cell.model_lock_key)} · ${esc(cell.prompt_id)}</dd></div><div><dt>Execution</dt><dd>${esc(lab.cellState(cell))} · Repeat ${esc(cell.repeat_index)}</dd></div><div><dt>Quality</dt><dd>${esc(cell.measurement_quality || 'UNKNOWN')}</dd></div><div><dt>Phase / provenance</dt><dd>${esc(researchState.campaign.experiment_type || researchState.campaign.phase)} · saved manifest</dd></div></dl><p>연결된 run ${ids.length ? ids.map(id => `<code>${esc(id)}</code>`).join(', ') : '없음 · 미실행 또는 missing result'}</p>${runs.filter(Boolean).map(run => `<p>Physical ${metricValue(run, 'throughput_tokens_s') ?? 'N/A'} tok/s · Effective ${metricValue(run, 'effective_user_tokens_per_s') ?? 'N/A'} tok/s · ${esc(run.provenance || 'UNKNOWN')}</p>`).join('')}</article>`;
    }).join('');
    dialog.showModal();
  }

  function renderCompare() {
    const runs = filterCompareRuns(researchState.compare.runs, researchState.filters);
    const metric = researchState.metric;
    const baselineSelect = document.querySelector("#compareBaseline");
    const previous = researchState.baseline;
    const candidates = runs.filter(run => run.status === "completed" && finiteNumber(metricValue(run, metric)));
    if (baselineSelect) {
      const absent = previous && !candidates.some(run => run.run_id === previous);
      baselineSelect.innerHTML = `<option value="">Baseline 선택 필요</option>${absent ? `<option value="${esc(previous)}">${esc(previous)} · 필터 밖 / 측정값 없음</option>` : ''}` + candidates.map(run => `<option value="${esc(run.run_id)}">${esc(run.run_id)} · ${esc(short(run.model_lock !== "unlocked" ? run.model_lock : run.model_id, 26))}</option>`).join('');
      baselineSelect.value = previous;
    }
    const baseline = candidates.find(run => run.run_id === previous) || null;
    const finalModel = compareChartModel(runs, metric, baseline?.run_id || '');
    const canvas = document.querySelector("#researchCompareChart");
    if (canvas) {
      if (finalModel.runs.length) global.setChartModel?.("researchCompareChart", finalModel);
      else { apiSurface().state?.chartModels?.delete('researchCompareChart'); global.drawEmptyChart?.(canvas, "필터에 맞는 완료 측정값이 없습니다"); }
    }
    const config = METRICS[metric];
    const title = document.querySelector("#researchCompareTitle");
    if (title) title.textContent = config.title;
    const column = document.querySelector("#compareMetricColumn");
    if (column) column.textContent = `${config.label} (${config.unit})`;
    const coverage = document.querySelector("#compareCoverage");
    if (coverage) coverage.textContent = `${runs.length} runs · ${finalModel.runs.length} measured`;
    const note = document.querySelector("#compareLegacyNote");
    const legacy = runs.filter(run => run.legacy_fallback).length;
    if (note) note.textContent = legacy ? `LEGACY ${legacy} · identity 근거 확인 필요` : 'Identity / gate 독립 판정';
    const incompatible = baseline ? runs.filter(run => !lab.comparisonEvidence(run, baseline, metric, researchState.controlled).comparable).length : runs.length;
    const notice = document.querySelector('#comparisonNotice');
    if (notice) notice.textContent = !baseline ? previous ? `Baseline ${previous}가 현재 집합 밖이거나 측정값이 없습니다. 새 baseline을 선택하세요. Ratio N/A.` : 'Baseline을 선택하세요. Metric 값은 표시하되 ratio는 N/A입니다.' : `Baseline ${baseline.run_id} · ${config.better === 'lower' ? '낮을수록 좋음 · speedup=baseline/current · change%=(current−baseline)/baseline' : metric === 'success_rate' ? '성공률 차이=percentage points (pp)' : '높을수록 좋음 · speedup=current/baseline'} · ${incompatible} runs 비교 근거 부족 / 불일치. 통제 축: ${researchState.controlled.join(', ') || '없음'}`;
    const table = document.querySelector("#compareRunsTable");
    if (table) table.innerHTML = runs.length ? runs.slice(0, 200).map(run => {
      const value = metricValue(run, metric);
      const evidence = lab.comparisonEvidence(run, baseline, metric, researchState.controlled);
      const ratio = baselineRatio(run, baseline, metric);
      const change = lab.baselineChange(run, baseline, metric, researchState.controlled);
      const diffs = evidence.differences.filter(item => item.changed || item.missing).map(item => `${item.label}: ${item.base ?? 'UNKNOWN'} → ${item.current ?? 'UNKNOWN'}${item.independent ? ' (통제 축)' : ''}`);
      return `<tr><td><strong>${esc(run.run_id)}</strong><br><small>${esc(run.campaign_id || 'uncampaigned')} · Provenance ${esc(run.provenance || 'UNKNOWN')}<br>Phase ${esc(run.experiment_type || 'UNKNOWN')}</small></td><td title="${esc(run.model_id)}">${esc(short(run.model_lock !== "unlocked" ? run.model_lock : run.model_id, 30))}<br><small>Hash ${esc(short(run.model_sha256 || 'UNKNOWN', 16))}</small></td><td>${esc(run.platform)} · ${run.node_count ?? 'UNKNOWN'}<br><small>${esc((run.nodes || []).join(", "))}</small></td><td>${esc(run.strategy)}</td><td><small>Runtime ${esc(short(run.runtime_fingerprint, 24))}<br>Source ${esc(short(run.source_fingerprint || 'UNKNOWN', 16))}<br>Power ${esc(run.power_mode || 'UNKNOWN')}</small></td><td>Quality <span class="quality-badge ${esc(run.measurement_quality || 'unknown')}">${esc((run.measurement_quality || 'UNKNOWN').toUpperCase())}</span><br>Execution <span class="run-status ${esc(run.status)}">${esc(String(run.status).toUpperCase())}</span></td><td><strong>${finiteNumber(value) ? `${value.toFixed(config.unit === "%" ? 1 : 2)} ${config.unit}` : "N/A"}</strong></td><td><strong>${ratio !== null ? `${ratio.toFixed(2)}×` : metric === 'success_rate' && change ? `${change.value.toFixed(1)} pp` : 'N/A'}</strong>${metric !== 'success_rate' && change ? `<br><small>Change ${change.value.toFixed(1)}${change.unit}</small>` : ''}${run.run_id === previous ? '<br><small>BASELINE</small>' : ''}<details><summary>${evidence.comparable ? '조건 확인됨' : '비교 조건 확인 필요'}</summary><p>${esc(evidence.reasons.join('; ') || '동일 조건 / 명시한 통제 축')}</p>${diffs.map(diff => `<p>${esc(diff)}</p>`).join('')}</details></td></tr>`;
    }).join("") : `<tr><td colspan="8" class="empty-cell">필터에 맞는 실행 없음</td></tr>`;
    renderContext(baseline);
  }

  function renderReadiness() {
    const value = researchState.readiness;
    const eligibility = document.querySelector("#researchEligibility");
    if (!value) {
      if (eligibility) eligibility.textContent = "GATE UNAVAILABLE";
      document.querySelectorAll("#readinessSummary, #approvedModelList, #researchWorkerList, #researchBlockers").forEach(target => { target.innerHTML = `<div class="research-empty"><strong>연구 데이터 unavailable · gate 미확인</strong></div>`; });
      return;
    }
    if (eligibility) {
      eligibility.className = `research-eligibility ${value.eligible ? "ready" : "blocked"}`;
      eligibility.textContent = value.execution_gate?.formal_execution_allowed === true && value.eligible ? "FORMAL GATE OPEN" : "FORMAL BLOCKED";
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
      const results = await Promise.allSettled([
        apiSurface().api("/api/campaigns"), apiSurface().api("/api/research/compare"), apiSurface().api("/api/research/readiness"),
      ]);
      const [campaigns, compare, readiness] = results;
      researchState.error = '';
      if (campaigns.status === 'fulfilled') {
        researchState.campaigns = campaigns.value.campaigns || [];
        if (!researchState.selectedCampaign && researchState.campaigns.length) researchState.selectedCampaign = researchState.campaigns[0].campaign_id;
        if (!researchState.campaigns.some(item => item.campaign_id === researchState.selectedCampaign)) { researchState.selectedCampaign = ''; researchState.campaign = null; }
      } else researchState.error = `Campaign unavailable / stale · ${campaigns.reason.message}`;
      if (compare.status === 'fulfilled') researchState.compare = compare.value || { runs: [], filters: {} };
      else researchState.error += ` Compare unavailable / stale · ${compare.reason.message}`;
      if (readiness.status === 'fulfilled') researchState.readiness = readiness.value;
      else { researchState.readiness = null; researchState.error += ` Gate unavailable · ${readiness.reason.message}`; }
      populateCompareFilters(); renderCampaignSummary(); renderCampaignDetail(); renderCompare(); renderReadiness(); renderMatrix();
      if (researchState.error) apiSurface().toast?.('연구 데이터 unavailable / stale', researchState.error, 'error');
      if (researchState.selectedCampaign && campaigns.status === 'fulfilled') await selectCampaign(researchState.selectedCampaign);
    } catch (error) {
      apiSurface().toast?.("연구 현황 조회 실패", error.message, "error");
    } finally {
      researchState.loading = false;
      renderMatrix();
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
    document.querySelector('#overviewCampaignSelect')?.addEventListener('change', event => selectCampaign(event.target.value));
    document.querySelector('#compareAxis')?.addEventListener('change', event => { researchState.controlled = event.target.value.split(',').filter(Boolean); renderCompare(); });
    document.querySelectorAll('#overviewMatrix, #campaignMatrix').forEach(target => target.addEventListener('click', event => { const cell = event.target.closest('[data-matrix-cell]'); if (cell) inspectMatrix(Number(cell.dataset.matrixCell)); }));
    document.querySelector('#matrixCompareLink')?.addEventListener('click', () => document.querySelector('#matrixInspectDialog')?.close());
    document.addEventListener('dashboard:pagechange', event => { if (event.detail.page === 'overview' || event.detail.page === 'campaign') renderMatrix(); });
    document.querySelector("#campaignDetail")?.addEventListener("click", event => {
      const button = event.target.closest("[data-campaign-action]");
      if (button) controlCampaign(button.dataset.campaignAction, button.dataset.campaignCellId || "");
    });
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
      renderCompare, renderMatrix,
      filterCompareRuns,
      compareChartModel,
      compareCsv,
      baselineRatio,
      controlCampaign,
    },
  });
})(globalThis);
