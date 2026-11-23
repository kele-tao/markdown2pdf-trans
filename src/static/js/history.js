/* 历史任务页：列表、搜索、重下载、删除、继续 */
(function () {
  "use strict";

  const api = window.M2PApi;
  const ui = window.M2PUI;
  const $ = (id) => document.getElementById(id);
  let timer = null;

  async function load() {
    const params = { limit: 50, offset: 0 };
    const query = $("history-search").value.trim();
    const state = $("history-state").value;
    if (query) params.q = query;
    if (state) params.state = state;
    const body = $("history-body");
    body.innerHTML = "";
    try {
      const data = await api.listTasks(params);
      render(data.items || []);
    } catch (err) {
      ui.toast({ kind: "error", title: "无法加载历史任务", desc: err.message });
    }
  }

  function render(items) {
    const body = $("history-body");
    const empty = $("history-empty");
    ui.clear(body);
    empty.hidden = items.length > 0;
    items.forEach((task) => {
      const tr = document.createElement("tr");

      const titleCell = document.createElement("td");
      titleCell.className = "cell-title";
      titleCell.appendChild(ui.el("div", "", task.title || task.id.slice(0, 8)));
      titleCell.appendChild(ui.el("div", "cell-sub", ui.time(task.created_at) + " · " + task.id.slice(0, 8)));

      const stateCell = document.createElement("td");
      const badge = ui.badge(ui.STATE_LABEL[task.state] || task.state, ui.STATE_TONE[task.state] || "info", task.state === "RUNNING");
      stateCell.appendChild(badge);
      if (task.error) {
        const hint = ui.el("div", "cell-sub", task.error.slice(0, 60));
        hint.title = task.error;
        stateCell.appendChild(hint);
      }

      const fileCell = document.createElement("td");
      fileCell.appendChild(ui.el("div", "", task.success_count + " / " + task.file_count));
      if (task.failed_count) {
        const fail = ui.el("div", "cell-sub", task.failed_count + " 失败");
        fail.style.color = "var(--danger)";
        fileCell.appendChild(fail);
      }

      const modeCell = document.createElement("td");
      modeCell.appendChild(ui.el("div", "", task.target_language || "—"));
      modeCell.appendChild(ui.el("div", "cell-sub", exportLabel(task.export_mode)));

      const timeCell = document.createElement("td");
      timeCell.textContent = ui.duration(task.elapsed_ms);

      const actionCell = document.createElement("td");
      actionCell.className = "cell-actions";
      if (task.state === "SUCCESS" || task.result_url) {
        const download = document.createElement("button");
        download.type = "button";
        download.className = "btn btn-sm";
        download.textContent = "下载";
        download.addEventListener("click", () => { window.location.href = api.downloadUrl(task.id); });
        actionCell.appendChild(download);
      }
      const open = document.createElement("button");
      open.type = "button";
      open.className = "btn btn-sm btn-ghost";
      open.textContent = "打开";
      open.addEventListener("click", () => {
        document.dispatchEvent(new CustomEvent("m2p:resume-task", { detail: { taskId: task.id } }));
      });
      actionCell.appendChild(open);

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn btn-sm btn-ghost btn-danger";
      remove.textContent = "删除";
      remove.addEventListener("click", async () => {
        if (!window.confirm("删除任务 " + (task.title || task.id.slice(0, 8)) + " 及其所有产物？该操作不可恢复。")) return;
        try {
          await api.remove(task.id);
          ui.toast({ kind: "success", title: "已删除任务" });
          load();
        } catch (err) {
          ui.toast({ kind: "error", title: "删除失败", desc: err.message });
        }
      });
      actionCell.appendChild(remove);

      tr.appendChild(titleCell);
      tr.appendChild(stateCell);
      tr.appendChild(fileCell);
      tr.appendChild(modeCell);
      tr.appendChild(timeCell);
      tr.appendChild(actionCell);
      body.appendChild(tr);
    });
  }

  function exportLabel(code) {
    return { translated: "仅译文", original: "仅原文", bilingual: "双语对照" }[code] || code || "—";
  }

  function init() {
    $("history-refresh").addEventListener("click", load);
    $("history-state").addEventListener("change", load);
    $("history-search").addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(load, 320);
    });
  }

  window.M2PHistory = { init, load };
})();
