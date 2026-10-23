/* Shared, browser-only presentation helpers.  API and orchestration stay in app.js. */
(() => {
  const dashboard = window.ClusterDashboard || (window.ClusterDashboard = {});

  const STATUS = {
    completed: { icon: "✓", label: "COMPLETED", tone: "completed" },
    partial: { icon: "!", label: "PARTIAL", tone: "partial" },
    failed: { icon: "×", label: "FAILED", tone: "failed" },
    cancelled: { icon: "−", label: "CANCELLED", tone: "cancelled" },
    running: { icon: "●", label: "RUNNING", tone: "running" },
    queued: { icon: "○", label: "QUEUED", tone: "queued" },
    unrun: { icon: "·", label: "NOT RUN", tone: "unknown" },
  };

  function statusPresentation(value) {
    const key = String(value || "unknown").toLowerCase();
    return STATUS[key] || { icon: "?", label: key.toUpperCase(), tone: "unknown" };
  }

  function bytes(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0) return "—";
    if (number < 1024 * 1024) return `${Math.round(number / 1024)} KB`;
    return `${(number / (1024 ** 3)).toFixed(number < 10 * 1024 ** 3 ? 2 : 1)} GB`;
  }

  function list(value) {
    return Array.isArray(value) ? value : [];
  }

  function progressHtml(observation) {
    const v = observation || {};
    const esc = value => dashboard.escapeHtml(String(value ?? "UNKNOWN"));
    const labels = { LOOP_ERROR: "진행 루프 오류", CHILD_ACTIVE: "child 작업 관측", COOLDOWN: "cooldown", CLEANUP: "정리 중", RESOURCE_WAIT: "자원 대기", PAUSED: "일시정지", PROGRESSING: "진행 중", GATE_BLOCKED: "formal gate 대기", COMPLETED: "완료", PARTIAL: "부분 완료", FAILED: "실패", CANCELLED: "취소", UNKNOWN: "확인 불가" };
    const display = v.display_state || "UNKNOWN";
    return `<section class="execution-observation" data-progress-state="${esc(display)}" aria-label="실행 진행 관측">
      <strong>${esc(labels[display] || "확인 불가")}</strong>
      <p>서버 ${esc(v.server)} · loop ${esc(v.loop_state)} · 조회 시 freshness ${esc(v.freshness)} · 요청 진행 ${esc(v.request_progress)}</p>
      <p>조회 ${esc(v.observed_at)} · Worker 응답 ${(v.workers?.nodes || []).map(n => `${esc(n.name)}: ${esc(n.response)}`).join(" · ") || "UNKNOWN"} (${esc(v.workers?.freshness)})</p>
      ${(v.children || []).map(c => `<p class="observed-child">${esc(c.trial_id || c.cell_id)} · ${esc(c.attempt_id)} · job ${esc(c.job_id)}<br>저장된 child ${esc(c.status)} / ${esc(c.phase)} · cleanup ${esc(c.cleanup_status)} · process live ${esc(c.process_liveness)}</p>`).join("")}
      <details><summary>진행 관측 근거</summary><dl>
        <dt>Serving instance / PID</dt><dd>${esc(v.serving_instance_id)} / ${esc(v.serving_pid)}</dd>
        <dt>Loop instance / owner 근거</dt><dd>${esc(v.loop_instance_id)} / ${esc(v.owner_evidence)} · 다른 instance UNKNOWN</dd>
        <dt>마지막 tick / 정상 tick</dt><dd>${esc(v.last_tick_at)} / ${esc(v.last_success_at)}</dd>
        <dt>마지막 dispatch / 오류</dt><dd>${esc(v.last_dispatch_at)} / ${esc(v.last_error_code || (v.loop_state && v.loop_state !== "UNKNOWN" ? "N/A" : "UNKNOWN"))}</dd>
      </dl><p>heartbeat는 요청 성공의 근거가 아닙니다. child 상태는 저장된 관측이며 현재 프로세스 생존 여부는 UNKNOWN입니다. 새로고침은 조회만 수행합니다.</p></details>
    </section>`;
  }

  dashboard.utils = { STATUS, statusPresentation, bytes, list, progressHtml };
})();
