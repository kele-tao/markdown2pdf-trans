/* 后端 API 封装：统一错误处理、超时、错误码 */
(function () {
  "use strict";

  const state = { taskId: null, task: null, files: [], options: {} };

  class ApiError extends Error {
    constructor(message, code, status, detail) {
      super(message);
      this.name = "ApiError";
      this.code = code || "E_UNKNOWN";
      this.status = status || 0;
      this.detail = detail;
    }
  }

  async function request(path, options) {
    const opts = Object.assign({ headers: {} }, options || {});
    if (opts.body && !(opts.body instanceof FormData)) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    let response;
    try {
      response = await fetch(path, opts);
    } catch (err) {
      throw new ApiError("无法连接服务器，请确认服务正在运行", "E_NETWORK", 0);
    }
    const contentType = response.headers.get("Content-Type") || "";
    if (!response.ok) {
      let payload = {};
      if (contentType.includes("application/json")) {
        payload = await response.json().catch(() => ({}));
      }
      throw new ApiError(
        payload.error || "请求失败（HTTP " + response.status + "）",
        payload.code,
        response.status,
        payload.detail
      );
    }
    if (contentType.includes("application/pdf")) {
      return { blob: await response.blob(), headers: response.headers };
    }
    if (contentType.includes("application/json")) return response.json();
    return response.text();
  }

  const api = {
    state,
    ApiError,
    request,
    meta: () => request("/api/meta"),
    health: () => request("/api/health"),
    getSettings: () => request("/api/settings"),
    saveSettings: (payload) => request("/api/settings", { method: "PUT", body: payload }),
    testSettings: (payload) => request("/api/settings/test", { method: "POST", body: payload || {} }),
    upload: (formData) => request("/api/uploads", { method: "POST", body: formData }),
    listTasks: (params) => request("/api/tasks?" + new URLSearchParams(params || {}).toString()),
    getTask: (id) => request("/api/tasks/" + encodeURIComponent(id)),
    status: (id) => request("/api/tasks/" + encodeURIComponent(id) + "/status"),
    start: (id, payload) => request("/api/tasks/" + encodeURIComponent(id) + "/start", { method: "POST", body: payload }),
    pause: (id) => request("/api/tasks/" + encodeURIComponent(id) + "/pause", { method: "POST" }),
    resume: (id) => request("/api/tasks/" + encodeURIComponent(id) + "/resume", { method: "POST" }),
    cancel: (id) => request("/api/tasks/" + encodeURIComponent(id) + "/cancel", { method: "POST" }),
    retry: (id, file) => request("/api/tasks/" + encodeURIComponent(id) + "/retry", { method: "POST", body: file ? { file } : {} }),
    remove: (id) => request("/api/tasks/" + encodeURIComponent(id), { method: "DELETE" }),
    readFile: (id, path, mode) =>
      request("/api/tasks/" + encodeURIComponent(id) + "/files/" + path.split("/").map(encodeURIComponent).join("/") + "?mode=" + (mode || "source")),
    preview: (id, payload) => request("/api/tasks/" + encodeURIComponent(id) + "/preview", { method: "POST", body: payload }),
    suggestGlossary: (payload) => request("/api/glossary/suggest", { method: "POST", body: payload }),
    downloadUrl: (id) => "/api/tasks/" + encodeURIComponent(id) + "/download",
  };

  window.M2PApi = api;
})();
