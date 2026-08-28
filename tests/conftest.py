import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """测试不触达真实大模型：登记表 AI 整理一律模拟为不可用，走数据层兜底链。"""
    import services.intake_service as intake_svc
    monkeypatch.setattr(intake_svc, "ai_polish_registration", lambda ticket: {}, raising=False)


# 阶段 2 起所有 /api/* 都需 session，否则 401。
# 共用的"自动 admin 登录"片段，注入到既有测试文件的 client() fixture 里。
# 新写的 test_auth_account.py 自己测登录（不引用此 helper）。
AUTO_LOGIN_BLOCK = """
        # 阶段 2+ 自动登录 admin（每个 client fixture 复用 helper）
        try:
            _autologin_admin(c)
        except Exception:
            pass
"""


def _autologin_admin(c):
    """在 c 上执行一次 admin/admin 登录。失败也不抛（不影响使用）。"""
    import threading
    from werkzeug.security import generate_password_hash
    import database
    database._ensure_default_admin()
    conn = database._local.get(threading.current_thread().ident)
    if conn is not None:
        conn.execute(
            "UPDATE users SET password_hash=?, session_version=0, status='启用' WHERE username='admin'",
            (generate_password_hash('admin'),),
        )
        conn.commit()
    c.post("/api/session/login", json={"username": "admin", "password": "admin"})


# 把 helper 暴露到 tests 命名空间，方便 `from conftest import _autologin_admin`
sys.modules[__name__]._autologin_admin = _autologin_admin
