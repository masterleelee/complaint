// 回归：投诉列表「来源」列与新增投诉「来源渠道」下拉必须共用同一份值域。
//
// 背景 bug：两者读写的是同一个字段 source_channel，但列表侧的下拉选项曾被实现为
// 「规范 7 项 ∪ 库内出现过的所有值」（useWorkbench 的 channelOptions /
// clChannelEditOptions），筛选下拉同理。2026-08-29 历史资料导入沿用了原始台账
// 口径（东坑交通局 / 虎门交通局 / 客服热线…，15 个非规范值、80 条工单），于是这些
// 脏值顺着「列内下拉 + 筛选下拉 + 看板来源渠道 TOP5」重新冒出来。
//
// 2026-09-15 处置：存量由 scripts/normalize_source_channel.py 归一；前端值域收口为
// 单一常量 SOURCE_CHANNELS，受理页下拉与列表下拉同源渲染。本测试守住后半句：
// 列表侧选项**不再**受库内存量值影响。
//
// 运行：node tests/js/test_source_channel_options.mjs  （全绿输出 PASS）
import { readFileSync, writeFileSync, rmSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));

// ── Vue 桩：computed 每次求值（不能用缓存桩，本测试要在改 ref 后重读） ──
globalThis.Vue = {
  ref: (v) => ({ value: v }),
  reactive: (o) => o,
  computed: (fn) => ({ get value() { return fn(); } }),
  watch: () => () => {},
  nextTick: async () => {},
};

// ── 裸模块名替换为本地桩（api / helpers） ─────────────────────────────
let src = readFileSync(
  join(__dirname, "../../static/js/composables/useWorkbench.js"),
  "utf8"
);
src = src.replace(/from\s+"api"/g, 'from "./stubs/api.mjs"');
src = src.replace(/from\s+"helpers"/g, 'from "./stubs/helpers.mjs"');
const tmpMod = join(__dirname, ".tmp_useWorkbench.mjs");
writeFileSync(tmpMod, src);
let mod;
try {
  mod = await import(tmpMod);
} finally {
  rmSync(tmpMod, { force: true });   // 失败也清理，不留临时模块
}

let failed = 0;
function check(name, cond) {
  if (cond) console.log(`  PASS ${name}`);
  else { failed++; console.error(`  FAIL ${name}`); }
}

const CANONICAL = ["12345", "交通部门", "电话来访", "信访", "邮件投诉", "驾培协会", "其他途径"];

const wb = mod.useWorkbench(() => {}, () => null, () => null, () => null);

function ticket(id, name, channel) {
  return {
    id, student_name: name, source_channel: channel,
    complaint_date: "2026-09-01", complaint_type: "A", handle_status: "待处理",
    archive_status: "", withdraw_status: "", school_short: "南城",
    phone: "13800000000", created_at: "2026-09-01 10:00:00", updated_at: "2026-09-01 10:00:00",
  };
}

function allItems(groups) {
  return groups.flatMap((g) => g.items.map((t) => t.id));
}

// ── 1. 值域常量本身 ─────────────────────────────────────────────────
check("SOURCE_CHANNELS 已导出", Array.isArray(wb.SOURCE_CHANNELS));
check(
  "SOURCE_CHANNELS 恰为受理页那 7 项（含「其他途径」）",
  JSON.stringify(wb.SOURCE_CHANNELS) === JSON.stringify(CANONICAL)
);

// ── 2. 筛选下拉：不再受库内存量脏值影响（本次 bug 的核心回归点） ──────
wb.allTickets.value = [
  ticket("1", "甲", "交通部门"),
  ticket("2", "乙", "虎门交通局"),     // 历史导入的残留脏值
  ticket("3", "丙", "客服热线"),       // 同上
  ticket("4", "丁", ""),
];
check(
  "筛选下拉只列规范 7 项，不合并库内存量脏值",
  JSON.stringify(wb.channelOptions.value) === JSON.stringify(CANONICAL)
);
check(
  "筛选下拉不含「虎门交通局」",
  !wb.channelOptions.value.includes("虎门交通局")
);

// ── 3. 列内下拉：规范值工单只给 7 项；异常值兜底保留当前值 ────────────
check(
  "规范值工单的列内下拉 = 7 项规范值",
  JSON.stringify(wb.clChannelEditOptions(ticket("1", "甲", "交通部门"))) === JSON.stringify(CANONICAL)
);
check(
  "异常值工单的列内下拉含当前值（避免 <select> 退化成空白）",
  wb.clChannelEditOptions(ticket("2", "乙", "虎门交通局")).includes("虎门交通局")
);
check(
  "空值工单的列内下拉不含空字符串",
  !wb.clChannelEditOptions(ticket("4", "丁", "")).includes("")
);

// ── 4. 筛选语义：只能筛规范值，且「其他途径」覆盖自由文本 ─────────────
// 「其他途径」是兜底渠道：受理页选它时会把「具体途径」的自由文本直接写进
// source_channel（useComplaint.js 的 concreteChannel），所以它必须同时覆盖
// 字面值与任何非规范值，否则这些工单筛不出来。
wb.allTickets.value = [
  ticket("1", "甲", "交通部门"),
  ticket("2", "乙", "其他途径"),
  ticket("3", "丙", "微信小程序反馈"),   // 经「其他途径」录入的自由文本
  ticket("4", "丁", ""),                 // 未填
  ticket("5", "戊", "虎门交通局"),       // 若再出现残留脏值，归入「其他途径」
];

wb.clChannel.value = "";
check("不选渠道 → 全部 5 条", allItems(wb.listGroups.value).length === 5);

wb.clChannel.value = "交通部门";
check(
  "筛「交通部门」→ 只命中交通部门",
  JSON.stringify(allItems(wb.listGroups.value).sort()) === JSON.stringify(["1"])
);

wb.clChannel.value = "其他途径";
check(
  "筛「其他途径」→ 命中字面值 + 自由文本 + 非规范残留值，不命中未填",
  JSON.stringify(allItems(wb.listGroups.value).sort()) === JSON.stringify(["2", "3", "5"])
);

wb.clChannel.value = "信访";
check("筛一个库内 0 条的规范值 → 空结果而非全量", allItems(wb.listGroups.value).length === 0);

console.log(failed ? `\n${failed} 项失败` : "\n全部通过");
process.exit(failed ? 1 : 0);
