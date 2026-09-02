"""工单 10 HTTP 集成：/api/contract/analyze 多份归并 + 单份回传（06 面板渲染前提）。"""

import json

import pytest

import database
import app as app_module
import services.upload_pipeline as upload_pipeline_module
from conftest import _autologin_admin  # noqa: F401

from test_multi_contract import TEXT_2019_PAY_AGENT, TEXT_2019_TRAINING


@pytest.fixture()
def fresh_db(tmp_path):
    database.DB_PATH = tmp_path / "test.db"
    database._local.clear()
    database.init_db()
    yield database
    database._local.clear()


@pytest.fixture()
def client(fresh_db, monkeypatch, tmp_path):
    archive_root = tmp_path / "archive-root"
    archive_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app_module, "load_config", lambda: {"archive_root": str(archive_root)})
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        try:
            _autologin_admin(c)
        except Exception:
            pass
        yield c, archive_root


def _stub_legacy(monkeypatch):
    """下载链路 legacy 分析打桩：本票只验证上传管线归并，不触发 LLM。"""
    monkeypatch.setattr(app_module, "analyze_contract_from_file", lambda **kw: {
        "total_fee": 3000, "actual_paid": 0, "deductions": [],
        "total_deduction": 0, "refund": 0,
    })


def _stub_extraction(monkeypatch, texts_by_path):
    """提取降级链打桩：按文件路径返回预设文本（真实档位识别 + 引擎 + 锚定照跑）。"""
    def _fake(filepath, image_paths=None):
        text = texts_by_path.get(filepath)
        if text is None:
            return {"error": "无法从合同文件中提取可分析文本"}
        return {"text": text, "source": "pdf_text", "can_confirm_fee_plan": True}
    monkeypatch.setattr(upload_pipeline_module, "extract_contract_text_from_file", _fake)


def _make_ticket_with_set(ticket_set):
    tid = database.save_ticket({
        "student_name": "李四",
        "id_card": "110101199003070011",
        "complaint_date": "2026-09-01",
        "complaint_type": "A",
        "school_short": "沙田",
        "organization_unit_type": "分店",
        "organization_unit_name": "沙田分店",
        "registration_date": "2019-05-01",
        "license_type": "C1",
        "exam_stage": "已受理",
        "handling_notes": "已沟通",
        "branch_cooperation": "好",
        "fee_plan_status": "draft",
        "total_fee": 3000,
    })
    database.update_ticket(tid, {"contract_set": json.dumps(ticket_set, ensure_ascii=False)})
    return tid


def test_analyze_multi_contract_set_aggregates(client, monkeypatch):
    """上传 2 份（代缴+培训）→ 逐份分析归并：N 份、考试费归属代缴、明细打份标签。"""
    c, archive_root = client
    _stub_legacy(monkeypatch)
    fa = str(archive_root / "a.pdf")
    fb = str(archive_root / "b.pdf")
    for p in (fa, fb):
        (archive_root / p.split("/")[-1]).write_bytes(b"%PDF-1.4 fake")
    _stub_extraction(monkeypatch, {fa: TEXT_2019_PAY_AGENT, fb: TEXT_2019_TRAINING})
    tid = _make_ticket_with_set({"contracts": [
        {"kind": "", "tier": "", "file": fa, "filename": "a.pdf", "sha256": "aa11", "text": "", "text_source": "", "text_confidence": ""},
        {"kind": "", "tier": "", "file": fb, "filename": "b.pdf", "sha256": "bb22", "text": "", "text_source": "", "text_confidence": ""},
    ]})

    resp = c.post("/api/contract/analyze", json={
        "filepath": fa, "ticket_id": tid, "exam_stage": "已受理",
        "training_hours": {}, "total_fee": 3000, "id_card": "110101199003070011",
        "exam_counts": {"subject1": 1},
    })
    assert resp.status_code == 200
    result = resp.get_json()
    assert result.get("error") is None
    # 06 三栏预览面板渲染前提：deductions_result 必须回传（本票前该键缺失 → 面板永不显示）
    dr = result.get("deductions_result")
    assert isinstance(dr, dict) and isinstance(dr.get("items"), list) and dr["items"]
    # 已识别 2 份（摘要条数据源）
    assert result["contract_count"] == 2
    assert result["contract_kinds"] == ["代缴", "培训"]
    assert "已识别 2 份" in (result.get("tier_based") or "")
    # 考试费只归属代缴份一次
    exam = [it for it in dr["items"] if "考试费" in (it.get("item") or "")]
    assert len(exam) == 1 and exam[0]["contract_kind"] == "代缴"
    # 每条明细带份标签
    assert all("contract_kind" in it for it in dr["items"])
    # 逐份记录与正文落库（ADR-0002）
    ca = result["contract_analyses"]
    assert len(ca) == 2 and ca[0]["text"] and ca[1]["text"]
    # 多份逐份总额未知 → 理论培训费 pending → refund_pending
    assert result["refund_pending"] is True


