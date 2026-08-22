"""Case-level policy for turning saved contract facts into fee plans."""
from __future__ import annotations

import json
from datetime import date

from core.refund_engine import calculate_fee_plan


def _as_object(value, field: str) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{field} is required")
    return value


def calculate_saved_fee_plan(ticket: dict, manual_overrides: dict | None = None) -> dict:
    """Recalculate only from saved contract facts and saved query facts."""
    contract_set = _as_object(ticket.get("contract_set"), "saved contract_set")
    query_result = _as_object(ticket.get("query_result") or {}, "saved query_result")
    progress_facts = {
        "registration_date": ticket.get("registration_date") or query_result.get("registration_date", ""),
        "student_status": ticket.get("student_status") or query_result.get("student_status", ""),
        "exam_stage": ticket.get("exam_stage") or query_result.get("exam_stage", ""),
        "exam_counts": query_result.get("exam_counts", {}),
        "training_hours": ticket.get("training_hours") or query_result.get("training_hours", {}),
        "as_of_date": date.today().isoformat(),
    }
    return calculate_fee_plan(contract_set, progress_facts, manual_overrides or {})
