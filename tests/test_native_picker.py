"""原生文件夹选择对话框 API 测试：/api/fs/native-picker。

覆盖：
1. osascript 成功 → 返回所选绝对路径（去掉尾部斜杠）
2. 用户取消（stderr 含 -128）→ cancelled=True
3. 非 macOS 环境 → 501 错误
"""
import subprocess
import tempfile
import types
from pathlib import Path

import pytest
from conftest import _autologin_admin  # noqa: F401

_TMP_DIR = Path(tempfile.mkdtemp(prefix="native-picker-tests-"))

import database  # noqa: E402

database.DB_PATH = _TMP_DIR / "test.db"
database._local.clear()

import app as app_module  # noqa: E402


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        try: _autologin_admin(c)
        except Exception: pass
        yield c


class _Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_native_picker_success(client, monkeypatch):
    monkeypatch.setattr(
        app_module.subprocess,
        "run",
        lambda *a, **k: _Proc(stdout="/Users/master/Desktop/投诉处理系统/\n"),
    )
    resp = client.post("/api/fs/native-picker", json={"start": "/Users/master/Desktop"})
    assert resp.status_code == 200
    assert resp.get_json() == {"success": True, "data": {"path": "/Users/master/Desktop/投诉处理系统"}}


def test_native_picker_user_cancel(client, monkeypatch):
    monkeypatch.setattr(
        app_module.subprocess,
        "run",
        lambda *a, **k: _Proc(stderr="execution error: User canceled. (-128)", returncode=1),
    )
    resp = client.post("/api/fs/native-picker", json={})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is False and body.get("cancelled") is True


def test_native_picker_unsupported_platform(client, monkeypatch):
    fake_sys = types.SimpleNamespace(platform="linux")
    monkeypatch.setattr(app_module, "sys", fake_sys)
    resp = client.post("/api/fs/native-picker", json={})
    assert resp.status_code == 501
    assert resp.get_json()["success"] is False


def test_native_picker_script_contains_default_location(client, monkeypatch):
    captured = {}

    def fake_run(argv, **k):
        captured["script"] = argv[2]
        return _Proc(stdout="/tmp/\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client.post("/api/fs/native-picker", json={"start": "/tmp"})
    assert 'default location POSIX file "/tmp"' in captured["script"]
