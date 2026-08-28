"""v2 工作台改造后端行为测试（Wave2 / 实施Agent C）。

覆盖：communications→handling_notes 迁移幂等、已归档案件可撤诉、
统计口径（撤诉不计有效投诉）、费用解锁后可再次确认、归档三闸门。
"""
import tempfile
from pathlib import Path

import pytest
from conftest import _autologin_admin  # noqa: F401

# 必须在导入 app 之前把数据库指向临时文件，避免测试触碰真实数据
_TMP_DIR = Path(tempfile.mkdtemp(prefix="v2-workbench-tests-"))

import database  # noqa: E402

database.DB_PATH = _TMP_DIR / "test.db"
database._local.clear()

import app as app_module  # noqa: E402


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
        "student_name": "张三",
        "id_card": "110101199003070011",
        "complaint_date": "2026-05-01",
        "complaint_type": "A",
        "school_short": "南城",
    }
    data.update(overrides)
    return database.save_ticket(data)


def test_communications_migration_idempotent(fresh_db):
    ticket_id = _make_ticket(id_card="110101199003070011")
    # 模拟存量旧库：手动重建已废弃的沟通记录表再灌数据
    with database.get_db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS communication_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT NOT NULL,
                fee_plan_version INTEGER NOT NULL DEFAULT 0,
                contact_time TEXT NOT NULL DEFAULT '',
                contact_method TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                student_intention TEXT NOT NULL DEFAULT '',
                next_follow_up TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT ''
            )"""
        )
    database.save_communication_record(
        ticket_id, contact_time="2026-05-01 10:00:00", summary="第一次电话沟通"
    )
    database.save_communication_record(
        ticket_id, contact_time="2026-05-02 15:30:00", summary="第二次到校协商"
    )

    assert database.migrate_communications_to_notes() == 1
    ticket = database.get_ticket(ticket_id)
    assert ticket["handling_notes"] == (
        "[2026-05-01] 第一次电话沟通；\n[2026-05-02] 第二次到校协商；"
    )
    # 迁移完成后源表应被物理删除
    with database.get_db() as conn:
        gone = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='communication_records'"
        ).fetchone()
    assert gone is None

    # 幂等：已打标记，重复执行返回 0 且内容不变（源表不存在也不报错）
    assert database.migrate_communications_to_notes() == 0
    assert database.get_ticket(ticket_id)["handling_notes"] == ticket["handling_notes"]


def test_withdraw_allowed_on_archived_case(client, fresh_db):
    ticket_id = _make_ticket(
        id_card="110101199003070011", archive_status="已归档", handle_status="已完结"
    )
    resp = client.put(f"/api/tickets/{ticket_id}/withdraw", json={"reason": "学员主动撤回"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["withdraw_status"] == "已撤诉"
    assert body["data"]["withdraw_reason"] == "学员主动撤回"

    ticket = database.get_ticket(ticket_id)
    assert ticket["withdrawn_at"]
    assert ticket["withdraw_reason"] == "学员主动撤回"
    assert ticket["archive_status"] == "已归档"


def test_withdraw_status_toggle_restores_active(client, fresh_db):
    """前端「取消撤诉」入口依赖 withdraw-status 契约：恢复未撤诉时清空 withdrawn_at、保留撤诉原因。"""
    ticket_id = _make_ticket(id_card="110101199003070011")
    marked = client.put(f"/api/tickets/{ticket_id}/withdraw", json={"reason": "和解撤回"})
    assert marked.status_code == 200

    resp = client.put(
        f"/api/tickets/{ticket_id}/withdraw-status", json={"withdraw_status": "未撤诉"}
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["withdraw_status"] == "未撤诉"

    ticket = database.get_ticket(ticket_id)
    assert ticket["withdraw_status"] == "未撤诉"
    assert ticket["withdrawn_at"] == ""
    # 撤诉原因不被重写
    assert ticket["withdraw_reason"] == "和解撤回"


def test_statistics_excludes_withdrawn_from_effective(fresh_db):
    before = database.get_ticket_statistics()
    active_id = _make_ticket(id_card="110101199003070011", complaint_type="A")
    withdrawn_id = _make_ticket(id_card="110101199003070011", complaint_type="B")
    database.update_ticket(withdrawn_id, {
        "withdrawn_at": "2026-08-22 10:00:00",
        "withdraw_reason": "和解撤回",
        "withdraw_status": "已撤诉",
    })

    stats = database.get_ticket_statistics()
    assert stats["total"] == before["total"] + 2
    assert stats["withdrawn_total"] == before["withdrawn_total"] + 1
    assert stats["effective_total"] == before["effective_total"] + 1
    assert stats["cancelled_total"] == stats["withdrawn_total"]

    by_type = {item["complaint_type"]: item["count"] for item in stats["by_complaint_type"]}
    assert by_type["A"] >= 1 and by_type["B"] >= 1

    # 时长与车辆数配置仍保留
    for key in ("avg_processing_hours", "overdue_count"):
        assert key in database.get_processing_duration_stats()
    assert database.get_org_vehicle_counts() != {}


def test_fee_unlock_then_reconfirm(client, fresh_db):
    ticket_id = _make_ticket(id_card="110101199003070011")
    payload = {
        "deductions": [{"item": "综合服务费", "amount": 1100}],
        "total_fee": 3880,
        "actual_paid": 3000,
        "confirmed_by": "tester",
    }
    first = client.post(f"/api/tickets/{ticket_id}/fee-confirm", json=payload)
    assert first.status_code == 200
    data = first.get_json()["data"]
    assert data["fee_plan_status"] == "confirmed"
    assert data["total_deduction"] == 1100
    assert data["refund"] == 1900

    # v2：不再区分三态/不可变快照，重复确认无需修改原因
    again = client.post(f"/api/tickets/{ticket_id}/fee-confirm", json=payload)
    assert again.status_code == 200

    unlocked = client.post(f"/api/tickets/{ticket_id}/fee-unlock")
    assert unlocked.status_code == 200
    assert unlocked.get_json()["data"]["fee_plan_status"] == "draft"

    reconfirmed = client.post(
        f"/api/tickets/{ticket_id}/fee-confirm", json={**payload, "actual_paid": 2800}
    )
    assert reconfirmed.status_code == 200
    ticket = database.get_ticket(ticket_id)
    assert ticket["fee_plan_status"] == "confirmed"
    assert ticket["actual_paid"] == 2800
    assert ticket["refund_fee"] == 1700


def test_archive_gate_three_conditions(fresh_db):
    from app import _completion_gate_error

    ticket_id = _make_ticket(id_card="110101199003070011")
    incoming = {"archive_status": "已归档"}

    ticket = database.get_ticket(ticket_id)
    assert "处理情况" in _completion_gate_error(ticket, incoming)

    database.update_ticket(ticket_id, {"handling_notes": "已协调网点退费"})
    ticket = database.get_ticket(ticket_id)
    assert "配合度" in _completion_gate_error(ticket, incoming)

    database.update_ticket(ticket_id, {"branch_cooperation": "配合"})
    ticket = database.get_ticket(ticket_id)
    assert "费用明细" in _completion_gate_error(ticket, incoming)

    database.update_ticket(ticket_id, {"fee_plan_status": "confirmed"})
    ticket = database.get_ticket(ticket_id)
    assert _completion_gate_error(ticket, incoming) == ""


def test_fee_confirm_no_fee_basis_unblocks_archive(client, fresh_db):
    """三系统查无记录案件：零口径确认豁免费用闸门，归档三闸门可通过。"""
    from services.archive_service import archive_gate_errors

    ticket_id = _make_ticket(id_card="110101199003070011")

    resp = client.post(
        f"/api/tickets/{ticket_id}/fee-confirm",
        json={"no_fee_basis": True, "confirmed_by": "tester"},
    )
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["fee_plan_status"] == "confirmed"
    assert data["total_fee"] == 0 and data["refund"] == 0

    ticket = database.get_ticket(ticket_id)
    assert ticket["fee_plan_status"] == "confirmed"
    assert ticket["fee_confirm_note"] == "三系统查无记录，无费用明细"
    assert ticket["deduction_fee"] == 0 and ticket["refund_fee"] == 0

    # 费用闸门通过后，补齐另两闸门即可归档
    database.update_ticket(ticket_id, {
        "handling_notes": "三系统查无该学员记录，已电话答复并留存材料",
        "branch_cooperation": "好",
    })
    ticket = database.get_ticket(ticket_id)
    assert archive_gate_errors(ticket) == []
