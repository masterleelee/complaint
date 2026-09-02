"""年份只做「下限」约束的档位识别测试（用户 2026-09-02 拍板）。

背景：旧逻辑用 year_to 做「上限」约束，导致「2023 年报名仍用 2021 版合同（违约金 10%）」
被错误排除 2021_2022 档、且按年份硬推 2023 档（违约金 20%）→ 识别失败。

正确语义：
- 报名年份不能早于合同版本发布年份（2019 年报名不可能用 2021/2023 版）→ 保留下限约束；
- 报名年份可以晚于版本（2023 年报名可能还用 2021 版）→ 去掉上限约束，靠条款特征匹配。
"""
import pytest

from services.contract_tiers import identify_tier

from test_contract_tiers import TEXT_2021, TEXT_2023_SCHOOL


def test_2023_registration_uses_2021_template_identifies_2021_2022():
    """23 年报名 + 2021 版文本（违约金 10%）→ 识别为 2021_2022 档（旧版跨年沿用）。"""
    result = identify_tier(
        registration_date="2023-05-10",
        org_unit_type="",
        contract_text=TEXT_2021,
    )
    assert result["tier_id"] == "2021_2022"
    assert result["confidence"] == "high"


def test_2019_registration_cannot_use_2021_template():
    """19 年报名 + 2021 版文本 → 下限约束：2019 年不可能用 2021 版，无法定档。"""
    result = identify_tier(
        registration_date="2019-05-10",
        org_unit_type="",
        contract_text=TEXT_2021,
    )
    assert result["tier_id"] == ""
    # 2021_2022 不得出现在候选（year_from=2020 > 2019）
    assert "2021_2022" not in result["candidates"]


def test_2023_registration_uses_2023_template_still_identifies_2023_school():
    """23 年报名 + 2023 新模板（违约金 20%）→ 仍正确识别为 2023·分校（特征优先不回归）。"""
    result = identify_tier(
        registration_date="2023-12-28",
        org_unit_type="分校",
        contract_text=TEXT_2023_SCHOOL,
    )
    assert result["tier_id"] == "2023_branch_school"


def test_2023_candidates_include_old_and_new_tiers():
    """23 年报名候选应同时包含旧版（2019/2021）与新版（2023），靠特征区分。"""
    result = identify_tier(
        registration_date="2023-05-10",
        org_unit_type="分校",
        contract_text=TEXT_2021,
    )
    candidates = set(result["candidates"])
    assert "2021_2022" in candidates       # 旧版跨年沿用，保留
    assert "2023_branch_school" in candidates  # 新版也保留
    assert "2023_branch_store" not in candidates  # 分店被网点类型过滤
