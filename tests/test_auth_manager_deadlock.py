# -*- coding: utf-8 -*-
"""auth_manager 登录锁回归测试（2026-09-22 P0 死锁事故护栏）。

事故回顾：工作区未提交的加锁改动让 `_do_login_sync()`（已持 `_login_lock`）
调用 `_try_restore_session()`，后者又对同一把非重入 Lock 执行
`with self._login_lock` → 预登录线程 `login()`（force=False）自死锁永久持锁，
所有查询接口（/api/students/search、/api/query/start）在 ensure_login 上挂起。

本文件三例护栏：
1. 缓存新鲜时 login(force=False) 必须在有限时间内完成（死锁回归主护栏）；
2. 会话超龄但缓存新鲜时 ensure_login 优先恢复缓存，不得全量重登；
3. 会话超龄且无有效缓存时 ensure_login 走 force=True 重登（旧行为）。
"""
import threading
import time
from datetime import datetime

import pytest

from core.auth_manager import BaseCrawler, LoginResult, SystemType
from utils.cache_manager import AuthCache, cache_manager


class _FakeCrawler(BaseCrawler):
    """最小可测爬虫：_do_login 可编程，网络层不被触达。"""

    # 与生产 InternalCrawler 对齐：ensure_login 的超龄判定依赖该子类类属性
    SESSION_MAX_AGE_SECONDS = 15 * 60

    def __init__(self, login_success=True):
        super().__init__(SystemType.INTERNAL, "tester", "pwd")
        self.login_calls = []
        self.login_success = login_success

    def _do_login(self) -> LoginResult:
        self.login_calls.append(time.monotonic())
        return LoginResult(self.login_success, "模拟登录", duration_ms=1)


@pytest.fixture(autouse=True)
def _isolated_memory_cache(monkeypatch):
    """替换进程级内存缓存 dict，避免读写真实 data/cache/*.json。"""
    monkeypatch.setattr(cache_manager, "_memory_cache", {})


def _put_fresh_cache(crawler: _FakeCrawler, age_seconds: float = 0.0):
    """写入一份指定年龄的会话缓存（is_valid 由 2h TTL 保证）。"""
    cookies = {"JSESSIONID": "test-session"}
    cache = AuthCache(
        system=crawler.system_type.value,
        username=crawler.username,
        cookies=cookies,
        created_at=time.time() - age_seconds,
        expires_at=time.time() + 3600,
    )
    cache_manager._memory_cache[cache_manager._get_cache_key(
        crawler.system_type.value, crawler.username)] = cache


def test_login_force_false_with_fresh_cache_no_deadlock():
    """缓存新鲜时 login(force=False) 必须在 5 秒内返回（死锁回归主护栏）。

    事故形态：_do_login_sync 持锁调用 _try_restore_session → 后者再拿同一把
    非重入锁 → 本例场景（预登录 force=False + 缓存新鲜）永久挂死。
    """
    crawler = _FakeCrawler()
    _put_fresh_cache(crawler, age_seconds=0.0)

    result_box = {}

    def _run():
        result_box["result"] = crawler.login()  # force=False，与预登录一致

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), (
        "login(force=False) 在缓存新鲜时 5 秒未返回：_do_login_sync 锁内调用 "
        "_try_restore_session 的重入死锁已回归"
    )
    result = result_box.get("result")
    assert result is not None and result.success
    assert "缓存" in result.message  # 走恢复路径，而非重新登录
    assert crawler.login_calls == []  # 未触发真实 _do_login


def test_ensure_login_expired_session_restores_cache_first():
    """内存会话超龄但磁盘/内存缓存新鲜：ensure_login 应恢复缓存而非全量重登。

    对应 P2：事故版本把该场景改成 force=True 全量重登，绕过缓存恢复，
    每次会话过期都对三系统发起真实登录（压力/风控风险）。
    """
    crawler = _FakeCrawler()
    crawler._logged_in = True
    crawler._session_started_at = time.time() - 16 * 60  # internal SESSION_MAX_AGE=15min，已超龄
    _put_fresh_cache(crawler, age_seconds=0.0)

    assert crawler.ensure_login() is True
    assert crawler.login_calls == []  # 未触发 _do_login（缓存恢复即视为已登录）
    assert crawler._logged_in is True


def test_ensure_login_expired_session_without_cache_forces_relogin():
    """会话超龄且无有效缓存：ensure_login 必须走 force=True 重登（旧行为）。"""
    crawler = _FakeCrawler()
    crawler._logged_in = True
    crawler._session_started_at = time.time() - 16 * 60

    assert crawler.ensure_login() is True
    assert len(crawler.login_calls) == 1  # 恰好一次真实登录


def test_ensure_login_first_visit_restores_cache():
    """首次访问（从未登录）且缓存新鲜：ensure_login 应恢复缓存，不全量重登。"""
    crawler = _FakeCrawler()
    _put_fresh_cache(crawler, age_seconds=0.0)

    assert crawler.ensure_login() is True
    assert crawler.login_calls == []
