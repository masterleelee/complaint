"""登录页改版（变体 A）后端配套：「记住我 7 天」session 生命周期

覆盖：
- 不勾「记住我」→ cookie 8 小时（SESSION_LIFETIME_HOURS，与改版前一致）
- 勾「记住我」  → cookie 7 天，且 session 内写入 _exp_at 绝对过期时间戳
- 缺省 / remember=0 / 旧客户端不传字段 → 一律回退 8 小时（向后兼容）
- 记住我 session 期间 /api/session/me 仍然 200
- 安全护栏：改密码 → session_version bump → 7 天 session 立即失效
- 登出 → cookie 被清除
- 回归：未登录访问受保护接口仍 401

不依赖真实三系统爬虫。
"""
import re
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import pytest

# 模块级隔离：import app 之前必须先切 DB_PATH，避免污染真实 data/complaints.db
_TMP_DIR = Path(tempfile.mkdtemp(prefix="remember-tests-"))

import database  # noqa: E402
database.DB_PATH = _TMP_DIR / "test.db"
database._local.clear()

import app as app_module  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402
from core import auth  # noqa: E402
from services import user_service  # noqa: E402


HOUR = 3600
DAY = 24 * HOUR


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test.db"
    database.DB_PATH = test_db
    database._local.clear()
    database.init_db()
    database._ensure_default_admin()
    conn = sqlite3.connect(str(test_db))
    conn.execute(
        "UPDATE users SET password_hash=?, session_version=0, status='启用' WHERE username='admin'",
        (generate_password_hash("admin"),),
    )
    conn.commit()
    conn.close()
    app_module.app.config["TESTING"] = True
    yield
    database._local.clear()


@pytest.fixture()
def client():
    return app_module.app.test_client()


def _login(client, **extra):
    payload = {"username": "admin", "password": "admin"}
    payload.update(extra)
    return client.post("/api/session/login", json=payload)


def _max_age(resp):
    """解析 Set-Cookie 里的 Expires，换算成"还剩多少秒过期"。

    注意：Flask 的 session cookie 只写 Expires，不写 Max-Age（见 flask/sessions.py
    的 save_session），所以这里不能去找 Max-Age。
    """
    m = re.search(r"Expires=([^;]+)", resp.headers.get("Set-Cookie", ""), re.I)
    if not m:
        return None
    dt = parsedate_to_datetime(m.group(1).strip())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int((dt - datetime.now(timezone.utc)).total_seconds())


def _session_cookie_value(resp):
    """从 Set-Cookie 里取出 session cookie 的值（用于伪造签名测试）。

    cookie 名已修正为 complaint_session（core.auth 用直接赋值覆盖 Flask 默认值 "session"）。
    """
    m = re.search(r"complaint_session=([^;]+)", resp.headers.get("Set-Cookie", ""))
    return m.group(1) if m else None


def _exp_at(client):
    """读取当前 session 里的 _exp_at（没勾记住我则为 None）。"""
    with client.session_transaction() as sess:
        return sess.get(auth.SESSION_KEY_EXP_AT)


# ═══════════════════════════════════════
#  常量与装配
# ═══════════════════════════════════════

def test_constants():
    assert auth.SESSION_REMEMBER_DAYS == 7
    assert auth.SESSION_LIFETIME_HOURS == 8
    assert auth.remember_me_seconds() == 7 * DAY
    assert auth.default_session_seconds() == 8 * HOUR


def test_custom_session_interface_installed():
    """configure_session 必须把自定义 interface 挂上，否则「记住我」不生效。"""
    assert isinstance(app_module.app.session_interface, auth._RememberMeSessionInterface)


# ═══════════════════════════════════════
#  生命周期：8 小时 vs 7 天
# ═══════════════════════════════════════

def test_default_login_is_8_hours(client):
    r = _login(client, remember=False)
    assert r.status_code == 200 and r.get_json()["success"]
    body = r.get_json()["data"]
    assert body["remember"] is False
    assert body["expires_in"] == 8 * HOUR
    # 容差 5 秒：cookie Max-Age 由服务端按当前时间计算
    assert _max_age(r) == pytest.approx(8 * HOUR, abs=5)
    assert _exp_at(client) is None


def test_remember_me_is_7_days(client):
    r = _login(client, remember=True)
    assert r.status_code == 200 and r.get_json()["success"]
    body = r.get_json()["data"]
    assert body["remember"] is True
    assert body["expires_in"] == 7 * DAY
    assert _max_age(r) == pytest.approx(7 * DAY, abs=5)


def test_remember_writes_absolute_exp_at(client):
    """_exp_at 是绝对时间戳（不滑动续期），约等于 now + 7 天。"""
    before = int(time.time())
    _login(client, remember=True)
    exp = _exp_at(client)
    assert exp is not None
    assert before + 7 * DAY <= exp <= int(time.time()) + 7 * DAY


# ═══════════════════════════════════════
#  向后兼容：旧客户端不传 remember
# ═══════════════════════════════════════

def test_missing_remember_field_falls_back_to_8h(client):
    r = _login(client)  # 不传 remember
    assert r.get_json()["data"]["remember"] is False
    assert _max_age(r) == pytest.approx(8 * HOUR, abs=5)


