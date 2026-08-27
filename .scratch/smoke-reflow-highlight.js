// 冒烟验证：reflowBody / feeKeywordsOf / clauseNosForItem / clauseHtml 逻辑
// 与 static/js/app.js 中实现保持一致（复制自本次改动版本）

function reflowBody(body) {
  const MARK = /^\s*(?:[（(][一二三四五六七八九十百零\d]{1,4}[）)]|[一二三四五六七八九十]{1,3}[、..．]|[0-9]{1,3}[、..．]|第[一二三四五六七八九十百零\d]+条|第[一二三四五]部分|附[则件表]|甲方[:：]|乙方[:：])/;
  const lines = [];
  for (const raw of String(body || "").split("\n")) {
    const t = raw.trim();
    if (!t) continue;
    if (!lines.length || MARK.test(t)) lines.push(t);
    else lines[lines.length - 1] += t;
  }
  return lines.join("\n");
}

function feeKeywordsOf(label) {
  const t = String(label || "");
  if (t.includes("科目二")) return ["第二部分", "科目二"];
  if (t.includes("科目三")) return ["第三部分", "科目三"];
  if (t.includes("综合服务费")) return ["综合服务费"];
  if (t.includes("理论")) return ["理论培训费", "理论费"];
  if (t.includes("违约金")) return ["违约金"];
  if (t.includes("合同总额")) return ["培训服务费合计", "培训服务费总额", "总金额"];
  return [];
}

function clauseNosForLabel(label, clauses, FEE_CLAUSE_KEYWORDS) {
  if (!clauses.length) return [];
  const text = String(label || "");
  const kws = [];
  for (const [k, ws] of FEE_CLAUSE_KEYWORDS) if (text.includes(k)) kws.push(...ws);
  if (!kws.length) return [];
  return clauses.filter(c => kws.some(w => (c.title || "").includes(w) || (c.body || "").includes(w))).map(c => c.no).filter(Boolean);
}

function escHtml(s) {
  return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function clauseHtml(cl, active) {
  const body = String(cl.body || "");
  const kws = active.nos.includes(cl.no) ? active.kws : [];
  if (!kws.length) return escHtml(body);
  const parts = body.split(/(。|；|\n)/);
  let html = "";
  for (const p of parts) {
    html += (p.trim() && kws.some(kw => p.includes(kw)))
      ? `<mark class="clause-mark">${escHtml(p)}</mark>`
      : escHtml(p);
  }
  return html;
}

let failed = 0;
function check(name, cond, detail) {
  console.log((cond ? "PASS" : "FAIL") + " | " + name + (cond ? "" : " | " + detail));
  if (!cond) failed++;
}

// ── 用例1：PDF 硬换行正文重排（来自真实工单第三条） ──
const brokenBody = [
  "培训服务费合计 3700 元（本合同均为人民币计算），其中1500元培训",
  "服务费由甲方通过“东莞驾培”平台支付给乙方，乙方将此 1500 元作为培训保",
  "证金划入第三方存管银行专用账户，扣除该 1500元后的",
  "剩余培训服务费 2200 元由甲乙双方通过商定的支付途径支付。培训服务费",
  "包含以下项目：",
  "1.综合服务费925 元（占培训服务费比例应≤25%），包含整理学员",
  "资料、培训学籍注册等相关服务费用；",
  "2.理论培训费（网络远程教育）185 元（占培训服务费比例应≤5%），",
  "包含第一部分道路交通安全法律、法规和相关知识和第四部分安全文明驾驶常",
  "识；",
].join("\n");

const reflowed = reflowBody(brokenBody);
check("重排后句子不再被硬换行截断", !reflowed.includes("培训\n服务费"), reflowed);
check("编号列表项保持独立段落", reflowed.includes("\n2.理论培训费"), reflowed);
check("重排后无空行堆积", !/\n{2,}/.test(reflowed), reflowed);

// ── 用例2：关键词数组命中金额句而非标题句 ──
const clause3 = { no: "三", title: "培训服务费约定", body: reflowed };
const active3 = { nos: ["三"], kws: feeKeywordsOf("综合服务费") };
const html3 = clauseHtml(clause3, active3);
check("综合服务费命中含金额的句子并打 mark",
  /<mark class="clause-mark">[^<]*1\.综合服务费925 元/.test(html3), html3.slice(0, 200));
check("未命中的句子不打 mark", !html3.includes("<mark>乙方"), html3);

// ── 用例3：科目二实操费 → ["第二部分","科目二"] 任一命中即高亮 ──
const subject2Body = reflowBody([
  "实际操作培训费按以下标准计算：",
  "第二部分科目二实际操作培训费1200元（学时单价150元/学时）；",
  "第三部分科目三实际操作培训费800元；",
].join("\n"));
const activeS2 = { nos: ["四"], kws: feeKeywordsOf("科目二实操培训费") };
const htmlS2 = clauseHtml({ no: "四", title: "", body: subject2Body }, activeS2);
check("科目二关键词数组任一命中即 mark", /<mark class="clause-mark">[^<]*第二部分科目二/.test(htmlS2), htmlS2);

// ── 用例4：多条款定位（违约金涉及第七条+第九条） ──
const FEE_CLAUSE_KEYWORDS = [
  ["综合服务费", ["综合服务费"]],
  ["科目二", ["第二部分"]],
  ["科目三", ["第三部分"]],
  ["理论费", ["理论培训费"]],
  ["违约金", ["违约金"]],
  ["合同总额", ["培训服务费合计"]],
];
const clauses = [
  { no: "七", title: "扣费细则", body: "综合服务费按100%扣除" },
  { no: "九", title: "违约责任", body: "甲方提前退学的，按剩余费用的10%支付违约金" },
];
const reason = "合同第七条及第九条";
const nosFromReason = [...reason.matchAll(/第([一二三四五六七八九十百零\d]+)条/g)].map(m => m[1]);
check("reason 中多条条款全部提取", JSON.stringify(nosFromReason) === '["七","九"]', JSON.stringify(nosFromReason));

const activePenalty = { nos: nosFromReason, kws: feeKeywordsOf("违约金") };
const h7 = clauseHtml(clauses[0], activePenalty);
const h9 = clauseHtml(clauses[1], activePenalty);
check("第七条不误高亮（无违约金字样）", !h7.includes("<mark"), h7);
check("第九条违约金句子被高亮", /<mark class="clause-mark">[^<]*甲方提前退学/.test(h9), h9);

// ── 用例5：无匹配时回退纯转义，不出错 ──
const activeEmpty = { nos: [], kws: [] };
check("nos 为空时整段仅转义输出", clauseHtml({ no: "三", body: "<b>x</b>" }, activeEmpty) === "&lt;b&gt;x&lt;/b&gt;");

process.exit(failed ? 1 : 0);
