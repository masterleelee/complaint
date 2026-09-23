// 合同原文对照弹窗（v2 简化形态）：左原文/原图 · 右扣费明细对照
// 数据源：ar（分析结果）—— tier_result / deductions_result.items（含 anchor_start/end、
// contract_index）/ contract_text / contract_analyses / summary
// 原件：useWorkflow 的 cPath + contractManifest（source_files / merged_pdf_path），
//       一律走现有 /api/contract/preview（电子合同下载件与上传件同一条通道）
// 交互：悬停/点击右栏明细 → 左栏原文滚动定位 + 锚点 mark 高亮；点击可钉住
//
// 设计原则：
//   - 只读消费 ar.value，不写回（避免与 useWorkflow 的 recalc() 互相覆盖）
//   - 锚点区间沿用后端口径：anchor_start/end 相对所属份（contract_index 对应份）的文本
//   - 无锚点（OCR 失败/429 降级）的行不显示定位，不报错
//
// 项目约定：Vue 由 <script src> 挂到 window.Vue，本文件用 Vue.ref/computed，
// 不在 importmap 登记 "Vue"。
// ⚠️ 必须以 Vue.reactive(...) 包裹返回值再暴露给模板：普通对象内嵌 computed
// 模板不解包 → 根渲染崩溃整页白屏（2026-09-20 三栏预览实锤，同类教训）。

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
function _previewUrl(filepath) {
  return "/api/contract/preview?path=" + encodeURIComponent(filepath || "");
}
const _isImgPath = (p) => /\.(png|jpe?g|gif|bmp|webp)$/i.test(p || "");

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
    html += `<mark class="cc-kw" data-id="${_esc(m.domId)}" title="${_esc(m.item)}">${_esc(text.slice(m.start, m.end))}</mark>`;
    cursor = m.end;
  });
  if (cursor < text.length) html += _esc(text.slice(cursor));
  return html;
}

// ── 主 composable ───────────────────────────────────────────────────────
export function useContractCompare(getResult, getSourcePath, getManifest) {
  const result = Vue.computed(() => (getResult ? getResult() : null));

  // ── 档位识别 ──
  const tierInfo = Vue.computed(() => {
    const t = (result.value && result.value.tier_result) || {};
    const raw = String(t.confidence || "").toLowerCase();
    const cls = (raw === "high" || raw === "h" || raw === "高") ? "high"
      : (raw === "medium" || raw === "m" || raw === "中") ? "medium" : "";
    const score = t.score != null ? Number(t.score) : null;
    return {
      name: t.display_name || (result.value && result.value.tier_id) || "",
      conf: score != null && score > 0 ? Math.round(score * 100) + "%" : (t.confidence || ""),
      cls,
      evidence: t.evidence || "",
    };
  });

  // ── Tab ──
  const cmpTab = Vue.ref("text");
  function setTab(t) { cmpTab.value = t === "orig" ? "orig" : "text"; }

  // ── 扣费明细行（右栏） ──
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
      return {
        idx,
        item: it.item || "",
        amount: Number(it.amount) || 0,
        category,
        tagKey,
        pending: Boolean(it.pending),
        basis: it.basis || it.reason || "",
        canLocate,
        start: start || 0,
        end: end || 0,
        contractIndex: (it.contract_index != null) ? Number(it.contract_index) : 0,
        domId: `cc-kw-${(it.contract_index != null) ? Number(it.contract_index) : 0}-${idx}`,
      };
    });
  });

  // ── 原文 sections：多份归并（contract_analyses）逐份渲染；单份/旧数据整体兜底 ──
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

  // ── 金额汇总（右栏顶部） ──
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

  // ── AI 摘要（额外约定等） ──
  const aiSummary = Vue.computed(() => {
    const r = result.value;
    return (r && r.summary) ? String(r.summary) : "";
  });

  // ── 交互：悬停定位 / 点击钉住 ──
  const activeIdx = Vue.ref(-1);
  const pinnedIdx = Vue.ref(-1);
  function _markEl(domId) {
    return document.querySelector(`mark.cc-kw[data-id="${domId}"]`);
  }
  function _highlight(idx, on) {
    const row = dedRows.value[idx];
    if (!row || !row.canLocate) return;
    const el = _markEl(row.domId);
    if (!el) return;
    el.classList.toggle("cc-hit", on);
    if (on) el.scrollIntoView({ behavior: "smooth", block: "center" });
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
  function reset() {
    activeIdx.value = -1;
    pinnedIdx.value = -1;
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
    aiSummary,
    // 交互
    activeIdx, pinnedIdx, hoverRow, leaveRow, pinRow, reset,
    // 工具
    money: _money,
  };
}
