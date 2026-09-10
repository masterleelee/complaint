"""OCR 文本禁止自动定档（P0-2）。

背景（诊断报告 `.scratch/contract-upload-parity/diagnosis-20260903.md` 根因 B/C）：
视觉模型额度耗尽时系统静默回退本地 EasyOCR，OCR 噪声使 2023 版合同的特征句
一条都命中不了，反被 2021 版那句「违约金为全部培训费用的10%」命中 3 分，
档位判成 `2021_2022` 且 **confidence 仍报 high** —— 用户拿到错档却毫不知情。

本闸门：文本来源为低质量 OCR（`local_ocr`）时，置信度强制降为 `low` 并附
降级证据，由经办人手选档位；文本层直读 / 视觉识别不受影响。
"""

import pytest

from services.contract_tiers import identify_tier
from tests.test_contract_tiers import TEXT_2023_SCHOOL

# 2023 分校：报名 2023 年、网点类型分校，正文含该档四条特征 → 正常情况下定档 high
REG_2023 = "2023-05-10"
ORG_SCHOOL = "分校"


def _tier(source):
    return identify_tier(
        registration_date=REG_2023,
        org_unit_type=ORG_SCHOOL,
        contract_text=TEXT_2023_SCHOOL,
        text_source=source,
    )


class TestTextSourceConfidenceGate:
    """置信度随文本来源降级。"""

    @pytest.mark.parametrize("source", ["pdf_text", "vision_text"])
    def test_high_quality_source_keeps_confidence(self, source):
        """文本层直读 / 视觉识别：行为不变，仍可 high。"""
        assert _tier(source)["confidence"] == "high"

    def test_unknown_source_keeps_legacy_behaviour(self):
        """未传 text_source（旧调用方）：保持原行为，避免破坏既有契约。"""
        legacy = identify_tier(
            registration_date=REG_2023,
            org_unit_type=ORG_SCHOOL,
            contract_text=TEXT_2023_SCHOOL,
        )
        assert legacy["confidence"] == "high"

    def test_local_ocr_forces_low_confidence(self):
        """本地 OCR 文本：置信度强制 low，禁止自动定档。"""
        result = _tier("local_ocr")
        assert result["tier_id"] == "2023_branch_school"
        assert result["confidence"] == "low"

    def test_local_ocr_emits_degradation_evidence(self):
        """降级必须在 evidence 里留可读证据，供前端提示经办人。"""
        evidence = _tier("local_ocr")["evidence"]
        hits = [e for e in evidence if e.get("type") == "degradation"]
        assert hits, "缺失降级证据"
        assert "local_ocr" in hits[0]["detail"]

    @pytest.mark.parametrize("source", ["pdf_text", "vision_text", ""])
    def test_high_quality_sources_emit_no_degradation(self, source):
        """高质量来源不产生降级证据。"""
        evidence = _tier(source)["evidence"]
        assert [e for e in evidence if e.get("type") == "degradation"] == []

    def test_local_ocr_with_empty_text_stays_unresolved(self):
        """OCR 失败（空文本）：不定档且 low。"""
        result = identify_tier(
            registration_date="",
            org_unit_type="",
            contract_text="",
            text_source="local_ocr",
        )
        assert result["tier_id"] == ""
        assert result["confidence"] == "low"


class TestPipelineIntegration:
    """上传管线把提取来源透传给档位识别。"""

    def test_upload_pipeline_forwards_text_source(self):
        """local_ocr 的提取结果经管线后，tier_result 置信度为 low。"""
        from services.upload_pipeline import analyze_upload_contract_text

        result = analyze_upload_contract_text(
            TEXT_2023_SCHOOL,
            {"registration_date": REG_2023, "organization_unit_type": ORG_SCHOOL},
            text_source="local_ocr",
        )
        assert result["tier_id"] == "2023_branch_school"
        assert result["tier_result"]["confidence"] == "low"

    def test_upload_pipeline_keeps_high_for_clean_text(self):
        """文本层直读经管线后置信度不受影响。"""
        from services.upload_pipeline import analyze_upload_contract_text

        result = analyze_upload_contract_text(
            TEXT_2023_SCHOOL,
            {"registration_date": REG_2023, "organization_unit_type": ORG_SCHOOL},
            text_source="pdf_text",
        )
        assert result["tier_result"]["confidence"] == "high"
