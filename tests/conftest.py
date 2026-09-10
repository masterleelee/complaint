import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """测试不触达真实大模型：登记表 AI 整理一律模拟为不可用，走数据层兜底链。"""
    import services.intake_service as intake_svc
    monkeypatch.setattr(intake_svc, "ai_polish_registration", lambda ticket: {}, raising=False)


# ── 归档根目录隔离（ISS-AP-00）────────────────────────────────────────────
# 背景：`app.py:32` / `services/archive_service.py:24` 都是
# `from config import load_config` —— 导入时**直接绑定**函数对象。测试里写
# `monkeypatch.setattr(config_module, "load_config", ...)` 只改 config 模块属性，
# 对这两个模块完全无效 → 归档路由仍读真实 data/config.json 的 archive_root
# （/Volumes/File/... 共享盘）→ 共享盘未挂载时 PermissionError，挂载时又会把
# 测试文件真实写进生产归档目录。
#
# 这里统一把「归档根」重定向到每例的 tmp_path：
#   * 只覆盖 archive_root 一个键，其余配置沿用真实值 —— 不波及其他域的用例；
#   * 用 wrap 而非替换，保留 load_config 的原有行为（读取/合并默认值）；
#   * 用例自身的 monkeypatch 在 fixture 之后生效，仍可覆盖本隔离。
_ARCHIVE_ROOT_BOUND_MODULES = ("app", "services.archive_service")


@pytest.fixture(autouse=True)
def _isolate_archive_root(tmp_path, monkeypatch):
    import config as config_module

    real_load_config = config_module.load_config

    def _load_config_with_tmp_archive_root():
        cfg = dict(real_load_config() or {})
        cfg["archive_root"] = str(tmp_path)
        return cfg

    # 只处理「已被导入」的模块：不主动 import app，避免打乱用例对导入时序的假设
    for mod_name in _ARCHIVE_ROOT_BOUND_MODULES:
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "load_config"):
            monkeypatch.setattr(mod, "load_config", _load_config_with_tmp_archive_root)
    yield


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
