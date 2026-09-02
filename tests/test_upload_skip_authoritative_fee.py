"""上传路径跳过东莞驾培金额覆盖（apply_authoritative_total_fee）测试。

背景：`_run_contract_analysis` 里东莞驾培 contract_fee 覆盖（apply_authoritative_total_fee）
此前不区分上传/下载路径——只要工单快照有 contract_fee>0 就覆盖。但「上传合同」本身即说明
东莞驾培无对应电子合同，残留的 contract_fee 快照会错误覆盖纸质合同 OCR 金额，造成
total_fee（东莞驾培金额）与扣费明细（上传合同算的）不一致。

修复：上传路径（is_upload_ticket=True）跳过 apply_authoritative_total_fee；下载路径保持覆盖。

覆盖：
1. 上传路径（contract_set 含 sha256）+ 工单快照 contract_fee=5000 → total_fee 不被覆盖（保持 legacy 3000）。
2. 下载路径（contract_set 无 sha256）+ contract_fee=5000 → total_fee 被覆盖成 5000。
"""
import json

import pytest

import database
import app as app_module
import services.upload_pipeline as upload_pipeline_module
from conftest import _autologin_admin  # noqa: F401

from test_multi_contract import TEXT_2019_TRAINING


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
    """legacy 分析打桩：total_fee=3000（区别于工单快照 contract_fee=5000，便于断言覆盖与否）。"""
    monkeypatch.setattr(app_module, "analyze_contract_from_file", lambda **kw: {
        "total_fee": 3000, "actual_paid": 3000, "deductions": [],
        "total_deduction": 0, "refund": 3000,
    })


def _stub_extraction(monkeypatch, text):
    monkeypatch.setattr(
        upload_pipeline_module,
        "extract_contract_text_from_file",
        lambda filepath, image_paths=None: {"text": text, "source": "pdf_text", "can_confirm_fee_plan": True},
    )


def _make_ticket(ticket_set, contract_fee):
    """建工单：query_result 快照带东莞驾培 contract_fee（模拟「查到金额但走上传」的边界）。"""
    tid = database.save_ticket({
        "student_name": "李四",
        "id_card": "110101199003070011",
        "complaint_date": "2026-09-01",
        "complaint_type": "A",
        "school_short": "沙田",
        "organization_unit_type": "分店",
        "organization_unit_name": "沙田分店",
        "registration_date": "2023-05-01",
        "license_type": "C1",
        "exam_stage": "已受理",
        "handling_notes": "已沟通",
        "branch_cooperation": "好",
        "fee_plan_status": "draft",
        "total_fee": 3000,
        "query_result": {"driving_fee": {"contract_fee": contract_fee}},
    })
    database.update_ticket(tid, {"contract_set": json.dumps(ticket_set, ensure_ascii=False)})
    return tid


def _analyze(c, archive_root, fa, tid):
    resp = c.post("/api/contract/analyze", json={
        "filepath": fa, "ticket_id": tid, "exam_stage": "已受理",
        "training_hours": {}, "total_fee": 3000, "id_card": "110101199003070011",
    })
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def test_upload_path_skips_authoritative_fee(client, monkeypatch):
    """上传路径（含 sha256）+ 快照 contract_fee=5000 → total_fee 不被覆盖（保持 legacy 3000）。"""
    c, archive_root = client
    _stub_legacy(monkeypatch)
    fa = str(archive_root / "up.pdf")
    (archive_root / "up.pdf").write_bytes(b"%PDF-1.4 fake")
    _stub_extraction(monkeypatch, TEXT_2019_TRAINING)
    tid = _make_ticket({"contracts": [
        {"kind": "", "tier": "", "file": fa, "filename": "up.pdf", "sha256": "aa11",
         "text": "", "text_source": "", "text_confidence": ""},
    ]}, contract_fee=5000)

    result = _analyze(c, archive_root, fa, tid)
    # 上传路径：东莞驾培 5000 不得覆盖上传合同分析结果
    assert result["total_fee"] == 3000


def test_download_path_applies_authoritative_fee(client, monkeypatch):
    """下载路径（无 sha256）+ 快照 contract_fee=5000 → total_fee 被覆盖成 5000。"""
    c, archive_root = client
    _stub_legacy(monkeypatch)
    fa = str(archive_root / "dl.pdf")
    (archive_root / "dl.pdf").write_bytes(b"%PDF-1.4 fake")
    tid = _make_ticket({"contracts": [
        {"kind": "", "tier": "", "file": fa, "filename": "dl.pdf",
         "text": "", "text_source": "", "text_confidence": ""},
    ]}, contract_fee=5000)

    result = _analyze(c, archive_root, fa, tid)
    # 下载路径：东莞驾培金额仍是权威，覆盖 legacy 的 3000
    assert result["total_fee"] == 5000
