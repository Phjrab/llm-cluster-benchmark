(function dashboardTheme(global) {
  'use strict';
  const KEY = 'llmClusterBenchmark.theme';
  const valid = value => value === 'light' || value === 'dark';
  const media = global.matchMedia?.('(prefers-color-scheme: dark)');
  let preference = null;
  try { const saved = global.localStorage.getItem(KEY); if (valid(saved)) preference = saved; } catch (_) { /* Private/blocked storage: keep an in-memory preference. */ }
  const systemTheme = () => media?.matches ? 'dark' : 'light';
  const root = global.document.documentElement;
  const darkSeries = {
    '#2458a6': '#8AB4F8', '#a56800': '#FFCC80', '#946200': '#FFCC80',
    '#24654e': '#80D8AE', '#8356a6': '#CAB3FF', '#ad3434': '#FF9C9C',
    '#526170': '#B7C4D6', '#646f7b': '#B7C4D6',
  };

  function updateButton() {
    const button = global.document.querySelector('#themeToggle');
    if (!button) return;
    const dark = root.dataset.theme === 'dark';
    button.setAttribute('aria-pressed', String(dark));
    button.title = dark ? '화이트 모드로 전환' : '다크 모드로 전환';
    global.document.querySelector('#themeIcon').textContent = dark ? '☾' : '☀';
    global.document.querySelector('#themeLabel').textContent = dark ? '다크' : '화이트';
  }

  function apply(theme, announce = true) {
    const changed = root.dataset.theme !== theme;
    root.dataset.theme = theme;
    root.style.colorScheme = theme;
    updateButton();
    if (changed && announce) global.document.dispatchEvent(new CustomEvent('dashboard:themechange', { detail: { theme } }));
  }

  function set(theme) {
    if (!valid(theme)) return;
    preference = theme;
    try { global.localStorage.setItem(KEY, theme); } catch (_) { /* The toggle still works without persistence. */ }
    apply(theme);
  }

  // This script runs in <head>, before styles and the first rendered frame.
  apply(preference || systemTheme(), false);
  global.ClusterDashboard = Object.assign(global.ClusterDashboard || {}, {
    theme: { key: KEY, set, current: () => root.dataset.theme,
      chartColor: color => root.dataset.theme === 'dark' ? darkSeries[String(color).toLowerCase()] || color : color },
  });
  global.document.addEventListener('DOMContentLoaded', () => {
    updateButton();
    global.document.querySelector('#themeToggle')?.addEventListener('click', () => set(root.dataset.theme === 'dark' ? 'light' : 'dark'));
  });
  media?.addEventListener?.('change', () => { if (!preference) apply(systemTheme()); });
  global.addEventListener('storage', event => {
    if (event.key !== KEY && event.key !== null) return;
    preference = valid(event.newValue) ? event.newValue : null;
    apply(preference || systemTheme());
  });
})(globalThis);
