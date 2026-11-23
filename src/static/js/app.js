/* 应用外壳：视图切换、主题、全局初始化 */
(function () {
  "use strict";

  const ui = window.M2PUI;
  const STORAGE_THEME = "m2p.theme";
  const STORAGE_VIEW = "m2p.view";

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(STORAGE_THEME, theme);
    const button = document.getElementById("theme-toggle");
    button.setAttribute("aria-label", theme === "dark" ? "切换到浅色主题" : "切换到深色主题");
    button.title = button.getAttribute("aria-label");
  }

  function initTheme() {
    const stored = localStorage.getItem(STORAGE_THEME);
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    applyTheme(stored || (prefersDark ? "dark" : "light"));
    document.getElementById("theme-toggle").addEventListener("click", () => {
      applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
    });
  }

  const VIEWS = ["workspace", "history", "settings"];

  function showView(view) {
    if (!VIEWS.includes(view)) view = "workspace";
    VIEWS.forEach((name) => {
      const section = document.getElementById("view-" + name);
      if (section) section.hidden = name !== view;
    });
    document.querySelectorAll(".nav-tab").forEach((tab) => {
      tab.setAttribute("aria-selected", String(tab.dataset.view === view));
    });
    localStorage.setItem(STORAGE_VIEW, view);
    if (view === "history" && window.M2PHistory) window.M2PHistory.load();
    if (view === "settings" && window.M2PSettings) {
      window.M2PSettings.load();
      window.M2PSettings.loadHealth();
    }
  }

  function initNav() {
    document.querySelectorAll(".nav-tab").forEach((tab) => {
      tab.addEventListener("click", () => showView(tab.dataset.view));
    });
    document.addEventListener("m2p:show-view", (event) => showView(event.detail && event.detail.view));
    const stored = localStorage.getItem(STORAGE_VIEW) || "workspace";
    showView(stored);
  }

  async function refreshAiStatus() {
    try {
      const health = await window.M2PApi.health();
      const badge = document.getElementById("ai-status");
      const mode = health.ai.mode;
      badge.textContent = mode === "mock" ? "离线演示 · " + health.ai.model : "已连接 · " + health.ai.model;
      badge.className = "badge badge-" + (mode === "mock" ? "warning" : "success") + " badge-dot";
      badge.title = mode === "mock"
        ? "当前为离线演示模式：翻译由本地 mock 引擎完成，无需密钥"
        : "真实 AI 服务已就绪";
    } catch (err) {
      const badge = document.getElementById("ai-status");
      badge.textContent = "服务不可用";
      badge.className = "badge badge-danger badge-dot";
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    initTheme();
    initNav();
    if (window.M2PPreview) window.M2PPreview.init();
    if (window.M2PWorkspace) window.M2PWorkspace.init();
    if (window.M2PHistory) window.M2PHistory.init();
    if (window.M2PSettings) window.M2PSettings.init();
    refreshAiStatus();
    setInterval(refreshAiStatus, 30000);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        const backdrop = document.querySelector(".modal-backdrop");
        if (backdrop) backdrop.remove();
      }
    });
  });

  window.M2PApp = { showView, refreshAiStatus };
})();
