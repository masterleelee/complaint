"""账号体系：session 校验 + 角色权限装饰器

与 /api/auth/* 区分：本模块管"系统账号登录"，
现有的 /api/auth/* 是"三系统爬虫登录"接口。
"""
from datetime import timedelta
from functools import wraps

from flask import g, jsonify, request, session

from services import user_service


SESSION_KEY_USER_ID = "user_id"
SESSION_KEY_VERSION = "sv"
SESSION_LIFETIME_HOURS = 8


def configure_session(app) -> None:
    """注册到 Flask app，配置 session 生命周期。"""
    import os
    import secrets
    # 优先从环境变量读，缺省随机（生产请 FLASK_SECRET_KEY 注入）
    secret = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
    app.config["SECRET_KEY"] = secret
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=SESSION_LIFETIME_HOURS)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config.setdefault("SESSION_COOKIE_NAME", "complaint_session")


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


def login_user(user: dict) -> None:
    """登录成功：把 user_id + 当前 session_version 写入 session。"""
    user_service.record_login(user["id"])
    fresh = user_service.get_user_by_id(user["id"])
    session.clear()
    session.permanent = True
    session[SESSION_KEY_USER_ID] = fresh["id"]
    session[SESSION_KEY_VERSION] = fresh["session_version"]


def logout_user() -> None:
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
