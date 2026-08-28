"""正式文书权威性与生命周期测试：
1. 回复函金额取数据库已确认快照（请求体金额不一致时仅告警）
2. 扣费行 amt/amount 与 basis/reason 双键兼容
3. 已归档工单撤诉 → 归档夹出现撤诉说明.txt 且登记表被刷新
4. 归档成功后 archived_dir 落库非空
5. 归档前登记表过期自动重生成，失败沿用旧文件并告警
6. fee-unlock 对已归档案件同步重新打开
"""
import tempfile
import time
from pathlib import Path

import pytest
from conftest import _autologin_admin  # noqa: F401
from docx import Document

_TMP_DIR = Path(tempfile.mkdtemp(prefix="doc-authority-tests-"))

import database  # noqa: E402

database.DB_PATH = _TMP_DIR / "test.db"
database._local.clear()

import app as app_module  # noqa: E402
import config as config_module  # noqa: E402
import services.archive_service as archive_service_module  # noqa: E402


@pytest.fixture()
def fresh_db():
    database._local.clear()
    database.init_db()
    yield database
    database._local.clear()


@pytest.fixture()
def client(fresh_db):
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        try: _autologin_admin(c)
        except Exception: pass
        yield c


def _make_ticket(**overrides):
    data = {
        "student_name": "李四",
        "id_card": "110101199003070011",
        "complaint_date": "2026-08-24",
        "complaint_type": "A",
        "school_short": "南城",
        "organization_unit_type": "分校",
        "organization_unit_name": "南城分校",
        "registration_date": "2026-02-01",
        "license_type": "C1",
        "exam_stage": "科目二",
        "total_fee": 3700,
        "actual_paid": 3700,
        "deduction_fee": 1700,
        "refund_fee": 2000,
        "deduction_detail": (
            '[{"item":"综合服务费扣除","amount":1100,"basis":"合同第九条"},'
            '{"item":"理论培训费扣除","amount":600,"basis":"合同第九条"}]'
        ),
        "fee_plan_status": "confirmed",
    }
    data.update(overrides)
    tid = database.save_ticket(data)
    return database.get_ticket(tid)


def _doc_text(path) -> str:
    doc = Document(str(path))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        seen = set()
        for row in table.rows:
            for cell in row.cells:
                if cell._tc in seen:
                    continue
                seen.add(cell._tc)
                parts.append(cell.text)
    return "\n".join(parts)


def _archive_patches(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "load_config", lambda: {"archive_root": str(tmp_path)})
    monkeypatch.setattr(archive_service_module, "load_config", lambda: {"archive_root": str(tmp_path)})


# ───────────────────────────────────────────────────────────
# 1) 回复函金额取快照，请求体金额仅触发告警
# ───────────────────────────────────────────────────────────
def test_reply_generate_uses_snapshot_amounts(client, fresh_db, tmp_path, monkeypatch):
    def _fake_build_dir(ticket, root=None):
        d = tmp_path
        return str(d), str(d / "投诉登记表.docx"), str(d / "投诉回复函.docx")
    monkeypatch.setattr(app_module, "build_archive_dir", _fake_build_dir)
    ticket = _make_ticket()

    resp = client.post("/api/reply/generate", json={
        "ticket_id": ticket["id"],
        "deductions": [{"item": "伪造扣费行", "amt": 999, "basis": "伪造依据"}],
        "refund": 3001,
    })
    body = resp.get_json()
    assert body["success"] is True
    assert body["warning"] == "以系统确认的费用明细为准"

    text = _doc_text(body["filepath"])
    assert "总扣费：1700元" in text
    assert "应退回：2000元" in text
    assert "伪造扣费行" not in text
    assert "伪造依据" not in text


# ───────────────────────────────────────────────────────────
# 2) amt/amount、basis/reason 双键兼容
# ───────────────────────────────────────────────────────────
def test_reply_docx_dual_key_compat(tmp_path):
    from services.reply_docx import generate_reply_docx
    out = tmp_path / "reply.docx"
    result = generate_reply_docx(
        ticket={"student_name": "王五", "actual_paid": 1000},
        deductions=[
            {"item": "科目一培训费", "amount": 300, "basis": "合同第九条"},
            {"item": "科目二实车费", "amt": 450, "reason": "中途退学"},
            {"item": "零金额行", "amount": 0, "basis": "应跳过"},
        ],
        output_path=str(out),
    )
    assert result["success"] is True
    text = _doc_text(out)
    assert "科目一培训费（合同第九条）：300元" in text
    assert "科目二实车费（中途退学）：450元" in text
    assert "总扣费：750元" in text
    assert "应退回：250元" in text
    assert "零金额行" not in text


