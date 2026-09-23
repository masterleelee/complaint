"""上传件「🤖 AI 摘要」规则文案测试（合同原文对照弹窗 v4.4 · S3b）。

口径冻结见 `.scratch/contract-preview-v44-landing/s4-contract.md` §1.4（照抄原型
`demo/contract-preview-v4-demo.html:517-524` 的 `aiSummary`，只按数据有无裁剪）。
用户拍板（Q2）：**不走 LLM**，纯规则文案。

本测试**不连真实 DB、不跑 OCR**：主体直接打纯函数 `build_rule_summary`；末尾一条用
`analyze_upload_contract_text` 验证 upload_pipeline 的接线（`deductions_result["rule_summary"]`）。

金额千分位取整、纯文本（无 HTML）、行以 `\\n` 分隔、`**x**` 表加粗。
"""

from __future__ import annotations

import re

from services.contract_template_text import template_text
from services.contract_tiers import TIERS_BY_ID
from services.upload_pipeline import analyze_upload_contract_text, build_rule_summary

TIER_2023 = {"display_name": "2023·分校"}


def _dr(items, paid, tail, total):
    """构造一个已就位的 deductions_result（只含本函数消费的字段）。"""
    return {
        "items": items,
        "paid_amount": paid,
        "tail_due": tail,
        "total_fee": total,
    }


def _lines(summary):
    return summary.split("\n")


# demo 案例（原型 ③）的扣费项：服务费 600 + 建档费 300 + 学员IC卡 100 + 违约金 716 = 1716
DEMO_ITEMS = [
    {"item": "服务费", "amount": 600},
    {"item": "建档费", "amount": 300},
    {"item": "学员IC卡", "amount": 100},
    {"item": "违约金", "amount": 716, "basis": "全部培训费用 × 档位默认 20%（2023·分校）"},
]


# ── demo 案例：总额 3580 / 实缴 2000 / 尾款 1580 / 扣费合计 1716 / 应退 284 ──
def test_demo_case_structure_and_content():
    summary = build_rule_summary(_dr(DEMO_ITEMS, 2000, 1580, 3580), TIER_2023)
    lines = _lines(summary)
    # 无实操项 → 第 3 行（实操学时）不出：行数为 1/2/4/5/6 = 5
    # （原型 aiSummary 的 6 个元素里，第 3 个仅在有实操学时出现。）
    assert len(lines) == 5, summary

    plain = summary.replace("**", "")
    # 必需子串（契约给的“含…”，指去掉加粗标记后的可见文本）
    assert "额外约定（手写）：首付 2,000 元，欠款 1,580 元" in plain
    assert "应退 = 实缴 2,000 − 扣费合计 1,716 = ¥284" in plain

    # 逐行核对
    assert lines[0].startswith("本合同为 **2023·分校** 档位标准合同（纸质照片识别）。")
    assert "合同总额 ¥3,580，实缴 ¥2,000，应付尾款 ¥1,580" in lines[0]
    assert lines[1] == "额外约定（手写）：**首付 2,000 元，欠款 1,580 元**。"
    assert lines[2] == "扣费合计 ¥1,716 = 服务费 600 + 建档费 300 + 学员IC卡 100 + 违约金 716。"
    assert lines[3] == "违约金按全部培训费用 3,580 × 20% = 716。"
    assert lines[4] == "应退 = 实缴 2,000 − 扣费合计 1,716 = **¥284**。"

    # 加粗标记以 `**` 表达（前端先转义再转 <b>）
    assert "**首付 2,000 元，欠款 1,580 元**" in summary
    assert "**¥284**" in summary

    # 纯文本：不含任何 HTML 标签
    assert "<" not in summary and ">" not in summary


def test_thousands_separator_on_every_amount():
    summary = build_rule_summary(_dr(DEMO_ITEMS, 2000, 1580, 3580), TIER_2023)
    # 千分位（删掉 f"{v:,.0f}" 的逗号会 FAIL）
    assert "¥3,580" in summary
    assert "¥2,000" in summary
    assert "¥1,580" in summary
    assert "¥1,716" in summary


