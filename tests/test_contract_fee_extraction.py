"""上传合同费用提取 + 一致性校验测试（旧模板兼容 + OCR 噪声容错）。

背景：罗炳灿合同（2021-2022 旧模板）此前走上传合同路径时，费用提取完全失败——
旧模板句式（「培训费用总额合计」「理论培训费及相关手续费」「按人民币75元/学时」）与
新模板正则不匹配，且 EasyOCR 噪声（下划线/破折号/金额尾随点）进一步阻断匹配。

覆盖：
1. 旧模板费用逐项提取（总额/理论费/实操单价/考试费/工本费/服务费/违约金比例）。
2. 一致性校验：服务费合计 460 与拆分 300+130+130=560 不符 → 告警提示人工核对。
3. OCR 噪声清洗。
"""
import pytest

from services.contract_service import _clean_ocr_noise, extract_contract_fees


# 罗炳灿合同费用部分（含 EasyOCR 噪声：—、_、金额尾随点、漏「币」「务」字）
TEXT_FEES = """
（2）实行普通培训，培训费用总额合计人民币—3980 元，一次性支付：
乙方于本合同订立之日起当日内一次性向甲方支付。其中理论培训费及相关手续费人民币1650 _元，
实际操作培训费人民币_2130_元（按人民75元/学时的标准计费）。
1、乙方委托甲方代收代交考试费、工本费、补考费等款项，费用合计_490.元，包含科目一70元、
科目二130元、科目三280.元、工本费-10.元。
（三）协助报考服务费
协助报考服务费合计_460.元，包含：报名服务和学员卡费300元，科目一服务费130，科目二，三服费130.元。
2、违约金为全部培训费用的10%，乙方因不可抗拒因素而退学的，甲方不收取违约金。
"""


def test_extract_old_template_fees():
    """旧模板费用逐项提取：总额/理论费/实操单价/考试费/工本费/服务费/违约金比例。"""
    d = extract_contract_fees(TEXT_FEES)
    assert d["total_fee"] == 3980.0
    assert d["theory_fee"] == 1650.0
    assert d["practical_unit_price"] == 75.0
    assert d["exam_fees"] == {"subject1": 70.0, "subject2": 130.0, "subject3": 280.0}
    assert d["material_fee"] == 10.0
    assert d["service_fee"] == 460.0
    assert d["penalty_rate"] == 0.1


def test_service_fee_breakdown_extracted():
    """服务费拆分逐项提取（报名/学员卡 + 科一服务费 + 科二三服务费）。"""
    d = extract_contract_fees(TEXT_FEES)
    assert d["service_breakdown"] == {
        "enroll_card": 300.0,
        "subject1_service": 130.0,
        "subject23_service": 130.0,
    }


def test_service_fee_mismatch_warning():
    """一致性校验：服务费合计 460 ≠ 拆分 300+130+130=560 → 告警提示人工核对。"""
    d = extract_contract_fees(TEXT_FEES)
    assert any("460" in w and "560" in w for w in d["warnings"])


def test_clean_ocr_noise():
    """OCR 噪声清洗：下划线/破折号→空格；金额尾随点去除。"""
    assert _clean_ocr_noise("协助报考服务费合计_460.元") == "协助报考服务费合计 460元"
    assert _clean_ocr_noise("人民币—3980 元") == "人民币 3980 元"


def test_extract_empty_text_returns_zeros():
    """空文本 → 全 0/空，无告警。"""
    d = extract_contract_fees("")
    assert d["total_fee"] == 0
    assert d["theory_fee"] == 0
    assert d["practical_unit_price"] == 0
    assert d["service_fee"] == 0
    assert d["warnings"] == []
