"""锚点解析与文本/档位冲突检测（工单 05-offset-anchoring，ADR-0001/0002）。

设计要点：
- 短语在合同原文中的 [start, end) 区间定位（ADR-0002：锚点随结果落库；前端按区间渲染 mark）。
- 空白容忍：OCR/Vision 引入的换行/空格不应阻断匹配；构建 normalize 索引→原文本索引映射，
  返回原文本的字符位置，让前端用原文本渲染、mark 落在原区间。
- `anchor_missing` 兜底：找不到不抛；前端该条不显示定位按钮（spec 06 工单第 4 验收）。
- 文本/档位冲突告警：扫描 "全部培训费用的X%" 与 tier.penalty_rate 对比，不符时输出 warning；
  金额按档位默认计算（spec 05 工单第 3 验收——OCR 与档位默认冲突时金额仍按档位）。
- 旧格式 items（无 anchor_phrase）向后兼容：直接用 hints 表的候选短语，不抛（spec 05 工单第 4 验收）。

LLM prompt 升级（spec 05 工单第 1 验收）在 06 阶段实现：prompt 要求每条扣费项附带 anchor_phrase、
手写金额附位置描述；本期仅做后端解析与冲突检测。

按 `item_basis` 定位（v4.4 · S1）：扣费项在 `tier["item_basis"]` 里登记了合同条款依据时，
锚点必须落在**该条款窗口内**，绝不回落「全文档首次命中」——旧行为正是把 ¥600 服务费锚到
第四条（一）1 的枚举句、把 ¥100 学员IC卡锚到办理义务句的根因。条款窗口用
`services.contract_template_text.find_clause_window` 切分（与模板正文同一套排版容忍规则）。

basis 解析字段（v4.4 · S3b/S4）：`resolve_anchors_for_items` 在每条明细上补 `basis_clause`
（中文数字条款号）/ `basis_section`（退费表分组或编号段）/ `basis_row`（退费表行名），供前端
「悬停扣费卡 → 高亮退费表对应行」直接消费，不必重复实现解析器。三者由 `parse_item_basis`
产出，`None` 一律归一为空串 `""`（前端按空串判断有无）。

东城自制档补位（v4.4 · S4）：该档第四条用裸序号（`1、2、3、`）编号，无「（一）」括号段，
`_section_label_line` 匹配不上；`find_by_basis` 补一条「窗口内行首为裸序号且行内含该项/行名」
的匹配步（仍严格限定窗口内、绝不回落条款标题行）。
"""

from __future__ import annotations

import re
from typing import Any

from services.contract_template_text import find_clause_window


# ── 已知扣费项 → 候选短语（按优先级匹配） ──────────────────────────────

ANCHOR_PHRASE_HINTS: dict[str, tuple[str, ...]] = {
    "服务费": ("服务费",),
    "建档费": ("建档费",),
    "学员IC卡": ("学员IC卡", "学员 IC 卡", "IC卡", "IC 卡"),
    "场地费": ("场地费",),
    "违约金": ("违约金",),
    "理论培训费": ("理论培训费", "理论培训"),
    "科目一考试费": ("科目一考试费", "科目一"),
    "科目二考试费": ("科目二考试费", "科目二"),
    "科目三考试费": ("科目三考试费", "科目三"),
    "科目一补考费": ("科目一补考", "科目一补考费"),
    "科目二补考费": ("科目二补考", "科目二补考费"),
    "科目三补考费": ("科目三补考", "科目三补考费"),
    "科目二实操培训费": ("科目二实操培训费", "科目二实操费", "科目二实操", "实操培训费"),
    "科目三实操培训费": ("科目三实操培训费", "科目三实操费", "科目三实操", "实操培训费"),
}


# ── 短语定位 ──────────────────────────────────────────────────────────

def _build_norm_index_map(text: str) -> tuple[str, list[int]]:
    """构建「normalize 索引 → 原文本索引」映射；空白字符不进 normalize。"""
    norm_chars: list[str] = []
    index_map: list[int] = []
    for i, ch in enumerate(text):
        if not ch.isspace():
            norm_chars.append(ch)
            index_map.append(i)
    return "".join(norm_chars), index_map


