"""未结案并入 · 复用既有工单查询档案（不重查三系统）测试。

覆盖：
1. 正常并入：快照原样复用（query_result 未变）、merged_into_existing=True、
   新投诉内容/渠道/处理人写入既有工单
2. 跨日并入：既有工单 8/22 仍在处理中，今天 8/29 受理可并入（不再要求日期一致）
3. 证件号不一致 → 400 拒绝并入
4. 已完结工单 → 400 拒绝并入（防止污染历史档案）
5. 已撤诉工单 → 400 拒绝并入
6. 已归档工单 → 400 拒绝并入
7. 快照缺失（无 query_result 或无姓名）→ 400 提示重查
8. 工单不存在 → 404
9. 缺 ticket_id → 400
10. same-day-check 跨日命中：8/22 的未结案工单被预检接口返回
"""
import tempfile
from pathlib import Path

import pytest
from conftest import _autologin_admin  # noqa: F401

_TMP_DIR = Path(tempfile.mkdtemp(prefix="merge-existing-tests-"))

import database  # noqa: E402

database.DB_PATH = _TMP_DIR / "test.db"
database._local.clear()

import app as app_module  # noqa: E402

VALID_ID = "110101199003070011"

_SNAPSHOT = {
    "name": "陈志明",
    "id_card": VALID_ID,
    "phone": "13800000000",
    "school_name": "(青C)",
    "school_short": "青C",
    "license_type": "C1",
    "registration_date": "2026-07-15",
    "exam_stage": "科目二",
    "student_status": "培训中",
    "training_hours": {"km1": 12, "km2": 8},
    "sources": {"internal": "success", "third": "success", "driving": "success"},
    "query_durations_ms": {"internal": 120, "third": 340, "driving": 210},
}


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
        try:
            _autologin_admin(c)
        except Exception:
            pass
        yield c


def _seed_ticket(**overrides) -> str:
    """造一张带有效查询快照的既有工单，返回 id。force_new=True 跳过同日去重，
    保证每条 fixture 独立成条、不被前序测试污染。"""
    data = {
        "student_name": "陈志明",
        "id_card": VALID_ID,
        "phone": "13800000000",
        "complaint_content": "第一次投诉内容",
        "source_channel": "12345热线",
        "complaint_date": "2026-08-28",
        "complaint_type": "A",
        "query_result": dict(_SNAPSHOT),
    }
    data.update(overrides)
    return database.save_ticket(data, force_new=True)


def _payload(**overrides):
    data = {
        "ticket_id": _TID,
        "id_card": VALID_ID,
        "complaint_date": "2026-08-28",
        "complaint_type": "B",
        "source_channel": "现场投诉",
        "handler_name": "测试处理人",
        "complaint_desc": "第二次投诉：约考被拖延",
        "complaint_summary": "",
        "complaint_demands": "",
        "attachments": [],
    }
    data.update(overrides)
    return data


_TID = ""


# ── 1) 正常并入：快照复用 + 新投诉内容写入 ──
def test_merge_reuses_snapshot(client):
    global _TID
    _TID = _seed_ticket()
    resp = client.post("/api/tickets/merge-existing", json=_payload())
    assert resp.status_code == 200
    d = resp.get_json()
    assert d["success"] is True
    result = d["data"]
    # 快照原样复用（含学员信息与 sources），并带并入标记
    assert result["merged_into_existing"] is True
    assert result["ticket_id"] == _TID
    assert result["name"] == "陈志明"
    assert result["sources"] == _SNAPSHOT["sources"]
    # 落库校验：新内容写入、query_result 未被改写
    t = database.get_ticket(_TID)
    assert t["complaint_content"] == "第二次投诉：约考被拖延"
    assert t["source_channel"] == "现场投诉"
    assert t["complaint_type"] == "B"
    assert t["handler_name"] == "测试处理人"
    assert t["query_result"]["exam_stage"] == "科目二"
    assert t["query_result"] == _SNAPSHOT


