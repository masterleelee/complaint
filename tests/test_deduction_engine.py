"""扣费引擎测试（工单 02-deduction-engine，ADR-0003）。

纯函数，无 IO、无 Flask、无 DB。表驱动覆盖六档 × 关键场景矩阵。
"""

import pytest

from services.contract_tiers import TIERS_BY_ID
from services.deduction_engine import _is_accepted, calculate_deductions


def _find(items, **filters):
    for it in items:
        if all(it.get(k) == v for k, v in filters.items()):
            return it
    return None


def _amount(items, item_name):
    it = _find(items, item=item_name)
    return it["amount"] if it else None


def _categories(items):
    return [it["category"] for it in items]


@pytest.mark.parametrize(
    "tier_id, stage, exam_counts, training_hours, license_type, total_fee, "
    "must_have, must_not_have, expected_total_deduction",
    [
        ("2019_service", "已受理", {}, {}, "C1", 1500,
         [], ["服务费", "建档费", "学员IC卡", "场地费", "违约金"], 0),
        ("2019_pay_agent", "已受理", {"subject1": 1}, {}, "C1", 130,
         [("科目一考试费", 70, "tier_default")],
         ["服务费", "建档费", "学员IC卡", "场地费", "违约金"], 70),
        ("2019_training", "已受理", {}, {}, "C1", 3000,
         [("理论培训费", 3000, "ocr")],
         ["科目二考试费", "科目二实操培训费", "违约金"], 3000),
        ("2019_training", "实操中", {}, {"subject2": 5}, "C1", 3000,
         [("理论培训费", 3000, "ocr"), ("科目二实操培训费", 600, "tier_default")],
         ["科目二考试费", "违约金"], 3600),
        ("2021_2022", "已受理", {"subject1": 1}, {}, "C1", 5000,
         [
             ("科目一考试费", 70, "tier_default"),
             ("理论培训费", 5000, "ocr"),
             ("违约金", 500, "tier_default"),
         ],
         ["科目二考试费", "科目二实操培训费", "服务费", "建档费"], 5570),
        ("2023_branch_school", "实操中", {"subject1": 1}, {"subject2": 10}, "C1", 6000,
         [
             ("服务费", 600, "tier_default"),
             ("建档费", 300, "tier_default"),
             ("学员IC卡", 100, "tier_default"),
             ("科目一考试费", 70, "tier_default"),
             ("科目二实操培训费", 1200, "tier_default"),
             ("理论培训费", 6000, "ocr"),
             ("违约金", 1200, "tier_default"),
         ],
         ["场地费"], 9470),
        ("2023_branch_store", "实操中", {"subject1": 1}, {"subject2": 10}, "C1", 6000,
         [
             ("服务费", 600, "tier_default"),
             ("建档费", 300, "tier_default"),
             ("学员IC卡", 100, "tier_default"),
             ("场地费", 700, "tier_default"),
             ("科目一考试费", 70, "tier_default"),
             ("科目二实操培训费", 1200, "tier_default"),
             ("理论培训费", 6000, "ocr"),
             ("违约金", 1200, "tier_default"),
         ],
         [], 10170),
    ],
    ids=[
        "2019服务-已受理",
        "2019代缴-已考科一",
        "2019培训-已受理",
        "2019培训-实操中-5学时",
        "2021-2022-已考科一",
        "2023分校-实操中-10学时",
        "2023分店-实操中-10学时",
    ],
)
def test_deduction_matrix(
    tier_id, stage, exam_counts, training_hours, license_type, total_fee,
    must_have, must_not_have, expected_total_deduction,
):
    tier = TIERS_BY_ID[tier_id]
    result = calculate_deductions(
        tier=tier,
        stage=stage,
        progress={"exam_counts": exam_counts, "training_hours": training_hours, "license_type": license_type},
        total_fee=total_fee,
    )
    items = result["items"]

    for item_name, expected_amount, expected_source in must_have:
        it = _find(items, item=item_name)
        assert it is not None, f"{tier_id}: 缺少 {item_name}"
        assert it["amount"] == pytest.approx(expected_amount), f"{tier_id}/{item_name}: {it['amount']} ≠ {expected_amount}"
        assert it["source"] == expected_source, f"{tier_id}/{item_name}: {it['source']} ≠ {expected_source}"
        assert it["pending"] is False

    for item_name in must_not_have:
        assert _find(items, item=item_name) is None, f"{tier_id}: 不应出现 {item_name}"

    non_pending = [it for it in items if not it["pending"]]
    assert result["total_deduction"] == pytest.approx(expected_total_deduction)
    assert result["total_deduction"] == pytest.approx(sum(it["amount"] for it in non_pending))


