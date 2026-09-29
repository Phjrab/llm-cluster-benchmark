(function dashboardNavigation() {
  "use strict";

  // Keep feature DOM and shared state mounted while showing only the current page.
  // Existing #models / #experiment links remain valid, including model-pack actions.
  document.addEventListener("DOMContentLoaded", () => {
    const pages = new Map([
      ["overview", ["클러스터 개요", "준비 · 관리"]],
      ["nodes", ["노드 관리", "준비 · 관리"]],
      ["models", ["모델 라이브러리", "준비 · 관리"]],
      ["experiment", ["실험 설계", "실험 실행"]],
      ["sweeps", ["스윕 설계 · 실행", "실험 실행"]],
      ["results", ["실험 결과", "분석 · 연구"]],
      ["campaign", ["캠페인 현황", "분석 · 연구"]],
      ["compare", ["실행 간 비교", "분석 · 연구"]],
      ["research", ["연구 준비도", "분석 · 연구"]],
    ]);
    const panels = [...document.querySelectorAll("[data-dashboard-page]")];
    const links = [...document.querySelectorAll(".nav-link[data-section]")];
    const title = document.getElementById("pageTitle");
    const group = document.getElementById("pageGroup");
    const select = document.getElementById("pageSelect");
    let currentPage = null;

    function showPage(id, { focus = true } = {}) {
      if (!pages.has(id)) {
        id = "overview";
        // Preserve query parameters and caller-owned history state on fallback.
        history.replaceState(history.state, "", `${location.pathname}${location.search}#${id}`);
      }
      if (id === currentPage) return;
      currentPage = id;
      const [label, category] = pages.get(id);
      panels.forEach(panel => { panel.hidden = panel.dataset.dashboardPage !== id; });
      links.forEach(link => {
        const active = link.dataset.section === id;
        link.classList.toggle("active", active);
        if (active) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
      });
      title.textContent = label;
      group.textContent = category;
      select.value = id;
      document.title = `${label} · MediFlow Cluster Lab`;
      if (focus) title.focus({ preventScroll: true });
      window.scrollTo({ top: 0, left: 0, behavior: "instant" });
      // Canvas charts need their new visible dimensions before redrawing.
      document.dispatchEvent(new CustomEvent("dashboard:pagechange", { detail: { page: id } }));
    }

    function navigate(id) {
      if (!pages.has(id)) return;
      if (location.hash !== `#${id}`) history.pushState(null, "", `#${id}`);
      showPage(id);
      window.scrollTo({ top: 0, left: 0, behavior: "instant" });
    }

    document.addEventListener("click", event => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = event.target.closest('a[href^="#"]');
      if (!link || link.hasAttribute("download") || (link.target && link.target !== "_self")) return;
      const id = link.getAttribute("href").slice(1);
      if (!pages.has(id)) return;
      event.preventDefault();
      navigate(id);
    });
    select.addEventListener("change", () => navigate(select.value));
    const followLocation = () => showPage(location.hash.slice(1) || "overview");
    window.addEventListener("popstate", followLocation);
    window.addEventListener("hashchange", followLocation);
    if ("scrollRestoration" in history) history.scrollRestoration = "manual";
    showPage(location.hash.slice(1) || "overview", { focus: false });
  });
})();
