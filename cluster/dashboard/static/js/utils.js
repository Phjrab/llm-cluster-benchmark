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

  dashboard.utils = { STATUS, statusPresentation, bytes, list };
})();
