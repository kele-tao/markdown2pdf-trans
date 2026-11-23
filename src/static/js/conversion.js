/* 转换控制模块：启动 / 轮询 / 暂停继续取消 / 结果渲染 / 失败重试 */
(function () {
  "use strict";

  const api = window.M2PApi;
  const ui = window.M2PUI;
  const $ = (id) => document.getElementById(id);
  const ws = () => window.M2PWorkspace;

  const runtime = { polling: null };

/* ------------------------------------------------------------ 转换 */
  async function start() {
    if (!ws().state.taskId) return;
    const button = $("start-btn");
    button.disabled = true;
    try {
      await api.start(ws().state.taskId, ws().collectOptions());
      $("card-progress").hidden = false;
      $("card-files").hidden = true;
      $("download-btn").hidden = true;
      ws().state.lastLogCount = 0;
      ui.clear($("log-box"));
      setStep(3);
      startPolling();
      ui.toast({ kind: "info", title: "已开始转换", desc: "可在右侧查看进度与日志" });
    } catch (err) {
      ui.toast({ kind: "error", title: "无法启动转换", desc: err.message });
    } finally {
      button.disabled = false;
    }
  }

  async function controlImpl(action) {
    if (!ws().state.taskId) return;
    try {
      await api[action](ws().state.taskId);
      poll();
    } catch (err) {
      ui.toast({ kind: "error", title: "操作失败", desc: err.message });
    }
  }

  async function poll() {
    if (!ws().state.taskId) return;
    let data;
    try {
      data = await api.status(ws().state.taskId);
    } catch (err) {
      stopPolling();
      ui.toast({ kind: "error", title: "无法获取任务状态", desc: err.message });
      return;
    }
    applyStatus(data);
    if (["SUCCESS", "FAILURE", "STOPPED"].includes(data.state)) {
      stopPolling();
      finish(data);
    }
  }

  function applyStatus(data) {
    const pct = Math.max(0, Math.min(100, data.progress || 0));
    $("progress-pct").textContent = pct + "%";
    $("progress-bar").style.width = pct + "%";
    $("progress-role").setAttribute("aria-valuenow", String(pct));
    $("stat-total").textContent = (data.counts && data.counts.total) || ws().state.files.length;
    $("stat-success").textContent = (data.counts && data.counts.success) || 0;
    $("stat-failed").textContent = (data.counts && data.counts.failed) || 0;
    $("progress-current").textContent = data.current_file || "";
    const badge = $("state-badge");
    badge.textContent = ui.STATE_LABEL[data.state] || data.state;
    badge.className = "badge badge-" + (ui.STATE_TONE[data.state] || "info") + " badge-dot";
    const bar = $("progress-bar");
    bar.classList.toggle("is-paused", data.state === "PAUSED");
    bar.classList.toggle("is-failed", data.state === "FAILURE");
    $("pause-btn").hidden = data.state !== "RUNNING";
    $("resume-btn").hidden = data.state !== "PAUSED";

    const box = $("log-box");
    (data.logs || []).forEach((entry) => {
      const line = document.createElement("div");
      line.className = "log-line";
      line.dataset.level = entry.level || "info";
      const ts = document.createElement("span");
      ts.className = "log-ts";
      const date = new Date((entry.ts || 0) * 1000);
      ts.textContent = String(date.getHours()).padStart(2, "0") + ":" + String(date.getMinutes()).padStart(2, "0") + ":" + String(date.getSeconds()).padStart(2, "0");
      const message = document.createElement("span");
      message.className = "log-msg";
      message.textContent = entry.message || "";
      line.appendChild(ts);
      line.appendChild(message);
      box.appendChild(line);
    });
    if (data.logs && data.logs.length) box.scrollTop = box.scrollHeight;

    if (data.files && data.files.length) renderResults(data.files);
  }

  function renderResults(files) {
    $("card-files").hidden = false;
    const container = $("result-files");
    ui.clear(container);
    const failed = files.filter((item) => item.state === "failed");
    $("files-sub").textContent = failed.length ? failed.length + " 个文件失败，可单独重试" : "全部文件已处理";
    files.forEach((file) => {
      const row = document.createElement("div");
      row.className = "result-file";
      const name = ui.el("span", "file-name", file.path);
      const tone = file.state === "success" ? "success" : file.state === "failed" ? "danger" : "info";
      const label = { success: "成功", failed: "失败", running: "处理中", pending: "等待", skipped: "跳过" }[file.state] || file.state;
      row.appendChild(ui.badge(label, tone));
      row.appendChild(name);
      if (file.translated_name) row.appendChild(ui.el("span", "file-meta", file.translated_name));
      const pages = file.pages || {};
      const pageText = [pages.original ? "原 " + pages.original : "", pages.translated ? "译 " + pages.translated : "", pages.bilingual ? "双 " + pages.bilingual : ""].filter(Boolean).join(" · ");
      if (pageText) row.appendChild(ui.el("span", "file-meta", pageText));
      if (file.error) {
        const err = ui.el("span", "file-meta", file.error);
        err.style.color = "var(--danger)";
        err.title = file.error;
        err.style.maxWidth = "260px";
        err.style.overflow = "hidden";
        err.style.textOverflow = "ellipsis";
        row.appendChild(err);
      }
      if (file.state === "failed") {
        const retry = document.createElement("button");
        retry.type = "button";
        retry.className = "btn btn-sm";
        retry.textContent = "重试";
        retry.addEventListener("click", () => retryFile(file.path));
        row.appendChild(retry);
      }
      container.appendChild(row);
    });
  }

  async function retryFile(path) {
    try {
      await api.retry(ws().state.taskId, path);
      ui.toast({ kind: "info", title: "已提交重试", desc: path });
      startPolling();
    } catch (err) {
      ui.toast({ kind: "error", title: "重试失败", desc: err.message });
    }
  }

  function finish(data) {
    setStep(3);
    if (data.state === "SUCCESS") {
      ui.toast({ kind: "success", title: "转换完成", desc: "可下载结果 ZIP" });
      $("download-btn").hidden = false;
    } else if (data.state === "FAILURE") {
      $("download-btn").hidden = false;
      ui.toast({ kind: "error", title: "部分文件失败", desc: data.error || "请查看文件明细并重试失败项" });
    } else {
      ui.toast({ kind: "warning", title: "任务已停止", desc: "已完成的部分仍可下载" });
    }
    ws().refreshPreview(true);
  }

  function setStep(step) {
    document.querySelectorAll(".step").forEach((node) => {
      const value = Number(node.dataset.step);
      node.dataset.state = value < step ? "done" : value === step ? "active" : "pending";
    });
  }

  function reset() {
    stopPolling();
    ws().state.taskId = null;
    ws().state.files = [];
    ws().state.selectedFile = "";
    ws().state.glossary = [];
    ws().state.theme = "default";
    $("card-config").hidden = true;
    $("card-progress").hidden = true;
    $("card-files").hidden = true;
    $("file-list").hidden = true;
    $("upload-summary").hidden = true;
    ws().renderGlossary();
    if (window.M2PPreview) window.M2PPreview.reset();
    setStep(1);
    ws().renderThemes();
  }

  function startPolling() {
    stopPolling();
    runtime.polling = setInterval(poll, 900);
  }

  function stopPolling() {
    if (runtime.polling) clearInterval(runtime.polling);
    runtime.polling = null;
  }

  window.M2PConversion = {
    start,
    control: controlImpl,
    poll,
    startPolling,
    stopPolling,
    reset,
    renderResults,
    setStep,
  };
})();