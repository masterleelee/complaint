"""上传件管线「合同错漏 → 降级为人工填写」测试。

背景：合同数额错漏多时（如罗炳灿服务费合计 460 ≠ 拆分 300+130+130=560），系统不应
自动计算金额，而应罗列扣费项目、金额标记 pending 让用户手动填写。

覆盖：
1. 合同错漏（合计≠拆分）→ 扣费项全部 pending、refund_pending=True、warnings 含错漏告警。
2. 无错漏合同 → 不降级，正常计算。
"""
import pytest

from services.upload_pipeline import _run_pipeline

# 含错漏的费用文本（服务费 460 vs 拆分 300+130+130=560）
TEXT_MISMATCH = """
（2）实行普通培训，培训费用总额合计人民币3980元，一次性支付：
其中理论培训费及相关手续费人民币1650元，实际操作培训费人民币2130元（按人民币75元/学时的标准计费）。
乙方委托甲方代收代交考试费、工本费、补考费等款项，费用合计490元，包含科目一70元、科目二130元、科目三280元、工本费10元。
协助报考服务费合计460元，包含：报名服务和学员卡费300元，科目一服务费130，科目二三服务费130元。
违约金为全部培训费用的10%。
"""

# 无错漏文本（无服务费拆分，不触发一致性告警）
TEXT_CLEAN = """
东莞市机动车驾驶员培训合同
依照《中华人民共和国民法典》等相关法律法规的规定，双方就甲方接受乙方委托，对乙方开展机动车驾驶培训服务相关事宜协商一致，订立本合同。
第五条 退学退费
注：1、已发生的实际操作培训费＝已产生的实际操作培训学时×约定的学时收费标准。
2、违约金为全部培训费用的10%，乙方因不可抗拒因素而退学的，甲方不收取违约金。
"""

# 无错漏且含费用明细（服务费拆分 200+130+130=460 一致，不触发降级）
TEXT_CLEAN_WITH_FEES = """
（2）实行普通培训，培训费用总额合计人民币3980元，一次性支付：
其中理论培训费及相关手续费人民币1650元，实际操作培训费人民币2130元（按人民币75元/学时的标准计费）。
乙方委托甲方代收代交考试费、工本费、补考费等款项，费用合计490元，包含科目一70元、科目二130元、科目三280元、工本费10元。
协助报考服务费合计460元，包含：报名服务和学员卡费200元，科目一服务费130，科目二三服务费130元。
违约金为全部培训费用的10%。
"""


def _ticket():
    return {
        "id": "t1",
        "registration_date": "2023-05-17",
        "organization_unit_type": "分校",
        "exam_stage": "科目一",
        "license_type": "C2",
        "total_fee": 3980,
        "service_fee": 0,
        "training_mode": "",
        "query_result": {"driving_hours": {}},
        "exam_counts": {},
    }


def _run(text, ticket):
    return _run_pipeline(
        {"text": text, "source": "local_ocr"},
        ticket,
        source_path="/tmp/t.pdf",
        image_paths=[],
    )


def test_fee_mismatch_demotes_to_pending():
    """合同错漏（服务费合计≠拆分）→ 扣费项全部 pending，金额待人工填写。"""
    r = _run(TEXT_MISMATCH, _ticket())
    dr = r["deductions_result"]
    assert dr is not None
    assert dr["refund_pending"] is True
    assert dr["total_deduction"] == 0.0
    assert dr["refund"] == 0.0
    # 所有扣费项 pending、金额归零（列项目，金额让用户填）
    assert dr["items"], "应至少列出扣费项目"
    assert all(it["pending"] and it["amount"] == 0 for it in dr["items"])
    # 错漏告警透传
    assert any("460" in w and "560" in w for w in dr["warnings"])


def test_fee_mismatch_lists_complete_fee_items():
    """降级后补全完整扣费项目清单（考试费/工本费/服务费拆分均列出待填）。"""
    r = _run(TEXT_MISMATCH, _ticket())
    dr = r["deductions_result"]
    names = {it["item"] for it in dr["items"]}
    for expected in ("理论培训费", "违约金", "科目一考试费", "科目二考试费", "科目三考试费",
                     "工本费", "报名/学员卡费", "科目一服务费", "科目二三服务费"):
        assert expected in names, f"缺扣费项目：{expected}"


def test_clean_contract_no_demotion():
    """无错漏合同 → 不降级，正常计算扣费。"""
    r = _run(TEXT_CLEAN, _ticket())
    dr = r["deductions_result"]
    assert dr is not None
    # 无服务费错漏 → 不应标记 pending（违约金按 3980×10% 正常算）
    assert not any("460" in w for w in dr.get("warnings", []))


def test_clean_contract_uses_contract_theory_fee_and_unit_price():
    """无错漏合同 → 采信合同正文的理论费 1650、实操单价 75（非档位默认 120/150）。"""
    ticket = _ticket()
    ticket["query_result"] = {"driving_hours": {"subject2": 10}}  # 科目二 10 学时
    r = _run(TEXT_CLEAN_WITH_FEES, ticket)
    dr = r["deductions_result"]
    assert dr is not None and not dr["refund_pending"]
    by_name = {it["item"]: it for it in dr["items"]}
    # 理论费 1650（不是 total_fee 整体 3980）
    assert by_name["理论培训费"]["amount"] == 1650.0
    # 实操费 = 75 × 10 = 750（不是档位默认 150 × 10 = 1500）
    assert by_name["科目二实操培训费"]["amount"] == 750.0
    assert "75 元/学时" in by_name["科目二实操培训费"]["basis"]


def test_service_fee_charged_by_progress():
    """服务费按进度扣：科目一阶段（考过一次科一）→ 报名费全额 + 科一服务费扣，科二三服务费不扣。"""
    ticket = _ticket()
    ticket["exam_counts"] = {"subject1": 1}  # 考过一次科目一，未考科二/三
    r = _run(TEXT_CLEAN_WITH_FEES, ticket)
    dr = r["deductions_result"]
    assert dr is not None and not dr["refund_pending"]
    by_name = {it["item"]: it for it in dr["items"]}
    assert by_name["报名/学员卡费"]["amount"] == 200.0
    assert by_name["科目一服务费"]["amount"] == 130.0
    # 未考科目二/三 → 科目二三服务费不扣
    assert "科目二三服务费" not in by_name


def test_refund_base_uses_total_amount_not_training_fee():
    """退费基数 = 总金额（培训费+代交费+服务费），非仅培训费总额。

    罗炳灿科目一阶段考过一次科一：总金额 4930（3980+490+460），扣费 2448，应退 2482。
    若误用培训费总额 3980 作基数，会错退成 3980-2448=1532。
    """
    ticket = _ticket()
    ticket["exam_counts"] = {"subject1": 1}
    r = _run(TEXT_CLEAN_WITH_FEES, ticket)
    dr = r["deductions_result"]
    assert dr["total_deduction"] == 2448.0
    assert dr["refund"] == 2482.0


def test_service_fee_not_charged_when_not_taken_exam():
    """未考任何科目 → 只有报名费扣，科目一/二三服务费都不扣。"""
    ticket = _ticket()
    ticket["exam_counts"] = {}
    r = _run(TEXT_CLEAN_WITH_FEES, ticket)
    dr = r["deductions_result"]
    by_name = {it["item"]: it for it in dr["items"]}
    assert by_name["报名/学员卡费"]["amount"] == 200.0
    assert "科目一服务费" not in by_name
    assert "科目二三服务费" not in by_name
