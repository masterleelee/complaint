// API 请求工具
async function _fetch(url, options = {}) {
  try {
    const resp = await fetch(url, options);
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
