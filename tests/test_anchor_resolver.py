"""锚点解析与冲突检测测试（工单 05-offset-anchoring，ADR-0001/0002）。

覆盖：
- 短语在合同原文中的 [start, end) 区间定位（容忍 OCR 换行/空白）
- `anchor_missing` 兜底（找不到不抛）
- 2023·分店全套必扣项 + 考试费 + 实操费 + 违约金锚点全部命中
- OCR 噪声：部分命中部分缺失
- 文本违约金率 vs 档位默认冲突告警
- 旧格式 items（无 anchor_phrase）向后兼容
"""

import pytest

from services.contract_tiers import TIERS_BY_ID
from services.anchor_resolver import (
    detect_text_conflicts,
    find_phrase,
    resolve_anchors_for_items,
    suggest_anchor_phrases,
)


# ── find_phrase 基础 ──────────────────────────────────────────────────

def test_find_phrase_basic():
    text = "甲方应收乙方服务费600元"
    s, e = find_phrase("服务费", text)
    assert s is not None and e is not None
    assert text[s:e] == "服务费"


def test_find_phrase_with_whitespace_in_text():
    """OCR 噪声：文本里短语被换行/空格打散，仍按无空白匹配。"""
    text = "甲方应收乙方\n服 务 费 600 元"
    s, e = find_phrase("服务费", text)
    assert s is not None and e is not None
    assert text[s:e].replace(" ", "").replace("\n", "") == "服务费"


def test_find_phrase_not_found():
    s, e = find_phrase("不存在的短语", "合同文本")
    assert s is None and e is None


def test_find_phrase_empty_inputs_safe():
    assert find_phrase("", "text") == (None, None)
    assert find_phrase("phrase", "") == (None, None)
    assert find_phrase("phrase", None) == (None, None)


def test_find_phrase_at_start_and_end():
    text = "服务费起头 + ... 末尾服务费"
    s1, e1 = find_phrase("服务费", text)
    assert text[s1:e1] == "服务费"
    # find 返回首次出现位置（不止一处时取首）
    assert s1 == 0


# ── suggest_anchor_phrases ─────────────────────────────────────────────

def test_suggest_known_items():
    assert "服务费" in suggest_anchor_phrases("服务费")
    assert "场地费" in suggest_anchor_phrases("场地费")
    assert "学员IC卡" in suggest_anchor_phrases("学员IC卡")


def test_suggest_unknown_falls_back_to_item_name():
    """未知 item 名：fallback 为 item 名本身。"""
    assert "特殊项" in suggest_anchor_phrases("特殊项")


# ── resolve_anchors_for_items ─────────────────────────────────────────

def test_resolve_uses_explicit_phrase_priority():
    items = [{"item": "服务费", "anchor_phrase": "服务费"}]
    enriched = resolve_anchors_for_items(items, "应收服务费600元")
    assert enriched[0]["anchor_missing"] is False
    assert enriched[0]["anchor_phrase"] == "服务费"
    assert enriched[0]["anchor_text"] == "服务费"


def test_resolve_uses_hint_when_no_explicit():
    items = [{"item": "服务费"}]
    enriched = resolve_anchors_for_items(items, "应收服务费600元")
    assert enriched[0]["anchor_missing"] is False


def test_resolve_marks_missing_when_phrase_absent():
    items = [{"item": "学员IC卡"}]
    enriched = resolve_anchors_for_items(items, "服务费600元")  # 文本里没 IC卡
    assert enriched[0]["anchor_missing"] is True
    assert enriched[0]["anchor_start"] is None
    assert enriched[0]["anchor_end"] is None
    assert enriched[0]["anchor_text"] is None


def test_resolve_does_not_mutate_input():
    items = [{"item": "服务费", "anchor_phrase": "服务费", "amount": 600}]
    snapshot = [dict(it) for it in items]
    resolve_anchors_for_items(items, "服务费600元", tier=TIERS_BY_ID["2019_service"])
    assert items == snapshot


def test_resolve_returns_new_list_with_anchor_fields():
    items = [{"item": "服务费", "amount": 600}]
    enriched = resolve_anchors_for_items(items, "应收服务费600元")
    assert enriched is not items  # 新列表
    assert enriched[0]["amount"] == 600  # 原字段保留
    assert "anchor_start" in enriched[0]
    assert "anchor_missing" in enriched[0]


# ── 验收 1：2023·分店全套锚点命中 ────────────────────────────────────

