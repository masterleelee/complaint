"""打包价合同的扣费口径与结算口径测试（ISS-VC-01 · P0-4 / P0-5）。

样本：郑智林 2023·分校纸质合同（押金 2026-09-14 受理）。
合同事实：
- 第四条（一）1（1）普通培训打包价，手写总额 **3580 元**（含建档费/学员IC卡/各阶段培训费）；
- 手写备注「首付2000元，欠1580，科二合格后1个月内全交」→ 已支付 2000、尾款 1580；
- 第八条退费表：基础服务必扣（服务费600+建档费300+学员IC卡100）+ 已代收考试费
  + 已产生实操培训费 + 违约金 20%（基数=全部培训费用）。

用户拍板口径（2026-09-14）：
1. 退费基数 = 已支付金额（2000），不是合同额；
2. 违约金基数 = 合同金额（3580）× 20% = 716；
3. 应退先冲抵尾款（1580），余额为实退 → 284 − 1580 < 0，实退 0。
"""

from __future__ import annotations

import pytest

from services.contract_service import extract_contract_fees
from services.contract_tiers import TIERS_BY_ID
from services.deduction_engine import calculate_deductions
from services.upload_pipeline import analyze_upload_contract_text, _attach_payment_context

# 一条能被 identify_tier 判为 2023·分校（high）的最小正文
TEXT_2023_BRANCH = """东莞市机动车驾驶员培训合同
依照《中华人民共和国民法典》、《中华人民共和国道路交通安全法》、《中华人民共和国道路运输条例》等相关法律法规的规定，订立本合同。
（一）乙方学习的准驾车型:□C1 ☑C2 □C5 □
（三）乙方提供全部所需的资料，甲方为乙方办理学员IC卡、建立培训档案。
第四条  费用及支付
1、乙方选择以下第 1 种方式支付培训费用（包含建档费/学员IC卡/各阶段培训费及相关手续费）：
（1）实行普通培训，培训费用总额合计人民币【手写】3580 元，□一次性支付，☑分期付款；
其他方式：【手写】首付2000元，欠1580，科二合格后1个月内全交。
第八条  退学退费
（一）培训费用的退费
基础服务（必扣项）服务费 600 建档费 300 学员IC卡 100
备注：再扣除全部培训费用的2 0%作为违约金后，甲方将剩余的费用退还给乙方。
第九条 合同的变更、终止
"""

TEXT_2023_BRANCH = TEXT_2023_BRANCH.replace("2 0%", "20%")

PROGRESS = {"exam_counts": {}, "training_hours": {}, "license_type": "C2"}


def _ticket(**over) -> dict:
    base = {
        "id": "ZZL",
        "student_name": "郑智林",
        "registration_date": "2024-03-04",
        "organization_unit_type": "分校",
        "exam_stage": "科目一",
        "license_type": "C2",
        "query_result": {"training_hours": {}, "exam_counts": {}},
    }
    base.update(over)
    return base


# ── P0-4：手写金额抽取 ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "fragment, expected",
    [
        ("人民币【手写】3580 元", 3580.0),
        ("人民币[手写] 3580 元", 3580.0),
        ("人民币　　3580　元", 3580.0),
        ("人民币 3280 元", 3280.0),
        ("人民币___3580___元", 3580.0),
    ],
)
def test_handwritten_total_fee_is_extracted(fragment, expected):
    text = f"实行普通培训，培训费用总额合计{fragment}，□一次性支付"
    assert extract_contract_fees(text)["total_fee"] == expected


def test_blank_amount_stays_zero():
    """金额留空时不得跨句误抽后文数字（旧 Issue：petit——总额错位）。"""
    text = (
        "实行普通培训，培训费用总额合计人民币     　    元，□一次性支付；"
        "包含乙方理论培训、实操培训相关服务费，后续另补 5000 元尾款另行约定。"
    )
    assert extract_contract_fees(text)["total_fee"] == 0


# ── P0-5：打包价不出理论培训费 ───────────────────────────────────────

