/* Raspberry Pi power-integrity presentation only. It never changes admission or run state. */
(() => {
  const dashboard = window.ClusterDashboard || (window.ClusterDashboard = {});
  const CONDITIONS = [
    ["undervoltage", "저전압"],
    ["frequency_capped", "주파수 제한"],
    ["throttled", "스로틀"],
    ["soft_temperature_limit", "온도 제한"],
  ];

  function text(value) {
    return typeof value === "string" ? value.trim() : "";
  }

  function platformIsPi(value) {
    return text(value).toLowerCase() === "raspberry-pi";
  }

  function truthyConditions(value) {
    const source = value && typeof value === "object" ? value : {};
    return CONDITIONS.filter(([key]) => source[key] === true).map(([, label]) => label);
  }

  function qualityMeta(quality) {
    const key = text(quality).toLowerCase();
    if (key === "clean" || key === "normal" || key === "ok") {
      return { quality: "clean", label: "NORMAL", tone: "normal", icon: "✓", description: "전력 상태에서 문제 기록이 없습니다." };
    }
    if (key === "warning" || key === "history_warning") {
      return { quality: "warning", label: "WARNING", tone: "warning", icon: "!", description: "이전 전력 또는 열 상태 기록이 있습니다." };
    }
    if (key === "degraded" || key === "active_degraded") {
      return { quality: "degraded", label: "DEGRADED", tone: "degraded", icon: "!", description: "현재 전력 또는 열 상태가 감지되었습니다." };
    }
    if (key === "not_recorded") {
      return { quality: "not_recorded", label: "NOT RECORDED", tone: "unknown", icon: "·", description: "이전 결과에는 전력 측정 품질이 기록되지 않았습니다." };
    }
    return { quality: "unknown", label: "UNKNOWN", tone: "unknown", icon: "?", description: "전력 품질 정보를 확인할 수 없습니다." };
  }

  function normalizeIntegrity(raw, platform) {
    const source = raw && typeof raw === "object" ? raw : null;
    const applicable = platformIsPi(platform) || Boolean(source);
    if (!applicable) return { applicable: false, status: "not_applicable", quality: qualityMeta("unknown") };
    const current = truthyConditions(source?.current);
    const history = truthyConditions(source?.history);
    const status = text(source?.status).toLowerCase();
    const available = source?.available === true;
    let quality;
    if (status === "ok") quality = qualityMeta("clean");
    else if (status === "history_warning") quality = qualityMeta("warning");
    else if (status === "active_degraded") quality = qualityMeta("degraded");
    else if (status === "unavailable" || source?.available === false || !source) quality = qualityMeta("unknown");
    else if (current.length) quality = qualityMeta("degraded");
    else if (history.length) quality = qualityMeta("warning");
    else quality = qualityMeta("unknown");
    return {
      applicable: true,
      available,
      status: status || "unavailable",
      quality,
      rawHex: text(source?.raw_hex),
      source: text(source?.source),
      observedAt: text(source?.observed_at),
      current,
      history,
      reasonCodes: Array.isArray(source?.reason_codes) ? source.reason_codes.map(text).filter(Boolean) : [],
      message: text(source?.message),
      unknownBits: Number.isFinite(Number(source?.unknown_bits)) ? Number(source.unknown_bits) : 0,
    };
  }

  function nodeIntegrity(node, live) {
    const platform = live?.profile?.platform_kind || node?.platform;
    return normalizeIntegrity(live?.power_integrity, platform);
  }

  function pillHtml(model) {
    if (!model?.applicable) return "";
    const meta = model.quality || qualityMeta("unknown");
    const suffix = meta.quality === "warning" ? " · HISTORY" : meta.quality === "degraded" ? " · ACTIVE" : "";
    return `<span class="power-quality-pill ${meta.tone}" title="${dashboard.escapeHtml(meta.description)}" aria-label="Pi power quality ${dashboard.escapeHtml(meta.label)}. ${dashboard.escapeHtml(meta.description)}"><i aria-hidden="true">${meta.icon}</i>POWER ${meta.label}${suffix}</span>`;
  }

  function warningKey(warning) {
    return `${text(warning?.node)}|${text(warning?.code)}|${text(warning?.message)}`;
  }

  function newPowerWarnings(warnings, seen) {
    const keys = seen instanceof Set ? seen : new Set();
    return (Array.isArray(warnings) ? warnings : []).filter(warning => {
      const code = text(warning?.code);
      const stage = text(warning?.stage);
      if (!code.startsWith("PI_") && !stage.startsWith("power")) return false;
      const key = warningKey(warning);
      if (keys.has(key)) return false;
      keys.add(key);
      return true;
    });
  }

  function detailHtml(model) {
    if (!model?.applicable) return "";
    const meta = model.quality || qualityMeta("unknown");
    const currentText = model.current?.length ? model.current.join(", ") : "현재 감지된 상태 없음";
    const historyText = model.history?.length ? model.history.join(", ") : "과거 기록 없음";
    let explanation;
    if (meta.quality === "warning") explanation = "0x50000 같은 과거 저전압·스로틀 기록입니다. 현재 상태가 감지된 것은 아니며 실험은 계속할 수 있습니다.";
    else if (meta.quality === "degraded") explanation = "현재 전력 또는 열 상태가 감지되었습니다. 결과를 해석할 때 이 측정 품질 정보를 함께 확인하세요. 실험은 계속할 수 있습니다.";
    else if (meta.quality === "unknown") explanation = "전력 상태를 읽을 수 없습니다. 이 정보는 실험 실행을 막지 않습니다.";
    else explanation = "전력 상태에서 현재 또는 과거 조건이 보고되지 않았습니다.";
    const raw = model.rawHex ? `<code>${dashboard.escapeHtml(model.rawHex)}</code>` : "raw 값 없음";
    return `<section class="power-integrity-detail ${meta.tone}" aria-labelledby="powerIntegrityTitle">
      <div class="power-integrity-head"><div><span>RASPBERRY PI POWER INTEGRITY</span><h3 id="powerIntegrityTitle">전력 측정 상태</h3></div><span class="power-quality-pill ${meta.tone}"><i aria-hidden="true">${meta.icon}</i>POWER ${meta.label}</span></div>
      <p>${dashboard.escapeHtml(explanation)}</p>
      <dl><div><dt>현재</dt><dd>${dashboard.escapeHtml(currentText)}</dd></div><div><dt>과거</dt><dd>${dashboard.escapeHtml(historyText)}</dd></div><div><dt>RAW</dt><dd>${raw}</dd></div><div><dt>OBSERVED</dt><dd>${dashboard.escapeHtml(model.observedAt || "기록 없음")}</dd></div></dl>
    </section>`;
  }

  function selectedBanner(nodes, status, startWarnings) {
    const selected = (nodes || []).filter(node => node && node.selected);
    const records = selected.map(node => ({ name: text(node.name) || "worker", model: nodeIntegrity(node, (status || []).find(item => item?.name === node.name)) })).filter(item => item.model.applicable);
    const active = records.filter(item => item.model.quality.quality === "degraded");
    const history = records.filter(item => item.model.quality.quality === "warning");
    const unknown = records.filter(item => item.model.quality.quality === "unknown");
    const supplied = (startWarnings || []).filter(item => text(item?.code).startsWith("PI_") || text(item?.stage).startsWith("power"));
    if (!records.length && !supplied.length) return { hidden: true, tone: "unknown", html: "" };
    const items = [];
    if (active.length) items.push(`${active.map(item => item.name).join(", ")} · 현재 상태 감지`);
    if (history.length) items.push(`${history.map(item => item.name).join(", ")} · 과거 저전압/스로틀 기록 (현재 감지 아님)`);
    if (unknown.length) items.push(`${unknown.map(item => item.name).join(", ")} · 상태 미확인`);
    supplied.forEach(item => { const message = text(item?.message); if (message && !items.includes(message)) items.push(message); });
    const tone = active.length ? "degraded" : history.length || supplied.length ? "warning" : "unknown";
    return { hidden: false, tone, html: `<strong>PI POWER QUALITY · ${tone.toUpperCase()}</strong><span>${dashboard.escapeHtml(items.join(" · ") || "선택한 Pi 전력 상태를 확인하세요.")}</span><small>이 표시는 측정 품질 맥락이며 실험 실행을 차단하지 않습니다.</small>` };
  }

  function resultEnvironment(run) {
    const integrity = run?.power_integrity;
    const rawQuality = text(run?.measurement_quality).toLowerCase();
    const overall = rawQuality ? qualityMeta(rawQuality) : qualityMeta("not_recorded");
    const nodes = integrity?.nodes && typeof integrity.nodes === "object" ? Object.entries(integrity.nodes) : [];
    const warnings = Array.isArray(integrity?.warnings) ? integrity.warnings : [];
    return { overall, nodes, warnings, recorded: Boolean(rawQuality || nodes.length) };
  }

  function snapshotLabel(snapshot) {
    const model = normalizeIntegrity(snapshot, "raspberry-pi");
    const meta = model.quality;
    const conditions = model.current.length ? `현재: ${model.current.join(", ")}` : model.history.length ? `과거: ${model.history.join(", ")}` : meta.description;
    return { model, text: conditions };
  }

  function resultEnvironmentHtml(run) {
    const context = resultEnvironment(run);
    const meta = context.overall;
    if (!context.recorded) return `<section class="measurement-environment" aria-label="측정 환경"><div class="measurement-environment-head"><div><span>MEASUREMENT ENVIRONMENT</span><h4>전력 측정 품질</h4></div><span class="measurement-quality ${meta.tone}">${meta.icon} ${meta.label}</span></div><p>이전 형식의 결과입니다. 전력 측정 품질이 기록되지 않았으며, 정상 상태로 가정하지 않습니다.</p></section>`;
    const rows = context.nodes.map(([name, record]) => {
      const pre = snapshotLabel(record?.pre_measurement || record?.preflight);
      const post = snapshotLabel(record?.postflight);
      const sampling = record?.measurement && typeof record.measurement === "object" ? record.measurement : {};
      const quality = qualityMeta(record?.quality);
      return `<article class="measurement-node"><header><strong>${dashboard.escapeHtml(name)}</strong><span class="measurement-quality ${quality.tone}">${quality.icon} ${quality.label}</span></header><dl><div><dt>BEFORE</dt><dd>${dashboard.escapeHtml(pre.text)}</dd></div><div><dt>DURING</dt><dd>${dashboard.escapeHtml(`${Number(sampling.valid_sample_count || 0)}/${Number(sampling.sample_count || 0)} valid samples · active ${Number(sampling.active_warning_samples || 0)}`)}</dd></div><div><dt>AFTER</dt><dd>${dashboard.escapeHtml(post.text)}</dd></div></dl></article>`;
    }).join("");
    const failureNote = ["failed", "cancelled", "partial"].includes(text(run?.status).toLowerCase()) ? "실패 기록의 마지막 전력 상태는 함께 보이지만, 실패 원인으로 단정하지 않습니다." : "전력 품질은 실행 상태와 별도로 기록됩니다.";
    return `<section class="measurement-environment" aria-label="측정 환경"><div class="measurement-environment-head"><div><span>MEASUREMENT ENVIRONMENT</span><h4>전력 측정 품질</h4></div><span class="measurement-quality ${meta.tone}">${meta.icon} ${meta.label}</span></div><p>${dashboard.escapeHtml(failureNote)}</p>${rows || `<p>Pi 전력 표본이 기록되지 않았습니다.</p>`}</section>`;
  }

  dashboard.power = { qualityMeta, normalizeIntegrity, nodeIntegrity, pillHtml, detailHtml, selectedBanner, resultEnvironment, resultEnvironmentHtml, platformIsPi, warningKey, newPowerWarnings };
})();