def test_makeup_fee_only_for_extra_attempts():
    tier = TIERS_BY_ID["2023_branch_school"]
    result = calculate_deductions(
        tier=tier,
        stage="实操中",
        progress={"exam_counts": {"subject2": 3}, "training_hours": {}, "license_type": "C1"},
        total_fee=6000,
    )
    exam_item = _find(result["items"], item="科目二考试费")
    assert exam_item["amount"] == pytest.approx(130)
    makeup = _find(result["items"], item="科目二补考费")
    assert makeup is not None
    assert makeup["amount"] == pytest.approx(130)


def test_exam_fee_skipped_when_no_exams():
    tier = TIERS_BY_ID["2023_branch_school"]
    result = calculate_deductions(
        tier=tier,
        stage="已受理",
        progress={"exam_counts": {}, "training_hours": {}, "license_type": "C1"},
        total_fee=6000,
    )
    assert _find(result["items"], item="科目一考试费") is None
    assert _find(result["items"], item="科目一补考费") is None


def test_practical_rate_uses_license_type():
    tier = TIERS_BY_ID["2019_training"]
    result = calculate_deductions(
        tier=tier,
        stage="实操中",
        progress={"exam_counts": {}, "training_hours": {"subject3": 10}, "license_type": "C2"},
        total_fee=5000,
    )
    assert _amount(result["items"], "科目三实操培训费") == pytest.approx(1500)


def test_output_order_mandatory_then_actual_then_penalty():
    tier = TIERS_BY_ID["2023_branch_store"]
    result = calculate_deductions(
        tier=tier,
        stage="实操中",
        progress={"exam_counts": {"subject1": 1, "subject2": 1}, "training_hours": {"subject2": 10}, "license_type": "C1"},
        total_fee=6000,
    )
    cats = _categories(result["items"])
    assert cats[0] == "必扣"
    assert cats[-1] == "违约金"
    assert _find(result["items"], category="违约金") == result["items"][-1]


def test_total_fee_missing_makes_penalty_and_refund_pending():
    tier = TIERS_BY_ID["2021_2022"]
    result = calculate_deductions(
        tier=tier,
        stage="实操中",
        progress={"exam_counts": {"subject1": 1}, "training_hours": {}, "license_type": "C1"},
        total_fee=None,
    )
    penalty = _find(result["items"], item="违约金")
    assert penalty["pending"] is True
    assert penalty["amount"] == 0
    assert penalty["source"] == "pending"
    assert penalty["confidence"] == "pending"
    assert result["refund_pending"] is True
    assert result["refund"] == 0


def test_zero_penalty_tier_never_has_penalty_even_without_total_fee():
    tier = TIERS_BY_ID["2019_training"]
    result = calculate_deductions(
        tier=tier,
        stage="已受理",
        progress={"exam_counts": {}, "training_hours": {}, "license_type": "C1"},
        total_fee=None,
    )
    assert _find(result["items"], item="违约金") is None
    assert _find(result["items"], item="理论培训费")["pending"] is True
    assert result["refund_pending"] is True


