"""账号体系：session 校验 + 角色权限装饰器

与 /api/auth/* 区分：本模块管"系统账号登录"，
现有的 /api/auth/* 是"三系统爬虫登录"接口。

Session 生命周期：
    不勾「记住我」→ SESSION_LIFETIME_HOURS（8 小时，会话级）
    勾「记住我」  → SESSION_REMEMBER_DAYS（7 天，绝对过期、不滑动续期）
两者都是 permanent cookie，过期时间在登录那一刻算定；
勾了记住我的 session 会在 cookie 里多带一个 _exp_at 绝对时间戳。
"""
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import g, jsonify, request, session
from flask.sessions import SecureCookieSessionInterface

from services import user_service


SESSION_KEY_USER_ID = "user_id"
SESSION_KEY_VERSION = "sv"
SESSION_KEY_EXP_AT = "_exp_at"  # 「记住我」专用：绝对过期时间戳（秒，epoch）
SESSION_LIFETIME_HOURS = 8
SESSION_REMEMBER_DAYS = 7


def _utcnow() -> datetime:
    """与 Flask 父类保持一致的 aware UTC 时间。

    不能用 datetime.utcnow()（3.12+ 弃用），更不能用 naive —— 父类返回的是
    aware，两个分支类型必须一致，否则 werkzeug 序列化 Expires 时会把 naive
    按本地时区解释，在东八区会让 cookie 提前 8 小时过期。
    """
    return datetime.now(timezone.utc)


class _RememberMeSessionInterface(SecureCookieSessionInterface):
    """在标准 SecureCookie 之上支持「记住我」的独立过期时间。

    Flask 的 PERMANENT_SESSION_LIFETIME 是 app 全局的，无法按 session 区分。
    这里覆写 get_expiration_time：session 里带 _exp_at 就用它，否则回退全局 8 小时。

    安全性：cookie 由 SECRET_KEY 签名，_exp_at 无法被伪造/篡改；
    且过期是绝对的（不随请求滑动续期），7 天后必然失效。
    """

    def get_expiration_time(self, app, session):
        exp_at = session.get(SESSION_KEY_EXP_AT)
        if not exp_at:
            return super().get_expiration_time(app, session)
        now = _utcnow()
        remain = int(exp_at) - int(time.time())
        return now + timedelta(seconds=remain) if remain > 0 else now


def configure_session(app) -> None:
    """注册到 Flask app，配置 session 生命周期。

    SECRET_KEY 读取优先级：
    1. FLASK_SECRET_KEY 环境变量；
    2. data/.flask_secret_key 持久化文件（自动生成并复用）；
    3. 随机生成（仅首次兜底）。
    """
    import os
    import secrets

    secret = os.environ.get("FLASK_SECRET_KEY")
    if not secret:
        key_file = Path(__file__).resolve().parent.parent / "data" / ".flask_secret_key"
        if key_file.exists():
            secret = key_file.read_text(encoding="utf-8").strip()
        else:
            secret = secrets.token_hex(32)
            key_file.write_text(secret, encoding="utf-8")
    if not secret:
        secret = secrets.token_hex(32)
    app.config["SECRET_KEY"] = secret
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=SESSION_LIFETIME_HOURS)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # 注意：必须用直接赋值，不能用 setdefault —— Flask 的 app.config 里
    # SESSION_COOKIE_NAME 已有默认值 "session"，setdefault 对已存在的键不生效，
    # 会导致 cookie 名仍是 "session" 而非期望的 "complaint_session"。
    app.config["SESSION_COOKIE_NAME"] = "complaint_session"
    # 必须在设置完上述 config 之后再挂：interface 读取 app.permanent_session_lifetime
    app.session_interface = _RememberMeSessionInterface()


def remember_me_seconds() -> int:
    """「记住我」session 的总时长（秒）。供前端/测试读取，避免各处硬编码。"""
    return SESSION_REMEMBER_DAYS * 24 * 3600


def default_session_seconds() -> int:
    """未勾「记住我」时的 session 时长（秒）。"""
    return int(SESSION_LIFETIME_HOURS) * 3600


def _load_current_user() -> dict | None:
    user_id = session.get(SESSION_KEY_USER_ID)
    session_version = session.get(SESSION_KEY_VERSION, 0)
    if not user_id:
        return None
    user = user_service.get_user_by_id(int(user_id))
    if not user:
        session.clear()
        return None
    if user.get("status") != "启用":
        session.clear()
        return None
    if int(user.get("session_version") or 0) != int(session_version):
        # 别人/同一账号别处登录踢掉了
        session.clear()
        return None
    return user


def login_user(user: dict, remember: bool = False) -> None:
    """登录成功：把 user_id + 当前 session_version 写入 session。

    remember=True 时额外写入 _exp_at（绝对过期时间戳），
    cookie 有效期由 8 小时延长到 SESSION_REMEMBER_DAYS 天。
    """
    user_service.record_login(user["id"])
    fresh = user_service.get_user_by_id(user["id"])
    session.clear()
    session.permanent = True
    session[SESSION_KEY_USER_ID] = fresh["id"]
    session[SESSION_KEY_VERSION] = fresh["session_version"]
    if remember:
        session[SESSION_KEY_EXP_AT] = int(time.time()) + remember_me_seconds()


def logout_user() -> None:
    """登出：清空 session（_exp_at 随之清除，cookie 立即失效）。"""
    session.clear()


def _unauthorized(msg: str, code: int = 401):
    return jsonify({"success": False, "error": msg, "code": "unauthorized"}), code


def _forbidden(msg: str):
    return jsonify({"success": False, "error": msg, "code": "forbidden"}), 403


def login_required(fn):
    """要求已登录。401 表示未登录/被踢/被停用，交给前端跳登录页。
    viewer 角色的写操作直接 403（防止误改）。"""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = _load_current_user()
        if not user:
            return _unauthorized("登录已失效，请重新登录")
        g.current_user = user
        if user["role"] == "viewer" and request.method != "GET":
            return _forbidden("只读审计账号不能修改数据")
        return fn(*args, **kwargs)
    return wrapper


def role_required(*roles: str):
    """要求当前用户角色在 roles 列表里。viewer 额外约束：所有写方法置 403。"""
    role_set = set(roles)

    def decorator(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            user = g.current_user
            if user["role"] not in role_set:
                return _forbidden(f"当前角色无权访问（需要 {sorted(role_set)}）")
            if user["role"] == "viewer" and request.method != "GET":
                return _forbidden("只读审计账号不能修改数据")
            return fn(*args, **kwargs)
        return wrapper
    return decorator