def find_phrase(phrase: str, text: str | None) -> tuple[int | None, int | None]:
    """短语在 text 中的 [start, end)（原文本位置）；空白容忍。找不到返回 (None, None)。"""
    if not phrase or text is None:
        return None, None
    norm_text, index_map = _build_norm_index_map(text)
    norm_phrase = re.sub(r"\s+", "", phrase)
    if not norm_phrase:
        return None, None
    n_start = norm_text.find(norm_phrase)
    if n_start == -1:
        return None, None
    o_start = index_map[n_start]
    o_end = index_map[n_start + len(norm_phrase) - 1] + 1
    return o_start, o_end


def suggest_anchor_phrases(item_name: str) -> tuple[str, ...]:
    """已知扣费项返回候选短语；未知项 fallback 为 item 名本身。"""
    return ANCHOR_PHRASE_HINTS.get(item_name, (item_name,))


# ── item_basis 解析与按条款定位（v4.4 · S1） ────────────────────────────

# item_basis 形如：
#   "第八条 退费表「基础服务（必扣项）·服务费」"        → (条, 组, 行)
#   "第八条 备注（违约金为全部培训费用的 20%）"          → (条, "备注", None)
#   "第四条（三）1 代收代交工本费" / "第四条（一）1（3）…" → (条, section, 描述)
#   "第三条 受理服务费、综合服务费、学员卡…"            → (条, None, 描述)
_CLAUSE_NO_RE = re.compile(r"^\s*第\s*([一二三四五六七八九十百零〇]+)\s*条")
_REFUND_ROW_RE = re.compile(r"「(?P<group>[^「」]*?)·(?P<row>[^「」]*)」")
# section token：短括号段（如（三））后跟可选序号，可连续出现（如（一）1（3））
_SECTION_RE = re.compile(r"^(?P<section>(?:（[^）]{1,3}）\d*)+)\s*(?P<rest>.*)$")


def parse_item_basis(basis: str) -> tuple[str | None, str | None, str | None]:
    """把 `item_basis` 描述解析为 (clause_no, section, row_name)。

    clause_no 用汉字（"八" / "六" / "十一"）；无法解析（空串 / 无「第X条」）返回
    (None, None, None)。
    """
    if not basis:
        return None, None, None
    text = str(basis).strip()
    m = _CLAUSE_NO_RE.match(text)
    if not m:
        return None, None, None
    clause_no = m.group(1)
    rest = text[m.end():].strip()

    if "退费表" in rest:  # 退费表「组·行」
        rm = _REFUND_ROW_RE.search(rest)
        if rm:
            return clause_no, rm.group("group").strip(), rm.group("row").strip()
        return clause_no, "退费表", None

    if rest.startswith("备注"):  # 备注（…）——无独立行名
        return clause_no, "备注", None

    sm = _SECTION_RE.match(rest)  # （section）…描述
    if sm:
        desc = sm.group("rest").strip()
        return clause_no, sm.group("section").strip(), desc or None

    return clause_no, None, rest or None  # 第X条 + 纯描述


def _tier_dict(tier: Any) -> dict | None:
    """接受 tier dict 或 tier_id 字符串；解析不出返回 None。"""
    if isinstance(tier, dict):
        return tier
    if isinstance(tier, str):
        from services.contract_tiers import TIERS_BY_ID

        return TIERS_BY_ID.get(tier.strip())
    return None


