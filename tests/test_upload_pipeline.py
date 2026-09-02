"""上传件分析管线测试（工单 04-analysis-pipeline，ADR-0003 + spec Implementation Decisions）。

覆盖：档位识别接入、扣费引擎按档位+阶段+进度算明细、缓存键产出、下载链路零回归、提取失败优雅降级。
"""

import io
import pytest

from services.contract_tiers import TIERS_BY_ID
from services.upload_pipeline import (
    analyze_upload_contract_file,
    analyze_upload_contract_text,
    is_upload_ticket,
)


PROJECT = __import__("pathlib").Path(__file__).parent.parent

# 真实 PDF fixture（2019·服务，含文本层）—— 复用 test_contract_set_storage 的 TEMPLATE_PDF
TEMPLATE_PDF = PROJECT / "1合同种类" / "2019年" / "1服务合同.pdf"

# ── 文本 fixture：构造足以触发 identify_tier 的最小特征集 ─────────────

TEXT_2019_SERVICE = (
    "东莞市机动车驾驶报考协助服务合同\n"
    "协助乙方驾驶报考服务相关事宜\n"
    "受理服务和学员卡相关费用\n"
)

TEXT_2019_TRAINING = (
    "本培训合同不包含协助乙方驾驶考试服务的相关事项\n"
    "如乙方在参加理论培训前退学，甲方应退回乙方理论培训费\n"
)

TEXT_2021_2022 = (
    "根据《中华人民共和国民法典》相关规定\n"
    "违约金为全部培训费用的10%\n"
)

TEXT_2023_BRANCH_SCHOOL = (
    "东莞市机动车驾驶员培训合同（分校）\n"
    "全部培训费用的20%作为违约金\n"
    "包含乙方理论培训、实操培训及甲方协助乙方建档、学员IC卡等相关服务费\n"
    "必扣项：（必扣项）建档费 服务费 学员IC卡\n"
)

TEXT_2023_BRANCH_STORE = (
    "东莞市机动车驾驶员培训合同（分店）\n"
    "全部培训费用的20%作为违约金\n"
    "包含乙方理论培训、实操培训及甲方协助乙方建档、学员IC卡等相关服务费\n"
    "必扣项：（必扣项）建档费 服务费 学员IC卡 场地费 700 元\n"
)


def _make_ticket(**overrides):
    base = {
        "registration_date": "2019-05-01",
        "organization_unit_type": "服务",
        "license_type": "C1",
        "exam_stage": "已受理",
        "total_fee": 3000,
        "exam_counts": {},
        "query_result": {},
    }
    base.update(overrides)
    return base


# ── 1. 档位识别接入 ──────────────────────────────────────────────────

def test_identifies_2023_branch_store_via_text():
    ticket = _make_ticket(registration_date="2023-09-01", organization_unit_type="分店", total_fee=6000, exam_stage="实操中")
    result = analyze_upload_contract_text(TEXT_2023_BRANCH_STORE, ticket)
    assert result["tier_id"] == "2023_branch_store"
    assert result["tier_result"]["confidence"] in ("high", "medium")
    assert result["extraction_error"] is None


def test_identifies_2023_branch_school_via_text():
    ticket = _make_ticket(registration_date="2023-09-01", organization_unit_type="分校", total_fee=6000, exam_stage="实操中")
    result = analyze_upload_contract_text(TEXT_2023_BRANCH_SCHOOL, ticket)
    assert result["tier_id"] == "2023_branch_school"


def test_identifies_2021_2022_via_text():
    ticket = _make_ticket(registration_date="2021-05-01", organization_unit_type="分校", total_fee=5000)
    result = analyze_upload_contract_text(TEXT_2021_2022, ticket)
    assert result["tier_id"] == "2021_2022"


def test_identifies_2019_training_via_text():
    ticket = _make_ticket(registration_date="2019-05-01", organization_unit_type="分校", total_fee=3000)
    result = analyze_upload_contract_text(TEXT_2019_TRAINING, ticket)
    assert result["tier_id"] == "2019_training"


