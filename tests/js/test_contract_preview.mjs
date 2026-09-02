// 工单 10：useContractPreview 多份归并派生逻辑测试（Node 直跑，Vue 桩替换）
// 运行：node tests/js/test_contract_preview.mjs  （全绿输出 PASS）

import { createRequire } from "node:module";

// ── Vue 桩：最小实现 ref/computed/nextTick ───────────────────────────
const require = createRequire(import.meta.url);
globalThis.Vue = {
  ref: (v) => ({ value: v }),
  computed: (fn) => ({ get value() { return fn(); } }),
  nextTick: async () => {},
};

const { useContractPreview } = await import(
  "../../static/js/composables/useContractPreview.js?v=2"
);

let failed = 0;
function check(name, cond) {
  if (cond) console.log(`  PASS ${name}`);
  else { failed++; console.error(`  FAIL ${name}`); }
}

// ── fixture：多份（代缴+培训）分析结果 ────────────────────────────────
const RESULT_MULTI = {
  total_fee: 3000,
  actual_paid: 2500,
  contract_count: 2,
  contract_analyses: [
    { index: 0, kind: "代缴", tier_display_name: "2019·代缴", tier_confidence: "high",
      filename: "a.pdf", text_source: "pdf_text", extraction_error: null,
      text: "前言内容\n第一条 代收代交考试费约定\n乙方考试费由甲方代收代交。\n第二条 其他\n无。" },
    { index: 1, kind: "培训", tier_display_name: "2019·培训", tier_confidence: "medium",
      filename: "b.pdf", text_source: "vision_text", extraction_error: null,
      text: "培训合同正文。\n第一条 理论培训费\n如退学应退回理论培训费。" },
  ],
  deductions_result: {
    items: [
      { category: "依实", item: "科目一考试费", amount: 70, pending: false,
        contract_index: 0, contract_kind: "代缴", contract_file: "a.pdf",
        anchor_start: 13, anchor_end: 21, anchor_missing: false },
      { category: "依实", item: "理论培训费", amount: 0, pending: true,
        contract_index: 1, contract_kind: "培训", contract_file: "b.pdf",
        anchor_missing: true },
    ],
    refund_pending: true,
  },
};

const RESULT_SINGLE_FALLBACK = {
  total_fee: 3000,
  contract_text: "无条款结构的一段合同正文。",
  deductions: [{ category: "必扣", item: "服务费", amount: 600, pending: false }],
};

const cp = useContractPreview(() => RESULT_MULTI, () => "");
const sections = cp.contractSections.value;

check("sections 数 = 2", sections.length === 2);
check("sec0 kind=代缴", sections[0].kind === "代缴");
check("sec1 kind=培训", sections[1].kind === "培训");
check("sec0 条款切分 ≥2", sections[0].clauses.length >= 2);
check("sec1 anchor_missing 项不注入 mark（html 无 <mark）",
  !sections[1].clauses.some((cl) => cl.html.includes("<mark")));
check("sec0 考试费条款注入 mark（html 含 <mark）",
  sections[0].clauses.some((cl) => cl.html.includes("<mark")));
check("mark data-id 与明细 _domId 对齐", (() => {
  const ded = cp.deductions.value[0];
  return sections[0].clauses.some((cl) => cl.html.includes(`data-id="${ded._domId}"`));
})());
check("每条明细带份标签", cp.deductions.value.every((d) => d._kindLabel));
check("contractCount=2（已识别 2 份）", cp.contractCount.value === 2);
check("contractKinds=[代缴,培训]", JSON.stringify(cp.contractKinds.value) === '["代缴","培训"]');
check("totalTextLength = 两份文本之和", cp.totalTextLength.value ===
  RESULT_MULTI.contract_analyses[0].text.length + RESULT_MULTI.contract_analyses[1].text.length);
check("summary.refundPending=true", cp.summary.value.refundPending === true);

// ── 兜底：无 contract_analyses（下载件/旧数据）→ 单节 ────────────────
const cp2 = useContractPreview(() => RESULT_SINGLE_FALLBACK, () => "");
check("fallback 单节", cp2.contractSections.value.length === 1);
check("fallback 不显示份数（contractKinds 空）", cp2.contractKinds.value.length === 0);
check("fallback 原文渲染", cp2.contractSections.value[0].text.includes("合同正文"));

// ── 未识别份计数不猜测 ────────────────────────────────────────────────
const RESULT_PARTIAL = {
  contract_analyses: [
    { index: 0, kind: "代缴", text: "x", extraction_error: null },
    { index: 1, kind: "", text: "", extraction_error: "提取失败" },
  ],
  deductions_result: { items: [], refund_pending: true },
};
const cp3 = useContractPreview(() => RESULT_PARTIAL, () => "");
check("部分识别：contractCount 只数已识别份", cp3.contractCount.value === 1);
check("部分识别：未识别份 kind 空串（前端显示未识别）",
  cp3.contractSections.value[1].kind === "" && cp3.contractSections.value[1].error === "提取失败");

console.log(failed === 0 ? "\nALL PASS" : `\n${failed} FAILED`);
process.exit(failed === 0 ? 0 : 1);
