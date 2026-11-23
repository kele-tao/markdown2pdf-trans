/* 工作台：上传 → 配置 → 转换 全流程 */
(function () {
  "use strict";

  const api = window.M2PApi;
  const ui = window.M2PUI;

  const state = {
    meta: null,
    taskId: null,
    files: [],
    selectedFile: "",
    uploadType: "zip",
    theme: "default",
    glossary: [],
    polling: null,
    lastLogCount: 0,
    lastState: "",
    options: null,
  };

  const $ = (id) => document.getElementById(id);

  /* ------------------------------------------------------------ 元数据 */
  async function loadMeta() {
    state.meta = await api.meta();
    const langSelect = $("target-language");
    ui.clear(langSelect);
    state.meta.languages.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.code;
      option.textContent = item.label;
      langSelect.appendChild(option);
    });
    const modeSelect = $("export-mode");
    ui.clear(modeSelect);
    state.meta.export_modes.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.code;
      option.textContent = item.label;
      modeSelect.appendChild(option);
    });
    const layoutSelect = $("bilingual-layout");
    ui.clear(layoutSelect);
    state.meta.bilingual_layouts.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.code;
      option.textContent = item.label;
      layoutSelect.appendChild(option);
    });
    const toneSelect = $("tone");
    ui.clear(toneSelect);
    state.meta.tones.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.code;
      option.textContent = item.label;
      toneSelect.appendChild(option);
    });
    const sizeSelect = $("page-size");
    ui.clear(sizeSelect);
    (state.meta.page_sizes || ["A4"]).forEach((item) => {
      const option = document.createElement("option");
      option.value = item;
      option.textContent = item;
      sizeSelect.appendChild(option);
    });
    renderThemes();
  }

  function renderThemes() {
    const grid = $("theme-grid");
    ui.clear(grid);
    const themes = state.meta.themes || {};
    Object.keys(themes).forEach((key) => {
      const theme = themes[key];
      const card = document.createElement("button");
      card.type = "button";
      card.className = "theme-card";
      card.dataset.theme = key;
      card.setAttribute("aria-pressed", String(key === state.theme));
      card.title = theme.description || theme.name;

      const swatch = document.createElement("div");
      swatch.className = "theme-swatch";
      [theme.heading_color, theme.accent_color || theme.link_color, theme.quote_bg_color, theme.code_bg].forEach((color) => {
        const part = document.createElement("span");
        part.style.background = color || "#eee";
        swatch.appendChild(part);
      });
      const name = ui.el("div", "theme-card-name", theme.name || key);
      const desc = ui.el("div", "theme-card-desc", theme.description || "");
      card.appendChild(swatch);
      card.appendChild(name);
      card.appendChild(desc);
      card.addEventListener("click", () => {
        state.theme = key;
        grid.querySelectorAll(".theme-card").forEach((node) => node.setAttribute("aria-pressed", String(node.dataset.theme === key)));
        applyThemeDefaults(theme);
        schedulePreview(true);
      });
      grid.appendChild(card);
    });
  }

  function applyThemeDefaults(theme) {
    if (!theme) return;
    $("font-size").value = theme.font_size || "11pt";
    $("line-height").value = theme.line_height || "1.75";
    $("page-margin").value = theme.page_margin || "2.2cm 2cm";
    $("page-size").value = theme.page_size || "A4";
    $("page-orientation").value = theme.page_orientation || "portrait";
    $("text-color").value = normalizeColor(theme.text_color);
    $("accent-color").value = normalizeColor(theme.accent_color || theme.link_color);
  }

  function normalizeColor(value) {
    if (!value) return "#1f2328";
    const match = String(value).match(/^#[0-9a-fA-F]{6}$/);
    return match ? value : "#1f2328";
  }

  /* ------------------------------------------------------------ 上传 */
  function setupUpload() {
    const input = $("file-input");
    const zone = $("dropzone");
    const typeGroup = $("upload-type");

    typeGroup.querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => {
        state.uploadType = button.dataset.type;
        typeGroup.querySelectorAll("button").forEach((node) => node.setAttribute("aria-pressed", String(node === button)));
        configureInput();
      });
    });

    function configureInput() {
      if (state.uploadType === "folder") {
        input.setAttribute("webkitdirectory", "");
        input.setAttribute("directory", "");
        input.multiple = true;
        input.accept = ".md";
        $("dropzone-title").textContent = "点击选择或拖拽文件夹到此处";
        $("dropzone-hint").textContent = "会保留子目录结构，仅处理 .md 文件";
      } else if (state.uploadType === "file") {
        input.removeAttribute("webkitdirectory");
        input.removeAttribute("directory");
        input.multiple = false;
        input.accept = ".md";
        $("dropzone-title").textContent = "点击选择或拖拽单个 .md 文件";
        $("dropzone-hint").textContent = "支持 UTF-8 / GBK 编码";
      } else {
        input.removeAttribute("webkitdirectory");
        input.removeAttribute("directory");
        input.multiple = false;
        input.accept = ".zip";
        $("dropzone-title").textContent = "点击选择或拖拽 ZIP 到此处";
        $("dropzone-hint").textContent = "ZIP 包内可含多级目录，仅处理 .md 文件";
      }
    }

    configureInput();

    zone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        input.click();
      }
    });
    ["dragenter", "dragover"].forEach((name) => {
      zone.addEventListener(name, (event) => {
        event.preventDefault();
        zone.classList.add("is-dragover");
      });
    });
    ["dragleave", "drop"].forEach((name) => {
      zone.addEventListener(name, (event) => {
        event.preventDefault();
        if (name === "dragleave" && zone.contains(event.relatedTarget)) return;
        zone.classList.remove("is-dragover");
      });
    });
    zone.addEventListener("drop", (event) => {
      const files = event.dataTransfer && event.dataTransfer.files;
      if (files && files.length) handleFiles(files);
    });
    input.addEventListener("change", () => {
      if (input.files && input.files.length) handleFiles(input.files);
      input.value = "";
    });
  }

  async function handleFiles(fileList) {
    const files = Array.from(fileList);
    if (!files.length) return;

    if (state.uploadType === "zip" && !files[0].name.toLowerCase().endsWith(".zip")) {
      ui.toast({ kind: "warning", title: "请上传 ZIP 文件", desc: "或切换到“单文件 / 文件夹”模式" });
      return;
    }
    if (state.uploadType === "file" && !files[0].name.toLowerCase().endsWith(".md")) {
      ui.toast({ kind: "warning", title: "请上传 .md 文件" });
      return;
    }

    const formData = new FormData();
    formData.append("type", state.uploadType);
    if (state.uploadType === "zip") {
      formData.append("zipfile", files[0]);
    } else if (state.uploadType === "file") {
      formData.append("file", files[0]);
    } else {
      files.forEach((file) => formData.append("files", file, file.webkitRelativePath || file.name));
    }

    $("dropzone-title").textContent = "正在上传…";
    try {
      const data = await api.upload(formData);
      state.taskId = data.task_id;
      state.files = data.files || [];
      state.selectedFile = state.files.length ? state.files[0].path : "";
      renderFileList(data.task);
      $("card-config").hidden = false;
      $("card-progress").hidden = true;
      $("card-files").hidden = true;
      setStep(2);
      const summary = $("upload-summary");
      summary.hidden = false;
      summary.textContent = "已上传 " + state.files.length + " 个 Markdown 文件，共 " + ui.bytes(state.files.reduce((sum, item) => sum + (item.size || 0), 0));
      ui.toast({ kind: "success", title: "上传完成", desc: state.files.length + " 个文件待转换" });
      schedulePreview(true);
    } catch (err) {
      ui.toast({ kind: "error", title: "上传失败", desc: err.message });
    } finally {
      $("dropzone-title").textContent = state.uploadType === "folder" ? "点击选择或拖拽文件夹到此处" : state.uploadType === "file" ? "点击选择或拖拽单个 .md 文件" : "点击选择或拖拽 ZIP 到此处";
    }
  }

  function renderFileList(task) {
    const list = $("file-list");
    ui.clear(list);
    list.hidden = false;
    state.files.forEach((file) => {
      const row = document.createElement("div");
      row.className = "file-row";
      row.setAttribute("role", "button");
      row.tabIndex = 0;
      row.setAttribute("aria-selected", String(file.path === state.selectedFile));
      row.dataset.path = file.path;
      const name = ui.el("span", "file-name", file.path);
      const meta = ui.el("span", "file-meta", ui.bytes(file.size));
      row.appendChild(name);
      row.appendChild(meta);
      const select = () => {
        state.selectedFile = file.path;
        list.querySelectorAll(".file-row").forEach((node) => node.setAttribute("aria-selected", String(node.dataset.path === file.path)));
        schedulePreview(true);
      };
      row.addEventListener("click", select);
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          select();
        }
      });
      list.appendChild(row);
    });
  }

  /* ------------------------------------------------------------ 配置 */
  function collectOptions() {
    const glossary = state.glossary
      .filter((item) => item.source && item.target)
      .map((item) => ({ source: item.source.trim(), target: item.target.trim() }));
    const document_ = {
      cover: Boolean(document.querySelector('[data-doc="cover"]').checked),
      toc: Boolean(document.querySelector('[data-doc="toc"]').checked),
      header: Boolean(document.querySelector('[data-doc="header"]').checked),
      footer: true,
      page_number: Boolean(document.querySelector('[data-doc="page_number"]').checked),
      title: $("doc-title").value.trim(),
      subtitle: $("doc-subtitle").value.trim(),
      author: $("doc-author").value.trim(),
      date: $("doc-date").value.trim(),
    };
    return {
      target_language: $("target-language").value,
      export_mode: $("export-mode").value,
      bilingual_layout: $("bilingual-layout").value,
      theme: state.theme,
      tone: $("tone").value,
      glossary,
      style_options: {
        theme: state.theme,
        page_size: $("page-size").value,
        page_orientation: $("page-orientation").value,
        font_size: $("font-size").value,
        line_height: $("line-height").value,
        page_margin: $("page-margin").value.trim(),
        text_color: $("text-color").value,
        accent_color: $("accent-color").value,
      },
      document: document_,
    };
  }

  function syncConditionalFields() {
    const mode = $("export-mode").value;
    const isBilingual = mode === "bilingual";
    $("bilingual-layout").hidden = !isBilingual;
    $("bilingual-layout-field").hidden = !isBilingual;
    const needsTranslate = mode !== "original";
    $("target-language").disabled = !needsTranslate;
  }

  let previewTimer = null;
  function schedulePreview(force) {
    if (!state.taskId || !state.selectedFile) return;
    clearTimeout(previewTimer);
    previewTimer = setTimeout(() => {
      if (window.M2PPreview) {
        window.M2PPreview.render({
          taskId: state.taskId,
          file: state.selectedFile,
          mode: window.M2PPreview.currentMode(),
          options: collectOptions(),
          force,
        });
      }
    }, force ? 60 : 420);
  }

  function renderGlossary() {
    const list = $("glossary-list");
    ui.clear(list);
    if (!state.glossary.length) {
      list.appendChild(ui.el("div", "field-hint", "尚未添加术语。可点击“自动提取候选”从原文提取，或手动添加。"));
      return;
    }
    state.glossary.forEach((entry, index) => {
      const row = document.createElement("div");
      row.className = "glossary-row";
      const source = document.createElement("input");
      source.className = "input";
      source.value = entry.source;
      source.placeholder = "原文术语";
      source.setAttribute("aria-label", "原文术语 " + (index + 1));
      source.addEventListener("input", () => { entry.source = source.value; schedulePreview(); });
      const arrow = ui.el("span", "glossary-arrow", "→");
      const target = document.createElement("input");
      target.className = "input";
      target.value = entry.target;
      target.placeholder = "固定译法";
      target.setAttribute("aria-label", "固定译法 " + (index + 1));
      target.addEventListener("input", () => { entry.target = target.value; schedulePreview(); });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn btn-icon btn-sm";
      remove.setAttribute("aria-label", "删除术语 " + (index + 1));
      if (window.M2PIcon) remove.appendChild(window.M2PIcon("trash", "icon-sm"));
      remove.addEventListener("click", () => {
        state.glossary.splice(index, 1);
        renderGlossary();
        schedulePreview();
      });
      row.appendChild(source);
      row.appendChild(arrow);
      row.appendChild(target);
      row.appendChild(remove);
      list.appendChild(row);
    });
  }

  function setupConfigEvents() {
    ["target-language", "export-mode", "bilingual-layout", "tone", "page-size", "page-orientation", "font-size", "line-height"].forEach((id) => {
      $(id).addEventListener("change", () => {
        syncConditionalFields();
        schedulePreview(true);
      });
    });
    ["page-margin", "text-color", "accent-color", "doc-title", "doc-subtitle", "doc-author", "doc-date"].forEach((id) => {
      $(id).addEventListener("input", () => schedulePreview());
    });
    document.querySelectorAll("[data-doc]").forEach((node) => {
      node.addEventListener("change", () => schedulePreview(true));
    });
    $("add-glossary").addEventListener("click", () => {
      state.glossary.push({ source: "", target: "" });
      renderGlossary();
      const inputs = $("glossary-list").querySelectorAll("input");
      if (inputs.length) inputs[inputs.length - 2].focus();
    });
    $("suggest-glossary").addEventListener("click", suggestGlossary);
    $("start-btn").addEventListener("click", startConversion);
    $("reset-btn").addEventListener("click", resetWorkspace);
    $("pause-btn").addEventListener("click", () => control("pause"));
    $("resume-btn").addEventListener("click", () => control("resume"));
    $("cancel-btn").addEventListener("click", () => control("cancel"));
    $("download-btn").addEventListener("click", () => {
      if (state.taskId) window.location.href = api.downloadUrl(state.taskId);
    });
  }

  async function suggestGlossary() {
    if (!state.taskId || !state.selectedFile) {
      ui.toast({ kind: "warning", title: "请先上传并选择文件" });
      return;
    }
    const button = $("suggest-glossary");
    button.disabled = true;
    button.textContent = "提取中…";
    try {
      const data = await api.suggestGlossary({
        task_id: state.taskId,
        file: state.selectedFile,
        target_language: $("target-language").value,
        max_terms: 30,
      });
      if (!data.terms || !data.terms.length) {
        ui.toast({ kind: "info", title: "未提取到候选术语", desc: "可手动添加，或换一篇术语更密集的文档" });
        return;
      }
      const existing = new Set(state.glossary.map((item) => item.source.toLowerCase()));
      data.terms.forEach((term) => {
        if (!existing.has(term.source.toLowerCase())) state.glossary.push({ source: term.source, target: term.target });
      });
      renderGlossary();
      ui.toast({ kind: "success", title: "已提取 " + data.terms.length + " 个候选术语", desc: data.mode === "ai" ? "由 AI 抽取，请核对后使用" : "离线启发式抽取，请核对后使用" });
      schedulePreview();
    } catch (err) {
      ui.toast({ kind: "error", title: "术语提取失败", desc: err.message });
    } finally {
      button.disabled = false;
      button.textContent = "自动提取候选";
    }
  }

  /* ------------------------------------------------------------ 转换 */
  async function startConversion() {
    if (!state.taskId) return;
    const button = $("start-btn");
    button.disabled = true;
    try {
      await api.start(state.taskId, collectOptions());
      $("card-progress").hidden = false;
      $("card-files").hidden = true;
      $("download-btn").hidden = true;
      state.lastLogCount = 0;
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

  async function control(action) {
    if (!state.taskId) return;
    try {
      await api[action](state.taskId);
      poll();
    } catch (err) {
      ui.toast({ kind: "error", title: "操作失败", desc: err.message });
    }
  }

  function startPolling() {
    stopPolling();
    state.polling = setInterval(poll, 900);
  }

  function stopPolling() {
    if (state.polling) clearInterval(state.polling);
    state.polling = null;
  }

  async function poll() {
    if (!state.taskId) return;
    let data;
    try {
      data = await api.status(state.taskId);
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
    $("stat-total").textContent = (data.counts && data.counts.total) || state.files.length;
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
      await api.retry(state.taskId, path);
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
    schedulePreview(true);
  }

  function setStep(step) {
    document.querySelectorAll(".step").forEach((node) => {
      const value = Number(node.dataset.step);
      node.dataset.state = value < step ? "done" : value === step ? "active" : "pending";
    });
  }

  function resetWorkspace() {
    stopPolling();
    state.taskId = null;
    state.files = [];
    state.selectedFile = "";
    state.glossary = [];
    state.theme = "default";
    $("card-config").hidden = true;
    $("card-progress").hidden = true;
    $("card-files").hidden = true;
    $("file-list").hidden = true;
    $("upload-summary").hidden = true;
    renderGlossary();
    if (window.M2PPreview) window.M2PPreview.reset();
    setStep(1);
    renderThemes();
  }

  function init() {
    setupUpload();
    setupConfigEvents();
    renderGlossary();
    loadMeta().catch((err) => ui.toast({ kind: "error", title: "无法加载元数据", desc: err.message }));
    document.addEventListener("m2p:preview-request", () => schedulePreview(true));
    document.addEventListener("m2p:tasks-changed", () => {});
    document.addEventListener("m2p:resume-task", async (event) => {
      const taskId = event.detail && event.detail.taskId;
      if (!taskId) return;
      try {
        const data = await api.getTask(taskId);
        state.taskId = taskId;
        state.files = (data.task.files || []).map((file) => ({ path: file.path, size: 0 }));
        state.selectedFile = state.files.length ? state.files[0].path : "";
        renderFileList(data.task);
        $("card-config").hidden = false;
        $("card-progress").hidden = false;
        $("card-files").hidden = !(data.task.files || []).length;
        setStep(3);
        renderResults(data.task.files || []);
        startPolling();
        document.dispatchEvent(new CustomEvent("m2p:show-view", { detail: { view: "workspace" } }));
      } catch (err) {
        ui.toast({ kind: "error", title: "无法打开任务", desc: err.message });
      }
    });
  }

  window.M2PWorkspace = { init, state, schedulePreview };
})();