def test_manual_theory_fee_overrides_total_fee():
    tier = TIERS_BY_ID["2019_training"]
    result = calculate_deductions(
        tier=tier,
        stage="已受理",
        progress={"exam_counts": {}, "training_hours": {}, "license_type": "C1"},
        total_fee=3000,
        manual_amounts={"theory_fee": 3200},
    )
    theory = _find(result["items"], item="理论培训费")
    assert theory["amount"] == pytest.approx(3200)
    assert theory["source"] == "manual"
    assert theory["confidence"] == "high"


def test_practical_fee_capped_with_warning():
    tier = TIERS_BY_ID["2023_branch_school"]
    result = calculate_deductions(
        tier=tier,
        stage="实操中",
        progress={"exam_counts": {}, "training_hours": {"subject2": 1000}, "license_type": "C1"},
        total_fee=6000,
    )
    assert any("封顶" in w or "超出" in w for w in result["warnings"])
    assert _amount(result["items"], "科目二实操培训费") == pytest.approx(120000)


def test_function_is_pure():
    tier = TIERS_BY_ID["2023_branch_store"]
    kwargs = dict(
        stage="实操中",
        progress={"exam_counts": {"subject1": 1}, "training_hours": {"subject2": 5}, "license_type": "C1"},
        total_fee=6000,
    )
    r1 = calculate_deductions(tier=tier, **kwargs)
    r2 = calculate_deductions(tier=tier, **kwargs)
    assert r1 == r2


# ── 东城自制第六条退费分支（工单 11-dongcheng-tier，2026-09-02） ──────

_DONGCHENG_PROGRESS = {
    "exam_counts": {"subject1": 1, "subject2": 1},
    "training_hours": {"subject2": 10},
    "license_type": "C1",
}


def test_dongcheng_normal_before_acceptance_charges_half_service_fee():
    """普通培训·受理前：服务费扣 50%。"""
    tier = TIERS_BY_ID["2019_dongcheng"]
    result = calculate_deductions(
        tier=tier, stage="受理中", progress=_DONGCHENG_PROGRESS,
        total_fee=3800, service_fee=1000, training_mode="",
    )
    svc = _find(result["items"], item="服务费（扣50%）")
    assert svc is not None
    assert svc["amount"] == pytest.approx(500)
    assert _find(result["items"], item="服务费") is None  # 受理前不出现全额服务费


def test_dongcheng_normal_after_acceptance_charges_full_service_fee():
    """普通培训·受理后：服务费全额扣（不退）。"""
    tier = TIERS_BY_ID["2019_dongcheng"]
    result = calculate_deductions(
        tier=tier, stage="已受理", progress=_DONGCHENG_PROGRESS,
        total_fee=3800, service_fee=1000, training_mode="",
    )
    svc = _find(result["items"], item="服务费")
    assert svc is not None
    assert svc["amount"] == pytest.approx(1000)
    assert _find(result["items"], item="服务费（扣50%）") is None


def test_dongcheng_penalty_20_percent():
    """东城自制违约金 = 总培训费用 × 20%。"""
    tier = TIERS_BY_ID["2019_dongcheng"]
    result = calculate_deductions(
        tier=tier, stage="已受理", progress=_DONGCHENG_PROGRESS,
        total_fee=3800, service_fee=1000, training_mode="",
    )
    penalty = _find(result["items"], item="违约金")
    assert penalty is not None
    assert penalty["amount"] == pytest.approx(760)


def test_dongcheng_practical_uses_80_not_c1_c2_standard():
    """实操单价采信合同正文 80 元/学时（C1/C2 统一，不沿用 120/150 标准）。"""
    tier = TIERS_BY_ID["2019_dongcheng"]
    result = calculate_deductions(
        tier=tier, stage="已受理", progress=_DONGCHENG_PROGRESS,
        total_fee=3800, service_fee=1000, training_mode="",
    )
    practical = _find(result["items"], item="科目二实操培训费")
    assert practical is not None
    assert practical["amount"] == pytest.approx(800)  # 10 学时 × 80，而非 1200