# ── 2) 跨日并入：不再要求投诉日期一致（投诉处理是长期过程） ──
def test_merge_cross_date_reuses_oldest_open(client):
    """8/22 的未结案工单，今天 8/29 再次受理可直接并入，跨日放行。"""
    global _TID
    # 既有工单 8/22，处理中（默认建出来就是「待处理」）
    _TID = _seed_ticket(complaint_date="2026-08-22", handle_status="处理中")
    # 本次投诉日期是 8/29，应该能并入
    resp = client.post("/api/tickets/merge-existing", json=_payload(complaint_date="2026-08-29"))
    assert resp.status_code == 200
    d = resp.get_json()
    assert d["success"] is True
    assert d["data"]["merged_into_existing"] is True
    assert d["data"]["ticket_id"] == _TID
    # 既有工单的投诉日期保持 8/22（不被本次日期覆盖）
    t = database.get_ticket(_TID)
    assert t["complaint_date"] == "2026-08-22"
    # 新投诉内容已写入
    assert t["complaint_content"] == "第二次投诉：约考被拖延"


# ── 3) 证件号不一致 → 400 ──
def test_reject_id_card_mismatch(client):
    resp = client.post("/api/tickets/merge-existing", json=_payload(id_card="110101199003070011"))
    assert resp.status_code == 400
    assert "证件号" in resp.get_json()["error"]


# ── 4) 已完结工单 → 400（防止污染历史档案） ──
def test_reject_completed_ticket(client):
    tid = _seed_ticket(handle_status="已完结")
    resp = client.post("/api/tickets/merge-existing", json=_payload(ticket_id=tid))
    assert resp.status_code == 400
    assert "已完结" in resp.get_json()["error"]


# ── 5) 已撤诉工单 → 400 ──
def test_reject_withdrawn_ticket(client):
    tid = _seed_ticket(withdraw_status="已撤诉")
    resp = client.post("/api/tickets/merge-existing", json=_payload(ticket_id=tid))
    assert resp.status_code == 400
    assert "已撤诉" in resp.get_json()["error"]


# ── 6) 已归档工单 → 400 ──
def test_reject_archived_ticket(client):
    tid = _seed_ticket(archive_status="已归档")
    resp = client.post("/api/tickets/merge-existing", json=_payload(ticket_id=tid))
    assert resp.status_code == 400
    assert "已归档" in resp.get_json()["error"]


# ── 7) 快照缺失 → 400 提示重查 ──
def test_reject_missing_snapshot(client):
    tid = _seed_ticket(query_result="")
    resp = client.post("/api/tickets/merge-existing", json=_payload(ticket_id=tid))
    assert resp.status_code == 400
    assert "重新" in resp.get_json()["error"]


# ── 8) 工单不存在 → 404 ──
def test_reject_unknown_ticket(client):
    resp = client.post("/api/tickets/merge-existing", json=_payload(ticket_id="no-such-id"))
    assert resp.status_code == 404


# ── 9) 缺 ticket_id → 400 ──
def test_reject_missing_ticket_id(client):
    resp = client.post("/api/tickets/merge-existing", json=_payload(ticket_id=""))
    assert resp.status_code == 400


# ── 10) same-day-check 跨日命中未结案工单 ──
def test_same_day_check_hits_open_ticket_across_dates(client):
    """8/22 处理中的工单，8/29 受理时 same-day-check 应返回该工单。"""
    # 用独立身份证，避免被本文件其它 fixture 抢先命中
    other_id = "110101199003070011"
    tid = database.save_ticket({
        "student_name": "测试跨日命中",
        "id_card": other_id,
        "phone": "13800000000",
        "complaint_content": "x",
        "source_channel": "12345",
        "complaint_date": "2026-08-22",
        "complaint_type": "A",
        "handle_status": "处理中",
        "query_result": {"name": "测试跨日命中"},
    }, force_new=True)
    resp = client.post("/api/tickets/same-day-check", json={
        "id_card": other_id,
        "complaint_date": "2026-08-29",
    })
    assert resp.status_code == 200
    d = resp.get_json()
    assert d["success"] is True
    assert d["data"] is not None
    assert d["data"]["id"] == tid
    assert d["data"]["handle_status"] == "处理中"


# ── 11) same-day-check 不命中已完结工单 ──
def test_same_day_check_skips_completed_ticket(client):
    """已完结工单不应触发并入预检，避免历史档案被污染。"""
    other_id = "110101199003070011"
    database.save_ticket({
        "student_name": "测试已完结",
        "id_card": other_id,
        "phone": "13800000000",
        "complaint_content": "x",
        "source_channel": "12345",
        "complaint_date": "2026-08-22",
        "complaint_type": "A",
        "handle_status": "已完结",
        "query_result": {"name": "测试已完结"},
    }, force_new=True)
    resp = client.post("/api/tickets/same-day-check", json={
        "id_card": other_id,
        "complaint_date": "2026-08-29",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body.get("data") is None
