"""补考费规则生成回归测试。

背景：`contract_set_from_ai_data` 曾在判断补考费时引用未定义变量 `makeup_count`，
导致 AI 合同分析路径一旦识别出补考费金额即抛 NameError。本地规则路径因硬编码
`includes_makeup_fee=False` 被 `and` 短路而从未暴露，测试全绿照样漏掉。

本文件锁定修复后的行为：本函数只产出「单次金额」规则模板，补考次数由下游结合
三系统考试次数计算，故此处不得判断次数。
"""

import pytest

from services.contract_service import contract_set_from_ai_data


def _rules(data, **kwargs):
    """取生成规则列表，统一走 contracts[0].rules。"""
    result = contract_set_from_ai_data(data, "fake.docx", **kwargs)
    return result["contracts"][0]["rules"]


def _items(rules):
    return {rule["item"]: rule["amount"] for rule in rules}


# ── 核心回归：AI 路径不得因补考费崩溃 ──────────────────────────────


def test_ai_path_with_makeup_fee_does_not_raise():
    """AI 路径识别出补考费时须正常生成规则（修复前：NameError: makeup_count）。

    includes_makeup_fee 缺省为 True，模拟 LLM 未明确说明补考费是否包含的常见情形。
    """
    rules = _rules({"total_fee": 5000, "makeup_fee_table": {"subject2": 200}})
    assert _items(rules) == {"科目二补考费": 200.0}


def test_ai_path_generates_makeup_rules_for_all_subjects():
    """三个科目各自独立生成补考费规则。"""
    rules = _rules({
        "total_fee": 5000,
        "makeup_fee_table": {"subject1": 35, "subject2": 65, "subject3": 140},
    })
    assert _items(rules) == {
        "科目一补考费": 35.0,
        "科目二补考费": 65.0,
        "科目三补考费": 140.0,
    }


# ── 与考试费规则对称：都只判断金额，不判断次数 ─────────────────────


def test_makeup_rule_is_symmetric_with_exam_rule():
    """考试费与补考费的生成条件应对称——都只看金额，不引入考试次数。"""
    rules = _rules({
        "total_fee": 5000,
        "exam_fee_table": {"subject2": 130},
        "makeup_fee_table": {"subject2": 65},
    })
    items = _items(rules)
    assert items == {"科目二考试费": 130.0, "科目二补考费": 65.0}


def test_makeup_rule_amount_is_unit_price_not_multiplied():
    """规则里存的是单次金额，重复次数由下游按考试次数折算，此处不做乘法。"""
    rules = _rules({
        "total_fee": 5000,
        "exam_fee_table": {"subject2": 130},
        "makeup_fee_table": {"subject2": 65},
    })
    makeup = next(r for r in rules if r["type"] == "makeup_fee")
    assert makeup["amount"] == 65.0
    assert "count" not in makeup, "次数不应在此阶段计算"


# ── 边界：不应生成规则，也不应崩溃 ────────────────────────────────


@pytest.mark.parametrize("makeup_table", [
    {},                        # 无补考费表
    {"subject2": 0},           # 金额为 0
    {"subject2": None},        # 金额为 None
])
def test_no_makeup_rule_when_fee_missing_or_zero(makeup_table):
    rules = _rules({"total_fee": 5000, "makeup_fee_table": makeup_table})
    assert [r for r in rules if r["type"] == "makeup_fee"] == []


def test_local_rules_path_still_excludes_makeup_fee():
    """本地规则路径硬编码 includes_makeup_fee=False，保持修复前行为不变。"""
    rules = _rules(
        {"total_fee": 5000, "includes_makeup_fee": False,
         "makeup_fee_table": {"subject2": 200}},
        source="local_rules",
    )
    assert [r for r in rules if r["type"] == "makeup_fee"] == []


def test_explicit_disabled_flag_wins_over_nonzero_fee():
    """合同明确说明不含补考费时，即使识别到金额也不生成规则。"""
    rules = _rules({
        "total_fee": 5000,
        "includes_makeup_fee": False,
        "makeup_fee_table": {"subject2": 200},
    })
    assert [r for r in rules if r["type"] == "makeup_fee"] == []