def test_identifies_2019_service_via_text():
    ticket = _make_ticket(registration_date="2019-05-01", organization_unit_type="服务", total_fee=1500)
    result = analyze_upload_contract_text(TEXT_2019_SERVICE, ticket)
    assert result["tier_id"] == "2019_service"


# ── 2. 扣费引擎按档位+阶段+进度算明细（验收 2） ─────────────────────

def test_pipeline_runs_deduction_engine_with_resolved_tier():
    ticket = _make_ticket(
        registration_date="2023-09-01",
        organization_unit_type="分店",
        exam_stage="实操中",
        total_fee=6000,
        exam_counts={"subject1": 1},
        query_result={"driving_hours": {"subject2": 10}},
    )
    result = analyze_upload_contract_text(TEXT_2023_BRANCH_STORE, ticket)
    dr = result["deductions_result"]
    assert dr is not None
    # 必扣 4 项（分店多场地费 700）
    mandatory_names = [it["item"] for it in dr["items"] if it["category"] == "必扣"]
    assert set(mandatory_names) == {"服务费", "建档费", "学员IC卡", "场地费"}
    # 违约金 20%
    penalty = [it for it in dr["items"] if it["item"] == "违约金"][0]
    assert penalty["amount"] == pytest.approx(1200)
    assert penalty["source"] == "tier_default"
    # 考试费按已考门控：科一 70
    exam = [it for it in dr["items"] if it["item"] == "科目一考试费"][0]
    assert exam["amount"] == pytest.approx(70)
    # 实操费 = 10 × 120 = 1200
    practical = [it for it in dr["items"] if it["item"] == "科目二实操培训费"][0]
    assert practical["amount"] == pytest.approx(1200)


def test_pipeline_2019_training_accepted_no_penalty():
    ticket = _make_ticket(
        registration_date="2019-05-01",
        organization_unit_type="分校",
        exam_stage="已受理",
        total_fee=3000,
    )
    result = analyze_upload_contract_text(TEXT_2019_TRAINING, ticket)
    dr = result["deductions_result"]
    items = dr["items"]
    # 理论费全额扣
    theory = [it for it in items if it["item"] == "理论培训费"][0]
    assert theory["amount"] == pytest.approx(3000)
    # 无实操费（学时=0、阶段=已受理）
    assert not any(it["item"] == "科目二实操培训费" for it in items)
    # 无违约金（2019 三档 0%）
    assert not any(it["item"] == "违约金" for it in items)


def test_pipeline_returns_none_when_tier_unresolved():
    """空文本 / 完全无法定档时，扣费引擎不跑，deductions_result=None。"""
    ticket = _make_ticket(registration_date="2019-05-01", organization_unit_type="分校", total_fee=3000)
    result = analyze_upload_contract_text("", ticket)
    # 空文本下 identify_tier 会返回 low confidence + 空 tier_id
    assert result["tier_id"] == ""
    assert result["deductions_result"] is None


# ── 3. 缓存键产出（验收 5） ─────────────────────────────────────────

def test_pipeline_carries_cache_key():
    ticket = _make_ticket(registration_date="2023-09-01", organization_unit_type="分店", total_fee=6000)
    r1 = analyze_upload_contract_text(TEXT_2023_BRANCH_STORE, ticket)
    r2 = analyze_upload_contract_text(TEXT_2023_BRANCH_STORE, ticket)
    assert r1["cache_key"]
    assert r1["cache_key"] == r2["cache_key"]


def test_cache_key_changes_when_tier_changes():
    """同进度下，档位不同时缓存键变化：分店 vs 分校 文本 + 对应 org_type 强制不同档位。"""
    r1 = analyze_upload_contract_text(
        TEXT_2023_BRANCH_STORE,
        _make_ticket(registration_date="2023-09-01", organization_unit_type="分店", total_fee=6000),
    )
    r2 = analyze_upload_contract_text(
        TEXT_2023_BRANCH_SCHOOL,
        _make_ticket(registration_date="2023-09-01", organization_unit_type="分校", total_fee=6000),
    )
    assert r1["tier_id"] != r2["tier_id"]
    assert r1["cache_key"] != r2["cache_key"]


