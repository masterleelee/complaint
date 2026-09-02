"""扣费计算引擎（工单 02-deduction-engine，ADR-0003）。

纯函数，无 IO、无 Flask、无 DB。按档位 + 阶段 + 进度 + 手写金额输出有序扣费明细。

设计要点：
- 沿用现网 float 金额口径（生产 `services.contract_service` 走的就是 float），不引入 Decimal。
- 不继承 `core.refund_engine` 契约：不要求 `contract_id`、不分制（ADR-0003）。
- 每条明细带来源（tier_default / ocr / manual / pending）与置信度（high / medium / low / pending）。
- pending 项不计入合计，应退合计若任一上游缺失则置 refund_pending=True 不给出数字。
- 输出顺序稳定：必扣 → 考试费/补考费 → 实操费/理论培训费 → 违约金（恒排最后）。
- 多份并行时违约金基数（拍板口径，见 ADR-0003 + CONTEXT.md）：Σ 各份手写总额之和、
  代缴份不计入（服务份计入）；当前六档矩阵无触发场景，本函数按单份合同计算，调用方做多份归并。
"""

from __future__ import annotations

from typing import Any

# ── 来源 ↔ 置信度映射 ──────────────────────────────────────────────

SOURCE_CONFIDENCE: dict[str, str] = {
    "tier_default": "high",   # 档位常量（考试费标准、违约金率等）
    "manual": "high",         # 人工录入
    "ocr": "medium",          # OCR/LLM 抽取
    "pending": "pending",     # 上游缺失
}

# 学科标签（与档位考试费/补考费键名对齐）
_SUBJECT_LABELS: dict[str, str] = {
    "subject1": "科目一",
    "subject2": "科目二",
    "subject3": "科目三",
}

# 培训档判定：只有这两类合同有「理论培训费视为已发生」的口径
_TRAINING_KINDS: frozenset[str] = frozenset({"培训", "单一培训"})


def _is_training_kind(kind: str) -> bool:
    return kind in _TRAINING_KINDS


def _make_item(
    category: str,
    name: str,
    amount: float,
    basis: str,
    source: str = "tier_default",
    pending: bool = False,
) -> dict[str, Any]:
    """构造一条扣费明细。pending=True 时 amount 仅作占位（0），不计入合计。"""
    return {
        "category": category,
        "item": name,
        "amount": float(amount),
        "basis": basis,
        "source": "pending" if pending else source,
        "confidence": SOURCE_CONFIDENCE["pending"] if pending else SOURCE_CONFIDENCE.get(source, "low"),
        "pending": pending,
    }


def _mandatory_items(tier: dict) -> list[dict]:
    items: list[dict] = []
    for entry in tier.get("mandatory_items") or []:
        items.append(_make_item(
            category="必扣",
            name=entry["item"],
            amount=entry["amount"],
            basis=f"{tier['display_name']} 合同必扣项",
            source="tier_default",
        ))
    return items


def _exam_items(tier: dict, exam_counts: dict[str, int]) -> list[dict]:
    items: list[dict] = []
    for key, label in _SUBJECT_LABELS.items():
        attempts = int(exam_counts.get(key, 0) or 0)
        if attempts <= 0:
            continue
        # 首考 + (attempts - 1) 次补考
        exam_fee = float(tier.get("exam_fees", {}).get(key, 0))
        makeup_fee = float(tier.get("makeup_fees", {}).get(key, 0))
        if exam_fee > 0:
            items.append(_make_item(
                category="依实",
                name=f"{label}考试费",
                amount=exam_fee,
                basis=f"已考 1 次 × 档位标准 {exam_fee:.0f} 元",
            ))
        extra = attempts - 1
        if extra > 0 and makeup_fee > 0:
            items.append(_make_item(
                category="依实",
                name=f"{label}补考费",
                amount=extra * makeup_fee,
                basis=f"补考 {extra} 次 × 档位标准 {makeup_fee:.0f} 元",
            ))
    return items


def _practical_items(
    tier: dict,
    training_hours: dict[str, float],
    license_type: str,
    total_fee: float | None,
) -> tuple[list[dict], list[str]]:
    """返回 (明细, 告警)。仅培训档 + 学时 > 0 才出。封顶校验只告警不截断。"""
    items: list[dict] = []
    warnings: list[str] = []
    if not _is_training_kind(tier.get("kind", "")):
        return items, warnings

    rate_table = tier.get("practical_rates") or {}
    rate = float(rate_table.get(license_type, 0))
    if rate <= 0:
        return items, warnings

    for key, label in _SUBJECT_LABELS.items():
        if key == "subject1":
            continue  # 科目一是理论培训，不走实操单价
        hours = float(training_hours.get(key, 0) or 0)
        if hours <= 0:
            continue
        amount = hours * rate
        basis = f"审核学时 {hours:g} × 档位单价 {rate:.0f} 元/学时（{license_type}）"
        items.append(_make_item(
            category="依实",
            name=f"{label}实操培训费",
            amount=amount,
            basis=basis,
        ))
        # 封顶校验：仅告警不截断
        if total_fee is not None and amount > float(total_fee):
            warnings.append(
                f"{label}实操培训费 {amount:.0f} 元超出培训费总额 {float(total_fee):.0f} 元，请人工核对"
            )
    return items, warnings


