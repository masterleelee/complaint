// 回归测试：复刻 app.js 中 clauseNosForLabel / feeKeywordsOf / clauseHtml 的定位+高亮逻辑，
// 用用户报告的真实合同文本验证修复效果。
let pass = 0, fail = 0;
function assert(cond, msg) {
  if (cond) { pass++; console.log("  PASS:", msg); }
  else { fail++; console.error("  FAIL:", msg); }
}

// ── 与 app.js 保持一致的实现（复制自修复后代码） ──
const FEE_CLAUSE_KEYWORDS = [
  ["科目二", ["第二部分", "基础和场地驾驶培训"]],
  ["科目三", ["第三部分", "道路驾驶培训"]],
  ["综合服务费", ["综合服务费"]],
  ["理论培训", ["理论培训费"]],
  ["理论费", ["理论培训费"]],
  ["违约金", ["违约金"]],
  ["合同总额", ["培训费用合计", "培训服务费合计"]],
];
const LABEL_TEXT_PHRASES = [
  ["科目二实操培训费", ["科目二实际操作培训费", "科目二实操培训费"]],
  ["科目二学时单价", ["科目二实际操作培训费", "科目二实操培训费"]],
  ["科目三实操培训费", ["科目三实际操作培训费", "科目三实操培训费"]],
  ["科目三学时单价", ["科目三实际操作培训费", "科目三实操培训费"]],
  ["科目二补训", ["科目二补训费", "科目二补训"]],
  ["科目三补训", ["科目三补训费", "科目三补训"]],
  ["平台备案", ["补训", "接送服务"]],
  ["接送", ["接送服务费用", "接送服务费", "接送"]],
];
const clauseFullText = c => String((c.title || "") + "\n" + (c.body || "")).replace(/\s+/g, "");

function makeClauseNosForLabel(clauses) {
  return function clauseNosForLabel(label) {
    if (!clauses.length) return [];
    const text = String(label || "");
    for (const [k, phrases] of LABEL_TEXT_PHRASES) {
      if (!text.includes(k)) continue;
      const nos = clauses
        .filter(c => { const t = clauseFullText(c); return phrases.some(p => t.includes(p.replace(/\s+/g, ""))); })
        .map(c => c.no).filter(Boolean);
      if (nos.length) return nos;
      break;
    }
    const kws = [];
    for (const [k, ws] of FEE_CLAUSE_KEYWORDS) if (text.includes(k)) kws.push(...ws);
    if (!kws.length) return [];
    return clauses
      .filter(c => kws.some(w => (c.title || "").includes(w) || (c.body || "").includes(w)))
      .map(c => c.no).filter(Boolean);
  };
}
function feeKeywordsOf(label) {
  const t = String(label || "");
  if (t.includes("科目二实操培训费") || t.includes("科目二学时单价")) return ["科目二实际操作培训费", "科目二实操培训费", "科目二"];
  if (t.includes("科目三实操培训费") || t.includes("科目三学时单价")) return ["科目三实际操作培训费", "科目三实操培训费", "科目三"];
  if (t.includes("科目二补训")) return ["科目二补训"];
  if (t.includes("科目三补训")) return ["科目三补训"];
  if (t.includes("平台备案")) return ["补训", "接送"];
  if (t.includes("接送")) return ["接送"];
  if (t.includes("科目二")) return ["第二部分", "科目二"];
  if (t.includes("科目三")) return ["第三部分", "科目三"];
  if (t.includes("综合服务费")) return ["综合服务费"];
  if (t.includes("理论")) return ["理论培训费", "理论费"];
  if (t.includes("违约金")) return ["违约金为"];
  if (t.includes("合同总额")) return ["培训费用合计", "培训服务费合计", "培训服务费总额", "总金额"];
  return [];
}
// clauseHtml 高亮（与修复后一致：去空白比对）
function clauseHtml(body, kws) {
  if (!kws.length) return body;
  const parts = body.split(/(。|；|\n)/);
  const norm = s => String(s).replace(/\s+/g, "");
  let html = "";
  for (const p of parts) {
    html += (p.trim() && kws.some(kw => norm(p).includes(norm(kw)))) ? `<mark>${p}</mark>` : p;
  }
  return html;
}

// ── 模拟用户报告的合同条款（条款号/标题/正文按"第X条切块"结构） ──
const clauses = [
  { no: "8",  title: "培训费用及支付方式", body: "本次培训费用总额人民币 4500 元。其中：\n综合服务费人民币 1300 元；\n理论培训费人民币 1200 元；\n科目二实际操作培训费人民币 1200 元（学时单价为 150 元/学时）；\n科目三实际操作培训费人民币 800 元（学时单价为 150 元/学时）。" },
  { no: "12", title: "其他约定", body: "1.科目二补训费 150 元/次或 150 元/学时；\n2.科目三补训费 150 元/次或 150 元/学时。甲方不提供免费接送服务，若乙方后续同意付费享受接送服务，接送服务费用 100 元/次。" },
  { no: "5",  title: "第二部分　基础和场地驾驶培训", body: "基础和场地驾驶培训（科目二）学时为 22 学时。" },
];
const clauseNosForLabel = makeClauseNosForLabel(clauses);

