"use strict";

function getToken() {
  const currentUrl = new URL(location.href);
  if (currentUrl.searchParams.has("token")) {
    const clean = new URL(location.href);
    clean.searchParams.delete("token");
    history.replaceState({}, "", clean.pathname + clean.search + clean.hash);
  }
  return sessionStorage.getItem("clusterToken") || "";
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.token) headers["X-Cluster-Token"] = state.token;
  if (options.body && typeof options.body !== "string") {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    const dialog = document.querySelector("#authDialog");
    if (dialog && !dialog.open) dialog.showModal();
  }
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}