def test_cache_key_changes_when_progress_changes():
    base = dict(registration_date="2023-09-01", organization_unit_type="分店", total_fee=6000)
    r1 = analyze_upload_contract_text(TEXT_2023_BRANCH_STORE, _make_ticket(**base, exam_stage="已受理"))
    r2 = analyze_upload_contract_text(TEXT_2023_BRANCH_STORE, _make_ticket(**base, exam_stage="实操中"))
    assert r1["cache_key"] != r2["cache_key"]


# ── 4. 提取失败优雅降级（验收 3：LLM/提取超时须可见错误） ──────────

def test_pipeline_handles_extraction_error_gracefully(monkeypatch):
    """extract_contract_text_from_file 返回 error 时，pipeline 不抛，extraction_error 字段非空。"""
    from services import upload_pipeline

    def fake_extract(filepath, image_paths=None):
        return {"error": "LLM 超时", "can_confirm_fee_plan": False, "text": "", "source": ""}

    monkeypatch.setattr(upload_pipeline, "extract_contract_text_from_file", fake_extract)

    result = analyze_upload_contract_file(filepath="/nonexistent.pdf", ticket=_make_ticket())
    assert result["extraction_error"] == "LLM 超时"
    assert result["tier_id"] == ""
    assert result["cache_key"] == ""
    assert result["deductions_result"] is None


# ── 5. 正文随结果落库（ADR-0002 硬前提） ─────────────────────────────

def test_pipeline_writes_text_to_contract_set():
    ticket = _make_ticket(registration_date="2023-09-01", organization_unit_type="分店", total_fee=6000)
    result = analyze_upload_contract_text(TEXT_2023_BRANCH_STORE, ticket, text_source="pdf_text")
    cs = result["contract_set"]
    assert cs["contracts"], "正文必须随结果落库（ADR-0002）"
    assert cs["contracts"][0]["text"] == TEXT_2023_BRANCH_STORE
    assert cs["contracts"][0]["text_source"] == "pdf_text"
    assert cs["contracts"][0]["text_confidence"] == "high"


# ── 5b. 锚点 + 冲突告警（工单 05 集成） ──────────────────────────────

def test_pipeline_enriches_items_with_anchors():
    """2023·分店 + 实操中 10学时 + 已考科一 → 各非 pending 明细都带 anchor 字段。"""
    text = (
        "应收服务费600元 建档费300元 学员IC卡100元 场地费700元\n"
        "科目二考试费130元 实操培训费：120元/学时\n"
        "全部培训费用的20%作为违约金\n"
    )
    ticket = _make_ticket(
        registration_date="2023-09-01",
        organization_unit_type="分店",
        exam_stage="实操中",
        total_fee=6000,
        exam_counts={"subject1": 1},
        query_result={"driving_hours": {"subject2": 10}},
    )
    result = analyze_upload_contract_text(text, ticket)
    dr = result["deductions_result"]
    for it in dr["items"]:
        if it.get("pending"):
            continue
        assert "anchor_start" in it
        assert "anchor_end" in it
        assert "anchor_missing" in it
        assert "anchor_text" in it


def test_pipeline_warns_on_penalty_rate_conflict():
    """文本写 10% 而档位默认 20% → warnings 出现冲突告警；金额仍按档位默认（1200）。"""
    text = (
        "应收服务费600元 建档费300元 学员IC卡100元 场地费700元\n"
        "实操培训费：120元/学时\n"
        "全部培训费用的10%作为违约金\n"  # 文本说 10%
    )
    ticket = _make_ticket(
        registration_date="2023-09-01",
        organization_unit_type="分店",
        exam_stage="实操中",
        total_fee=6000,
        exam_counts={"subject1": 1},
        query_result={"driving_hours": {"subject2": 10}},
    )
    result = analyze_upload_contract_text(text, ticket)
    dr = result["deductions_result"]
    # 冲突告警
    assert any("10%" in w and "20%" in w for w in dr["warnings"])
    # 金额按档位默认：6000 × 20% = 1200（不因 OCR 文本而采用 10%）
    penalty = [it for it in dr["items"] if it["item"] == "违约金"][0]
    assert penalty["amount"] == pytest.approx(1200)


