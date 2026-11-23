/* 配置中心：AI 配置读写、连接测试、运行环境自检 */
(function () {
  "use strict";

  const api = window.M2PApi;
  const ui = window.M2PUI;
  const $ = (id) => document.getElementById(id);

  async function load() {
    try {
      const data = await api.getSettings();
      const ai = data.ai || {};
      $("set-api-url").value = ai.api_url || "";
      $("set-model").value = ai.model || "";
      $("set-concurrency").value = ai.concurrency || 3;
      $("set-timeout").value = ai.timeout || 180;
      $("set-mock").checked = Boolean(ai.mock_enabled);
      $("set-api-key").value = "";
      $("set-api-key").placeholder = ai.key_set ? "已设置（" + ai.api_key_masked + "），留空不修改" : "未设置密钥";
      $("set-key-state").textContent = ai.key_set ? "已配置" : "未配置";
      $("settings-source").textContent = "来源：" + ({ env: "环境变量", runtime: "界面配置", default: "默认值" }[ai.source] || ai.source || "—");
      const mode = $("settings-mode");
      mode.textContent = ai.mode === "mock" ? "离线演示模式" : "真实 AI 模式";
      mode.className = "badge " + (ai.mode === "mock" ? "badge-warning" : "badge-success");
      renderLimits(data.limits || {}, data.version);
      updateHeader(ai);
    } catch (err) {
      ui.toast({ kind: "error", title: "无法读取配置", desc: err.message });
    }
  }

  function renderLimits(limits, version) {
    const body = $("limits-body");
    ui.clear(body);
    const rows = [
      ["版本", version || "—"],
      ["单次上传上限", limits.max_upload_mb + " MB"],
      ["单图上限", limits.max_image_mb + " MB"],
      ["任务保留", limits.task_ttl_hours + " 小时"],
    ];
    rows.forEach(([label, value]) => {
      const row = document.createElement("div");
      row.className = "switch-row";
      row.style.padding = "6px 0";
      row.appendChild(ui.el("span", "", label));
      const strong = ui.el("strong", "", value);
      row.appendChild(strong);
      body.appendChild(row);
    });
  }

  async function loadHealth() {
    const body = $("health-body");
    try {
      const data = await api.health();
      ui.clear(body);
      const rows = [
        ["状态", data.status, data.status === "ok" ? "success" : "warning"],
        ["Pandoc", data.checks.pandoc ? "可用" : "缺失", data.checks.pandoc ? "success" : "danger"],
        ["WeasyPrint", data.checks.weasyprint ? "可用" : "缺失", data.checks.weasyprint ? "success" : "danger"],
        ["本地字体", data.checks.fonts + " 个", data.checks.fonts ? "success" : "warning"],
        ["数据目录可写", data.checks.output_dir_writable ? "是" : "否", data.checks.output_dir_writable ? "success" : "danger"],
        ["翻译引擎", data.ai.mode === "mock" ? "离线 mock" : "真实 AI", data.ai.mode === "mock" ? "warning" : "success"],
        ["任务总数", String(data.tasks.total), "info"],
        ["运行时长", Math.round(data.uptime_s) + " 秒", "info"],
        ["磁盘可用", (data.disk_free_mb >= 0 ? data.disk_free_mb + " MB" : "未知"), "info"],
      ];
      rows.forEach(([label, value, tone]) => {
        const row = document.createElement("div");
        row.className = "switch-row";
        row.appendChild(ui.el("span", "", label));
        row.appendChild(ui.badge(String(value), tone));
        body.appendChild(row);
      });
    } catch (err) {
      ui.clear(body);
      body.appendChild(ui.el("div", "field-hint", "自检失败：" + err.message));
    }
  }

  function updateHeader(ai) {
    const badge = $("ai-status");
    const label = ai.mode === "mock" ? "离线演示 · " + (ai.model || "mock") : "已连接 · " + (ai.model || "AI");
    badge.textContent = label;
    badge.className = "badge badge-" + (ai.mode === "mock" ? "warning" : "success") + " badge-dot";
  }

  async function save() {
    const button = $("settings-save");
    button.disabled = true;
    try {
      const payload = {
        api_url: $("set-api-url").value.trim(),
        model: $("set-model").value.trim(),
        concurrency: Number($("set-concurrency").value) || 3,
        timeout: Number($("set-timeout").value) || 180,
        mock_enabled: $("set-mock").checked,
        clear_key: $("set-clear-key").checked,
      };
      const key = $("set-api-key").value.trim();
      if (key) payload.api_key = key;
      const data = await api.saveSettings(payload);
      $("set-api-key").value = "";
      $("set-clear-key").checked = false;
      updateHeader(data.ai || {});
      ui.toast({ kind: "success", title: "配置已保存", desc: "无需重启，立即生效" });
      load();
      loadHealth();
    } catch (err) {
      ui.toast({ kind: "error", title: "保存失败", desc: err.message });
    } finally {
      button.disabled = false;
    }
  }

  async function test() {
    const button = $("settings-test");
    button.disabled = true;
    button.textContent = "测试中…";
    try {
      const result = await api.testSettings({});
      ui.toast({
        kind: result.ok ? "success" : "error",
        title: result.ok ? "连接正常（" + result.mode + "）" : "连接失败",
        desc: result.message + (result.latency_ms ? " · " + result.latency_ms + " ms" : ""),
      });
    } catch (err) {
      ui.toast({ kind: "error", title: "连接失败", desc: err.message });
    } finally {
      button.disabled = false;
      button.textContent = "测试连接";
    }
  }

  function init() {
    $("settings-save").addEventListener("click", save);
    $("settings-test").addEventListener("click", test);
    $("health-refresh").addEventListener("click", loadHealth);
    $("set-clear-key").addEventListener("change", () => {
      if ($("set-clear-key").checked) $("set-api-key").value = "";
    });
  }

  window.M2PSettings = { init, load, loadHealth };
})();