def test_analyze_single_contract_returns_deductions_result(client, monkeypatch):
    """单份上传行为与 06 一致（回归）：原字段不变 + 新增 deductions_result/contract_analyses。"""
    c, archive_root = client
    _stub_legacy(monkeypatch)
    fa = str(archive_root / "single.pdf")
    (archive_root / "single.pdf").write_bytes(b"%PDF-1.4 fake")
    _stub_extraction(monkeypatch, {fa: TEXT_2019_TRAINING})
    tid = _make_ticket_with_set({"contracts": [
        {"kind": "", "tier": "", "file": fa, "filename": "single.pdf", "sha256": "cc33", "text": "", "text_source": "", "text_confidence": ""},
    ]})

    resp = c.post("/api/contract/analyze", json={
        "filepath": fa, "ticket_id": tid, "exam_stage": "已受理",
        "training_hours": {}, "total_fee": 3000, "id_card": "110101199003070011",
    })
    assert resp.status_code == 200
    result = resp.get_json()
    assert result.get("error") is None
    # 单份：ticket.total_fee 仍作为该份培训费总额（理论费非 pending）
    dr = result["deductions_result"]
    theory = [it for it in dr["items"] if it.get("item") == "理论培训费"][0]
    assert theory["pending"] is False and theory["amount"] == 3000
    assert result["contract_count"] == 1
    assert result["contract_kinds"] == ["培训"]
    assert len(result["contract_analyses"]) == 1
    # legacy 顶层字段不缩（下载链路护栏同款承诺）
    for key in ("total_fee", "deductions", "total_deduction", "refund"):
        assert key in result


def test_download_ticket_has_no_multi_fields(client, monkeypatch):
    """下载件（contract_set 无 sha256）→ 不走新管线：无 deductions_result，面板不渲染。"""
    c, archive_root = client
    _stub_legacy(monkeypatch)
    fa = str(archive_root / "dl.pdf")
    (archive_root / "dl.pdf").write_bytes(b"%PDF-1.4 fake")
    tid = _make_ticket_with_set({"contracts": [
        {"kind": "", "tier": "", "file": fa, "filename": "dl.pdf", "text": "", "text_source": "", "text_confidence": ""},
    ]})
    resp = c.post("/api/contract/analyze", json={
        "filepath": fa, "ticket_id": tid, "exam_stage": "已受理",
        "training_hours": {}, "total_fee": 3000, "id_card": "110101199003070011",
    })
    assert resp.status_code == 200
    result = resp.get_json()
    assert "deductions_result" not in result
    assert "contract_analyses" not in result


def test_multi_legacy_first_file_incomplete_demoted(client, monkeypatch):
    """真实样张验收修复：多份套件首份 legacy 识别不完整（如 2019 服务合同本就无费用
    字段）→ 降级为面板警告继续归并，不得整套报错（否则三栏面板永不渲染）。"""
    c, archive_root = client
    legacy_err = "合同关键字段识别不完整，请补充清晰合同或人工补录后再确认费用方案。"
    monkeypatch.setattr(app_module, "analyze_contract_from_file", lambda **kw: {"error": legacy_err})
    fa = str(archive_root / "inc-a.pdf")
    fb = str(archive_root / "inc-b.pdf")
    for p in (fa, fb):
        (archive_root / p.split("/")[-1]).write_bytes(b"%PDF-1.4 fake")
    _stub_extraction(monkeypatch, {fa: TEXT_2019_PAY_AGENT, fb: TEXT_2019_TRAINING})
    tid = _make_ticket_with_set({"contracts": [
        {"kind": "", "tier": "", "file": fa, "filename": "inc-a.pdf", "sha256": "dd44", "text": "", "text_source": "", "text_confidence": ""},
        {"kind": "", "tier": "", "file": fb, "filename": "inc-b.pdf", "sha256": "ee55", "text": "", "text_source": "", "text_confidence": ""},
    ]})

    resp = c.post("/api/contract/analyze", json={
        "filepath": fa, "ticket_id": tid, "exam_stage": "已受理",
        "training_hours": {}, "total_fee": 3000, "id_card": "110101199003070011",
        "exam_counts": {"subject1": 1},
    })
    assert resp.status_code == 200
    result = resp.get_json()
    # error 必须被摘除（前端 d.error 走错误分支 → 面板不渲染）
    assert "error" not in result
    assert result["contract_count"] == 2
    assert result["contract_kinds"] == ["代缴", "培训"]
    dr = result["deductions_result"]
    assert isinstance(dr, dict) and dr["items"]
    # 降级消息进面板 warnings（cp.warnings 渲染）
    assert any("识别不完整" in w for w in dr.get("warnings", []))


def test_single_legacy_error_still_early_return(client, monkeypatch):
    """单份/下载件行为不变：legacy 报错仍早退（人工补录是设计 UX），不进上传管线。"""
    c, archive_root = client
    monkeypatch.setattr(app_module, "analyze_contract_from_file", lambda **kw: {
        "error": "合同关键字段识别不完整，请补充清晰合同或人工补录后再确认费用方案。",
    })
    fa = str(archive_root / "single-inc.pdf")
    (archive_root / "single-inc.pdf").write_bytes(b"%PDF-1.4 fake")
    _stub_extraction(monkeypatch, {fa: TEXT_2019_TRAINING})
    tid = _make_ticket_with_set({"contracts": [
        {"kind": "", "tier": "", "file": fa, "filename": "single-inc.pdf", "sha256": "ff66", "text": "", "text_source": "", "text_confidence": ""},
    ]})
    resp = c.post("/api/contract/analyze", json={
        "filepath": fa, "ticket_id": tid, "exam_stage": "已受理",
        "training_hours": {}, "total_fee": 3000, "id_card": "110101199003070011",
    })
    assert resp.status_code == 200
    result = resp.get_json()
    assert "识别不完整" in (result.get("error") or "")
    assert "deductions_result" not in result
