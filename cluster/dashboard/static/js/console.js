/* Node-operation and experiment terminals deliberately use separate bounded buffers. */
(function () {
  const dashboard = window.ClusterDashboard || (window.ClusterDashboard = {});
  const LIMIT = 200;
  const terminals = {
    node: { selector: "#environmentLog", autoScroll: true },
    experiment: { selector: "#consoleLog", autoScroll: true },
  };

  function targetFor(channel) {
    const terminal = terminals[channel];
    return terminal ? document.querySelector(terminal.selector) : null;
  }

  function append(channel, label, message) {
    const target = targetFor(channel);
    if (!target) return false;
    const line = document.createElement("p");
    const tag = document.createElement("time");
    tag.textContent = String(label || "INFO");
    line.append(tag, document.createTextNode(String(message || "")));
    target.append(line);
    while (target.children.length > LIMIT) target.firstElementChild.remove();
    if (terminals[channel].autoScroll) target.scrollTop = target.scrollHeight;
    return true;
  }

  function clear(channel) {
    const target = targetFor(channel);
    if (target) target.replaceChildren();
  }

  async function copy(channel) {
    const target = targetFor(channel);
    const text = target && target.innerText ? target.innerText.trim() : "";
    if (!text) {
      dashboard.toast("복사할 로그 없음", "현재 표시할 로그가 없습니다.", "error");
      return;
    }
    try {
      if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(text);
      dashboard.toast("로그 복사 완료", `${channel === "node" ? "노드 작업" : "실험"} 로그 ${target.children.length}줄을 복사했습니다.`);
    } catch (_error) {
      dashboard.toast("로그 복사 실패", "브라우저 복사 권한을 확인하세요.", "error");
    }
  }

  dashboard.terminals = { append, clear, copy, limit: LIMIT };

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-terminal-autoscroll]").forEach(function (input) {
      input.addEventListener("change", function (event) {
        const channel = event.currentTarget.dataset.terminalAutoscroll;
        if (terminals[channel]) terminals[channel].autoScroll = event.currentTarget.checked;
      });
    });
    document.querySelectorAll("[data-terminal-clear]").forEach(function (button) {
      button.addEventListener("click", function () { clear(button.dataset.terminalClear); });
    });
    document.querySelectorAll("[data-terminal-copy]").forEach(function (button) {
      button.addEventListener("click", function () { copy(button.dataset.terminalCopy); });
    });
  });
}());