# ── 扣费超实缴 → 应退兜底 0 且不出现负数 ──
def test_deduction_exceeds_paid_uses_zero_floor_without_negative():
    items = [
        {"item": "服务费", "amount": 4116},
        {"item": "违约金", "amount": 716, "basis": "全部培训费用 × 档位默认 20%"},
    ]
    summary = build_rule_summary(_dr(items, 2000, 1580, 3580), TIER_2023)
    last = _lines(summary)[-1]
    assert "按公式兜底为 0，不出现负数" in last
    assert "**¥0**" in last
    # 不出现负数：整条摘要里没有「-数字」或「¥-」
    assert re.search(r"-\d", summary) is None
    assert "¥-" not in summary


# ── 条件行的出现 / 省略 ──
def test_no_penalty_item_omits_penalty_line():
    summary = build_rule_summary(_dr([{"item": "服务费", "amount": 600}], 2000, 1580, 3580), TIER_2023)
    assert "违约金" not in summary


def test_no_practical_item_omits_hours_line():
    summary = build_rule_summary(_dr(DEMO_ITEMS, 2000, 1580, 3580), TIER_2023)
    assert "已产生实操学时" not in summary


def test_practical_items_add_hours_line():
    items = [
        {"item": "科目二实操培训费", "amount": 1920, "basis": "审核学时 16 × 档位单价 120 元/学时（C1）"},
        {"item": "科目三实操培训费", "amount": 480, "basis": "审核学时 4 × 档位单价 120 元/学时（C1）"},
        {"item": "违约金", "amount": 716, "basis": "全部培训费用 × 档位默认 20%"},
    ]
    summary = build_rule_summary(_dr(items, 2000, 1580, 3580), TIER_2023)
    lines = _lines(summary)
    # 有实操项 → 6 行（1/2/3/4/5/6）
    assert len(lines) == 6, summary
    hours_line = next(ln for ln in lines if "已产生实操学时（计时平台）" in ln)
    assert "科目二实操 16 学时" in hours_line
    assert "科目三实操 4 学时" in hours_line
    assert "**¥2,400**" in hours_line  # 1920 + 480


def test_extra_agreement_line_requires_both_paid_and_tail():
    # 实缴 0 → 无「额外约定」行
    assert "额外约定" not in build_rule_summary(_dr(DEMO_ITEMS, 0, 3580, 3580), TIER_2023)
    # 尾款 0 → 无「额外约定」行
    assert "额外约定" not in build_rule_summary(_dr(DEMO_ITEMS, 3580, 0, 3580), TIER_2023)
    # 二者皆 > 0 → 出现
    assert "额外约定" in build_rule_summary(_dr(DEMO_ITEMS, 2000, 1580, 3580), TIER_2023)


# ── 退化 / 非法输入：不崩，返回空串（本实现选择「返回 \"\"」而非省略） ──
def test_empty_items_returns_empty_string():
    assert build_rule_summary({"items": []}, TIER_2023) == ""


def test_none_and_bad_inputs_return_empty_string_without_raise():
    assert build_rule_summary(None, TIER_2023) == ""
    assert build_rule_summary({}, TIER_2023) == ""
    assert build_rule_summary("not-a-dict", TIER_2023) == ""


def test_missing_display_name_degrades_and_never_shows_none():
    full = _dr(DEMO_ITEMS, 2000, 1580, 3580)
    assert build_rule_summary(full, {}) == ""
    assert build_rule_summary(full, {"display_name": ""}) == ""
    assert build_rule_summary(full, {"display_name": None}) == ""
    assert build_rule_summary(full, None) == ""
    # 正常路径也不出现 "None" 字样
    assert "None" not in build_rule_summary(full, TIER_2023)


# ── upload_pipeline 接线：deductions_result 带上 rule_summary ──
def test_pipeline_attaches_rule_summary_for_upload_ticket():
    tier_text = template_text(TIERS_BY_ID["2023_branch_school"])
    ticket = {
        "registration_date": "2023-05-01",
        "organization_unit_type": "分校",
        "exam_stage": "已受理",
        "total_fee": 3580,
        "actual_paid": 2000,
        "license_type": "C1",
        "exam_counts": {},
        "query_result": {},
    }
    result = analyze_upload_contract_text(tier_text, ticket, text_source="vision_text")
    dr = result["deductions_result"]
    summary = dr["rule_summary"]
    assert isinstance(summary, str) and summary
    assert "本合同为 **2023·分校** 档位标准合同" in summary
    assert "应退 = 实缴 2,000 − 扣费合计 1,716 = **¥284**" in summary
