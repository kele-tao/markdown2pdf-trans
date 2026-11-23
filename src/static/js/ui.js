/* UI 基础：Toast、格式化、剪贴板 */
(function () {
  "use strict";

  const stack = () => document.getElementById("toast-stack");

  function toast(options) {
    const opts = typeof options === "string" ? { title: options } : options || {};
    const node = document.createElement("div");
    node.className = "toast";
    node.dataset.kind = opts.kind || "info";
    node.setAttribute("role", opts.kind === "error" ? "alert" : "status");

    const iconName = { success: "check", error: "x", warning: "alert", info: "info" }[opts.kind || "info"];
    const iconWrap = document.createElement("span");
    iconWrap.className = "toast-icon";
    if (window.M2PIcon) iconWrap.appendChild(window.M2PIcon(iconName, "icon-sm"));

    const body = document.createElement("div");
    body.className = "toast-body";
    const title = document.createElement("div");
    title.className = "toast-title";
    title.textContent = opts.title || "";
    body.appendChild(title);
    if (opts.desc) {
      const desc = document.createElement("div");
      desc.className = "toast-desc";
      desc.textContent = opts.desc;
      body.appendChild(desc);
    }

    const close = document.createElement("button");
    close.className = "toast-close";
    close.type = "button";
    close.setAttribute("aria-label", "关闭提示");
    close.textContent = "×";
    close.addEventListener("click", () => dismiss());

    node.appendChild(iconWrap);
    node.appendChild(body);
    node.appendChild(close);
    stack().appendChild(node);

    let timer = setTimeout(dismiss, opts.duration || (opts.kind === "error" ? 7000 : 4200));
    function dismiss() {
      clearTimeout(timer);
      node.remove();
    }
    node.addEventListener("mouseenter", () => clearTimeout(timer));
    node.addEventListener("mouseleave", () => { timer = setTimeout(dismiss, 1800); });
    return dismiss;
  }

  function bytes(value) {
    if (value === null || value === undefined) return "—";
    const units = ["B", "KB", "MB", "GB"];
    let size = Number(value);
    let index = 0;
    while (size >= 1024 && index < units.length - 1) {
      size /= 1024;
      index += 1;
    }
    return (index === 0 ? size : size.toFixed(1)) + " " + units[index];
  }

  function duration(ms) {
    if (!ms) return "—";
    if (ms < 1000) return ms + " ms";
    const seconds = ms / 1000;
    if (seconds < 60) return seconds.toFixed(1) + " s";
    const minutes = Math.floor(seconds / 60);
    return minutes + " 分 " + Math.round(seconds - minutes * 60) + " 秒";
  }

  function time(epoch) {
    if (!epoch) return "—";
    const date = new Date(epoch * 1000);
    const pad = (n) => String(n).padStart(2, "0");
    return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate()) + " " + pad(date.getHours()) + ":" + pad(date.getMinutes());
  }

  const STATE_LABEL = {
    PREPARING: "准备中",
    READY: "待开始",
    RUNNING: "转换中",
    PAUSED: "已暂停",
    STOPPING: "取消中",
    STOPPED: "已停止",
    SUCCESS: "已完成",
    FAILURE: "有失败",
  };

  const STATE_TONE = {
    PREPARING: "info",
    READY: "info",
    RUNNING: "info",
    PAUSED: "warning",
    STOPPING: "warning",
    STOPPED: "warning",
    SUCCESS: "success",
    FAILURE: "danger",
  };

  function badge(text, kind, dot) {
    const span = document.createElement("span");
    span.className = "badge badge-" + (kind || "") + (dot ? " badge-dot" : "");
    span.textContent = text;
    return span;
  }

  function clear(node) {
    if (node) node.textContent = "";
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  window.M2PUI = {
    toast,
    bytes,
    duration,
    time,
    badge,
    clear,
    el,
    STATE_LABEL,
    STATE_TONE,
  };
})();
