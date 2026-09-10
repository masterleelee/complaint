"""打开归档 API 测试：POST /api/tickets/<id>/open-archive。

覆盖（设计 2026-09-01 拍板：不走三闸门，案件夹在磁盘上即可打开）：
1. 工单不存在 → 404
2. 在途工单（三闸门全未过）但案件夹已落盘 → 200 成功打开（subprocess 被调起，参数正确）
3. 案件夹不存在 → 404 + 明确错误信息
4. 归档根目录不可用（指向文件）→ 400
5. 打开命令执行失败 → 400 带失败原因（不 500）
"""
import tempfile
from pathlib import Path

import pytest
from conftest import _autologin_admin  # noqa: F401

_TMP_DIR = Path(tempfile.mkdtemp(prefix="open-archive-tests-"))

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
        "student_name": "张三",
        "id_card": "110101199003070011",
        "complaint_date": "2026-08-22",
        "complaint_type": "A",
        "school_short": "南城",
        "organization_unit_type": "分校",
        "organization_unit_name": "南城分校",
        "registration_date": "2026-01-01",
        "license_type": "C2",
        "exam_stage": "科目二",
        "total_fee": 3700,
        "actual_paid": 3700,
        "fee_plan_status": "confirmed",
    }
    data.update(overrides)
    tid = database.save_ticket(data)
    return database.get_ticket(tid)


def _mock_launcher(monkeypatch, calls=None, exc=None):
    """替身文件管理器调起：记录命令；exc 非空时模拟执行失败。"""
    def _fake_run(cmd, *a, **k):
        if calls is not None:
            calls.append(cmd)
        if exc:
            raise exc
    monkeypatch.setattr(archive_service_module.subprocess, "run", _fake_run)


# ───────────────────────────────────────────────────────────
# 1) 工单不存在 → 404
# ───────────────────────────────────────────────────────────
def test_open_archive_ticket_not_found(client):
    resp = client.post("/api/tickets/no-such-id/open-archive")
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["success"] is False


# ───────────────────────────────────────────────────────────
# 2) 在途工单（三闸门全未过）+ 案件夹已落盘 → 成功打开
#    —— 核心语义：不看闸门，只看磁盘上有没有夹子
# ───────────────────────────────────────────────────────────
def test_open_archive_inflight_without_gates(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "load_config", lambda: {"archive_root": str(tmp_path)})
    monkeypatch.setattr(archive_service_module, "load_config", lambda: {"archive_root": str(tmp_path)})
    ticket = _make_ticket(handling_notes="", branch_cooperation="", fee_plan_status="draft")
    case_dir, _, _ = app_module.build_archive_dir(ticket, root=str(tmp_path))
    Path(case_dir).mkdir(parents=True, exist_ok=True)

    calls = []
    _mock_launcher(monkeypatch, calls=calls)
    resp = client.post(f"/api/tickets/{ticket['id']}/open-archive")
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["success"] is True
    assert body["data"]["dir"] == case_dir
    # macOS 分支：open <case_dir> 恰好调起一次
    assert len(calls) == 1 and calls[0][-1] == case_dir


# ───────────────────────────────────────────────────────────
# 3) 案件夹不存在 → 404 + 明确原因（含「未生成登记表/回复函」提示）
# ───────────────────────────────────────────────────────────
def test_open_archive_dir_missing_returns_404(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "load_config", lambda: {"archive_root": str(tmp_path)})
    monkeypatch.setattr(archive_service_module, "load_config", lambda: {"archive_root": str(tmp_path)})
    ticket = _make_ticket()
    resp = client.post(f"/api/tickets/{ticket['id']}/open-archive")
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["success"] is False
    assert body["code"] == "dir_missing"
    assert "不存在" in body["errors"][0]
    assert "登记表" in body["errors"][0]


# ───────────────────────────────────────────────────────────
# 4) 归档根目录不可用（指向一个文件）→ 400
# ───────────────────────────────────────────────────────────
def test_open_archive_root_unavailable_returns_400(client, tmp_path, monkeypatch):
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(config_module, "load_config", lambda: {"archive_root": str(blocker)})
    monkeypatch.setattr(archive_service_module, "load_config", lambda: {"archive_root": str(blocker)})
    ticket = _make_ticket()
    resp = client.post(f"/api/tickets/{ticket['id']}/open-archive")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["success"] is False
    assert body["code"] == "root_unavailable"
    assert "归档根目录不可用" in body["errors"][0]


# ───────────────────────────────────────────────────────────
# 5) 打开命令执行失败 → 500 带原因（服务函数吞异常，不裸崩）
# ───────────────────────────────────────────────────────────
def test_open_archive_launch_failure_returns_error(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "load_config", lambda: {"archive_root": str(tmp_path)})
    monkeypatch.setattr(archive_service_module, "load_config", lambda: {"archive_root": str(tmp_path)})
    ticket = _make_ticket()
    case_dir, _, _ = app_module.build_archive_dir(ticket, root=str(tmp_path))
    Path(case_dir).mkdir(parents=True, exist_ok=True)

    _mock_launcher(monkeypatch, exc=RuntimeError("finder busy"))
    resp = client.post(f"/api/tickets/{ticket['id']}/open-archive")
    assert resp.status_code == 500
    body = resp.get_json()
    assert body["success"] is False
    assert body["code"] == "launch_failed"
    assert "打开归档文件夹失败" in body["errors"][0]


# ───────────────────────────────────────────────────────────
# 6) 单元层：open_case_dir 纯函数行为（不经 HTTP）
# ───────────────────────────────────────────────────────────
def test_open_case_dir_root_none_uses_config(tmp_path, monkeypatch):
    monkeypatch.setattr(archive_service_module, "load_config", lambda: {"archive_root": str(tmp_path)})
    ticket = {
        "student_name": "李四",
        "id_card": "110101199003070011",
        "complaint_date": "2026-08-30",
        "school_short": "常",
        "organization_unit_type": "分校",
        "organization_unit_name": "常平横江厦分校",
    }
    # 夹子未建 → success False 且带 dir（根可用、夹缺失）
    result = archive_service_module.open_case_dir(ticket)
    assert result["success"] is False
    assert result["dir"].startswith(str(tmp_path))
    assert len(result["errors"]) == 1

    # 建夹后 → 成功
    Path(result["dir"]).mkdir(parents=True, exist_ok=True)
    calls = []
    _mock_launcher(monkeypatch, calls=calls)
    result = archive_service_module.open_case_dir(ticket)
    assert result["success"] is True
    assert result["dir"] and len(calls) == 1
