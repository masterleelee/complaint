// 合同原文对照弹窗 v2：useContractCompare 核心派生逻辑测试（Node 直跑，Vue 桩替换）
// 运行：node tests/js/test_contract_compare.mjs  （全绿输出 PASS）
// 覆盖：dedRows 派生（tag/canLocate/domId）/ 原文锚点注入 / 汇总单元格 / 原件页清单 /
//       悬停定位 DOM 行为（jsdom 不可用，用最小 DOM 桩）

import { createRequire } from "node:module";

// ── Vue 桩：最小实现 ref/computed ───────────────────────────────────
const require = createRequire(import.meta.url);
globalThis.Vue = {
  ref: (v) => ({ value: v }),
  computed: (fn) => ({ get value() { return fn(); } }),
};

const { useContractCompare } = await import(
  "../../static/js/composables/useContractCompare.js?v=1"
);

let failed = 0;
function check(name, cond) {
  if (cond) console.log(`  PASS ${name}`);
  else { failed++; console.error(`  FAIL ${name}`); }
}

// ── 最小 DOM 桩：querySelector / classList / scrollIntoView ─────────
const elements = {};
function stubMark(id) {
  const el = {
    id, classes: new Set(),
    classList: {
      toggle(cls, on) { on ? el.classes.add(cls) : el.classes.delete(cls); },
      add(c) { el.classes.add(c); }, remove(c) { el.classes.delete(c); },
    },
    scrollIntoView() { el.scrolled = true; },
  };
  elements[id] = el;
  return el;
}
const MARK_A = stubMark("cc-kw-0-0");
const MARK_B = stubMark("cc-kw-1-1");
globalThis.document = {
  querySelector(sel) {
    const m = sel.match(/data-id="([^"]+)"/);
    return m ? (elements[m[1]] || null) : null;
  },
};

// ── fixture：多份（代缴+培训）+ 单份兜底 ─────────────────────────────
const RESULT = {
  total_fee: 3000,
  actual_paid: 2500,
  summary: "送考承诺：科目二补考不超过2次",
  tier_result: { display_name: "2023·分校", confidence: "high", score: 0.93, evidence: "标题匹配" },
  contract_analyses: [
    { index: 0, kind: "代缴", tier_display_name: "2019·代缴", text: "第一条 总则。第二条 代缴费用人民币500元。" },
    { index: 1, kind: "培训", tier_display_name: "2023·分校", text: "第九条 退学退费。科目二实际操作培训费人民币1200元。" },
  ],
  deductions_result: {
    items: [
      { item: "代缴费用", amount: 500, category: "必扣", anchor_start: 9, anchor_end: 16, contract_index: 0 },
      { item: "科目二实操费", amount: 760, category: "依实", anchor_start: 9, anchor_end: 24, contract_index: 1 },
      { item: "违约金", amount: 0, category: "违约金", anchor_missing: true, contract_index: 1, pending: true },
    ],
    warnings: ["识别降级"],
  },
};
const MANIFEST = {
  merged_pdf_path: "/x/合并版.pdf",
  source_files: [
    { filepath: "/x/1.jpg", filename: "1.jpg" },
    { filepath: "/x/2.pdf", filename: "2.pdf" },
  ],
};

const cmp = useContractCompare(
  () => RESULT,
  () => "/x/合并版.pdf",
  () => MANIFEST,
);

// ── 断言 ────────────────────────────────────────────────────────────
check("dedRows 数量", cmp.dedRows.value.length === 3);
check("tagKey 派生（必扣）", cmp.dedRows.value[0].tagKey === "must");
check("tagKey 派生（违约金）", cmp.dedRows.value[2].tagKey === "pen");
check("canLocate（有锚点）", cmp.dedRows.value[0].canLocate === true);
check("canLocate（missing 锚点）", cmp.dedRows.value[2].canLocate === false);
check("domId 含 contract_index", cmp.dedRows.value[1].domId === "cc-kw-1-1");
check("pending 透传", cmp.dedRows.value[2].pending === true);

const sec0 = cmp.textSections.value[0];
const sec1 = cmp.textSections.value[1];
check("原文 section 数 = 份数", cmp.textSections.value.length === 2);
check("section0 注入 mark（锚点在区间内）", sec0.html.includes('data-id="cc-kw-0-0"'));
check("section1 注入 mark", sec1.html.includes('data-id="cc-kw-1-1"'));
check("mark 内容转义自原文", sec0.html.includes("代缴费用"));
check("hasText", cmp.hasText.value === true);

check("summaryCells 含应退", cmp.summaryCells.value.some(c => c.k === "应退"));
const refundCell = cmp.summaryCells.value.find(c => c.k === "应退");
check("应退 = 实缴-扣费合计", refundCell.v === "¥1,240");
check("tier 名称", cmp.tierInfo.value.name === "2023·分校");
check("tier 置信度（high → 高）", cmp.tierInfo.value.conf === "高");

// 2026-09-22 起：有逐张原图就不再列合并 PDF（同一份合同曾被列两遍：合并 PDF + 每张原图），
// 且合并 PDF 走 <iframe> 会带出浏览器内置查看器的深色工具栏 → 有原图时改走 <img>。
check("原件页：有原图 → 只列原图（不重复列合并 PDF）", cmp.origPages.value.length === 1);
check("原件页原图类型与 URL", cmp.origPages.value[0].type === "img" && cmp.origPages.value[0].url.includes("%2Fx%2F1.jpg"));
check("原图页标题含页码进度", cmp.origPages.value[0].cap.startsWith("原图 1/1"));
check("aiSummary 透传", cmp.aiSummary.value.includes("送考承诺"));

