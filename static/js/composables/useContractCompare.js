// 合同原文对照弹窗（v4.4 形态）：左原文/原图 · 右扣费明细对照
// 数据源（两条，优先级从前到后）：
//   ① /api/contract/comparison/<ticket_id> —— 契约 s4-contract.md §1.1
//      { source:"template"|"pdf"|"none", clauses:[{no,title,body}],
//        refund_rows:[[c1,c2,c3,c4],…], tier_id, tier_display_name, … }
//      template = 上传件走档位模板条款；pdf = 电子合同 PDF 文本层；none = 拿不到
//   ② ar（分析结果）—— tier_result / deductions_result.items / contract_text /
//      contract_analyses / summary。**无 ticket_id 或接口拿不到时**回落到这一路（不退化）。
// 原件：useWorkflow 的 cPath + contractManifest（source_files / merged_pdf_path），
//       一律走现有 /api/contract/preview（电子合同下载件与上传件同一条通道）
// 交互：悬停/点击右栏明细（或顶部汇总格）→ 左栏原文滚动定位 + 目标高亮；点击可钉住
//
// 设计原则：
//   - 只读消费 ar.value / 拉取结果，不写回（避免与 useWorkflow 的 recalc() 互相覆盖）
//   - 目标定位一律「自算可视盒 → 直接设 pane.scrollTop」，**禁止 scrollIntoView**：
//     .grid-table 的行是 display:contents（没有盒子），scrollIntoView 对它是空操作
//   - 无锚点（OCR 失败/429 降级）的行不显示定位，不报错
//
// 项目约定：Vue 由 <script src> 挂到 window.Vue，本文件用 Vue.ref/computed，
// 不在 importmap 登记 "Vue"。
// ⚠️ 必须以 Vue.reactive(...) 包裹返回值再暴露给模板：普通对象内嵌 computed
// 模板不解包 → 根渲染崩溃整页白屏（2026-09-20 三栏预览实锤，同类教训）。
// ⚠️ 模板里对 setup 顶层 ref 不写 .value；本文件内部的异步数据全部进 Vue.ref。

// ── 工具 ────────────────────────────────────────────────────────────────
function _money(n) {
  const v = Number(n) || 0;
  return v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}
function _esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, m => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]
  ));
}
// 富文本：**先转义、再**把 **x** 换成 <b>x</b>（绝不对后端原始串直接 v-html）
function _rich(escaped) {
  return String(escaped).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
}
function _previewUrl(filepath) {
  return "/api/contract/preview?path=" + encodeURIComponent(filepath || "");
}
const _isImgPath = (p) => /\.(png|jpe?g|gif|bmp|webp)$/i.test(p || "");

// 档位置信度 → 人话标签。⚠️ 绝不使用「%」：tier_result.score 是「档位特征加权分」
// （如 10），不是概率；旧代码 Math.round(score * 100) + "%" 会把它显示成 1000%。
function _confLabel(raw) {
  const s = String(raw ?? "").trim().toLowerCase();
  if (s === "high" || s === "h" || s === "高") return "高";
  if (s === "medium" || s === "m" || s === "中") return "中";
  if (s === "low" || s === "l" || s === "低") return "低";
  return "";
}

// 一段正文字符串注入 anchor mark（区间重叠时后者失效，保证 HTML 合法）
function _injectMarks(text, marks) {
  const sorted = [...marks].sort((m1, m2) => m1.start - m2.start);
  const accepted = [];
  let lastEnd = 0;
  sorted.forEach((m) => {
    if (m.start < lastEnd || m.end <= m.start) return;
    accepted.push(m);
    lastEnd = m.end;
  });
  if (!accepted.length) return _esc(text);
  let html = "";
  let cursor = 0;
  accepted.forEach((m) => {
    if (cursor < m.start) html += _esc(text.slice(cursor, m.start));
    // data-id 保留（兼容既有 hoverRow）；data-dom 供 v4.4 统一 hoverDom 定位
    html += `<mark class="cc-kw" data-id="${_esc(m.domId)}" data-dom="${_esc(m.domId)}" title="${_esc(m.item)}">${_esc(text.slice(m.start, m.end))}</mark>`;
    cursor = m.end;
  });
  if (cursor < text.length) html += _esc(text.slice(cursor));
  return html;
}

// ── DOM 访问（全部加守卫，Node 单测里没有完整 DOM 也不抛）────────────────
function _qsa(sel) {
  try {
    return (typeof document !== "undefined" && document.querySelectorAll)
      ? Array.from(document.querySelectorAll(sel)) : [];
  } catch (e) { return []; }
}
function _qs(sel) {
  try {
    return (typeof document !== "undefined" && document.querySelector)
      ? document.querySelector(sel) : null;
  } catch (e) { return null; }
}
function _computedStyle(el) {
  try {
    return (typeof getComputedStyle === "function") ? getComputedStyle(el) : null;
  } catch (e) { return null; }
}

