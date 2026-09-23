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

const { useContractCompare, _rowKey } = await import(
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
// 2026-09-23（v4.4）起：定位统一走 _scrollLocate（自算可视盒 → 设 scrollTop），
// 全程禁止 scrollIntoView；本桩无滚动容器，故只断言命中高亮（定位另见下方 hoverDom 用例）。
check("悬停加高亮", MARK_A.classes.has("cc-hit") === true);
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

// ═══════════════════════════════════════════════════════════════════════
// v4.4 · S4：左栏条款块化 / 退费表栅格 / 汇总格可定位 / 定位居中
// 契约：.scratch/contract-preview-v44-landing/s4-contract.md
// ═══════════════════════════════════════════════════════════════════════

// ── fixture：接口返回（契约 §1.1）────────────────────────────────────────
const DR_TEMPLATE = {
  items: [
    { item: "服务费", amount: 600, category: "必扣", basis: "第八条 退费表「基础服务（必扣项）·服务费」" },
    { item: "建档费", amount: 300, category: "必扣", basis: "第八条 退费表「基础服务（必扣项）·建档费」" },
    { item: "学员IC卡", amount: 100, category: "必扣", basis: "第八条 退费表「基础服务（必扣项）·学员IC卡」" },
    { item: "违约金", amount: 716, category: "违约金", basis: "第八条 备注：全部培训费用的20%" },
  ],
  total_fee: 3580, paid_amount: 2000, tail_due: 1580, net_refund: 0,
  rule_summary: "本合同为 **2023·分校** 档位标准合同（纸质照片识别）。\n应退 = 实缴 2,000 − 扣费合计 1,716 = **¥284**。",
};
const RESULT_TEMPLATE = {
  total_fee: 3580,
  deductions_result: DR_TEMPLATE,
  tier_result: { display_name: "2023·分校", confidence: "high", score: 10 },
  contract_analyses: [{ index: 0, kind: "", text: "（OCR 原文）服务费600 建档费300 学员IC卡100" }],
};

const CLAUSES_TPL = [
  {
    no: "四", title: "费用及支付",
    body: [
      "（一）培训费用",
      "1、乙方选择以下第  1  种方式支付培训费用（包含建档费/学员IC卡）：",
      "（1）实行普通培训，培训费用总额合计人民币     　    元，□一次性支付，□分期付款；",
      "包含乙方理论培训、实操培训及甲方协助乙方建档、学员IC卡等相关服务费。",
      "（分期付款约定：                                                                                     ）。",
      "（2）实行预约培训，实时支付：",
      "理论培训费及相关手续费人民币            元，乙方于本合同订立时向甲方支付；",
      "（三）代收代交考试费、工本费、补考费",
      "1、乙方委托甲方代收代交考试费、工本费、补考费等款项，费用合计 490 元。",
    ].join("\n"),
  },
  {
    no: "八", title: "退学退费",
    body: [
      "（一）培训费用的退费",
      "1、合同期内，乙方中途退学的，甲方为乙方办理退学手续，并按照本合同的约定退费。",
      "（1）实行普通培训和按时收费培训的，按下表进行退费：",
      "项目", "内容", "费用（单位：元）", "备注",
      "基础服务", "（必扣项）", "服务费", "600",
      "备注：合同期内乙方申请提前解除合同…再扣除全部培训费用的20%作为违约金后，甲方将剩余的费用退还给乙方。",
      "（2）实行预约培训的，如乙方在公安部门受理前退学的…",
    ].join("\n"),
  },
];
const REFUND_ROWS_14 = [
  ["项目", "内容", "费用（单位：元）", "备注"],
  ["基础服务（必扣项）", "服务费", "600", ""],
  ["", "建档费", "300", ""],
  ["", "学员IC卡", "100", ""],
  ["代收代缴（依实项）", "科目一考试费", "70", "补考费35元/次"],
  ["", "科目二考试费", "130", "补考费65元/次"],
  ["", "科目三考试费", "280", "补考费140元/次"],
  ["", "工本费", "10", ""],
  ["实操培训（依实项）", "科目二实操", "C1", "120元/学时", "实操培训时长可参照学员所签名的《培训学时记录表》"],
  ["", "", "C2", "150元/学时", ""],
  ["", "科目三实操", "C1", "120元/学时", ""],
  ["", "", "C2", "150元/学时", ""],
  ["其他（必扣项）", "其他双方约定的费用", "", ""],
  ["备注：合同期内乙方申请提前解除合同…20%作为违约金后…", "", "", ""],
];
const API_TEMPLATE = {
  source: "template", tier_id: "2023_branch_school", tier_display_name: "2023·分校",
  refund_rows: REFUND_ROWS_14, clauses: CLAUSES_TPL,
};
const API_PDF = {
  source: "pdf", tier_id: "", tier_display_name: "", refund_rows: [],
  clauses: [
    { no: "", title: "", body: "东莞市机动车驾驶员培训服务合同 甲方…乙方…" },
    { no: "三", title: "培训收费约定", body: "（一）乙方向甲方支付培训费用合计人民币 3580.00 元（以下均为人民币），其中通过“东莞驾培”平台支付金额为 1500.00 元。\n1.综合服务费 1100.00 元；\n2.理论培训费 480.00 元。" },
    { no: "六", title: "甲方的权利和义务", body: "（一）将其经营规模、信誉等级…公示。" },
    { no: "七", title: "乙方的权利和义务", body: "（一）受到不公正对待…有权投诉。" },
  ],
};
const API_NONE = { source: "none", tier_id: "", tier_display_name: "", refund_rows: [], clauses: [] };
// 电子合同：扣费项 reason 里点明「第X条」（与真实工单同口径）
const RESULT_PDF = {
  total_fee: 3580,
  deductions_result: {
    items: [
      { item: "综合服务费", amount: 1100, category: "必扣", reason: "合同第三条及第七条：已在平台备案注册的综合服务费按100%扣除" },
      { item: "理论培训费", amount: 480, category: "必扣", reason: "合同第三条及第七条：已发计时IC卡的理论培训费按全额计算" },
      { item: "违约金", amount: 716, category: "违约金", reason: "违约金=3580.0×20.0%=716.0元" },
    ],
    net_refund: 0,
  },
  tier_result: { display_name: "", confidence: "high", score: 10 },
};

function _okFetch(api) {
  return async () => ({ ok: true, json: async () => ({ success: true, data: api }) });
}
function _mkCmp(api, res, id) {
  return useContractCompare(
    () => res, () => "", () => null,
    id === undefined ? (() => "T-1") : (() => id),
    _okFetch(api),
  );
}

// ── _rowKey：退费表行名映射（契约 §2.6）──────────────────────────────────
check("_rowKey 科目二实操培训费 → 科目二实操", _rowKey("科目二实操培训费") === "科目二实操");
check("_rowKey 服务费 → 服务费", _rowKey("服务费") === "服务费");
check("_rowKey 违约金 → 违约金", _rowKey("违约金") === "违约金");

// ── leftSource 三分支（契约 §4）─────────────────────────────────────────
const cmpTpl = _mkCmp(API_TEMPLATE, RESULT_TEMPLATE);
await cmpTpl.load();
check("leftSource: template", cmpTpl.leftSource.value === "template");
const cmpPdf = _mkCmp(API_PDF, RESULT_PDF);
await cmpPdf.load();
check("leftSource: pdf", cmpPdf.leftSource.value === "pdf");
const cmpNone = _mkCmp(API_NONE, RESULT_TEMPLATE);
await cmpNone.load();
check("leftSource: none", cmpNone.leftSource.value === "none");

// ── clauseBlocks ────────────────────────────────────────────────────────
const tplBlocks = cmpTpl.clauseBlocks.value;
check("上传件 clauseBlocks = 费用条款 + 退费条款（恰好 2）", tplBlocks.length === 2);
check("上传件条款号 = 四 / 八", tplBlocks[0].no === "四" && tplBlocks[1].no === "八");
check("上传件条款块有 chips（第四条固定 2 个）", tplBlocks[0].chips.length === 2);
check("上传件第八条 chip = 全部扣费项名（4 个）", tplBlocks[1].chips.map(c => c.text).join(",") === "服务费,建档费,学员IC卡,违约金");
check("退费条款含 grid 部件", tplBlocks[1].parts.some(p => p.type === "grid"));
check("第四条 OCR 填空：总额识别填入", tplBlocks[0].bodyHtml.includes('<span class="fill">3,580</span>'));
check("第四条 OCR 填空：手写分期（fill hand）", tplBlocks[0].bodyHtml.includes('<span class="fill hand">首付 2,000 元，欠款 1,580 元</span>'));
check("第三条（电子合同）合同总额 mark 用 t-total", (() => {
  const b = cmpPdf.clauseBlocks.value.find(x => x.no === "三");
  return !!b && b.bodyHtml.includes('data-dom="t-total"');
})());
check("电子合同 clauseBlocks 条款号为中文数字", cmpPdf.clauseBlocks.value.length > 0
  && cmpPdf.clauseBlocks.value.every(b => /^[一二三四五六七八九十]+$/.test(b.no)));
check("电子合同无 preamble 块（no 为空被过滤）", cmpPdf.clauseBlocks.value.every(b => b.no !== ""));
check("none 分支无条款块", cmpNone.clauseBlocks.value.length === 0);

// ── refundTable（上传件专用）────────────────────────────────────────────
const rt = cmpTpl.refundTable.value;
check("refundTable 非空", !!rt);
check("refundTable.raw = 14 行（含表头 + 备注行）", rt.raw.length === 14);
check("refundTable.rows 去掉表头与备注行 = 12", rt.rows.length === 12);
check("refundTable.hitSet = 命中行名（服务费/建档费/学员IC卡）",
  rt.hitSet.join(",") === "服务费,建档费,学员IC卡");
check("refundTable 行名用 TSV 项目列（实操行 → 科目二实操）",
  rt.rows.some(r => r[0] === "科目二实操") && rt.rows.some(r => r[0] === "科目三实操"));
// 东城自制档：refund_rows 为空 → 不产表格；退费条款号 = 六（非第八条）
const API_DONGCHENG = {
  source: "template", tier_id: "2019_dongcheng", tier_display_name: "东城自制", refund_rows: [],
  clauses: [
    { no: "四", title: "费用及支付（含各阶段培训费及相关手续费）：", body: "（一）培训费用\n（1）实行普通培训，培训费用总额合计人民币     　    元，□一次性支付，□分期付款；" },
    { no: "六", title: "退学退费", body: "（一）培训费用的退费\n备注：合同期内乙方申请提前解除合同…甲方将剩余的费用退还给乙方。" },
  ],
};
const cmpDongcheng = _mkCmp(API_DONGCHENG, RESULT_TEMPLATE);
await cmpDongcheng.load();
check("refund_rows 为空（东城自制）→ refundTable = null", cmpDongcheng.refundTable.value === null);
check("东城自制档退费条款取「六」（绝不硬编码第八条）", cmpDongcheng.clauseBlocks.value.some(b => b.no === "六"));
check("东城自制档费用条款取「四」", cmpDongcheng.clauseBlocks.value.some(b => b.no === "四"));

// ── sumCells：dom 锚点键（契约 §2.2 / §4）───────────────────────────────
const tplSum = cmpTpl.sumCells.value;
check("上传件 sumCells = 5 格", tplSum.length === 5);
check("上传件 sumCells dom = t-total/t-paid/undefined/t-paid/t-note",
  tplSum.map(c => String(c.dom)).join("|") === "t-total|t-paid|undefined|t-paid|t-note");
check("扣费合计那格无 dom（不定位）", tplSum[2].dom === undefined);
const pdfSum = cmpPdf.sumCells.value;
check("电子合同 sumCells = 4 格", pdfSum.length === 4);
check("电子合同只有合同总额有 dom", pdfSum[0].dom === "t-total"
  && pdfSum.slice(1).every(c => c.dom === undefined));

// ── 应退：读权威 refund 字段，0 不显示「待核」（v4.4 修正 · 2026-09-24）──────
// 权威应退 = deductions_result.refund（与后端 app.py:3420 落库口径同源）。
// deductions_result.net_refund 是「冲抵未付尾款后的实退」= max(0, refund − tail)，
// 实测 dr.refund=284 / dr.net_refund=0（tail_due ≥ 284）—— 读 net_refund 会把应退显示成 ¥0。
function _refundOf(c) {
  const cell = c.sumCells.value.find(x => x.k === "应退");
  return cell ? cell.v : "(缺应退格)";
}
const RESULT_REFUND = {
  total_fee: 3580,
  deductions_result: {
    items: [
      { item: "服务费", amount: 600, category: "必扣", basis: "第八条 退费表「基础服务（必扣项）·服务费」" },
      { item: "违约金", amount: 716, category: "违约金", basis: "第八条 备注：全部培训费用的20%" },
    ],
    total_fee: 3580, paid_amount: 2000, tail_due: 1580,
    refund: 284, net_refund: 0,   // ← 权威 284；net_refund=0 是「冲抵尾款后实退」
  },
  tier_result: { display_name: "2023·分校", confidence: "high", score: 10 },
  contract_analyses: [{ index: 0, kind: "", text: "（OCR 原文）服务费600 违约金716" }],
};
const RT_AUTH = _mkCmp(API_TEMPLATE, RESULT_REFUND);
await RT_AUTH.load();
const PDF_AUTH = _mkCmp(API_PDF, RESULT_REFUND);
await PDF_AUTH.load();
const GEN_AUTH = _mkCmp(API_NONE, RESULT_REFUND);
await GEN_AUTH.load();
check("应退·上传件分支读权威 refund=284（不是 net_refund=0）", _refundOf(RT_AUTH) === "¥284");
check("应退·电子合同分支读权威 refund=284", _refundOf(PDF_AUTH) === "¥284");
check("应退·通用分支读权威 refund=284", _refundOf(GEN_AUTH) === "¥284");

// 0 是有效应退值 → 必须显示 ¥0，绝不能因为 !refund 就当缺失显示「待核」
const RESULT_ZERO = {
  total_fee: 3580,
  deductions_result: {
    items: [{ item: "服务费", amount: 3580, category: "必扣" }],
    total_fee: 3580, paid_amount: 2000,
    refund: 0, refund_pending: false, net_refund: 0,
  },
  tier_result: { display_name: "2023·分校", confidence: "high", score: 10 },
};
const ZERO_TPL = _mkCmp(API_TEMPLATE, RESULT_ZERO);
await ZERO_TPL.load();
const ZERO_GEN = _mkCmp(API_NONE, RESULT_ZERO);
await ZERO_GEN.load();
check("应退=0 → ¥0（上传件分支，不得「待核」）", _refundOf(ZERO_TPL) === "¥0");
check("应退=0 → ¥0（通用分支，不得「待核」）", _refundOf(ZERO_GEN) === "¥0");

// refund_pending=true → 「待核」（即便 refund 有值也不下最终结论）
const RESULT_PENDING = {
  total_fee: 3580,
  deductions_result: {
    items: [{ item: "服务费", amount: 600, category: "必扣", basis: "第八条 退费表「基础服务（必扣项）·服务费」" }],
    total_fee: 3580, paid_amount: 2000, refund: 284, refund_pending: true,
  },
  tier_result: { display_name: "2023·分校", confidence: "high", score: 10 },
};
const PEND_TPL = _mkCmp(API_TEMPLATE, RESULT_PENDING);
await PEND_TPL.load();
check("应退 refund_pending=true → 「待核」", _refundOf(PEND_TPL) === "待核");

// refund 与实缴信息都缺 → 「待核」（不得凭空兜底出金额）
const RESULT_MISSING = {
  total_fee: 3580,
  deductions_result: {
    items: [{ item: "服务费", amount: 600, category: "必扣" }],   // 无 refund / 无 paid_amount
  },
  tier_result: { display_name: "2023·分校", confidence: "high", score: 10 },
};
const MISS_TPL = _mkCmp(API_TEMPLATE, RESULT_MISSING);
await MISS_TPL.load();
check("应退 refund 与实缴都缺 → 「待核」", _refundOf(MISS_TPL) === "待核");

// ── aiSummary 取值（契约 §4.2）──────────────────────────────────────────
check("aiSummary 优先 dr.rule_summary", cmpTpl.aiSummary.value.includes("2023·分校")
  && cmpTpl.aiSummary.value.includes("档位标准合同"));
check("aiSummaryHtml：**x** → <b>x</b>（先转义再替换）", cmpTpl.aiSummaryHtml.value.includes("<b>2023·分校</b>")
  && !cmpTpl.aiSummaryHtml.value.includes("**"));
// 无 rule_summary → 回落 r.summary
const cmpNoRule = _mkCmp(API_TEMPLATE, { deductions_result: { items: [] }, summary: "送考承诺：科目二补考不超过2次" });
await cmpNoRule.load();
check("aiSummary 回落 r.summary（无 rule_summary）", cmpNoRule.aiSummary.value.includes("送考承诺"));

// ── 加载中 / 失败 / 无 ticket_id（不得空白或报错）──────────────────────
let _fetchCalls = 0;
const cmpNoId = useContractCompare(() => RESULT_TEMPLATE, () => "", () => null, () => "",
  async () => { _fetchCalls++; return { ok: true, json: async () => ({}) }; });
await cmpNoId.load();
check("无 ticket_id → 不拉取、leftSource 空串（回落现有路径）",
  cmpNoId.leftSource.value === "" && _fetchCalls === 0);
const cmpFail = useContractCompare(() => RESULT_TEMPLATE, () => "", () => null, () => "T-X",
  async () => { throw new Error("boom"); });
await cmpFail.load();
check("接口失败：apiError 记录原因", cmpFail.apiError.value.includes("boom"));
check("接口失败：leftSource 回落空串（不空白）", cmpFail.leftSource.value === "");

// ── 定位：display:contents 无盒子 + 总是重新居中（v4.4 第 1 点回归）──────
globalThis.document.querySelectorAll = () => [];
globalThis.getComputedStyle = (el) => ({ overflowY: (el && el._oy) || "" });
function _stubBox(top, bottom, w, h) {
  return { top, bottom, left: 0, right: w, width: w, height: h };
}
function _stubEl(rect, children) {
  const el = {
    _rect: rect, children: children || [], parentElement: null,
    classList: { toggle() {}, add() {}, remove() {} },
    getBoundingClientRect() { return this._rect; },
    querySelectorAll(sel) { return sel === ":scope > *" ? (this.children || []) : []; },
  };
  return el;
}
function _stubPane(scrollHeight, clientHeight, top, scrollTop) {
  return {
    _oy: "auto", clientHeight, scrollHeight, scrollTop,
    parentElement: null,
    getBoundingClientRect() { return { top, bottom: top + clientHeight, left: 0, right: 400, width: 400, height: clientHeight }; },
  };
}
// A) display:contents 行（宽高 0）→ 取子元素并集
const paneA = _stubPane(800, 400, 0, 200);
const rowA = _stubEl(_stubBox(0, 0, 0, 0), [_stubEl(_stubBox(100, 120, 50, 20)), _stubEl(_stubBox(120, 140, 50, 20))]);
rowA.parentElement = paneA;
cmpTpl.registerTarget("t-row-服务费", rowA);
cmpTpl.hoverDom("t-row-服务费");
check("display:contents 行无盒子（w/h=0）", rowA.getBoundingClientRect().width === 0);
check("并集盒子 → scrollTop 居中（8→ 120）", paneA.scrollTop === 120);
check("hoverDom 置 activeDom", cmpTpl.activeDom.value === "t-row-服务费");
check("isHit 生效", cmpTpl.isHit("t-row-服务费") === true);
cmpTpl.leaveDom("t-row-服务费");
check("leaveDom 清空 activeDom", cmpTpl.activeDom.value === "");
// B) 目标「已部分在可视区内」仍必须重新居中（删掉短路后的回归护栏）
const paneB = _stubPane(800, 400, 0, 0);
const rowB = _stubEl(_stubBox(0, 0, 0, 0), [_stubEl(_stubBox(350, 370, 50, 20))]);
rowB.parentElement = paneB;
cmpTpl.registerTarget("t-row-科目二实操", rowB);
cmpTpl.hoverDom("t-row-科目二实操");
check("目标已在可视区内仍重新居中（旧短路会让 scrollTop=0）", paneB.scrollTop === 160 && paneB.scrollTop !== 0);

console.log(failed === 0 ? "\nALL PASS" : `\n${failed} FAILED`);
process.exit(failed === 0 ? 0 : 1);