TEXT_2023_BRANCH_STORE = """\
东莞市机动车驾驶员培训合同（分店）
甲方协助乙方办理机动车驾驶员培训相关事宜。
全部培训费用的20%作为违约金。
必扣项：（必扣项）建档费 服务费 学员IC卡 场地费 700 元。
科目二考试费：130 元。
实操培训费：120 元/学时。
"""


def test_resolve_2023_branch_store_full_set():
    """服务费/建档费/IC卡/场地费/科二考试费/实操单价/违约金各项均有合法区间。"""
    text = TEXT_2023_BRANCH_STORE
    tier = TIERS_BY_ID["2023_branch_store"]
    items = [
        {"item": "服务费"},
        {"item": "建档费"},
        {"item": "学员IC卡"},
        {"item": "场地费"},
        {"item": "科目二考试费"},
        {"item": "科目二实操培训费"},
        {"item": "违约金"},
    ]
    enriched = resolve_anchors_for_items(items, text, tier=tier)
    by_name = {it["item"]: it for it in enriched}
    for name in ("服务费", "建档费", "学员IC卡", "场地费", "科目二考试费", "科目二实操培训费", "违约金"):
        it = by_name[name]
        assert it["anchor_missing"] is False, f"{name} 锚点缺失"
        s, e = it["anchor_start"], it["anchor_end"]
        assert 0 <= s < e <= len(text)
        # 回读 = 锚点短语（去空白后）
        assert text[s:e].replace(" ", "").replace("\n", "") == it["anchor_phrase"].replace(" ", "")


# ── 验收 2：OCR 噪声部分缺失 ──────────────────────────────────────────

def test_resolve_ocr_noise_partial_missing():
    """文本只含部分短语 → 命中项 anchor_missing=False、缺失项 True，整体不抛。"""
    text = "服务费600元 建档费300元"  # 缺 IC卡/场地费/考试费/...
    items = [
        {"item": "服务费"},
        {"item": "建档费"},
        {"item": "学员IC卡"},
        {"item": "场地费"},
        {"item": "科目二考试费"},
    ]
    enriched = resolve_anchors_for_items(items, text)
    by_name = {it["item"]: it for it in enriched}
    assert by_name["服务费"]["anchor_missing"] is False
    assert by_name["建档费"]["anchor_missing"] is False
    assert by_name["学员IC卡"]["anchor_missing"] is True
    assert by_name["场地费"]["anchor_missing"] is True
    assert by_name["科目二考试费"]["anchor_missing"] is True


# ── 验收 3：文本违约金率与档位默认冲突告警 ──────────────────────────

def test_detect_penalty_rate_conflict_text_below_default():
    tier = TIERS_BY_ID["2023_branch_store"]  # 20%
    text = "全部培训费用的10%作为违约金"
    warnings = detect_text_conflicts(text, tier)
    assert any("10%" in w and "20%" in w for w in warnings)


def test_detect_no_conflict_when_match():
    tier = TIERS_BY_ID["2023_branch_store"]
    text = "全部培训费用的20%作为违约金"
    warnings = detect_text_conflicts(text, tier)
    assert not any("不符" in w for w in warnings)


def test_detect_no_warning_for_zero_penalty_tier():
    """2019 三档 0%：即使文本有 X%，也不产生冲突告警（penalty=0 即无违约金额预期）。"""
    tier = TIERS_BY_ID["2019_training"]
    text = "全部培训费用的10%作为违约金"
    warnings = detect_text_conflicts(text, tier)
    assert warnings == []


def test_detect_empty_inputs_safe():
    assert detect_text_conflicts("", TIERS_BY_ID["2023_branch_store"]) == []
    assert detect_text_conflicts("some text", None) == []
    assert detect_text_conflicts(None, TIERS_BY_ID["2023_branch_store"]) == []


# ── 验收 4：旧格式 items 向后兼容 ─────────────────────────────────────

def test_resolve_handles_legacy_items_without_anchor_phrase():
    """旧格式 items（仅 item/amount/source）走通过，不抛。"""
    items = [{"item": "服务费", "amount": 600, "source": "tier_default"}]
    enriched = resolve_anchors_for_items(items, "应收服务费600元")
    assert enriched[0]["anchor_missing"] is False
    assert enriched[0]["amount"] == 600
    assert enriched[0]["source"] == "tier_default"


def test_resolve_legacy_items_phrase_absent_just_marks_missing():
    items = [{"item": "服务费", "amount": 600, "source": "tier_default"}]
    enriched = resolve_anchors_for_items(items, "其他合同文本")
    assert enriched[0]["anchor_missing"] is True
    # 原字段仍保留
    assert enriched[0]["amount"] == 600
    assert enriched[0]["source"] == "tier_default"