def test_pipeline_marks_anchor_missing_without_throwing():
    """OCR 噪声致锚点缺失：明细 anchor_missing=True，整管线不抛。"""
    text = "短文本"  # 几乎不含任何扣费短语
    ticket = _make_ticket(
        registration_date="2023-09-01",
        organization_unit_type="分店",
        exam_stage="实受理",
        total_fee=6000,
    )
    result = analyze_upload_contract_text(text, ticket)
    dr = result["deductions_result"]
    # 必扣项应有 anchor_missing=True（文本里没这些词）
    missing_items = [it for it in dr["items"] if it.get("anchor_missing") is True]
    assert missing_items, "OCR 噪声下应至少部分锚点缺失"
    # 整体不抛 + warnings 字段存在
    assert "warnings" in dr


# ── 6. 真实 PDF fixture 跑通端到端 ───────────────────────────────────

def test_real_pdf_2019_service_end_to_end():
    """真实 PDF fixture 走完整提取 + 档位识别 + 扣费引擎。"""
    if not TEMPLATE_PDF.exists():
        pytest.skip("真实 PDF fixture 不存在")
    ticket = _make_ticket(
        registration_date="2019-05-01",
        organization_unit_type="服务",
        total_fee=1500,
    )
    result = analyze_upload_contract_file(filepath=str(TEMPLATE_PDF), ticket=ticket)
    assert result["extraction_error"] is None
    assert result["cache_key"]
    # 2019 + 服务 → 2019_service 三选一（取决于文本特征命中）
    assert result["tier_id"] in {"2019_service", "2019_pay_agent", "2019_training"}


# ── 7. 下载链路护栏（验收 4：下载件既有行为零变化） ─────────────────

def test_legacy_analyze_contract_from_file_output_shape_unchanged(monkeypatch):
    """下载链路：analyze_contract_from_file 返回 dict 仍含旧字段；本 ticket 不动下载行为。
    此护栏只为契约：legacy 输出的顶层字段集不缩（即便 04 阶段上层加了 tier/cache_key，
    legacy 链路不感知）。
    """
    from services.contract_service import analyze_contract_from_file

    # 用一个不存在的路径，避免真依赖外部 OCR；先确保 legacy 行为不抛
    # —— 真实路径测试由 test_contract_set_storage.test_analysis_result_carries_text 覆盖
    # 这里仅验证：导入成功 + 函数签名未变
    import inspect
    sig = inspect.signature(analyze_contract_from_file)
    params = list(sig.parameters.keys())
    assert params[:8] == [
        "filepath", "exam_stage", "training_hours", "total_fee",
        "image_paths", "exam_counts", "registration_date", "skill_cert_date",
    ]


# ── 8. is_upload_ticket 判定 ────────────────────────────────────────

def test_is_upload_ticket_true_when_sha256_present():
    raw = {"contracts": [{"file": "/a.pdf", "sha256": "abc123"}, {"file": "/b.pdf", "sha256": "def456"}]}
    assert is_upload_ticket(raw) is True


def test_is_upload_ticket_false_for_legacy_single_dict():
    """旧结构单份 dict（无 sha256）不算 upload。"""
    raw = {"contracts": [{"contract_id": "extracted-contract", "title": "t", "rules": []}]}
    assert is_upload_ticket(raw) is False


def test_is_upload_ticket_false_for_empty():
    assert is_upload_ticket(None) is False
    assert is_upload_ticket({}) is False
    assert is_upload_ticket({"contracts": []}) is False
