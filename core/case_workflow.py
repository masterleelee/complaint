"""Case-level policy for turning saved fee rows into a confirmable plan."""
from __future__ import annotations

import json


def _as_rows(value) -> list:
    if isinstance(value, str):
        try:
            value = json.loads(value) if value else []
        except json.JSONDecodeError as exc:
            raise ValueError("saved deduction_detail must be valid JSON") from exc
    if not isinstance(value, list):
        return []
    return value


def calculate_saved_fee_plan(ticket: dict, manual_overrides: dict | None = None) -> dict:
    """v2 简化：直接读取已保存的扣费行集并汇总金额，不再从合同条款重算。"""
    overrides = manual_overrides or {}
    deductions = _as_rows(overrides.get("deductions", ticket.get("deduction_detail") or []))
    total_fee = float(overrides.get("total_fee", ticket.get("total_fee", 0)) or 0)
    actual_paid = float(overrides.get("actual_paid", ticket.get("actual_paid", 0)) or 0)
    total_deduction = round(
        sum(float(d.get("amount", 0) or 0) for d in deductions if isinstance(d, dict)), 2
    )
    refund = max(0, round(actual_paid - total_deduction, 2))

    blockers = []
    if not deductions:
        blockers.append({"field": "deduction_detail", "message": "缺少扣费明细"})
    if actual_paid <= 0:
        blockers.append({"field": "actual_paid", "message": "缺少学员实缴金额"})

    return {
        "total_fee": total_fee,
        "actual_paid": actual_paid,
        "deductions": deductions,
        "total_deduction": total_deduction,
        "refund": refund,
        "can_confirm": not blockers,
        "blockers": blockers,
        "warnings": [],
    }