// ── 电子合同：条款 → 命中扣费项（照抄原型 renderElectronic 口径）──────────
const _CLAUSE_KW = [
  ["综合服务费", ["综合服务费"]],
  ["理论培训费", ["理论培训费"]],
  ["科目二实操费", ["科目二实际操作培训费", "科目二实操培训费", "第二部分基础和场地驾驶培训费"]],
  ["科目三实操费", ["科目三实际操作培训费", "科目三实操培训费", "第三部分道路驾驶培训费"]],
  ["违约金", ["违约金为培训费总额的20%", "违约金为", "违约金"]],
];
const _REFLOW_MARK = /^\s*(?:[（(][一二三四五六七八九十百零\d]{1,4}[）)]|[一二三四五六七八九十]{1,3}[、..．]|[0-9]{1,3}[、..．]|第[一二三四五六七八九十百零\d]+条|附[则件表]|甲方[:：]|乙方[:：])/;
// 电子合同里「与扣费无关但需要铺陈的通用条款」固定显示的四条
const _GENERAL_CLAUSES = ["第六", "第九", "第十", "第十一"];

function _reflowBody(body) {
  const out = [];
  String(body || "").split("\n").forEach(raw => {
    const t = raw.trim();
    if (!t) return;
    if (!out.length || _REFLOW_MARK.test(t)) out.push(t);
    else out[out.length - 1] += t;
  });
  return out.join("\n");
}
function _kwOf(item) {
  const s = String(item);
  for (const [k, ws] of _CLAUSE_KW) if (s.includes(k)) return ws;
  for (const [k, ws] of _CLAUSE_KW) if (s.includes(k.slice(0, 2))) return ws;
  return [];
}
function _nosOf(item, reason, clauses) {
  const direct = [...String(reason || "").matchAll(/第([一二三四五六七八九十百零\d]+)条/g)].map(m => m[1]);
  if (direct.length) return [...new Set(direct)];
  const kws = _kwOf(item);
  if (!kws.length) return [];
  return [...new Set((clauses || [])
    .filter(c => c && c.no && kws.some(kw => _reflowBody(c.body).replace(/\s+/g, "").includes(kw.replace(/\s+/g, ""))))
    .map(c => c.no))];
}

// ── 上传件：退费表行 ↔ 扣费项名映射（契约 §2.6）────────────────────────────
// 「科目二实操培训费」→「科目二实操」；「服务费」→「服务费」；「违约金」→「违约金」
export function _rowKey(item) {
  // ⚠️ 与契约 §2.6 字面正则的差异（已核对契约自身的例子）：
  //   字面写法 `.replace(/(?:实操)?培训费$/,"")` 会把「科目二实操培训费」吃掉「实操」得到
  //   「科目二」，与契约同段给出的例子「科目二实操培训费 → 科目二实操」自相矛盾。
  //   这里按**行为契约/例子**实现：只剥「培训费」，保留「实操」。
  //   「…实操培训费」→「…实操」；「…实操费」→「…实操」；「…培训费」→「…」；其余原样。
  return String(item)
    .replace(/(?:实操)?培训费$/, (m) => (m.includes("实操") ? "实操" : ""))
    .replace(/实操费$/, "实操");
}

// 退费表 TSV 行 → 4 列栅格行 [项目, 内容, 费用（元）, 备注]
// TSV 结构：【类别, 项目名, (车型), 费用, (备注)】列数可变（实操行多一列车型）。
function _normRow(cells) {
  const c = Array.isArray(cells) ? cells.map(x => String(x == null ? "" : x)) : [""];
  const out = [c[1] || "", "", "", ""];
  if (c.length >= 5) { out[1] = c[2] || ""; out[2] = c[3] || ""; out[3] = c[4] || ""; }
  else { out[2] = c[2] || ""; out[3] = c[3] || ""; }
  return out;
}

function _lineCls(line) {
  return (/^[（(][一二三四五六七八九十]{1,3}[）)]/.test(line) && String(line).length < 16)
    ? "b-sub" : "b-para";
}
function _findByTitle(clauses, kw) {
  const c = (clauses || []).find(x => x && String(x.title || "").includes(kw));
  return c ? c.no : "";
}
// 退费条款号**不固定**：2023·分校/门店 = 八，东城自制 = 六，2021/2019 培训档 = 五。
// 绝不能硬编码「第八条」（东城自制档的第八条是「甲方的权利和义务」）。优先按标题
// 「退学退费」定位；退费表非空的档一定落在退费条款上；最后兜底「八」→「六」。
const _FEE_CLAUSE_TITLE_KW = "费用及支付";
const _REFUND_CLAUSE_TITLE_KW = "退学退费";
const _FEE_CLAUSE_NO_FALLBACK = "四";
const _REFUND_CLAUSE_NO_FALLBACK = "八";
const _REFUND_CLAUSE_NO_ALT = "六";
function _refundClauseNo(clauses, refundRows) {
  const byTitle = _findByTitle(clauses, _REFUND_CLAUSE_TITLE_KW);
  if (byTitle) return byTitle;
  if (Array.isArray(refundRows) && refundRows.length) return _REFUND_CLAUSE_NO_FALLBACK;
  return _REFUND_CLAUSE_NO_ALT;
}
// 模板正文里的 OCR 填空：**x** = 识别填入（蓝底），%%x%% = 手写（橙底）
function _fillify(line) {
  return _esc(line)
    .replace(/\*\*(.+?)\*\*/g, '<span class="fill">$1</span>')
    .replace(/%%(.+?)%%/g, '<span class="fill hand">$1</span>');
}

