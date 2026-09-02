"""闸门判定测试（工单 07-confidence-gates）。

表驱动覆盖 services.gating 的四个公开函数 + 一个异常类引用。
纯函数，零外部依赖；测试只构造 dict，不触达 Flask/DB/磁盘。
"""

import pytest

from services.gating import (
    GatingError,
    check_archive,
    check_fee_plan_confirm,
    has_pending_items,
    pending_items_summary,
)


# ── 构造器：模拟 deduction_engine 输出的 items 结构 ────────────────────────

def _item(*, category="必扣", item="服务费", amount=0.0, source="tier_default",
          confidence="high", pending=False, basis="档位默认"):
    """构造一条扣费明细 dict；与 deduction_engine._make_item 输出字段对齐。"""
    return {
        "category": category,
        "item": item,
        "amount": float(amount),
        "basis": basis,
        "source": "pending" if pending else source,
        "confidence": "pending" if pending else confidence,
        "pending": pending,
    }


def _result(*items):
    return {
        "items": list(items),
        "total_deduction": sum(it["amount"] for it in items if not it["pending"]),
        "refund": 0,
        "refund_pending": any(it["pending"] for it in items),
        "warnings": [],
        "tier_id": "2021_2022",
        "stage": "实操中",
    }


# ── has_pending_items ────────────────────────────────────────────────────

class TestHasPendingItems:
    def test_none_is_false(self):
        assert has_pending_items(None) is False

    def test_empty_items_is_false(self):
        assert has_pending_items({"items": []}) is False

    def test_non_dict_items_ignored(self):
        assert has_pending_items({"items": ["str", 42, None]}) is False

    def test_all_tier_default_is_false(self):
        dr = _result(
            _item(category="必扣", item="服务费", amount=600, source="tier_default"),
            _item(category="必扣", item="建档费", amount=300, source="tier_default"),
            _item(category="违约金", item="违约金", amount=1200, source="tier_default"),
        )
        assert has_pending_items(dr) is False

    def test_one_pending_is_true(self):
        dr = _result(
            _item(category="必扣", item="服务费", amount=600),
            _item(category="违约金", item="违约金", amount=0, pending=True,
                  basis="培训费总额缺失（待人工补录）"),
        )
        assert has_pending_items(dr) is True

    def test_all_pending_is_true(self):
        dr = _result(
            _item(category="依实", item="理论培训费", amount=0, pending=True,
                  basis="培训费总额缺失（待人工补录）"),
            _item(category="违约金", item="违约金", amount=0, pending=True,
                  basis="基数缺失（pending）"),
        )
        assert has_pending_items(dr) is True


# ── pending_items_summary ─────────────────────────────────────────────────

class TestPendingItemsSummary:
    def test_returns_correct_structure_with_all_fields(self):
        dr = _result(
            _item(category="违约金", item="违约金", amount=0, pending=True,
                  basis="培训费总额缺失（待人工补录）"),
        )
        rows = pending_items_summary(dr)
        assert len(rows) == 1
        row = rows[0]
        assert set(row.keys()) == {"item", "category", "source", "pending_reason"}
        assert row["item"] == "违约金"
        assert row["category"] == "违约金"
        assert row["source"] == "pending"
        # basis 优先作为 pending_reason
        assert "培训费总额缺失" in row["pending_reason"]

    def test_falls_back_to_source_when_basis_empty(self):
        dr = _result(_item(item="违约金", amount=0, pending=True, basis=""))
        rows = pending_items_summary(dr)
        assert len(rows) == 1
        assert rows[0]["pending_reason"].startswith("来源 ")
        assert "pending" in rows[0]["pending_reason"]

    def test_empty_inputs_return_empty_list(self):
        assert pending_items_summary(None) == []
        assert pending_items_summary({}) == []
        assert pending_items_summary({"items": []}) == []
        assert pending_items_summary({"items": [
            _item(item="服务费", amount=600),  # 无 pending
        ]}) == []

    def test_preserves_original_order(self):
        dr = _result(
            _item(category="依实", item="理论培训费", amount=0, pending=True),
            _item(category="必扣", item="服务费", amount=600),  # 不应出现
            _item(category="违约金", item="违约金", amount=0, pending=True),
        )
        rows = pending_items_summary(dr)
        assert [r["item"] for r in rows] == ["理论培训费", "违约金"]


# ── check_fee_plan_confirm ────────────────────────────────────────────────