console.log("\n[1] 科目二实操培训费 → 定位到含『科目二实际操作培训费人民币 1200 元』的条款");
{
  const nos = clauseNosForLabel("科目二实操培训费");
  assert(nos.length === 1 && nos[0] === "8", `定位条款 = 第${nos.join(",")}条（期望：8）`);
  const cl = clauses.find(c => c.no === nos[0]);
  const html = clauseHtml(cl.body, feeKeywordsOf("科目二实操培训费"));
  assert(html.includes("<mark>") && /mark>科目二实际操作培训费人民币 1200 元/.test(html), "『科目二实际操作培训费人民币 1200 元（学时单价…）』整句被高亮");
}

console.log("\n[2] 科目二学时单价(元/学时) → 同样定位到第8条并高亮学时单价");
{
  const nos = clauseNosForLabel("科目二学时单价(元/学时)");
  assert(nos.length === 1 && nos[0] === "8", `定位条款 = 第${nos.join(",")}条（期望：8）`);
  const cl = clauses.find(c => c.no === nos[0]);
  const html = clauseHtml(cl.body, feeKeywordsOf("科目二学时单价(元/学时)"));
  assert(html.includes("<mark>") && html.includes("学时单价为 150 元/学时"), "学时单价句被包含在高亮段内");
}

console.log("\n[3] 科目三实操培训费 / 科目三学时单价 → 定位到第8条并高亮科目三原文");
{
  const nos = clauseNosForLabel("科目三实操培训费");
  assert(nos.length === 1 && nos[0] === "8", `科目三实操培训费定位 = 第${nos.join(",")}条（期望：8）`);
  const html = clauseHtml(clauses.find(c => c.no === "8").body, feeKeywordsOf("科目三实操培训费"));
  assert(html.includes("mark>科目三实际操作培训费人民币 800 元"), "『科目三实际操作培训费人民币 800 元』被高亮");
  const nos2 = clauseNosForLabel("科目三学时单价(元/学时)");
  assert(nos2.length === 1 && nos2[0] === "8", `科目三学时单价定位 = 第${nos2.join(",")}条（期望：8）`);
}

console.log("\n[4] 平台备案、合同未载明 → 定位到含补训费/接送费的条款（第12条）");
{
  const nos = clauseNosForLabel("平台备案、合同未载明");
  assert(nos.length === 1 && nos[0] === "12", `分组标题定位 = 第${nos.join(",")}条（期望：12）`);
  const html = clauseHtml(clauses.find(c => c.no === "12").body, feeKeywordsOf("平台备案、合同未载明"));
  assert(html.includes("mark>1.科目二补训费") || html.includes("mark>1.科目二补训"), "科目二补训费句被高亮");
  assert(html.includes("接送服务费用 100 元/次") && html.includes("mark>") && html.includes("接送"), "接送服务费句被高亮");
}

console.log("\n[5] platform_extra 各行标签（科目二补训费/科目三补训费/接送费）");
for (const label of ["科目二补训费", "科目三补训费", "接送费"]) {
  const nos = clauseNosForLabel(label);
  assert(nos.length === 1 && nos[0] === "12", `${label} → 第${nos.join(",")}条（期望：12）`);
}

console.log("\n[6] PDF 文本层带空格的情况（『科目二 实际操作培训费』）仍可定位+高亮");
{
  const spaced = [{ no: "8", title: "培训费用", body: "科 目 二 实 际 操 作 培 训 费人民币 1200 元（学时单价为 150 元/学时）；" }];
  const fn = makeClauseNosForLabel(spaced);
  const nos = fn("科目二实操培训费");
  assert(nos.length === 1 && nos[0] === "8", `去空白比对定位成功 = 第${nos.join(",")}条`);
  const html = clauseHtml(spaced[0].body, feeKeywordsOf("科目二实操培训费"));
  assert(html.includes("<mark>"), "带空格文本仍能高亮");
}

console.log("\n[7] 回归：原有定位路径不受影响");
{
  const nos = clauseNosForLabel("综合服务费");
  assert(nos.length === 1 && nos[0] === "8", `综合服务费 → 第${nos.join(",")}条`);
  const nos2 = clauseNosForLabel("违约金");
  assert(Array.isArray(nos2), `违约金返回数组（空数组兜底，不抛错）`);
  const nos3 = clauseNosForLabel("理论培训费");
  assert(nos3.length === 1 && nos3[0] === "8", `理论培训费 → 第${nos3.join(",")}条`);
}

console.log(`\n════════════════════════════`);
console.log(`结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail ? 1 : 0);