def _strip_ws(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _lines_with_offset(seg: str) -> list[tuple[int, str]]:
    """把窗口文本切成 (相对偏移, 行文本) 列表（偏移不含被 split 掉的 '\\n'）。"""
    lines: list[tuple[int, str]] = []
    offset = 0
    for line in seg.split("\n"):
        lines.append((offset, line))
        offset += len(line) + 1
    return lines


def _locate_in_window(target: str, text: str, start: int, end: int) -> tuple[int, int] | None:
    """在 [start, end) 窗口内定位 `target`：整行精确 → 行首 → 窗口内短语。全在窗口内。

    返回区间的内容恒为 target 本身（或其所在整行 = target），**不会**返回窗口首行（条款标题）。
    """
    seg = text[start:end]
    tgt_norm = _strip_ws(target)
    if not tgt_norm:
        return None

    lines = _lines_with_offset(seg)

    # 1) 整行精确等于 target（空白容忍）
    for rel, line in lines:
        if _strip_ws(line) == tgt_norm:
            lead = len(line) - len(line.lstrip())
            trail = len(line.rstrip())
            return start + rel + lead, start + rel + trail

    # 2) 行首即 target
    for rel, line in lines:
        s, e = find_phrase(target, line)
        if s is not None and line[:s].strip() == "":
            return start + rel + s, start + rel + e

    # 3) 窗口内短语匹配
    s, e = find_phrase(target, seg)
    if s is not None:
        return start + s, start + e
    return None


# 段落标签：括号段，容忍全角/半角括号。仅当括号内是**编号**（一/二/…/十/阿拉伯数字）时
# 才作为「段标签」用于定位——「（必扣项）」这类非编号括号段不作标签，避免误命中。
_SECTION_BRACKET_RE = re.compile(r"[（(](?P<inner>[^（()）]*)[)）]")
_SECTION_NUMBER_RE = re.compile(r"[一二三四五六七八九十百零〇\d]+")


def _last_section_label(section: str | None) -> str | None:
    """取 section 里**最后一个括号段**的编号文字；非编号（如「必扣项」）或无括号 → None。

    例："（三）" → "三"；"（一）1（3）" → "3"；"（三）1" → "三"；"基础服务（必扣项）" → None。
    """
    if not section:
        return None
    last: str | None = None
    for m in _SECTION_BRACKET_RE.finditer(section):
        last = m.group("inner").strip()
    if last and _SECTION_NUMBER_RE.fullmatch(last):
        return last
    return None


def _section_label_line(section: str | None, text: str, start: int, end: int) -> tuple[int, int] | None:
    """在窗口内找**行首为该括号编号段**的行，锚点 = 该行整行（如「（三）…」那一行）。"""
    inner = _last_section_label(section)
    if not inner:
        return None
    inner_norm = _strip_ws(inner)
    for rel, line in _lines_with_offset(text[start:end]):
        stripped = line.lstrip()
        lead = len(line) - len(stripped)
        m = _SECTION_BRACKET_RE.match(stripped)
        if m and _strip_ws(m.group("inner")) == inner_norm:
            trail = len(line.rstrip())
            if trail > lead:
                return start + rel + lead, start + rel + trail
    return None


# 裸序号行（东城自制等旧模板用「1、」「2.」编号，无括号）：行首（容忍缩进）为阿拉伯数字 +
# 顿号/点/逗号/冒号 或其后直接跟空白 的行。
_BARE_ORDINAL_LINE_RE = re.compile(r"^[ \t\u3000]*\d+\s*(?:[、.．,，:：]|\s)")

# 括号注释（如「（第六条（二）退费基数）」）：从 key 里剔除，避免其内部编号（「第六条」）
# 与本步无关地误命中其它行。
_PAREN_ANNOTATION_RE = re.compile(r"[（(][^（()）]*[)）]")

# 行与 key 视为「同一对象」所需的最短公共子串长度：短于此（如「费」「用」）易误命中。
_MIN_ORDINAL_KEY_OVERLAP = 3


def _strip_parens(text: str) -> str:
    """去掉括号注释（含嵌套一层），用于从 basis 行名里剥离「（第六条（二）…）」括号段。"""
    return _PAREN_ANNOTATION_RE.sub("", text or "")


def _shares_key_substring(line: str, key: str) -> bool:
    """line 与 key（均去空白；key 先去括号注释）是否存在长度 >= `_MIN_ORDINAL_KEY_OVERLAP`
    的公共子串；key 短于阈值时退化为「包含」。用于容忍 basis 行名与正文的行文差异
    （如 basis 写「培驾费用合计」，正文行只写「培驾费用」）。"""
    a = _strip_ws(line)
    b = _strip_ws(_strip_parens(key))
    if not a or not b:
        return False
    n = _MIN_ORDINAL_KEY_OVERLAP
    if len(b) < n:
        return b in a
    for i in range(len(b) - n + 1):
        if b[i:i + n] in a:
            return True
    return False


def _ordinal_keyed_line(
    item_name: str | None, row_name: str | None, text: str, start: int, end: int
) -> tuple[int, int] | None:
    """在窗口内找**行首为裸序号**且行内含 `item_name` / `row_name` 的行，锚点 = 该行整行。

    东城自制档第四条用「1、2、3、」（裸序号）编号、无「（一）」括号段，`_section_label_line`
    匹配不上；此步以「行首裸序号 + 行内出现该项/行名（含去括号后的公共子串）」定位，
    仍严格限定在条款窗口 [start, end) 内，**绝不**返回窗口首行（条款标题）。
    """
    keys = [k for k in (item_name, row_name) if k]
    if not keys:
        return None
    for rel, line in _lines_with_offset(text[start:end]):
        if not _BARE_ORDINAL_LINE_RE.match(line):
            continue
        if any(_shares_key_substring(line, k) for k in keys):
            lead = len(line) - len(line.lstrip())
            trail = len(line.rstrip())
            if trail > lead:
                return start + rel + lead, start + rel + trail
    return None


def find_by_basis(item_name: str, tier: Any, text: str | None) -> tuple[int | None, int | None]:
    """按 `tier["item_basis"]` 在 `text` 中定位该扣费项，返回原文本 [start, end)。

    定位链（**全部限定在条款窗口 [w_start, w_end) 内**；任何一步都不出窗口、不回落全文档
    `find_phrase` 首次命中）：

    1. `row_name`：整行精确 → 行首 → 窗口内短语。
    2. `item_name`（扣费项名）：整行精确 → 行首 → 窗口内短语。
    3. `section` 的**末级编号括号段**所在行（`（三）`→三、`（一）1（3）`→3、`（三）1`→三；
       `（必扣项）` 这类非编号段不作标签）→ 锚点 = 该行整行。
    4. **裸序号行**（东城自制档）：行首为 `1、`/`2.`/`3 ` 这类裸序号、且行内含 `item_name`
       或 `row_name`（或二者去括号后的公共子串）→ 锚点 = 该行整行。
    5. 全不中 → `(None, None)`（→ `anchor_missing=True`，前端不显示定位按钮——比指向
       条款标题诚实）。

    切不出条款窗口（无 basis / 无「第X条」标题）同样返回 `(None, None)`。
    """
    td = _tier_dict(tier)
    if not td or not item_name or not text:
        return None, None
    basis = (td.get("item_basis") or {}).get(item_name)
    if not basis:
        return None, None
    clause_no, section, row_name = parse_item_basis(basis)
    if not clause_no:
        return None, None
    window = find_clause_window(text, clause_no)
    if window is None:
        return None, None
    w_start, w_end = window

    for target in (row_name, item_name):
        if target:
            located = _locate_in_window(target, text, w_start, w_end)
            if located is not None:
                return located

    labeled = _section_label_line(section, text, w_start, w_end)
    if labeled is not None:
        return labeled

    numbered = _ordinal_keyed_line(item_name, row_name, text, w_start, w_end)
    if numbered is not None:
        return numbered
    return None, None


# ── 明细锚定 ──────────────────────────────────────────────────────────

def resolve_anchors_for_items(
    items: list[dict],
    contract_text: str | None,
    *,
    tier: dict | None = None,
    basis_text: str | None = None,
) -> list[dict]:
    """为明细里每条解析锚点。

    语义（v4.4 · S1）：

    - item 在 `tier["item_basis"]` 里有条目 → 在 `basis_text or contract_text` 里用
      `find_by_basis` 定位（**不**回落 ANCHOR_PHRASE_HINTS）。命中写 anchor_*；不命中写
      `anchor_missing=True`。空串 `basis_text` 回退到 `contract_text`（避免调用方传入
      空模板正文时定位被静默关闭；`find_by_basis` 本身不出窗口，回退最差是 `(None, None)`）。
      设计意图：上传件左栏渲染「档位模板正文 + OCR 填空」→ 调用方传
      `template_text(tier)` 作 basis_text；电子合同左栏是 PDF 真实条款 → 调用方不传
      basis_text，走 contract_text。
    - item 无 basis（或 tier=None）→ 保持原有 hints 逻辑，向后兼容。

    返回新列表（深拷贝），不修改原 items；不抛——找不到的项标 anchor_missing=True。
    """
    basis_source = basis_text or contract_text
    tier_dict = _tier_dict(tier)
    item_basis = (tier_dict or {}).get("item_basis") or {}

    enriched: list[dict] = []
    for it in items:
        new = dict(it)
        explicit = (it.get("anchor_phrase") or "").strip()
        item_name = it.get("item", "")

        if item_name in item_basis:
            # 三个「已解析」basis 字段（v4.4 · S3b/S4）：供前端悬停扣费卡时定位退费表对应行，
            # 前端不重复实现解析器。None 归一为空串（前端按空串判断）。
            clause_no, basis_section, basis_row = parse_item_basis(item_basis[item_name])
            new["basis_clause"] = clause_no or ""
            new["basis_section"] = basis_section or ""
            new["basis_row"] = basis_row or ""
            start, end = find_by_basis(item_name, tier_dict, basis_source)
            if start is not None:
                located = (basis_source or "")[start:end]
                new["anchor_phrase"] = located
                new["anchor_start"] = start
                new["anchor_end"] = end
                new["anchor_text"] = located
                new["anchor_missing"] = False
            else:
                new.setdefault("anchor_phrase", explicit)
                new["anchor_start"] = None
                new["anchor_end"] = None
                new["anchor_text"] = None
                new["anchor_missing"] = True
            enriched.append(new)
            continue

        # ── 无 basis：保持既有 hints 行为 ──
        new["basis_clause"] = ""
        new["basis_section"] = ""
        new["basis_row"] = ""
        phrases: list[str] = []
        if explicit:
            phrases.append(explicit)
        for p in suggest_anchor_phrases(item_name):
            if p and p not in phrases:
                phrases.append(p)

        anchored = False
        for phrase in phrases:
            start, end = find_phrase(phrase, contract_text)
            if start is not None:
                new["anchor_phrase"] = phrase
                new["anchor_start"] = start
                new["anchor_end"] = end
                new["anchor_text"] = (contract_text or "")[start:end]
                new["anchor_missing"] = False
                anchored = True
                break

        if not anchored:
            # 找不到锚点：保留 explicit phrase（便于排错），其余字段置空
            new.setdefault("anchor_phrase", explicit)
            new["anchor_start"] = None
            new["anchor_end"] = None
            new["anchor_text"] = None
            new["anchor_missing"] = True

        enriched.append(new)
    return enriched


# ── 文本与档位默认冲突检测 ──────────────────────────────────────────

_PENALTY_RATE_PATTERN = re.compile(r"全部培训费用的(\d{1,2})%")


def detect_text_conflicts(contract_text: str | None, tier: dict | None) -> list[str]:
    """扫描文本中的标准值与档位默认对比，输出告警列表（V1 仅违约金率）。

    OCR 与档位默认冲突时金额仍按档位默认（spec 05 工单第 3 验收）：本函数只生成
    告警，不修改引擎金额。
    """
    warnings: list[str] = []
    if not contract_text or not tier:
        return warnings
    norm = re.sub(r"\s+", "", contract_text)
    default_rate = int(tier.get("penalty_rate", 0) or 0)
    if default_rate <= 0:
        # 2019 三档 0%：即使文本写了非零也不告警（该档本来就不算违约金）
        return warnings
    for m in _PENALTY_RATE_PATTERN.finditer(norm):
        try:
            text_rate = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if text_rate != default_rate:
            warnings.append(
                f"文本违约金率 {text_rate}% 与档位默认 {default_rate}% 不符（ADR-0001），请人工核对"
            )
    return warnings
