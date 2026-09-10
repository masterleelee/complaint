// 回归：useComplaint.updateQueryProgress 对 sources 中非进度卡键（third_profile）
// 的容错。2026-09-03 修复「查询失败: Cannot set properties of undefined
// (setting 'class')」——后端 _merge_results 在 sources 里加了第四个键
// third_profile，前端进度卡只初始化 internal/third/driving，导致崩溃。
// 运行：node tests/js/test_query_progress.mjs  （全绿输出 PASS）

import { createRequire } from "node:module";
import { readFileSync, writeFileSync, rmSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const require = createRequire(import.meta.url);
const __dirname = dirname(fileURLToPath(import.meta.url));

// ── Vue 桩：最小实现 ref/reactive ────────────────────────────────────
globalThis.Vue = {
  ref: (v) => ({ value: v }),
  reactive: (o) => o,
  computed: (fn) => ({ get value() { return fn(); } }),
  nextTick: async () => {},
};

// ── 裸模块名替换为本地桩（api / helpers） ─────────────────────────────
let src = readFileSync(
  join(__dirname, "../../static/js/composables/useComplaint.js"),
  "utf8"
);
src = src.replace(/from\s+"api"/g, 'from "./stubs/api.mjs"');
src = src.replace(/from\s+"helpers"/g, 'from "./stubs/helpers.mjs"');
// data: URL 模块无法解析相对导入，落盘为临时文件再 import
const tmpMod = join(__dirname, ".tmp_useComplaint.mjs");
writeFileSync(tmpMod, src);
const mod = await import(tmpMod);
rmSync(tmpMod, { force: true });

let failed = 0;
function check(name, cond) {
  if (cond) console.log(`  PASS ${name}`);
  else { failed++; console.error(`  FAIL ${name}`); }
}

const { useComplaint } = mod;
const c = useComplaint();

// ── 用例 1：sources 含 third_profile 键不崩溃（修复点） ────────────────
c.resetQueryProgress();
let threw = null;
try {
  c.updateQueryProgress({
    internal: "success",
    third: "profile",
    driving: "pending",
    third_profile: "success",
  });
} catch (e) { threw = e; }
check("third_profile 键不抛异常", threw === null);
const qp = c.queryProgress;
check("third 橙档样式 source-warning", qp.items.third.class === "source-warning");
check("third 图标 bi-folder-check", qp.items.third.icon === "bi-folder-check");
check("profile 计入完成数（percent=67）", qp.percent === 67);
check("third 提示文案", qp.items.third.tip.startsWith("查到学员档案"));
check("third_profile 未生成进度卡", !qp.items.third_profile);

// ── 用例 2：未知键 + 未知状态值均不崩溃 ───────────────────────────────
c.resetQueryProgress();
threw = null;
try {
  c.updateQueryProgress({ internal: "success", some_future_key: "weird" });
} catch (e) { threw = e; }
check("未知键/状态不抛异常", threw === null);
check("internal 正常更新", qp.items.internal.class === "source-success");

// ── 用例 3：三系统全绿走 done 流程 ───────────────────────────────────
c.resetQueryProgress();
c.updateQueryProgress({
  internal: "success", third: "success", driving: "success", third_profile: "success",
});
check("全部完成 done=true", qp.done === true);
check("percent=100", qp.percent === 100);
check("完成文案", qp.message === "查询完成");

process.exit(failed ? 1 : 0);