// ── 主 composable ───────────────────────────────────────────────────────
export function useContractCompare(getResult, getSourcePath, getManifest, getTicketId, fetcher) {
  const result = Vue.computed(() => (getResult ? getResult() : null));

  // ── 接口数据（异步进 Vue.ref）──
  const apiData = Vue.ref(null);      // /api/contract/comparison/<id> 的 data
  const apiLoading = Vue.ref(false);
  const apiError = Vue.ref("");
  const apiTicketId = Vue.ref("");

  function _clauses() {
    const a = apiData.value;
    return (a && Array.isArray(a.clauses)) ? a.clauses : [];
  }
  function _dr() {
    const r = result.value;
    return (r && r.deductions_result) || {};
  }

  // ── 档位识别 ──
  const tierInfo = Vue.computed(() => {
    const r = result.value || {};
    const t = r.tier_result || {};
    const api = apiData.value || {};
    const raw = String(t.confidence || "").toLowerCase();
    const cls = (raw === "high" || raw === "h" || raw === "高") ? "high"
      : (raw === "medium" || raw === "m" || raw === "中") ? "medium" : "";
    return {
      name: api.tier_display_name || t.display_name || r.tier_id || "",
      // conf 只做人话置信度（高/中/低/""）；score 是特征加权分，单独展示，绝不乘 100
      conf: _confLabel(t.confidence),
      cls,
      evidence: t.evidence || "",
      score: Number(t.score) || 0,
    };
  });

  // ── Tab ──
  const cmpTab = Vue.ref("text");
  function setTab(t) { cmpTab.value = t === "orig" ? "orig" : "text"; }

  // ── 左栏来源（契约 §4）：template / pdf / none；"" = 未拉到接口（回落现有路径）──
  const leftSource = Vue.computed(() => {
    const a = apiData.value;
    if (!a) return "";
    return a.source || "none";
  });

  // ── 扣费明细行（右栏）──
  const dedRows = Vue.computed(() => {
    const r = result.value;
    if (!r) return [];
    const dr = r.deductions_result;
    const items = (dr && Array.isArray(dr.items)) ? dr.items
      : (Array.isArray(r.deductions) ? r.deductions : []);
    return items.map((it, idx) => {
      const category = String(it.category || "依实");
      const tagKey = category === "必扣" ? "must"
        : category === "违约金" ? "pen" : "actual";
      const start = (typeof it.anchor_start === "number") ? it.anchor_start : null;
      const end = (typeof it.anchor_end === "number") ? it.anchor_end : null;
      const canLocate = !it.anchor_missing && start != null && end != null && end > start;
      const contractIndex = (it.contract_index != null) ? Number(it.contract_index) : 0;
      const domId = `cc-kw-${contractIndex}-${idx}`;
      return {
        idx,
        item: it.item || "",
        amount: Number(it.amount) || 0,
        category,
        tagKey,
        pending: Boolean(it.pending),
        basis: it.basis || it.reason || "",
        reason: it.reason || it.basis || "",
        canLocate,
        start: start || 0,
        end: end || 0,
        contractIndex,
        domId,
        // v4.4：右栏卡片定位键。template→t-row-*／t-note；pdf→e-{条}-{项}；
        // 回落（无接口）→沿用现有锚点，用 domId 作键（左栏 mark 已带 data-dom）
        dom: _dedDom(it, idx, contractIndex, canLocate),
      };
    });
  });

  function _dedDom(it, idx, contractIndex, canLocate) {
    const src = leftSource.value;
    const name = String(it.item || "");
    if (src === "template") {
      if (name === "违约金") return "t-note";
      return "t-row-" + _rowKey(name);
    }
    if (src === "pdf") {
      const nos = _nosOf(name, it.basis || it.reason, _clauses());
      return nos.length ? ("e-" + nos[0] + "-" + name) : "";
    }
    return canLocate ? `cc-kw-${contractIndex}-${idx}` : "";
  }

  // ── 原文 sections（回落路径：多份归并 contract_analyses 逐份渲染）──
  const textSections = Vue.computed(() => {
    const r = result.value;
    if (!r) return [];
    const rows = dedRows.value;
    const list = (Array.isArray(r.contract_analyses) && r.contract_analyses.length)
      ? r.contract_analyses.map((a, i) => ({
          index: (a.index != null) ? Number(a.index) : i,
          kind: String(a.kind || ""),
          title: String(a.tier_display_name || a.filename || ""),
          text: String(a.text || ""),
          error: a.extraction_error || null,
        }))
      : (String(r.contract_text || r.text || "")
        ? [{ index: 0, kind: "", title: "", text: String(r.contract_text || r.text || ""), error: r.extraction_error || null }]
        : []);
    return list.map((sec) => {
      const marks = [];
      const seen = new Set();
      rows.forEach((rw) => {
        if (!rw.canLocate || rw.contractIndex !== sec.index) return;
        const sig = `${rw.start}:${rw.end}`;
        if (seen.has(sig)) return;
        seen.add(sig);
        marks.push({ start: rw.start, end: rw.end, domId: rw.domId, item: rw.item });
      });
      return { ...sec, html: _injectMarks(sec.text, marks) };
    });
  });
  const hasText = Vue.computed(() => textSections.value.some(s => s.text.trim()));
  const textError = Vue.computed(() => {
    const r = result.value;
    if (!r) return "";
    if (r.extraction_error) return String(r.extraction_error);
    const errs = (Array.isArray(r.contract_analyses) ? r.contract_analyses : [])
      .map(a => a.extraction_error).filter(Boolean);
    return errs.length ? String(errs[0]) : "";
  });

  // ── 原件页（逐张原图优先；无原图才用合并 PDF；下载件回退 cPath 单页） ──
  //
  // 为什么逐张原图优先（2026-09-22 修复「同一份合同看两遍」）：
  //   旧逻辑先把合并 PDF 列一遍、再把每张原图列一遍 —— 2 张照片的合同会在弹窗里
  //   出现 3 个页面块（内容是同一份），页面长度翻倍；且合并 PDF 走 <iframe>，
  //   必然带出浏览器内置 PDF 查看器的整条深色工具栏（缩放/下载/打印），与本站 UI 完全不搭。
  //   逐张 <img> 尺寸可控（CSS 限高）、可点击放大，故有原图时不再内嵌合并 PDF。
  //   下载的电子合同（manifest 只有 PDF、无原图）仍走 iframe 分支，行为不变。
  const origPages = Vue.computed(() => {
    const manifest = (getManifest && getManifest()) || null;
    const src = (getSourcePath && getSourcePath()) || "";
    const pages = [];
    const merged = (manifest && manifest.merged_pdf_path) || "";
    const srcs = (manifest && Array.isArray(manifest.source_files)) ? manifest.source_files : [];
    const imgs = srcs.filter(f => _isImgPath(f.filepath));
    if (imgs.length) {
      imgs.forEach((f, i) => {
        pages.push({
          type: "img",
          url: _previewUrl(f.filepath),
          cap: `原图 ${i + 1}/${imgs.length}${f.filename ? " · " + f.filename : ""}`,
        });
      });
      return pages;
    }
    if (merged) {
      pages.push({ type: "pdf", url: _previewUrl(merged), cap: "合同 PDF" });
      return pages;
    }
    if (src) {
      pages.push({ type: _isImgPath(src) ? "img" : "pdf", url: _previewUrl(src), cap: (srcs[0] && srcs[0].filename) || "" });
    }
    return pages;
  });
  const hasOrig = Vue.computed(() => origPages.value.length > 0);

  // ── 金额汇总（右栏顶部 · 回落路径，保持既有字形与取值不变）──
  const summaryCells = Vue.computed(() => {
    const r = result.value;
    if (!r) return [];
    const dr = r.deductions_result || {};
    const paid = Number(r.actual_paid != null ? r.actual_paid : r.paid_amount) || 0;
    const tot = Number(r.total_fee) || 0;
    const dedSum = dedRows.value.reduce((s, x) => s + (x.amount || 0), 0);
    const netRefund = (dr.net_refund != null) ? Number(dr.net_refund) : null;
    const refund = netRefund != null ? netRefund
      : Number(dr.refund != null ? dr.refund : r.refund) || Math.max(paid - dedSum, 0);
    const cells = [
      { k: "合同总额", v: tot ? "¥" + _money(tot) : "—" },
      { k: "实缴", v: paid ? "¥" + _money(paid) : "—" },
      { k: "扣费合计", v: "¥" + _money(dedSum) },
    ];
    if (Number(dr.tail_due) > 0) cells.push({ k: "应付尾款", v: "¥" + _money(dr.tail_due) });
    cells.push({ k: "应退", v: refund ? "¥" + _money(refund) : "待核", refund: true });
    return cells;
  });

  // ── 汇总格（v4.4）：带 dom 锚点键（契约 §2.2）。没有原文依据的格子不给 dom ──
  const sumCells = Vue.computed(() => {
    const r = result.value;
    if (!r) return [];
    const dr = r.deductions_result || {};
    const dedSum = dedRows.value.reduce((s, x) => s + (x.amount || 0), 0);
    const src = leftSource.value;
    if (src === "template") {
      const tot = Number(dr.total_fee) || 0;
      const paid = Number(dr.paid_amount) || 0;
      const tail = Number(dr.tail_due) || 0;
      const refund = (dr.net_refund != null) ? Number(dr.net_refund) : Math.max(paid - dedSum, 0);
      return [
        { k: "合同总额", v: tot ? "¥" + _money(tot) : "—", dom: "t-total" },
        { k: "实缴", v: paid ? "¥" + _money(paid) : "—", dom: "t-paid" },
        { k: "扣费合计", v: "¥" + _money(dedSum) },              // 无 dom：扣费合计不定位
        { k: "应付尾款", v: "¥" + _money(tail), dom: "t-paid" },
        { k: "应退", v: refund ? "¥" + _money(refund) : "待核", dom: "t-note", refund: true },
      ];
    }
    if (src === "pdf") {
      const tot = Number(r.total_fee) || 0;
      const paid = Number(r.actual_paid != null ? r.actual_paid : r.paid_amount) || 0;
      const refund = (dr.net_refund != null) ? Number(dr.net_refund)
        : (Number(dr.refund != null ? dr.refund : r.refund) || Math.max(paid - dedSum, 0));
      return [
        { k: "合同总额", v: tot ? "¥" + _money(tot) : "—", dom: "t-total" },  // 只有合同总额挂 dom
        { k: "实缴", v: paid ? "¥" + _money(paid) : "—" },
        { k: "扣费合计", v: "¥" + _money(dedSum) },
        { k: "应退", v: refund ? "¥" + _money(refund) : "待核", refund: true },
      ];
    }
    return summaryCells.value.map(c => ({ ...c }));
  });

  // ── 左栏条款块 ──
  const clauseBlocks = Vue.computed(() => {
    const src = leftSource.value;
    const clauses = _clauses();
    if (src === "pdf") return _buildElectronicBlocks(clauses);
    if (src === "template") return _buildTemplateBlocks(clauses, apiData.value || {});
    return [];
  });

  function _buildElectronicBlocks(clauses) {
    const items = dedRows.value;
    const linked = {};
    items.forEach(d => {
      _nosOf(d.item, d.basis || d.reason, clauses).forEach(no => {
        (linked[no] = linked[no] || []);
        if (!linked[no].includes(d.item)) linked[no].push(d.item);
      });
    });
    const show = clauses.filter(c => c && c.no && (linked[c.no] || _GENERAL_CLAUSES.includes(c.no)));
    return show.map(c => {
      const body = _reflowBody(c.body);
      const marks = [];
      (linked[c.no] || []).forEach(item => {
        const kws = _kwOf(item);
        const parts = body.split(/(。|；|：)/);
        let pos = 0;
        parts.forEach(p => {
          if (p.trim() && kws.some(kw => p.replace(/\s+/g, "").includes(kw.replace(/\s+/g, "")))) {
            marks.push({ s: pos, e: pos + p.length, dom: "e-" + c.no + "-" + item });
          }
          pos += p.length;
        });
      });
      // 合同总额锚点 = 第三条（一）「培训费用合计人民币 3580.00 元」这一句
      if (c.no === "三") {
        const kw = "培训费用合计人民币";
        const i = body.indexOf(kw);
        if (i >= 0) {
          let e = body.length;
          for (const ch of ["，", "。", "\n", "；"]) {
            const j = body.indexOf(ch, i);
            if (j >= 0 && j < e) e = j;
          }
          marks.push({ s: i, e, dom: "t-total" });
        }
      }
      let html = "";
      let cur = 0;
      marks.sort((a, b) => a.s - b.s).forEach(m => {
        if (m.s < cur) return;
        const cls = (m.dom === "t-total") ? "cc-kw tgt" : "cc-kw";
        html += _esc(body.slice(cur, m.s)) + `<mark class="${cls}" data-dom="${_esc(m.dom)}">${_esc(body.slice(m.s, m.e))}</mark>`;
        cur = m.e;
      });
      html += _esc(body.slice(cur));
      const chips = (linked[c.no] || []).map(i => ({ text: i, tagKey: "actual" }));
      return { no: c.no, title: c.title || "", chips, kind: "electronic", bodyHtml: html };
    });
  }

  function _buildTemplateBlocks(clauses, api) {
    const dr = _dr();
    const items = dedRows.value;
    const refundRows = Array.isArray(api.refund_rows) ? api.refund_rows : [];
    const feeNo = _findByTitle(clauses, _FEE_CLAUSE_TITLE_KW) || _FEE_CLAUSE_NO_FALLBACK;
    const refundNo = _refundClauseNo(clauses, refundRows);
    const feeClause = clauses.find(c => c && c.no === feeNo);
    const refundClause = clauses.find(c => c && c.no === refundNo);
    const total = Number(dr.total_fee) || 0;
    const paid = Number(dr.paid_amount) || 0;
    const tail = Number(dr.tail_due) || 0;
    const installment = paid > 0 && tail > 0;

    const blocks = [];

    // ① 费用条款（四／三）：模板正文 + OCR 填空，裁掉与扣费无关段，其余进折叠区 1
    if (feeClause) {
      const lines = String(feeClause.body || "").split("\n").map(s => s.trim()).filter(Boolean);
      const cutFrom = lines.findIndex(l => l.includes("（2）实行预约培训"));
      const cutTo = lines.findIndex(l => l.includes("（三）代收代交"));
      const main = cutFrom >= 0 ? lines.slice(0, cutFrom) : lines;
      const tailLines = cutTo >= 0 ? lines.slice(cutTo) : [];
      const parts = main.map(l => _fillLine(l, total, paid, tail, installment));
      parts.push({
        type: "details",
        summary: "▸ 代收代交考试费 / 工本费 / 补考费（模板印值，与识别值一致）",
        html: tailLines.map(l => `<div class="b-para" style="color:#64748B">${_esc(l)}</div>`).join(""),
      });
      blocks.push({
        no: feeNo, title: feeClause.title || "", kind: "expense",
        chips: [{ text: "合同总额", tagKey: "actual" }, { text: "实缴 / 应付尾款", tagKey: "pen" }],
        parts,
        bodyHtml: parts.filter(p => p.type === "line").map(p => `<div class="${p.cls}">${p.html}</div>`).join(""),
      });
    }

    // ② 退费条款（八／六／五）：正文 + 退费表栅格 + 备注（t-note）+ 折叠区 2
    if (refundClause) {
      const lines = String(refundClause.body || "").split("\n").map(s => s.trim());
      const tblIdx = lines.findIndex(l => l === "项目");
      const noteIdx = lines.findIndex(l => l.startsWith("备注："));
      const endMain = tblIdx > 0 ? tblIdx : (noteIdx > 0 ? noteIdx : lines.length);
      const main = lines.slice(0, endMain).filter(Boolean);
      const cells = (tblIdx > 0 && noteIdx > tblIdx) ? lines.slice(tblIdx, noteIdx) : [];
      const noteLine = noteIdx >= 0 ? lines[noteIdx] : "";
      const parts = main.map(l => ({ type: "line", cls: _lineCls(l), html: _esc(l), dom: "" }));
      if (refundRows.length) parts.push({ type: "grid" });
      if (noteLine) parts.push({ type: "line", cls: "b-para", html: _esc(noteLine), dom: "t-note" });
      parts.push({
        type: "details",
        summary: "▸ 模板退费表的原始排版（逐单元格一行，供逐字核对）",
        html: `<div class="raw" style="max-height:200px">${_esc(cells.join("\n"))}</div>`,
      });
      blocks.push({
        no: refundNo, title: refundClause.title || "", kind: "refund",
        chips: items.map(d => ({ text: d.item, tagKey: d.tagKey })),
        parts,
        bodyHtml: parts.filter(p => p.type === "line").map(p => `<div class="${p.cls}">${p.html}</div>`).join(""),
      });
    }

    return blocks;
  }

  // 一行的 OCR 填空 + 锚点 + 类名。【填空值不硬编码】
  function _fillLine(line, total, paid, tail, installment) {
    let s = line;
    s = s.replace(/培训费用总额合计人民币[\s\u3000]+元/, `培训费用总额合计人民币 **${_money(total)}** 元`);
    if (installment) {
      s = s.replace(/□一次性支付，□分期付款/, "□一次性支付，**☑分期付款**");
      s = s.replace(/（分期付款约定：[\s\u3000]*）/, `（分期付款约定：%%首付 ${_money(paid)} 元，欠款 ${_money(tail)} 元%%）`);
    }
    const dom = line.includes("培训费用总额合计") ? "t-total"
      : (line.includes("分期付款约定") ? "t-paid" : "");
    return { type: "line", cls: _lineCls(line), html: _fillify(s), dom };
  }

  // ── 退费表栅格（上传件专用）──
  const refundTable = Vue.computed(() => {
    if (leftSource.value !== "template") return null;
    const a = apiData.value || {};
    const raw = Array.isArray(a.refund_rows) ? a.refund_rows : [];
    if (!raw.length) return null;
    const header = raw[0].map(String);
    // ⚠️ 备注行（以「备注：」开头，带全角冒号）不是表行 —— 只匹配「备注」会命中表头第 4 格
    const dataRaw = raw.slice(1).filter(r => !(String((r && r[0]) || "").startsWith("备注：")));
    const rows = dataRaw.map(_normRow);
    const hitItems = dedRows.value.map(d => _rowKey(d.item));
    const hitSet = rows.map(r => r[0]).filter(n => hitItems.includes(n));
    return { raw, header, rows, hitSet };
  });

  // ── 折叠区内容（上传件 3 个中的内容来源）──
  const leftFolds = Vue.computed(() => {
    if (leftSource.value !== "template") return { tail: [], rawRefund: "", ocrText: "" };
    const a = apiData.value || {};
    const clauses = _clauses();
    const feeNo = _findByTitle(clauses, _FEE_CLAUSE_TITLE_KW) || _FEE_CLAUSE_NO_FALLBACK;
    const feeClause = clauses.find(c => c && c.no === feeNo);
    const fLines = feeClause ? String(feeClause.body || "").split("\n").map(s => s.trim()).filter(Boolean) : [];
    const cutTo = fLines.findIndex(l => l.includes("（三）代收代交"));
    const tail = cutTo >= 0 ? fLines.slice(cutTo) : [];
    const refundRows = Array.isArray(a.refund_rows) ? a.refund_rows : [];
    const refundNo = _refundClauseNo(clauses, refundRows);
    const rc = clauses.find(c => c && c.no === refundNo);
    const rLines = rc ? String(rc.body || "").split("\n").map(s => s.trim()) : [];
    const tblIdx = rLines.findIndex(l => l === "项目");
    const noteIdx = rLines.findIndex(l => l.startsWith("备注："));
    const rawRefund = (tblIdx > 0 && noteIdx > tblIdx) ? rLines.slice(tblIdx, noteIdx).join("\n") : "";
    return { tail, rawRefund, ocrText: _ocrText() };
  });

  function _ocrText() {
    const r = result.value;
    if (!r) return "";
    if (Array.isArray(r.contract_analyses) && r.contract_analyses.length) {
      return r.contract_analyses.map(a => String((a && a.text) || "")).join("\n\n");
    }
    return String(r.contract_text || r.text || "");
  }

  // ── AI 摘要（额外约定等）──
  const aiSummary = Vue.computed(() => {
    const r = result.value;
    const dr = (r && r.deductions_result) || null;
    if (dr && dr.rule_summary) return String(dr.rule_summary);
    return (r && r.summary) ? String(r.summary) : "";
  });
  // 渲染容器用 v-html，内容 = _rich(_esc(raw))（先转义再转 <b>，防 XSS）
  const aiSummaryHtml = Vue.computed(() => {
    const raw = aiSummary.value;
    if (!raw) return "";
    return _rich(_esc(raw)).replace(/\n/g, "<br>");
  });

  // ── 交互：悬停定位 / 点击钉住（v4.4：按 data-dom 键联动）──
  const activeDom = Vue.ref("");
  const pinnedDom = Vue.ref("");
  const _targets = {};  // dom 键 → 左栏目标元素（普通对象，不入响应式）

  // 左栏目标元素的 ref 回调（模板 :ref）。null（卸载）时移除。
  function registerTarget(k, el) {
    if (!k) return;
    if (el) _targets[k] = el;
    else delete _targets[k];
  }
  function _resolveTarget(k) {
    if (!k) return null;
    const cached = _targets[k];
    if (cached && cached.isConnected !== false) return cached;
    // 回落：v-html 注入的元素（电子合同 mark / 回落路径 mark）挂不了 ref，用 DOM 查
    return _qs('[data-dom="' + k + '"]');
  }
  function _applyHit(k, on) {
    _qsa('[data-dom="' + k + '"]').forEach(t => {
      if (t && t.classList && t.classList.toggle) t.classList.toggle("cc-hit", on);
    });
  }
  // .grid-table 的行是 display:contents —— getBoundingClientRect 宽高全 0，
  // 必须取 :scope > * 子元素盒子的并集，否则定位落空
  function _visualBox(el) {
    const b = el.getBoundingClientRect();
    if (b && (b.width || b.height)) return b;
    let box = null;
    if (el.querySelectorAll) {
      el.querySelectorAll(":scope > *").forEach(k => {
        const kb = k.getBoundingClientRect();
        if (!kb || (!kb.width && !kb.height)) return;
        box = box
          ? { top: Math.min(box.top, kb.top), bottom: Math.max(box.bottom, kb.bottom) }
          : { top: kb.top, bottom: kb.bottom };
      });
    }
    return box;
  }
  // 禁止 scrollIntoView：向上找最近的滚动容器，**总是**把目标中心对齐容器中心（死区 4px）。
  // ⚠️ 不设「已在可视区内就 return」的短路 —— 那会让目标贴在视口底边时页面纹丝不动。
  function _scrollLocate(el) {
    const box = _visualBox(el);
    if (!box) return;
    let pane = el.parentElement;
    while (pane) {
      const cs = _computedStyle(pane);
      const oy = cs ? String(cs.overflowY || "") : "";
      if (/(auto|scroll)/.test(oy) && pane.scrollHeight > pane.clientHeight + 1) break;
      pane = pane.parentElement;
    }
    if (!pane) return;
    const pr = pane.getBoundingClientRect();
    const visH = pane.clientHeight;
    const delta = (box.top + box.bottom) / 2 - (pr.top + visH / 2);
    if (Math.abs(delta) < 4) return;
    pane.scrollTop += delta;
  }
  function _scheduleLocate(el) {
    const run = () => { try { _scrollLocate(el); } catch (e) { /* 定位失败不影响交互 */ } };
    const nt = (typeof Vue !== "undefined" && Vue.nextTick) ? Vue.nextTick : null;
    if (nt) { nt(run); return; }
    if (typeof requestAnimationFrame === "function") { requestAnimationFrame(run); return; }
    run();
  }

  function hoverDom(k) {
    if (!k) return;
    activeDom.value = k;
    _applyHit(k, true);
    const el = _resolveTarget(k);
    if (el) _scheduleLocate(el);
  }
  function leaveDom(k) {
    if (!k) return;
    if (pinnedDom.value === k) return;   // 钉住的行移出仍保持高亮
    activeDom.value = "";
    _applyHit(k, false);
  }
  function pinDom(k) {
    if (!k) return;
    if (pinnedDom.value === k) {         // 再次点击取消钉住
      pinnedDom.value = "";
      activeDom.value = "";
      _applyHit(k, false);
      return;
    }
    if (pinnedDom.value && pinnedDom.value !== k) _applyHit(pinnedDom.value, false);
    pinnedDom.value = k;
    activeDom.value = k;
    _applyHit(k, true);
    const el = _resolveTarget(k);
    if (el) _scheduleLocate(el);
  }
  function isHit(k) { return !!k && activeDom.value === k; }

  // ── 既有行级悬停（回落路径仍保留；右栏模板优先用 hoverDom）──
  const activeIdx = Vue.ref(-1);
  const pinnedIdx = Vue.ref(-1);
  function _markEl(domId) {
    return document.querySelector(`mark.cc-kw[data-id="${domId}"]`);
  }
  // ⚠️ 禁止 scrollIntoView（契约 §4.1）：统一走 _scrollLocate（自算可视盒 → 设 scrollTop）
  function _highlight(idx, on) {
    const row = dedRows.value[idx];
    if (!row) return;
    const el = _resolveTarget(row.dom) || _markEl(row.domId);
    if (!el) return;
    if (el.classList && el.classList.toggle) el.classList.toggle("cc-hit", on);
    if (on) _scheduleLocate(el);
  }
  function hoverRow(idx) {
    activeIdx.value = idx;
    _highlight(idx, true);
  }
  function leaveRow(idx) {
    if (pinnedIdx.value !== idx) {
      activeIdx.value = -1;
      _highlight(idx, false);
    }
  }
  function pinRow(idx) {
    if (pinnedIdx.value === idx) {
      pinnedIdx.value = -1;
      _highlight(idx, false);
      return;
    }
    if (pinnedIdx.value >= 0) _highlight(pinnedIdx.value, false);
    pinnedIdx.value = idx;
    _highlight(idx, true);
  }

  // ── 异步拉取接口（有 ticket_id 优先；拿不到 → 回落现有路径，绝不空白/报错）──
  function _defaultFetch(url) {
    const f = (typeof fetch === "function") ? fetch
      : (typeof globalThis !== "undefined" ? globalThis.fetch : null);
    if (!f) return Promise.reject(new Error("fetch 不可用"));
    return f(url);
  }
  async function load() {
    const id = getTicketId ? String(getTicketId() || "") : "";
    apiTicketId.value = id;
    if (!id) {
      apiData.value = null;
      apiError.value = "";
      apiLoading.value = false;
      return;
    }
    apiLoading.value = true;
    apiError.value = "";
    try {
      const f = fetcher || _defaultFetch;
      const res = await f("/api/contract/comparison/" + encodeURIComponent(id));
      if (res && res.ok === false) throw new Error("HTTP " + (res.status || ""));
      let data = res;
      if (res && typeof res.json === "function") data = await res.json();
      if (data && data.success === false) throw new Error(data.error || "接口返回失败");
      apiData.value = (data && data.data) ? data.data : (data || null);
    } catch (e) {
      apiError.value = String((e && e.message) || e || "");
      apiData.value = null;   // 失败即回落现有渲染路径
    } finally {
      apiLoading.value = false;
    }
  }

  function reset() {
    activeIdx.value = -1;
    pinnedIdx.value = -1;
    activeDom.value = "";
    pinnedDom.value = "";
    cmpTab.value = "text";
  }

  return {
    // 状态
    cmpTab, setTab,
    tierInfo,
    dedRows,
    textSections, hasText, textError,
    origPages, hasOrig,
    summaryCells,
    sumCells,
    aiSummary, aiSummaryHtml,
    // v4.4 左栏
    leftSource, clauseBlocks, refundTable, leftFolds,
    // v4.4 接口
    apiData, apiLoading, apiError, apiTicketId, load,
    // 交互
    activeIdx, pinnedIdx, hoverRow, leaveRow, pinRow,
    activeDom, pinnedDom, hoverDom, leaveDom, pinDom, isHit, registerTarget,
    reset,
    // 工具
    money: _money,
  };
}
