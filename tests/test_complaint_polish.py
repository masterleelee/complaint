"""④卡片「投诉内容/诉求」AI 润色接口 + 落库契约测试。

覆盖：/api/complaint/polish 参数校验（非法 field / 空 text / 未配置 LLM）、
mock LLM 成功返回、PUT /api/tickets/<id> 持久化 complaint_content/complaint_demands
（即前端 saveProgress 的数据契约），以及 detail 接口回读一致性。
"""
import tempfile
from pathlib import Path

import pytest
from conftest import _autologin_admin  # noqa: F401

_TMP_DIR = Path(tempfile.mkdtemp(prefix="complaint-polish-tests-"))

import database  # noqa: E402

database.DB_PATH = _TMP_DIR / "test.db"
database._local.clear()

import app as app_module  # noqa: E402


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


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


def _mock_llm(monkeypatch, payload):
    monkeypatch.setattr(
        app_module, "_llm_config",
        lambda section: {"api_url": "http://fake", "api_key": "k", "model": "m"},
    )
    monkeypatch.setattr("requests.post", lambda *a, **k: _FakeResp(200, payload))


def test_polish_invalid_field(client):
    resp = client.post("/api/complaint/polish", json={"field": "summary", "text": "内容"})
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


def test_polish_empty_text(client):
    resp = client.post("/api/complaint/polish", json={"field": "content", "text": "  "})
    assert resp.status_code == 400


def test_polish_no_llm_configured(client, monkeypatch):
    monkeypatch.setattr(
        app_module, "_llm_config",
        lambda section: {"api_url": "", "api_key": "", "model": ""},
    )
    resp = client.post("/api/complaint/polish", json={"field": "content", "text": "原文"})
    assert resp.status_code == 400
    assert "未配置" in resp.get_json()["error"]


def test_polish_content_success(client, monkeypatch):
    _mock_llm(monkeypatch, {"choices": [{"message": {"content": "润色后的投诉内容"}}]})
    resp = client.post("/api/complaint/polish", json={"field": "content", "text": "原文"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["polished"] == "润色后的投诉内容"


def test_polish_demands_success(client, monkeypatch):
    _mock_llm(monkeypatch, {"choices": [{"message": {"content": "要求尽快退费"}}]})
    resp = client.post("/api/complaint/polish", json={"field": "demands", "text": "诉求"})
    assert resp.status_code == 200
    assert resp.get_json()["data"]["polished"] == "要求尽快退费"


def test_put_ticket_persists_complaint_fields(client, fresh_db):
    """前端「保存进度」契约：PUT 携带 complaint_content/complaint_demands 应落库并被 detail 回读。"""
    ticket_id = _make_ticket(id_card="110101199003070011")
    resp = client.put(
        f"/api/tickets/{ticket_id}",
        json={
            "handling_notes": "已协调退费",
            "branch_cooperation": "好",
            "complaint_content": "2026年7月报名C1缴费3800元，申请退费被拒",
            "complaint_demands": "要求全额退费",
        },
    )
    assert resp.status_code == 200

    ticket = database.get_ticket(ticket_id)
    assert ticket["complaint_content"] == "2026年7月报名C1缴费3800元，申请退费被拒"
    assert ticket["complaint_demands"] == "要求全额退费"

    # detail 接口（openTicket 数据源）应原样回读
    detail = client.get(f"/api/tickets/{ticket_id}/detail").get_json()
    assert detail["data"]["ticket"]["complaint_content"] == ticket["complaint_content"]
    assert detail["data"]["ticket"]["complaint_demands"] == ticket["complaint_demands"]