# ───────────────────────────────────────────────────────────
# 3) 已归档撤诉 → 撤诉说明.txt + 登记表刷新
# ───────────────────────────────────────────────────────────
def test_withdraw_archived_case_writes_note_and_refreshes_form(client, fresh_db, tmp_path, monkeypatch):
    _archive_patches(monkeypatch, tmp_path)
    ticket = _make_ticket(handling_notes="已协调网点退费2000元", branch_cooperation="配合")

    reg_src = tmp_path / "登记表_src.docx"
    time.sleep(0.01)
    Document().save(str(reg_src))
    database.update_ticket(ticket["id"], {"registration_form_path": str(reg_src)})

    resp = client.post(f"/api/tickets/{ticket['id']}/archive")
    body = resp.get_json()
    assert body["success"] is True
    case_dir = Path(body["dir"])
    reg_target = case_dir / "投诉登记表.docx"
    assert reg_target.is_file()
    assert "已协调网点退费2000元" not in _doc_text(reg_target)

    wresp = client.put(f"/api/tickets/{ticket['id']}/withdraw", json={"reason": "已协商解决"})
    wbody = wresp.get_json()
    assert wbody["success"] is True
    assert wbody["data"]["warnings"] == []

    note = case_dir / "2026-08-24_李四_撤诉说明.txt"
    assert note.is_file()
    content = note.read_text(encoding="utf-8")
    assert "学员姓名：李四" in content
    assert "441900********0022" in content
    assert "投诉日期：2026-08-24" in content
    assert wbody["data"]["withdrawn_at"] in content
    assert "撤诉原因：已协商解决" in content
    assert "经办说明：" in content

    assert "已协调网点退费2000元" in _doc_text(reg_target)


# ───────────────────────────────────────────────────────────
# 4) 归档成功后 archived_dir 非空且等于归档目录
# ───────────────────────────────────────────────────────────
def test_archive_persists_archived_dir(client, fresh_db, tmp_path, monkeypatch):
    _archive_patches(monkeypatch, tmp_path)
    ticket = _make_ticket(handling_notes="已协调网点退费", branch_cooperation="配合")
    reg_src = tmp_path / "reg_src.docx"
    reply_src = tmp_path / "reply_src.docx"
    time.sleep(0.01)
    Document().save(str(reg_src))
    Document().save(str(reply_src))
    database.update_ticket(ticket["id"], {
        "registration_form_path": str(reg_src),
        "reply_path": str(reply_src),
    })

    resp = client.post(f"/api/tickets/{ticket['id']}/archive")
    body = resp.get_json()
    assert body["success"] is True

    fresh = database.get_ticket(ticket["id"])
    assert fresh["archived_dir"]
    assert fresh["archived_dir"] == body["dir"]
    assert Path(fresh["archived_dir"]).is_dir()


# ───────────────────────────────────────────────────────────
# 5) 登记表缺失时归档先重生成；失败沿用旧文件并告警
# ───────────────────────────────────────────────────────────
def test_archive_regen_failure_falls_back_with_warning(client, fresh_db, tmp_path, monkeypatch):
    _archive_patches(monkeypatch, tmp_path)
    monkeypatch.setattr(
        app_module, "_regen_registration_form",
        lambda t, output_dir="": {"success": False, "error": "boom"},
    )
    ticket = _make_ticket(handling_notes="已协调网点退费", branch_cooperation="配合")
    reply_src = tmp_path / "reply_src.docx"
    Document().save(str(reply_src))
    database.update_ticket(ticket["id"], {
        "registration_form_path": str(tmp_path / "不存在.docx"),
        "reply_path": str(reply_src),
    })

    resp = client.post(f"/api/tickets/{ticket['id']}/archive")
    body = resp.get_json()
    assert body["success"] is True
    assert "登记表重新生成失败" in body.get("warning", "")


# ───────────────────────────────────────────────────────────
# 6) fee-unlock 已归档矛盾态 → 同步重新打开案件
# ───────────────────────────────────────────────────────────
def test_fee_unlock_reopens_archived_case(client, fresh_db, tmp_path, monkeypatch):
    _archive_patches(monkeypatch, tmp_path)
    ticket = _make_ticket(handling_notes="已协调网点退费", branch_cooperation="配合")
    database.update_ticket(ticket["id"], {
        "archive_status": "已归档",
        "handle_status": "已完结",
        "completed_at": "2026-08-24 10:00:00",
    })

    resp = client.post(f"/api/tickets/{ticket['id']}/fee-unlock")
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["case_reopened"] is True

    fresh = database.get_ticket(ticket["id"])
    assert fresh["fee_plan_status"] == "draft"
    assert fresh["archive_status"] == "未归档"
    assert fresh["handle_status"] == "处理中"
    assert fresh["completed_at"] == ""
