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
  // 电子合同（东莞驾培平台）用「第二部分/第三部分」表述费用定义句，放最前优先命中；
  // 旧纸质/旧电子合同无此短语，自动落回后面的原文短语，行为不变。
  ["科目二实操培训费", ["第二部分基础和场地驾驶培训费", "科目二实际操作培训费", "科目二实操培训费"]],
  ["科目二学时单价", ["第二部分基础和场地驾驶培训费", "科目二实际操作培训费", "科目二实操培训费"]],
  ["科目二实操费", ["第二部分基础和场地驾驶培训费", "科目二实际操作培训费", "科目二实操培训费"]],
  ["科目三实操培训费", ["第三部分道路驾驶培训费", "科目三实际操作培训费", "科目三实操培训费"]],
  ["科目三学时单价", ["第三部分道路驾驶培训费", "科目三实际操作培训费", "科目三实操培训费"]],
  ["科目三实操费", ["第三部分道路驾驶培训费", "科目三实际操作培训费", "科目三实操培训费"]],
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
      // 短语按优先级排序，首个命中任何条款的短语生效（避免低优短语把退费条款等误带进来）
      for (const p of phrases) {
        const np = p.replace(/\s+/g, "");
        const nos = clauses.filter(c => clauseFullText(c).includes(np)).map(c => c.no).filter(Boolean);
        if (nos.length) return nos;
      }
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
  // 优先用合同原文短语做高亮（如"科目二实际操作培训费…学时单价…"整句命中；
  // 电子合同的费用定义句是"第二部分基础和场地驾驶培训费…学时单价…"，两套命名并存覆盖）
  if (t.includes("科目二实操培训费") || t.includes("科目二学时单价")) return ["第二部分基础和场地驾驶培训费", "科目二实际操作培训费", "科目二实操培训费", "科目二"];
  if (t.includes("科目二实操费")) return ["第二部分基础和场地驾驶培训费", "科目二实际操作培训费", "科目二实操培训费", "科目二实操费"];
  if (t.includes("科目三实操培训费") || t.includes("科目三学时单价")) return ["第三部分道路驾驶培训费", "科目三实际操作培训费", "科目三实操培训费", "科目三"];
  if (t.includes("科目三实操费")) return ["第三部分道路驾驶培训费", "科目三实际操作培训费", "科目三实操培训费", "科目三实操费"];
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
// clauseHtml 高亮（与修复后一致：去空白比对；按完整句（。；）切分，PDF 跨行句整句命中）
function clauseHtml(body, kws) {
  if (!kws.length) return body;
  const parts = body.split(/(。|；)/);
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
  assert(html.includes("<mark>") && html.replace(/\s+/g, "").includes("<mark>科目二实际操作培训费人民币1200元"),
    "『科目二实际操作培训费人民币 1200 元（学时单价…）』整句被高亮");
}

console.log("\n[2] 科目二学时单价(元/学时) → 同样定位到第8条并高亮学时单价");
{
  const nos = clauseNosForLabel("科目二学时单价(元/学时)");
  assert(nos.length === 1 && nos[0] === "8", `定位条款 = 第${nos.join(",")}条（期望：8）`);
  const cl = clauses.find(c => c.no === nos[0]);
  const html = clauseHtml(cl.body, feeKeywordsOf("科目二学时单价(元/学时)"));
  assert(html.includes("<mark>") && html.replace(/\s+/g, "").includes("学时单价为150元/学时"),
    "学时单价句被包含在高亮段内");
}

console.log("\n[3] 科目三实操培训费 / 科目三学时单价 → 定位到第8条并高亮科目三原文");
{
  const nos = clauseNosForLabel("科目三实操培训费");
  assert(nos.length === 1 && nos[0] === "8", `科目三实操培训费定位 = 第${nos.join(",")}条（期望：8）`);
  const html = clauseHtml(clauses.find(c => c.no === "8").body, feeKeywordsOf("科目三实操培训费"));
  assert(html.replace(/\s+/g, "").includes("mark>科目三实际操作培训费人民币800元"), "『科目三实际操作培训费人民币 800 元』被高亮");
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

console.log("\n[8] 电子合同（东莞驾培平台，董昌华案真实结构）：科目二/科目三 → 定位第三条费用定义句并整句高亮");
{
  // 复刻真实条款：费用定义在第三条；第九条退费约定也含"科目三实际操作培训费"字样（旧逻辑误定位根因）
  const ec = [
    { no: "三", title: "培训服务费约定", body: "培训服务费合计 5060.00 元（本合同均为人民币计算），其中1500元培训\n服务费由甲方通过“东莞驾培”平台支付给乙方，乙方将此 1500 元作为培训保\n证金划入第三方存管银行专用账户，扣除该 1500元后的\n剩余培训服务费 3560.00 元由甲乙双方通过商定的支付途径支付。培训服务费\n包含以下项目：\n1.综合服务费1263.00 元（占培训服务费比例应≤25%），包含整理学员\n资料、培训学籍注册等相关服务费用；\n2.理论培训费（网络远程教育）252.00 元（占培训服务费比例应≤5%），\n包含第一部分道路交通安全法律、法规和相关知识和第四部分安全文明驾驶常\n识；\n3.第二部分基础和场地驾驶培训费1774.00 元，退学退费时折算\n学时单价150.00 元/学时；\n4.第三部分道路驾驶培训费1771.00 元（占培训服务费比例应≥35%），\n退学退费时折算学时单价 150.00 元/学时。" },
    { no: "九", title: "退学退费相关约定", body: "2.已产生的培训费。其中理论培训费已核发计时 IC 卡的按全额扣除，实际\n操作培训费按照本合同第三条的学时单价×广东省驾驶培训监管服务平台有效学\n时数计算。\n3.违约金为培训服务费总额的10%。\n4.理论培训费、科目二、科目三实际操作培训费不超过本合同第三条对应部\n分费用，扣除费用总数（含违约金）不超过总培训服务费。" },
  ];
  const fn = makeClauseNosForLabel(ec);
  const c3 = ec.find(c => c.no === "三");

  const nos2 = fn("科目二实操培训费");
  assert(nos2.length === 1 && nos2[0] === "三", `科目二实操培训费定位 = 第${nos2.join(",")}条（期望：三）`);
  const html2 = clauseHtml(c3.body, feeKeywordsOf("科目二实操培训费"));
  assert(html2.replace(/\s+/g, "").includes("<mark>3.第二部分基础和场地驾驶培训费1774.00元，退学退费时折算学时单价150.00元/学时"),
    "『第二部分基础和场地驾驶培训费1774.00 元，退学退费时折算学时单价150.00 元/学时；』整句被高亮");
  assert(!html2.replace(/\s+/g, "").includes("mark>4.第三部分"), "科目二悬停不高亮第三部分句");

  const nos3 = fn("科目三实操培训费");
  assert(nos3.length === 1 && nos3[0] === "三", `科目三实操培训费定位 = 第${nos3.join(",")}条（期望：三；旧逻辑误定位第九条）`);
  const html3 = clauseHtml(c3.body, feeKeywordsOf("科目三实操培训费"));
  assert(html3.replace(/\s+/g, "").includes("<mark>4.第三部分道路驾驶培训费1771.00元（占培训服务费比例应≥35%），退学退费时折算学时单价150.00元/学时"),
    "『第三部分道路驾驶培训费1771.00 元（占培训服务费比例应≥35%），退学退费时折算学时单价 150.00 元/学时。』整句被高亮");
}

console.log(`\n════════════════════════════`);
console.log(`结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail ? 1 : 0);