class TestCheckFeePlanConfirm:
    def test_passes_when_no_pending(self):
        dr = _result(_item(category="必扣", item="服务费", amount=600))
        ok, err = check_fee_plan_confirm(dr)
        assert ok is True
        assert err == ""

    def test_passes_when_deductions_result_none(self):
        ok, err = check_fee_plan_confirm(None)
        assert ok is True
        assert err == ""

    def test_rejects_single_pending_with_message(self):
        dr = _result(
            _item(category="违约金", item="违约金", amount=0, pending=True,
                  basis="培训费总额缺失"),
        )
        ok, err = check_fee_plan_confirm(dr)
        assert ok is False
        assert "存在 1 项待确认" in err
        assert "违约金" in err
        assert "pending" in err

    def test_rejects_three_pending_with_aggregated_message(self):
        # 引擎规则：pending=True 时 source 被强制置为 "pending"（deduction_engine._make_item:57），
        # 因此「pending 项的 source」一律是 "pending"，与原始 category 无关；这里直接构造 dict
        # 而不走 _item()，以验证闸门对各种 category（必扣 / 依实 / 违约金）的聚合都能正确识别。
        dr = _result(
            {"category": "依实", "item": "理论培训费", "amount": 0.0, "basis": "缺失",
             "source": "pending", "confidence": "pending", "pending": True},
            {"category": "违约金", "item": "违约金", "amount": 0.0, "basis": "基数缺失",
             "source": "pending", "confidence": "pending", "pending": True},
            {"category": "必扣", "item": "服务费", "amount": 0.0, "basis": "待人工",
             "source": "pending", "confidence": "pending", "pending": True},
        )
        ok, err = check_fee_plan_confirm(dr)
        assert ok is False
        assert "存在 3 项待确认" in err
        # 聚合顺序应与原 items 顺序一致
        assert "理论培训费" in err
        assert "违约金" in err
        assert "服务费" in err
        # pending 项 source 一律是 pending（引擎契约）
        assert err.count("(pending)") == 3


# ── check_archive（聚合 archive 三闸门 + 07 pending 闸门）────────────────

class TestCheckArchive:
    def _gates_pass(self):
        return {"handling_notes": "已协调网点退费", "branch_cooperation": "配合"}

    def test_rejects_when_pending_with_three_gates_pass(self):
        dr = _result(_item(item="违约金", amount=0, pending=True, basis="基数缺失"))
        ok, err = check_archive(
            dr,
            fee_plan_status="confirmed",
            **self._gates_pass(),
        )
        assert ok is False
        # pending 错误应出现，但 archive 三闸门错误不应出现
        assert "存在 1 项待确认" in err
        assert "违约金" in err
        assert "处理情况未填写" not in err
        assert "配合度未评定" not in err
        assert "费用明细未确认" not in err

    def test_passes_when_pending_resolved_and_three_gates_pass(self):
        # pending 已补齐 → items 全部 pending=False
        dr = _result(
            _item(category="必扣", item="服务费", amount=600, source="tier_default"),
            _item(category="违约金", item="违约金", amount=1200, source="tier_default"),
        )
        ok, err = check_archive(
            dr,
            fee_plan_status="confirmed",
            **self._gates_pass(),
        )
        assert ok is True
        assert err == ""

    def test_rejects_when_three_gates_fail_without_pending(self):
        # 三闸门缺失，deductions_result 无 pending → 错误不应包含「待确认」
        dr = _result(_item(category="必扣", item="服务费", amount=600))
        ok, err = check_archive(
            dr,
            fee_plan_status="draft",
            handling_notes="   ",
            branch_cooperation="",
        )
        assert ok is False
        assert "处理情况未填写" in err
        assert "配合度未评定" in err
        assert "费用明细未确认" in err
        assert "待确认" not in err
        assert "pending" not in err

    def test_aggregates_all_four_failure_modes(self):
        # 三闸门全失败 + 1 pending → 错误信息应含 4 项
        dr = _result(_item(item="违约金", amount=0, pending=True))
        ok, err = check_archive(
            dr,
            fee_plan_status="draft",
            handling_notes="",
            branch_cooperation="",
        )
        assert ok is False
        assert "处理情况未填写" in err
        assert "配合度未评定" in err
        assert "费用明细未确认" in err
        assert "存在 1 项待确认" in err

    def test_none_deductions_result_passes_pending_gate(self):
        # deductions_result=None 时不阻挡，与 fee-confirm 闸门一致
        ok, err = check_archive(
            None,
            fee_plan_status="confirmed",
            **self._gates_pass(),
        )
        assert ok is True
        assert err == ""

    def test_extra_gate_kwargs_are_ignored(self):
        # 未知 gate 键不应影响判定（防御性，避免上层误传整个 ticket dict 时崩）
        dr = _result(_item(item="服务费", amount=600))
        ok, err = check_archive(
            dr,
            fee_plan_status="confirmed",
            handling_notes="已处理",
            branch_cooperation="配合",
            student_name="张三",  # 未知键
            id_card="110101199003070011",  # 未知键
        )
        assert ok is True
        assert err == ""


# ── 异常类：保留作占位 API，不强求函数内部抛 ────────────────────────────

def test_gating_error_is_exception_subclass():
    # 仅断言继承关系，验证模块顶层正常暴露异常类（主 Agent / 路由层可选用）
    assert issubclass(GatingError, Exception)
    err = GatingError("smoke")
    assert str(err) == "smoke"
