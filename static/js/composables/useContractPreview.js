// 三栏预览组合式函数：上传合同 → 原文 / 原件 / 扣费明细
// 数据源：/api/contract/analyze 异步结果 d
//   d.tier_id / d.tier_result / d.deductions_result（含 anchor_* 字段）
//   d.contract_text / d.text_source / d.extraction_error / d.cache_key
//
// 用法（在 app.js setup() 中）：
//   const preview = useContractPreview(() => ar.value, () => cPath.value);
//   // 模板里调用 preview.locate(key)、preview.tierDisplayName 等
//
// 设计原则：
//   - 只读消费 ar.value，不写回（避免和 useWorkflow 的 recalc() 互相覆盖）
//   - 把 deductions_result.items 的每条 item 派生稳定 key（category:item:idx），
//     供右侧明细点击定位、anchor 高亮定位
//   - contract_text 按「第X条」切片，便于左侧分组渲染
//
// 项目约定：Vue 全局由 <script src="...vue..."> 直接挂到 window.Vue，
// 其他 composable 均用 Vue.ref / Vue.computed / Vue.nextTick，本文件同此风格，
// 避免在 importmap 里登记 "Vue"（HTML 中并无对应路径）。

// ── 工具：本地货币格式 / HTML 转义 ──────────────────────────────────────
function _money(n) {
  const v = Number(n) || 0;
  return v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}
function _esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, m => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]
  ));
}
function _normKey(s) {
  // 用于 mark[data-id] 的稳定 key；避免包含 CSS 选择器敏感字符
  return String(s || "").replace(/[^A-Za-z0-9_-]/g, "_");
}
const TAG_LABEL = { must: "必扣", actual: "依实", pen: "违约金" };

// ── 按「第X条」切分合同正文（demo parity：第四条 / 第八条 / 第九条 等） ──
// 工单 10：返回条款在原文中的字符区间 [start, end)，供逐条注入 anchor mark
// （修复 06 版「每条条款重复渲染整篇原文」的缺陷：每条条款只渲染自己的正文）。
function _splitClausesRanged(text) {
  if (!text || !text.trim()) return [];
  const re = /第[一二三四五六七八九十百零\d]+条[^\n]*/g;
  const s = String(text);
  const matches = [...s.matchAll(re)];
  const out = [];
  const firstStart = matches.length ? matches[0].index : s.length;
  const preamble = s.slice(0, firstStart).trim();
  if (preamble) {
    out.push({ no: "", title: "前言", body: preamble, start: 0, end: firstStart });
  }
  matches.forEach((m, i) => {
    const header = m[0] || "";
    const start = m.index;
    const end = i + 1 < matches.length ? matches[i + 1].index : s.length;
    const body = s.slice(start, end).trim();
    const mm = header.match(/第([一二三四五六七八九十百零\d]+)条\s*([^\n.。;；]*)/);
    const no = mm ? mm[1] : "";
    const title = mm && mm[2] ? mm[2].trim() : header.trim();
    out.push({ no, title, body, start, end });
  });
  return out;
}

function _splitClauses(text) {
  if (!text || !text.trim()) return [];
  return _splitClausesRanged(text).map(({ no, title, body }) => ({ no, title, body, page: 1 }));
}

// ── mark 注入（工单 10 从 originalHtml 抽出，供逐份/逐条款复用） ──────
//   marks: [{start, end, domId, item}]；区间重叠时后者失效（保证 HTML 合法）
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
    const piece = text.slice(m.start, m.end);
    html += `<mark class="cp-mark" data-id="${_esc(m.domId)}" data-item="${_esc(m.item)}" title="${_esc(m.item)}">${_esc(piece)}</mark>`;
    cursor = m.end;
  });
  if (cursor < text.length) html += _esc(text.slice(cursor));
  return html;
}

// 一段正文（某一份合同）的条款列表：每条条款注入落在自己区间内的 anchor mark
function _sectionClauses(text, items) {
  const ranged = _splitClausesRanged(text);
  if (!ranged.length) return [];
  const s = String(text);
  // 该份的 marks（anchor 相对该份正文）
  const marks = [];
  const seen = new Set();
  (items || []).forEach((it) => {
    if (it.missing) return;
    if (typeof it.start !== "number" || typeof it.end !== "number") return;
    if (it.end <= it.start) return;
    const sig = `${it.start}:${it.end}`;
    if (seen.has(sig)) return;
    seen.add(sig);
    marks.push({ start: it.start, end: it.end, domId: it.domId, item: it.item || "" });
  });
  return ranged.map((cl) => {
    // 精确切片不 trim，保证 mark 偏移与正文对齐（trim 只影响展示空白）
    const body = s.slice(cl.start, cl.end);
    // 只注入完全落在条款区间内的 mark（跨界 mark 略去，避免切片错位）
    const inClause = marks
      .filter((m) => m.start >= cl.start && m.end <= cl.end)
      .map((m) => ({ ...m, start: m.start - cl.start, end: m.end - cl.start }));
    return {
      no: cl.no,
      title: cl.title,
      body: body.trim(),
      html: _injectMarks(body, inClause),
    };
  });
}