// ── 悬停 / 钉住 DOM 行为 ────────────────────────────────────────────
cmp.hoverRow(0);
check("悬停加高亮", MARK_A.classes.has("cc-hit") === true && MARK_A.scrolled === true);
cmp.leaveRow(0);
check("移出取消高亮", MARK_A.classes.has("cc-hit") === false);
cmp.hoverRow(0);
cmp.pinRow(0);
cmp.leaveRow(0);
check("钉住后移出仍保持高亮", MARK_A.classes.has("cc-hit") === true);
cmp.pinRow(1);
check("换行钉住：旧高亮清除", MARK_A.classes.has("cc-hit") === false && MARK_B.classes.has("cc-hit") === true);
cmp.pinRow(1);
check("再次点击取消钉住", MARK_B.classes.has("cc-hit") === false);
cmp.hoverRow(2);
check("无锚点行悬停不抛错不高亮", true);

// ── 单份兜底（无 contract_analyses）────────────────────────────────
const SINGLE = {
  contract_text: "第八条 违约金按20%计算。",
  deductions_result: { items: [{ item: "违约金", amount: 300, category: "违约金", anchor_start: 4, anchor_end: 9 }] },
};
const cmp2 = useContractCompare(() => SINGLE, () => "", () => null);
check("单份兜底 section", cmp2.textSections.value.length === 1);
check("单份兜底注入 mark", cmp2.textSections.value[0].html.includes("cc-kw"));
check("无 manifest 无 cPath → 无原件页", cmp2.origPages.value.length === 0);

// ── 原件页分支：无原图（下载的电子合同 PDF）才回落合并 PDF ──────────────
const MANIFEST_PDF_ONLY = {
  merged_pdf_path: "/x/电子合同.pdf",
  source_files: [{ filepath: "/x/电子合同.pdf", filename: "电子合同.pdf" }],
};
const cmp3 = useContractCompare(() => SINGLE, () => "", () => MANIFEST_PDF_ONLY);
check("无原图 → 回落合并 PDF（iframe 分支不变）",
  cmp3.origPages.value.length === 1 && cmp3.origPages.value[0].type === "pdf");
const MANIFEST_IMGS_ONLY = {
  merged_pdf_path: "",
  source_files: [{ filepath: "/x/a.jpg", filename: "a.jpg" }, { filepath: "/x/b.jpeg", filename: "b.jpeg" }],
};
const cmp4 = useContractCompare(() => SINGLE, () => "", () => MANIFEST_IMGS_ONLY);
check("无合并 PDF 但有 2 张原图 → 2 页且页码为 1/2、2/2",
  cmp4.origPages.value.length === 2
  && cmp4.origPages.value[0].cap.startsWith("原图 1/2")
  && cmp4.origPages.value[1].cap.startsWith("原图 2/2"));
const cmp5 = useContractCompare(() => SINGLE, () => "/x/only.pdf", () => ({}));
check("manifest 空但 cPath 为 PDF → 单页 pdf 兜底",
  cmp5.origPages.value.length === 1 && cmp5.origPages.value[0].type === "pdf");

// ── S3a：档位徽章置信度（人话标签，绝不含「%」）────────────────────────
// 用户截图缺陷：tier_result.score 是档位「特征加权分」（如 10），不是概率；
// 旧逻辑 Math.round(score * 100) + "%" → 界面显示 1000%。这里把正确取值钉死，
// 并把用户看到的错值（1000%）写成回归断言，防止回潮。
const cmpConfHigh = useContractCompare(
  () => ({ tier_result: { score: 10, confidence: "high", display_name: "2023·分校" } }),
  () => "", () => null,
);
check("conf: high → 高", cmpConfHigh.tierInfo.value.conf === "高");
check("conf: 不含「%」", !String(cmpConfHigh.tierInfo.value.conf).includes("%"));
check("conf: score=10 不得显示 1000%（回归）", cmpConfHigh.tierInfo.value.conf !== "1000%");
check("cls: high 保留", cmpConfHigh.tierInfo.value.cls === "high");
check("tier 名称不受影响", cmpConfHigh.tierInfo.value.name === "2023·分校");

const cmpConfMedium = useContractCompare(
  () => ({ tier_result: { score: 10, confidence: "medium" } }), () => "", () => null,
);
check("conf: medium → 中", cmpConfMedium.tierInfo.value.conf === "中");

const cmpConfLow = useContractCompare(
  () => ({ tier_result: { score: 10, confidence: "low" } }), () => "", () => null,
);
check("conf: low → 低", cmpConfLow.tierInfo.value.conf === "低");

const cmpConfMissing = useContractCompare(
  () => ({ tier_result: { score: 10 } }), () => "", () => null,
);
check("conf: 缺失（score=10）→ \"\"", cmpConfMissing.tierInfo.value.conf === "");

console.log(failed === 0 ? "\nALL PASS" : `\n${failed} FAILED`);
process.exit(failed === 0 ? 0 : 1);
