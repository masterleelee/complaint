// 跨标签页登录态同步 + 30s 心跳兜底
// 解决问题：同一账号在另一处登录后，本标签页要尽快知情并提示用户
// 实现思路：
//   1. BroadcastChannel('auth') 监听其他标签发来的消息
//      - 收到 {type: 'logged_in'}：主动调一次 /api/session/me 验证身份
//        若 401 → 存 kick_reason + 跳登录页
//      - 收到 {type: 'kicked'}：本标签已是受害方，存 kick_reason + 跳登录页
//   2. setInterval 每 30s 调一次 /api/session/me 兜底
//      - 防止停留在纯静态页时永远不触发请求
//      - 30s 一发，登录用户 10 人并发时 20 req/min，Flask 几乎无感

import { getJ } from "api";
import { _recordKickReason } from "api";

const HEARTBEAT_MS = 30_000;

let _channel = null;
let _timer = null;
let _started = false;

function _goLogin() {
  if (window.location.pathname === "/login") return;
  window.location.href = "/";
}

async function _verify() {
  try {
    const r = await getJ("/api/session/me");
    if (r && r.code === "unauthorized") {
      _recordKickReason("unauthorized");
      _goLogin();
    }
  } catch (e) { /* 网络错误静默，下次心跳重试 */ }
}

function _onMessage(ev) {
  const data = ev && ev.data;
  if (!data || !data.type) return;
  if (data.type === "logged_in") {
    // 别的标签有人刚登录成功，可能是本账号也可能是别人；主动验一下
    _verify();
  } else if (data.type === "kicked") {
    // 别的标签确认被踢
    _recordKickReason(data.code || "unauthorized");
    _goLogin();
  }
}

export function startAuthWatch() {
  if (_started) return;
  _started = true;
  try {
    if (typeof BroadcastChannel !== "undefined") {
      _channel = new BroadcastChannel("auth");
      _channel.addEventListener("message", _onMessage);
    }
  } catch (e) { /* 不支持就退化为纯心跳模式 */ }
  _timer = setInterval(_verify, HEARTBEAT_MS);
}

export function stopAuthWatch() {
  if (_timer) { clearInterval(_timer); _timer = null; }
  if (_channel) { try { _channel.close(); } catch (e) {} _channel = null; }
  _started = false;
}
