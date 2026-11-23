/* 预览模块：把真实 PDF 渲染到 iframe（blob URL），带防抖与状态提示 */
(function () {
  "use strict";

  const api = window.M2PApi;
  const ui = window.M2PUI;
  const runtime = { mode: "original", url: null, busy: false, seq: 0, blob: null };

  function elements() {
    return {
      frame: document.getElementById("preview-frame"),
      empty: document.getElementById("preview-empty"),
      loading: document.getElementById("preview-loading"),
      pages: document.getElementById("preview-pages"),
      refresh: document.getElementById("preview-refresh"),
      download: document.getElementById("preview-download"),
    };
  }

  function setBusy(busy, message) {
    const el = elements();
    runtime.busy = busy;
    el.loading.hidden = !busy;
    // 无 PDF 且不在加载中时，展示空态提示
    el.empty.hidden = busy || Boolean(runtime.blob);
    el.frame.hidden = busy || !runtime.blob;
    if (message) {
      const desc = el.loading.querySelector(".preview-empty-desc");
      if (desc) desc.textContent = message;
    }
  }

  function releaseUrl() {
    if (runtime.url) {
      URL.revokeObjectURL(runtime.url);
      runtime.url = null;
    }
  }

  function showBlob(blob, pageCount, elapsed) {
    const el = elements();
    releaseUrl();
    runtime.blob = blob;
    runtime.url = URL.createObjectURL(new Blob([blob], { type: "application/pdf" }));
    el.frame.src = runtime.url;
    el.loading.hidden = true;
    el.frame.hidden = false;
    el.empty.hidden = true;
    el.pages.textContent = (pageCount || "—") + " 页" + (elapsed ? " · " + elapsed + " ms" : "");
  }

  async function render(options) {
    const opts = options || {};
    const el = elements();
    if (!opts.taskId || !opts.file) {
      el.empty.hidden = false;
      el.frame.hidden = true;
      el.pages.textContent = "— 页";
      return;
    }
    const seq = ++runtime.seq;
    const mode = opts.mode || runtime.mode;
    runtime.mode = mode;
    setBusy(true, mode === "original" ? "正在排版预览…" : "正在翻译并排版（首次可能较慢）…");
    try {
      const payload = {
        file: opts.file,
        mode,
        target_language: opts.options.target_language,
        export_mode: opts.options.export_mode,
        bilingual_layout: opts.options.bilingual_layout,
        theme: opts.options.theme,
        tone: opts.options.tone,
        glossary: opts.options.glossary,
        style_options: opts.options.style_options,
        document: opts.options.document,
      };
      const result = await api.preview(opts.taskId, payload);
      if (seq !== runtime.seq) return;
      const pages = result.headers.get("X-Page-Count");
      const elapsed = result.headers.get("X-Elapsed-Ms");
      showBlob(result.blob, pages, elapsed);
    } catch (err) {
      if (seq !== runtime.seq) return;
      runtime.blob = null;
      setBusy(false);
      el.empty.hidden = false;
      el.frame.hidden = true;
      const title = el.empty.querySelector(".preview-empty-title");
      const desc = el.empty.querySelector(".preview-empty-desc");
      if (title) title.textContent = "预览失败";
      if (desc) desc.textContent = err.message || "无法生成预览，请稍后重试";
      el.pages.textContent = "— 页";
      if (mode !== "original") ui.toast({ kind: "error", title: "预览失败", desc: err.message });
    } finally {
      if (seq === runtime.seq) setBusy(false);
    }

  }

  function setMode(mode) {
    document.querySelectorAll("#preview-mode button").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
    });
    runtime.mode = mode;
  }

  function currentMode() {
    return runtime.mode;
  }

  function download() {
    const el = elements();
    if (!runtime.blob) {
      ui.toast({ kind: "warning", title: "暂无可下载的预览" });
      return;
    }
    const link = document.createElement("a");
    link.href = runtime.url;
    link.download = "preview-" + runtime.mode + ".pdf";
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  function init() {
    document.querySelectorAll("#preview-mode button").forEach((button) => {
      button.addEventListener("click", () => {
        const mode = button.dataset.mode;
        setMode(mode);
        document.dispatchEvent(new CustomEvent("m2p:preview-request", { detail: { mode } }));
      });
    });
    const el = elements();
    el.refresh.addEventListener("click", () => {
      document.dispatchEvent(new CustomEvent("m2p:preview-request", { detail: { mode: runtime.mode, force: true } }));
    });
    el.download.addEventListener("click", download);
  }

  window.M2PPreview = { init, render, setMode, currentMode, reset: () => { releaseUrl(); runtime.blob = null; setBusy(false); elements().empty.hidden = false; elements().frame.hidden = true; } };
})();