def test_falsy_remember_variants(client):
    for val in (0, "", None, False):
        c = app_module.app.test_client()
        r = _login(c, remember=val)
        assert r.get_json()["data"]["remember"] is False, f"remember={val!r} 应为 False"
        assert _max_age(r) == pytest.approx(8 * HOUR, abs=5), f"remember={val!r} 应走 8 小时"


def test_truthy_string_remember_accepted(client):
    """前端若传 "true" 字符串也应当识别为记住我。"""
    r = _login(client, remember="true")
    assert r.get_json()["data"]["remember"] is True
    assert _max_age(r) == pytest.approx(7 * DAY, abs=5)


# ═══════════════════════════════════════
#  会话可用性
# ═══════════════════════════════════════

def test_remembered_session_can_call_me(client):
    _login(client, remember=True)
    r = client.get("/api/session/me")
    assert r.status_code == 200
    assert r.get_json()["data"]["username"] == "admin"


def test_remembered_session_survives_many_requests(client):
    """7 天 session 不应因为后续请求被重置成 8 小时。"""
    _login(client, remember=True)
    for _ in range(5):
        client.get("/api/session/me")
    with client.session_transaction() as sess:
        assert sess.get(auth.SESSION_KEY_EXP_AT) is not None


# ═══════════════════════════════════════
#  安全护栏
# ═══════════════════════════════════════

def test_password_reset_kills_remembered_session(client):
    """改密码 bump session_version → 即使 7 天未到也立即失效（防 cookie 被盗用）。"""
    _login(client, remember=True)
    assert client.get("/api/session/me").status_code == 200

    admin = user_service.get_user_by_username("admin")
    user_service.reset_password(admin["id"], "newpass123")

    r = client.get("/api/session/me")
    assert r.status_code == 401


def test_disabled_account_kills_remembered_session(client):
    _login(client, remember=True)
    admin = user_service.get_user_by_username("admin")
    user_service.set_user_status(admin["id"], "停用")
    assert client.get("/api/session/me").status_code == 401


def test_logout_clears_cookie(client):
    """登出：cookie 的 Expires 被置到 1970（浏览器立即丢弃），且 session 立即失效。"""
    _login(client, remember=True)
    r = client.post("/api/session/logout")
    assert r.status_code == 200
    assert _max_age(r) is not None and _max_age(r) <= 0   # Expires 落在过去
    assert client.get("/api/session/me").status_code == 401


def test_exp_at_cannot_be_forged(client):
    """篡改 session 内容会让签名失效 → 视为未登录，而不是拿到超长 session。"""
    r = _login(client, remember=True)
    value = _session_cookie_value(r)
    assert value, "登录响应里应能取到 session cookie"
    forged = value[:-4] + ("AAAA" if not value.endswith("AAAA") else "BBBB")
    client.set_cookie("complaint_session", forged, domain="localhost")
    assert client.get("/api/session/me").status_code == 401


def test_expired_exp_at_yields_past_expiration():
    """_exp_at 已过期时，interface 必须给出"已过去"的过期时间（浏览器据此丢弃 cookie）。

    直接测 interface 而不是走 test_client —— cookie 是否过期由浏览器判定，
    werkzeug 测试客户端的 cookie jar 行为不代表真实浏览器。
    """
    app = app_module.app
    iface = app.session_interface
    with app.test_request_context():
        from flask import session
        session[auth.SESSION_KEY_EXP_AT] = int(time.time()) - 10
        exp = iface.get_expiration_time(app, session)
        assert exp <= datetime.now(timezone.utc)


def test_no_exp_at_falls_back_to_global_lifetime():
    """没有 _exp_at 时（未勾记住我）必须回退到全局 8 小时，而不是继承某个脏值。"""
    app = app_module.app
    iface = app.session_interface
    with app.test_request_context():
        from flask import session
        session.permanent = True          # 与 login_user() 的行为一致
        delta = iface.get_expiration_time(app, session) - datetime.now(timezone.utc)
        assert delta.total_seconds() == pytest.approx(8 * HOUR, abs=5)


def test_expiration_time_is_timezone_aware():
    """两个分支必须都返回 aware datetime —— naive 会被 werkzeug 按本地时区解释，
    在东八区会让「记住我」的 cookie 提前 8 小时过期。"""
    app = app_module.app
    iface = app.session_interface
    with app.test_request_context():
        from flask import session
        session.permanent = True
        assert iface.get_expiration_time(app, session).tzinfo is not None   # 回退分支
        session[auth.SESSION_KEY_EXP_AT] = int(time.time()) + 7 * DAY
        assert iface.get_expiration_time(app, session).tzinfo is not None   # 记住我分支


# ═══════════════════════════════════════
#  回归：未登录保护不变
# ═══════════════════════════════════════

def test_unauthenticated_api_still_401(client):
    assert client.get("/api/session/me").status_code == 401


def test_unauthenticated_index_returns_login_page(client):
    r = client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "记住我" in html          # 新登录页特征
    assert "投诉处理系统" in html


def test_wrong_password_still_rejected(client):
    r = _login(client, password="wrong", remember=True)
    assert r.status_code == 401
    assert client.get("/api/session/me").status_code == 401
