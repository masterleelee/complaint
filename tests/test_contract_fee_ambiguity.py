"""手写总额可疑性闸门测试（ISS-VC-01，2026-09-14 实跑暴露）。

背景：换上可用视觉模型后，识别**不再失败**（2/2 页成功、档位 high 置信度），
但数字被幻觉污染。同一份合同、同一页，人工看图确认手写是：

    培训费用总额合计人民币  ⌈3580⌉（划掉）→ 3580   元
    其他方式：首付2000元 · 欠尾款1580 · 科一合格交清尾款

而模型读成：

    培训费用总额合计人民币  【手写】¥6800-2500   元
    其他方式：【手写】首付2000，欠4800，科一合格交1000

即 3580 → 6800-2500、1580 → 4800、交清尾款 → 交1000。
且读出的组内自洽（2000+4800=6800），首付+欠款的一致性校验无法发现。

结论：数字幻觉必须靠「可疑即拒绝」兜底，不能让错误金额静默进明细。
"""

from __future__ import annotations

from services.contract_service import extract_contract_fees
from services.upload_pipeline import analyze_upload_contract_text

# 真实视觉输出（tmp/vision_text_dump.txt 摘要），保留换行以复现原始形态
TEXT_HALLUCINATED = """第四条　费用及支付
1、乙方选择以下第【手写】1种方式支付培训费用：
（1）实行普通培训，培训费用总额合计人民币
【手写】¥6800-2500
元，□一次性支付，☑分期付款；
（4）其他方式：
【手写】首付2000，欠4800，科一合格交1000
"""


def test_two_numbers_in_total_slot_are_rejected():
    """「合计人民币 ¥6800-2500 元」含两组数字 → 拒绝自动采信并给出告警。"""
    result = extract_contract_fees(TEXT_HALLUCINATED)

    assert result["total_fee"] == 0.0
    assert any("请人工核对" in w and "6800" in w for w in result["warnings"])


def test_clean_handwritten_total_still_extracted():
    """模型识别正确时（单一数字）必须照常抽取 —— 闸门不能误伤正常路径。"""
    for marker in ("【手写】", "[手写]", "¥", "￥", "　"):
        text = f"（1）实行普通培训，培训费用总额合计人民币{marker}3580 元，□一次性支付"
        assert extract_contract_fees(text)["total_fee"] == 3580.0, marker


def test_blank_slot_does_not_warn():
    """空槽位（未填写）不应报「修改痕迹」，避免噪声告警淹没真问题。"""
    text = "（1）实行普通培训，培训费用总额合计人民币________元，□一次性支付"
    result = extract_contract_fees(text)

    assert result["total_fee"] == 0.0
    assert not any("修改痕迹" in w for w in result["warnings"])


def test_page_number_suffix_is_not_flagged():
    """单一金额 + 页脚等非数字噪声，不得被误判为多组数字。"""
    text = "（1）实行普通培训，培训费用总额合计人民币 3580 元，□一次性支付，☑分期付款"
    result = extract_contract_fees(text)

    assert result["total_fee"] == 3580.0
    assert result["warnings"] == []


TEXT_MIN_TIER = """东莞市机动车驾驶员培训合同
依照《中华人民共和国民法典》、《中华人民共和国道路交通安全法》、《中华人民共和国道路运输条例》等相关法律法规的规定，订立本合同。
（一）乙方学习的准驾车型:□C1 ☑C2 □C5 □
（三）乙方提供全部所需的资料，甲方为乙方办理学员IC卡、建立培训档案。
第四条  费用及支付
1、乙方选择以下第 1 种方式支付培训费用（包含建档费/学员IC卡/各阶段培训费及相关手续费）：
（1）实行普通培训，培训费用总额合计人民币 3580 元，□一次性支付，☑分期付款；
第八条  退学退费
（一）培训费用的退费
基础服务（必扣项）服务费 600 建档费 300 学员IC卡 100
备注：再扣除全部培训费用的20%作为违约金后，甲方将剩余的费用退还给乙方。
"""


def _ticket():
    return {
        "id": "AMB",
        "registration_date": "2024-03-04",
        "organization_unit_type": "分校",
        "exam_stage": "科目一",
        "license_type": "C2",
        "query_result": {"exam_counts": {"科目一": 0}, "training_hours": {}},
    }


def test_local_ocr_fallback_is_surfaced_to_panel():
    """回退本地 OCR 时必须在面板告警，不能静默降级（否则错误金额看起来与正常无异）。"""
    out = analyze_upload_contract_text(
        contract_text=TEXT_MIN_TIER, ticket=_ticket(), text_source="local_ocr",
    )
    warnings = out["deductions_result"]["warnings"]

    assert any("回退本地 OCR" in w for w in warnings)


def test_vision_source_has_no_fallback_warning():
    """正常走视觉识别时不得出现「回退本地 OCR」告警（避免狼来了）。"""
    out = analyze_upload_contract_text(
        contract_text=TEXT_MIN_TIER, ticket=_ticket(), text_source="vision_text",
    )
    warnings = out["deductions_result"]["warnings"]

    assert not any("回退本地 OCR" in w for w in warnings)
