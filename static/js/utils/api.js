// API 请求工具

// 把"被踢原因"暂存到 sessionStorage，供 login.html 展示横幅
export function _recordKickReason(code) {
  try {
    sessionStorage.setItem(
      "kick_reason",
      JSON.stringify({ code: code || "unauthorized", ts: Date.now(), from: location.pathname })
    );
  } catch (e) { /* sessionStorage 不可用就静默 */ }
  // 通知同源其他标签页：本标签已确认被踢
  try {
    if (typeof BroadcastChannel !== "undefined") {
      const bc = new BroadcastChannel("auth");
      bc.postMessage({ type: "kicked", code: code || "unauthorized", at: Date.now() });
      bc.close();
    }
  } catch (e) { /* BroadcastChannel 不可用就静默 */ }
}

async function _fetch(url, options = {}) {
  try {
    const resp = await fetch(url, options);
    // 未登录 / 被踢 → 存原因 + 跳登录页（但放过登录接口本身的 401 提示）
    if (resp.status === 401 && !url.includes("/api/session/login")) {
      _recordKickReason("unauthorized");
      if (window.location.pathname !== "/login") {
        window.location.href = "/";
      }
      return { success: false, error: "登录已失效，请重新登录", code: "unauthorized" };
    }
    const text = await resp.text();
    if (!text) return { error: "服务器返回空响应" };
    try {
      const data = JSON.parse(text);
      if (!resp.ok && !data.error) {
        data.error = `HTTP ${resp.status} ${resp.statusText || ""}`.trim();
      }
      return data;
    } catch {
      const preview = text.replace(/\s+/g, " ").trim().slice(0, 160);
      return { error: `HTTP ${resp.status}，服务器返回非JSON: ${preview || resp.statusText || "无内容"}` };
    }
  } catch (e) {
    return { error: "请求失败，请确认服务是否正常运行（" + e.message + "）" };
  }
}

export async function getJ(url) {
  return _fetch(url);
}

export async function postJ(url, body) {
  return _fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function putJ(url, body) {
  return _fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function delJ(url) {
  return _fetch(url, { method: "DELETE" });
}

export async function uploadFile(url, file, extraFields = {}) {
  const fd = new FormData();
  fd.append("file", file);
  for (const [k, v] of Object.entries(extraFields)) {
    fd.append(k, v);
  }
  return _fetch(url, { method: "POST", body: fd });
}

/**
 * 统一错误检查：后端返回 {success: false, error: "..."} 或 {error: "..."}
 * 调用方统一使用 checkError(result) 检查
 */
export function checkError(result) {
  if (!result) return "未知错误";
  if (result.success === false) return result.error || "操作失败";
  if (result.error && !result.success) return result.error;
  return null; // 无错误
}