// ── 主 composable ──────────────────────────────────────────────────────
export function useContractPreview(getResult, getSourcePath) {
  // 状态：tier
  const tierResult = Vue.computed(() => {
    const r = getResult ? getResult() : null;
    if (!r) return null;
    if (r.tier_result && typeof r.tier_result === "object") return r.tier_result;
    // 兜底：扁平 tier_id + 旧字段也能渲染
    return {
      tier_id: r.tier_id || "",
      display_name: "",
      confidence: "low",
      score: 0,
      candidates: [],
      evidence: "",
    };
  });
  const tierId = Vue.computed(() => tierResult.value?.tier_id || "");
  const tierDisplayName = Vue.computed(() => {
    const t = tierResult.value;
    if (!t) return "—";
    return t.display_name || t.tier_id || "—";
  });
  const tierConfidence = Vue.computed(() => {
    const t = tierResult.value;
    if (!t) return "low";
    const c = String(t.confidence || "").toLowerCase();
    if (c === "high" || c === "h" || c === "强" || c === "高") return "high";
    if (c === "medium" || c === "med" || c === "m" || c === "中") return "medium";
    return "low";
  });
  const tierConfidenceLabel = Vue.computed(() => {
    const t = tierResult.value;
    if (!t || t.score == null) return "";
    const score = Number(t.score) || 0;
    return score > 0 ? `${Math.round(score * 100)}%` : "";
  });
  const tierCandidates = Vue.computed(() => {
    const t = tierResult.value;
    return Array.isArray(t?.candidates) ? t.candidates : [];
  });
  const tierEvidence = Vue.computed(() => tierResult.value?.evidence || "");

  // 状态：扣费明细（来自 deductions_result.items）
  const deductionsRaw = Vue.computed(() => {
    const r = getResult ? getResult() : null;
    const dr = r?.deductions_result;
    if (dr && Array.isArray(dr.items)) return dr.items;
    // 兜底：旧的 ar.deductions 也能展示
    if (r && Array.isArray(r.deductions)) return r.deductions;
    return [];
  });
  // 派生：稳定 key + 类别标签 + 可定位标记
  const deductions = Vue.computed(() => {
    return deductionsRaw.value.map((it, idx) => {
      const category = String(it.category || "依实");
      const tagKey = category === "必扣" ? "must"
        : category === "违约金" ? "pen"
        : "actual";
      const stableKey = `${tagKey}:${it.item || "item"}:${idx}`;
      return {
        ...it,
        _key: stableKey,
        _domId: _normKey(`cp-${stableKey}`),
        _tagKey: tagKey,
        _tagLabel: TAG_LABEL[tagKey] || category,
        _amount: Number(it.amount) || 0,
        // 工单 10：所属合同份（多份归并时后端打标签；单份无标签不渲染）
        _kindLabel: String(it.contract_kind || ""),
        _contractIndex: (it.contract_index != null) ? Number(it.contract_index) : 0,
      };
    });
  });
  // 按 category 分组（必扣 / 依实 / 违约金）
  const groupedDeductions = Vue.computed(() => {
    const order = [
      { key: "must", label: "必扣", items: [] },
      { key: "actual", label: "依实", items: [] },
      { key: "pen", label: "违约金", items: [] },
    ];
    deductions.value.forEach((it) => {
      const g = order.find((o) => o.key === it._tagKey) || order[1];
      g.items.push(it);
    });
    return order.filter((g) => g.items.length > 0);
  });
  const deductionsWarnings = Vue.computed(() => {
    const r = getResult ? getResult() : null;
    const dr = r?.deductions_result;
    if (dr && Array.isArray(dr.warnings)) return dr.warnings;
    return [];
  });
  const pendingCount = Vue.computed(() =>
    deductions.value.filter((it) => it.pending || it._amount === 0).length
  );
  const hasPending = Vue.computed(() => pendingCount.value > 0);
  const warnings = deductionsWarnings;

  // 状态：原文
  const contractText = Vue.computed(() => {
    const r = getResult ? getResult() : null;
    return r?.contract_text || r?.text || "";
  });
  const originalText = contractText;
  const sourcePath = Vue.computed(() => {
    if (getSourcePath) {
      const p = getSourcePath();
      if (p) return p;
    }
    const r = getResult ? getResult() : null;
    return r?.source_path || r?.filepath || "";
  });
  const textSource = Vue.computed(() => getResult?.()?.text_source || "");
  const extractionError = Vue.computed(() => getResult?.()?.extraction_error || null);
  const cacheKey = Vue.computed(() => getResult?.()?.cache_key || "");

  // 派生：切片后的 clauses（左侧按条款分组）
  const clauses = Vue.computed(() => _splitClauses(contractText.value));

  // 派生：anchor 映射（itemKey → {start, end, phrase, text, missing}）
  const anchorMap = Vue.computed(() => {
    const map = {};
    deductions.value.forEach((it) => {
      map[it._key] = {
        start: it.anchor_start,
        end: it.anchor_end,
        phrase: it.anchor_phrase || "",
        text: it.anchor_text || "",
        missing: Boolean(it.anchor_missing),
        domId: it._domId,
      };
    });
    return map;
  });

  // 派生：summary（顶部 summary 条用的聚合数据）
  const summary = Vue.computed(() => {
    const r = getResult ? getResult() : null;
    const items = deductions.value;
    const sumByCat = (cat) =>
      items.filter((it) => it.category === cat).reduce((s, x) => s + (x._amount || 0), 0);
    return {
      totalFee: Number(r?.total_fee) || 0,
      paid: Number(r?.actual_paid || r?.paid_amount) || 0,
      mustTotal: sumByCat("必扣"),
      actualTotal: sumByCat("依实"),
      penaltyTotal: sumByCat("违约金"),
      refund: Number(
        r?.deductions_result?.refund ?? r?.refund ?? 0
      ) || 0,
      refundPending: Boolean(r?.deductions_result?.refund_pending),
    };
  });

  // ── 工单 10：多份合同归并展示 ─────────────────────────────────────
  // 逐份记录：后端 contract_analyses（多份逐份分析 + 单份统一回传）；
  // 无逐份记录（下载件/旧数据）时用整体文本兜底成单节。
  const contractSections = Vue.computed(() => {
    const r = getResult ? getResult() : null;
    if (!r) return [];
    const list = Array.isArray(r.contract_analyses) ? r.contract_analyses : null;
    const build = (sections) => sections
      .map((sec) => {
        const anchorItems = deductions.value
          .filter((it) => it._contractIndex === sec.index)
          .map((it) => ({
            ...(anchorMap.value[it._key] || {}),
            item: it.item || "",
          }));
        return {
          ...sec,
          clauses: _sectionClauses(sec.text, anchorItems),
        };
      });
    if (list && list.length) {
      return build(list.map((a, i) => ({
        index: (a.index != null) ? Number(a.index) : i,
        kind: String(a.kind || ""),
        tierName: String(a.tier_display_name || ""),
        tierConfidence: String(a.tier_confidence || ""),
        filename: String(a.filename || ""),
        text: String(a.text || ""),
        textSource: String(a.text_source || ""),
        error: a.extraction_error || null,
      })));
    }
    // 兜底：整体文本单节（contract_text / text）
    const t = String(r.contract_text || r.text || "");
    if (!t) return [];
    return build([{
      index: 0, kind: "", tierName: "", tierConfidence: "",
      filename: "", text: t, textSource: String(r.text_source || ""), error: null,
    }]);
  });
  // 摘要条：「已识别 N 份」——N 只数份类型已识别的份（缺失份不猜测）
  const contractCount = Vue.computed(() => {
    const r = getResult ? getResult() : null;
    if (!r || !Array.isArray(r.contract_analyses)) return 0;
    return r.contract_analyses.filter((a) => a.kind).length;
  });
  const contractKinds = Vue.computed(() => {
    const r = getResult ? getResult() : null;
    if (!r || !Array.isArray(r.contract_analyses)) return [];
    return r.contract_analyses.map((a) => String(a.kind || ""));
  });
  const totalTextLength = Vue.computed(() =>
    contractSections.value.reduce((s, sec) => s + (sec.text || "").length, 0)
  );

  // 派生：原文字符串里注入 anchor mark（HTML），保留 anchor 起点位置
  //   - 按 anchor_start/end 直接 slice（更可靠，不依赖 phrase 在 text 中存在）
  //   - phrase 兜底：在 text 中再做一次 indexOf 作为可视依据
  const originalHtml = Vue.computed(() => {
    const text = contractText.value;
    if (!text) return "";
    // 收集所有 mark，按 start 排序，去重
    const marks = [];
    const seen = new Set();
    deductions.value.forEach((it) => {
      const a = anchorMap.value[it._key];
      if (!a || a.missing) return;
      if (typeof a.start !== "number" || typeof a.end !== "number") return;
      if (a.end <= a.start) return;
      const sig = `${a.start}:${a.end}`;
      if (seen.has(sig)) return;
      seen.add(sig);
      marks.push({
        start: a.start,
        end: a.end,
        domId: a.domId,
        itemKey: it._key,
        item: it.item || "",
      });
    });
    marks.sort((m1, m2) => m1.start - m2.start);
    // 区间不能重叠，重叠则后者失效（保证 HTML 合法）
    const accepted = [];
    let lastEnd = 0;
    marks.forEach((m) => {
      if (m.start < lastEnd) return;
      accepted.push(m);
      lastEnd = m.end;
    });
    if (!accepted.length) return _esc(text);
    let html = "";
    let cursor = 0;
    accepted.forEach((m) => {
      if (cursor < m.start) html += _esc(text.slice(cursor, m.start));
      const piece = text.slice(m.start, m.end);
      html += `<mark class="cp-mark" data-id="${_esc(m.domId)}" data-item="${_esc(m.item)}" title="${_esc(m.item)}">${_esc(piece)}</mark>`;
      cursor = m.end;
    });
    if (cursor < text.length) html += _esc(text.slice(cursor));
    return html;
  });

  // 交互：原件面板折叠
  const folded = Vue.ref(false);
  function toggleFolded() {
    folded.value = !folded.value;
  }

  // 交互：当前激活的 itemKey（CSS 用来高亮右侧行）
  const activeItemKey = Vue.ref("");
  function clearActive() {
    activeItemKey.value = "";
  }

  // 交互：定位（点击右侧明细 → 高亮原文 mark + 滚动；原件页闪烁）
  async function locate(itemKey) {
    activeItemKey.value = itemKey || "";
    const a = anchorMap.value[itemKey];
    if (!a) return;
    if (a.missing || typeof a.start !== "number") {
      // 锚点缺失：no-op（按 spec：不显示定位按钮/不报错）
      return;
    }
    await Vue.nextTick();
    try {
      // 移除所有 cp-hit
      document.querySelectorAll(".cp-mark").forEach((el) => el.classList.remove("cp-hit"));
      // 当前 mark 加 cp-hit
      const mark = document.querySelector(`mark[data-id="${a.domId}"]`);
      if (mark) {
        mark.classList.add("cp-hit");
        mark.scrollIntoView({ behavior: "smooth", block: "center" });
      }
      // 原件页闪烁（demo 用 .flash；我们对所有 page-card 都闪一下）
      const pages = document.querySelectorAll(".cp-page-card");
      pages.forEach((card) => {
        card.classList.remove("cp-flash");
        // 强制重排重启动画
        void card.offsetWidth;
        card.classList.add("cp-flash");
      });
    } catch (e) {
      // 静默失败，避免在定位时抛错打断流程
      console.warn("useContractPreview.locate failed:", e);
    }
  }

  // 派生：是否可定位（缺失锚点的项不渲染定位按钮）
  function canLocate(itemKey) {
    const a = anchorMap.value[itemKey];
    if (!a) return false;
    if (a.missing) return false;
    if (typeof a.start !== "number" || typeof a.end !== "number") return false;
    return true;
  }

  return {
    // 状态
    tierId,
    tierResult,
    tierDisplayName,
    tierConfidence,
    tierConfidenceLabel,
    tierCandidates,
    tierEvidence,
    deductions,
    groupedDeductions,
    deductionsWarnings,
    contractText,
    originalText,
    sourcePath,
    textSource,
    extractionError,
    cacheKey,
    clauses,
    anchorMap,
    originalHtml,
    // 交互
    locate,
    canLocate,
    clearActive,
    folded,
    toggleFolded,
    activeItemKey,
    // 派生
    pendingCount,
    hasPending,
    warnings,
    summary,
    // 工单 10：多份归并
    contractSections,
    contractCount,
    contractKinds,
    totalTextLength,
    // 工具（模板里偶尔用）
    money: _money,
    esc: _esc,
  };
}