def test_packaged_price_skips_theory_fee():
    res = calculate_deductions(
        tier=TIERS_BY_ID["2023_branch_school"],
        stage="科目一",
        progress=PROGRESS,
        total_fee=3580.0,
        total_amount=2000.0,
    )
    names = [it["item"] for it in res["items"]]
    assert "理论培训费" not in names
    assert res["total_deduction"] == 1716.0        # 600+300+100+716
    assert res["refund"] == 284.0                  # 2000-1716
    assert res["refund_pending"] is False
    assert any("打包价" in w for w in res["warnings"])


def test_manual_theory_fee_still_wins():
    """人工补录理论培训费时仍以人工值为准（medium→manual）。"""
    res = calculate_deductions(
        tier=TIERS_BY_ID["2023_branch_school"],
        stage="科目一",
        progress=PROGRESS,
        total_fee=3580.0,
        total_amount=2000.0,
        manual_amounts={"theory_fee": 500},
    )
    theory = [it for it in res["items"] if it["item"] == "理论培训费"]
    assert theory and theory[0]["amount"] == 500 and theory[0]["source"] == "manual"


def test_legacy_tier_keeps_theory_from_total():
    """非打包价档位（2019·培训）保持旧语义：无可信理论费时以总额计。"""
    res = calculate_deductions(
        tier=TIERS_BY_ID["2019_training"],
        stage="已受理",
        progress=PROGRESS,
        total_fee=3000.0,
    )
    theory = [it for it in res["items"] if it["item"] == "理论培训费"]
    assert theory and theory[0]["amount"] == 3000 and theory[0]["confidence"] == "medium"


# ── 结算口径：尾款冲抵 ───────────────────────────────────────────────

def test_tail_due_offsets_refund():
    dr = {"items": [], "total_deduction": 1716.0, "refund": 284.0, "refund_pending": False, "warnings": []}
    out = _attach_payment_context(dr, total_fee=3580.0, paid_amount=2000.0, tail_due=1580.0)
    assert out["total_fee"] == 3580
    assert out["paid_amount"] == 2000
    assert out["tail_due"] == 1580
    assert out["net_refund"] == 0
    assert any("实退 0" in w for w in out["warnings"])
    # refund（应退核算值）语义不变
    assert out["refund"] == 284.0


def test_pending_result_has_no_net_refund():
    dr = {"items": [], "total_deduction": 0, "refund": 0.0, "refund_pending": True, "warnings": []}
    out = _attach_payment_context(dr, total_fee=0.0, paid_amount=0.0, tail_due=0.0)
    assert out["net_refund"] is None


# ── 端到端：上传管线（文本入口） ─────────────────────────────────────

def test_pipeline_end_to_end_zhengzhilin():
    """合同额自动回填 3580；已付 2000 → 应退 284 → 冲抵尾款后实退 0。"""
    out = analyze_upload_contract_text(
        contract_text=TEXT_2023_BRANCH,
        ticket=_ticket(actual_paid=2000),
        text_source="vision_text",
    )
    assert out["tier_id"] == "2023_branch_school"
    assert out["tier_result"]["confidence"] == "high"

    dr = out["deductions_result"]
    assert dr["total_fee"] == 3580          # 由合同正文抽取回填
    assert dr["paid_amount"] == 2000
    assert dr["tail_due"] == 1580
    assert dr["refund"] == 284
    assert dr["net_refund"] == 0
    assert [it["item"] for it in dr["items"]] == ["服务费", "建档费", "学员IC卡", "违约金"]


def test_pipeline_without_paid_amount_falls_back():
    """未录已支付金额时以合同总数为退费基数（不得退化成 0/None）。"""
    out = analyze_upload_contract_text(
        contract_text=TEXT_2023_BRANCH,
        ticket=_ticket(),
        text_source="vision_text",
    )
    dr = out["deductions_result"]
    assert dr["paid_amount"] == 0
    assert dr["tail_due"] == 3580
    assert dr["refund"] == 3580 - 1716       # 1864
