"""S2：实缴没抽到 → 付款计划回落到实缴（upload_pipeline._resolve_paid_and_tail）。

用户实测（东莞 2023·分校档）：合同总额 3580，手写「首付 2000 元，欠款 1580 元」。
`contract_service._extract_payment_plan` 已算出 `{down_payment:2000, balance:1580}` 并挂到
`extraction["payment_plan"]`，但 upload_pipeline 此前只读 `ticket.actual_paid`（=0）
→ 实缴 0、尾款 3580、退费基数误取合同总额。

本测试**不连真实 DB、不跑 OCR**：直接打口径纯函数 `_resolve_paid_and_tail`，逐条断言具体数值。
"""

from __future__ import annotations

from services.upload_pipeline import _resolve_paid_and_tail


def _resolve(actual_paid, payment_plan, total_fee, fees_total_amount=None):
    ticket = {"actual_paid": actual_paid}
    return _resolve_paid_and_tail(ticket, payment_plan, total_fee, fees_total_amount)


# ── 分支 1：实缴已录入（权威） → 尾款由实缴侧算出（= 余额，二者此处巧合一致）──
def test_branch1_actual_paid_authoritative():
    paid, tail, refund_base = _resolve(
        actual_paid=2000,
        payment_plan={"down_payment": 2000, "balance": 1580},
        total_fee=3580,
    )
    assert paid == 2000
    assert tail == 1580
    assert refund_base == 2000


# ── 分支 2：实缴=0 → 回落付款计划首付；尾款用 balance；退费基数用回落实缴 ──
def test_branch2_fallback_to_down_payment_with_balance():
    paid, tail, refund_base = _resolve(
        actual_paid=0,
        payment_plan={"down_payment": 2000, "balance": 1580, "balance_source": "derived"},
        total_fee=3580,
    )
    assert paid == 2000
    assert tail == 1580
    assert refund_base == 2000


# ── 分支 3：无付款计划 → 实缴 0、尾款=总额、退费基数回落合同总金额 ──
def test_branch3_no_payment_plan_falls_back_to_fees_total_amount():
    paid, tail, refund_base = _resolve(
        actual_paid=0,
        payment_plan=None,
        total_fee=3580,
        fees_total_amount=4840,
    )
    assert paid == 0
    assert tail == 3580
    assert refund_base == 4840

    # payment_plan 为 {} 同义（缺键）
    paid2, tail2, refund_base2 = _resolve(
        actual_paid=0, payment_plan={}, total_fee=3580, fees_total_amount=4840,
    )
    assert (paid2, tail2, refund_base2) == (0, 3580, 4840)

    # 合同总金额也缺失 → 退费基数回落 total_fee（既有兜底不变）
    paid3, tail3, refund_base3 = _resolve(
        actual_paid=0, payment_plan=None, total_fee=3580, fees_total_amount=None,
    )
    assert (paid3, tail3, refund_base3) == (0, 3580, 3580)


# ── 分支 4：付款计划只有首付、缺 balance → 尾款走 max(total_fee − 首付) ──
def test_branch4_down_payment_without_balance_uses_max_fallback():
    paid, tail, refund_base = _resolve(
        actual_paid=0,
        payment_plan={"down_payment": 2000},
        total_fee=3580,
    )
    assert paid == 2000
    assert tail == 1580
    assert refund_base == 2000


# ── 分支 5：权威优先级——实缴 > 0 时，实缴侧尾款优先于付款计划 balance ──
# 付款计划 balance 与实缴侧算出的尾款**故意矛盾**（balance=2580 vs 实缴侧 1580）：
# 实缴是权威值，必须取实缴侧算出的 1580，不能被 balance 反压。
def test_authority_actual_paid_overrides_plan_balance():
    paid, tail, refund_base = _resolve(
        actual_paid=2000,
        payment_plan={"down_payment": 1000, "balance": 2580},
        total_fee=3580,
    )
    assert paid == 2000
    assert tail == 1580  # = total_fee − actual_paid，不是 plan.balance(2580)
    assert refund_base == 2000


# ── 分支 6（捕获「忽略 balance」变异）：回落路径下 balance ≠ total−down 时以 balance 为准 ──
# 若把 tail_due 改成永远 max(0, total − paid)，此处会误得 1580，正确应为 1000。
def test_fallback_prefers_plan_balance_over_total_minus_down():
    paid, tail, refund_base = _resolve(
        actual_paid=0,
        payment_plan={"down_payment": 2000, "balance": 1000},
        total_fee=3580,
    )
    assert paid == 2000
    assert tail == 1000  # 优先采信付款计划 balance
    assert refund_base == 2000


# ── 边界：总额缺失（None）时尾款/退费基数不为负、不抛错 ──
def test_none_total_fee_is_safe():
    paid, tail, refund_base = _resolve(
        actual_paid=0, payment_plan=None, total_fee=None, fees_total_amount=None,
    )
    assert (paid, tail, refund_base) == (0.0, 0.0, 0.0)
