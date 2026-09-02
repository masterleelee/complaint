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
"""

from __future__ import annotations

import re
from typing import Any


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


# ── 明细锚定 ──────────────────────────────────────────────────────────

def resolve_anchors_for_items(
    items: list[dict],
    contract_text: str | None,
    *,
    tier: dict | None = None,
) -> list[dict]:
    """为明细里每条解析锚点。优先 item.anchor_phrase，否则按 ANCHOR_PHRASE_HINTS 候选。

    返回新列表（深拷贝），不修改原 items；不抛——找不到的项标 anchor_missing=True。
    """
    enriched: list[dict] = []
    for it in items:
        new = dict(it)
        explicit = (it.get("anchor_phrase") or "").strip()
        # 候选短语：先 explicit，再 hints（去重）
        phrases: list[str] = []
        if explicit:
            phrases.append(explicit)
        for p in suggest_anchor_phrases(it.get("item", "")):
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