def _theory_fee_items(
    tier: dict,
    total_fee: float | None,
    manual_amounts: dict | None,
) -> list[dict]:
    """理论培训费（仅培训档）。manual_amounts.theory_fee 优先（高置信 manual），
    否则用 total_fee（中置信 ocr），再否则 pending。"""
    if not _is_training_kind(tier.get("kind", "")):
        return []

    manual = (manual_amounts or {}).get("theory_fee")
    if manual is not None and manual != "":
        return [_make_item(
            category="依实",
            name="理论培训费",
            amount=float(manual),
            basis="人工录入（高置信覆盖）",
            source="manual",
        )]

    if total_fee is None or total_fee == "":
        return [_make_item(
            category="依实",
            name="理论培训费",
            amount=0,
            basis="培训费总额缺失（待人工补录）",
            pending=True,
        )]

    return [_make_item(
        category="依实",
        name="理论培训费",
        amount=float(total_fee),
        basis="培训费总额（OCR/LLM 抽取）",
        source="ocr",
    )]


def _penalty_item(tier: dict, total_fee: float | None) -> list[dict]:
    """违约金（恒排最后）。tier.penalty_rate 是整数百分比（10 = 10%），计算时除以 100；
    penalty_rate=0 直接跳过；total_fee 缺失时 pending。"""
    rate_pct = float(tier.get("penalty_rate", 0) or 0)
    if rate_pct <= 0:
        return []
    rate = rate_pct / 100.0
    if total_fee is None or total_fee == "":
        return [_make_item(
            category="违约金",
            name="违约金",
            amount=0,
            basis=f"{tier['display_name']} 档默认违约金率 {rate_pct:.0f}%，基数缺失（pending）",
            pending=True,
        )]
    amount = float(total_fee) * rate
    return [_make_item(
        category="违约金",
        name="违约金",
        amount=amount,
        basis=f"全部培训费用 × 档位默认 {rate_pct:.0f}%（{tier['display_name']}）",
    )]


def calculate_deductions(
    tier: dict,
    stage: str,
    progress: dict,
    total_fee: float | None = None,
    manual_amounts: dict | None = None,
) -> dict:
    """按档位 + 阶段 + 进度 + 手写金额算一份合同的有序扣费明细。

    Args:
        tier: 一份合同的档位 dict（来自 `services.contract_tiers.TIERS_BY_ID[tier_id]`）。
        stage: "已受理" | "实操中"（与 CONTEXT.md「阶段」一致）。
        progress: {"exam_counts": {subjectN: attempts}, "training_hours": {subjectN: hours}, "license_type": "C1"|"C2"}。
        total_fee: 该份合同的「培训费总额」手写值；缺失 → 违约金 pending、理论费 pending（培训档）。
        manual_amounts: 人工覆盖字典，可含 {"theory_fee": float}；高置信 manual 优先于 total_fee。

    Returns:
        {
          "items": [扣费明细，按 必扣 → 考试费 → 实操费 → 理论费 → 违约金 排序],
          "total_deduction": float,    # 仅非 pending 项求和
          "refund": float,             # 0 若 refund_pending；否则 = total_fee - total_deduction（占位）
          "refund_pending": bool,      # 任一上游缺失时为 True，不给出 refund 数字
          "warnings": [str],           # 封顶校验等告警
          "tier_id": str,
          "stage": str,
        }
    """
    exam_counts = (progress or {}).get("exam_counts") or {}
    training_hours = (progress or {}).get("training_hours") or {}
    license_type = (progress or {}).get("license_type") or "C1"

    items: list[dict] = []
    warnings: list[str] = []

    items.extend(_mandatory_items(tier))
    items.extend(_exam_items(tier, exam_counts))
    practical, w_practical = _practical_items(tier, training_hours, license_type, total_fee)
    items.extend(practical)
    warnings.extend(w_practical)
    items.extend(_theory_fee_items(tier, total_fee, manual_amounts))
    items.extend(_penalty_item(tier, total_fee))

    total_deduction = sum(it["amount"] for it in items if not it["pending"])

    refund_pending = any(it["pending"] for it in items)
    refund = 0.0
    if not refund_pending and total_fee is not None and total_fee != "":
        # 单份合同内：理论费 + 必扣 + 考试费 + 实操费 + 违约金 = 培训费总额 - 应退
        refund = max(0.0, float(total_fee) - total_deduction)

    return {
        "items": items,
        "total_deduction": total_deduction,
        "refund": refund,
        "refund_pending": refund_pending,
        "warnings": warnings,
        "tier_id": tier.get("id", ""),
        "stage": stage,
    }