def test_dongcheng_agency_fee_refund_warning():
    """代交费用 490 元扣除已完成科目考试费后剩余应退还（第六条（五）告警）。"""
    tier = TIERS_BY_ID["2019_dongcheng"]
    result = calculate_deductions(
        tier=tier, stage="已受理", progress=_DONGCHENG_PROGRESS,
        total_fee=3800, service_fee=1000, training_mode="",
    )
    # 已完成科一(70)+科二(130)=200，剩余 490-200=290 应退还
    assert any("剩余 290 元应退还" in w for w in result["warnings"])


def test_dongcheng_pay_first_then_train_charges_800_and_warns():
    """先培后付：补交 800 元 + 人工核对告警。"""
    tier = TIERS_BY_ID["2019_dongcheng"]
    result = calculate_deductions(
        tier=tier, stage="已受理", progress=_DONGCHENG_PROGRESS,
        total_fee=3800, service_fee=1000, training_mode="先培后付",
    )
    payback = _find(result["items"], item="先培后付退学补交款")
    assert payback is not None
    assert payback["amount"] == pytest.approx(800)
    assert any("先培后付" in w and "人工" in w for w in result["warnings"])


def test_dongcheng_penalty_pending_when_total_fee_missing():
    """东城自制 total_fee 缺失 → 违约金 pending。"""
    tier = TIERS_BY_ID["2019_dongcheng"]
    result = calculate_deductions(
        tier=tier, stage="已受理", progress=_DONGCHENG_PROGRESS,
        total_fee=None, service_fee=1000, training_mode="",
    )
    penalty = _find(result["items"], item="违约金")
    assert penalty["pending"] is True
    assert result["refund_pending"] is True


# ── P0-1 修复回归：生产 exam_stage 短词（科目一/二/三/四）应判已受理 ──

@pytest.mark.parametrize(
    "stage, expected_accepted",
    [
        ("科目一", True),
        ("科目二", True),
        ("科目三", True),
        ("科目四", True),
        ("待考科目一", True),
        ("待考科目二", True),
        ("待补考科目一", True),
        ("已毕业", True),
        ("已受理", True),
        ("科一约考", True),
        ("科一通过", True),
        ("", False),
        ("无", False),
        ("未报名", False),
        ("待受理", False),
    ],
)
def test_dongcheng_is_accepted_stage_short_words(stage, expected_accepted):
    """生产 exam_stage 短词判定：科目N/待考/已毕业=已受理；空/无/未报名/待受理=未受理。"""
    assert _is_accepted(stage) is expected_accepted


def test_dongcheng_stage_subject_one_charges_full_service_fee():
    """王俊林工单 exam_stage='科目一' 已受理 → 服务费全额扣（修复前误扣 50%）。"""
    tier = TIERS_BY_ID["2019_dongcheng"]
    result = calculate_deductions(
        tier=tier, stage="科目一", progress=_DONGCHENG_PROGRESS,
        total_fee=3800, service_fee=1000, training_mode="",
    )
    svc = _find(result["items"], item="服务费")
    assert svc is not None
    assert svc["amount"] == pytest.approx(1000)  # 全额不退，非 500
    assert _find(result["items"], item="服务费（扣50%）") is None


def test_dongcheng_stage_not_enrolled_charges_half_service_fee():
    """未受理（空 stage）→ 服务费扣 50%。"""
    tier = TIERS_BY_ID["2019_dongcheng"]
    result = calculate_deductions(
        tier=tier, stage="", progress=_DONGCHENG_PROGRESS,
        total_fee=3800, service_fee=1000, training_mode="",
    )
    svc = _find(result["items"], item="服务费（扣50%）")
    assert svc is not None
    assert svc["amount"] == pytest.approx(500)
