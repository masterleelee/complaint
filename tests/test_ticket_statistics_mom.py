# -*- coding: utf-8 -*-
"""回归测试：投诉统计 KPI「环比」(monthly_compare.mom_change) 必须窗口感知。

防回归点（见诊断 2026-09-02）：
  - 全部/今年 不应再显示 -40%；本周 不应再显示 -70%。
  - 分子（本期）与分母（上期）必须用同一套范围/网点条件。
  - 全部时间（无起止）无"上一周期"可比，mom_change 应为 None。
"""
import os
import sqlite3
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402

# 真实生产库路径——其它测试模块会在 module 级别把 database.DB_PATH 切到临时
# test.db，且不会还原；本测试通过 fixture 在调用期间强制使用真实库，结束后还原，
# 既保证自身可重现，又不污染其它测试。
REAL_DB_PATH = Path(__file__).parent.parent / "data" / "complaints.db"


@pytest.fixture
def real_db():
    """临时把 database 指向真实生产库，并清理线程级连接；退出时原样还原。"""
    saved_path = database.DB_PATH
    saved_conn = database._local.get(threading.current_thread().ident)
    database.DB_PATH = REAL_DB_PATH
    tid = threading.current_thread().ident
    if database._local.get(tid) is not None:
        try:
            database._local[tid].rollback()
        except Exception:
            pass
        database._local[tid] = None
    yield
    # 还原现场，避免影响后续测试
    database.DB_PATH = saved_path
    database._local[tid] = saved_conn


DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "complaints.db")


def _fmt(d):
    return d.strftime("%Y-%m-%d")


def shifted(start, end):
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    length = (e - s).days + 1
    return _fmt(s - timedelta(days=length)), _fmt(s - timedelta(days=1))


def eff_count(start, end, scope="all"):
    """有效投诉计数（排除撤诉）= 与 effective_total 同口径。"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.text_factory = str
    where = [
        "complaint_date >= ?", "complaint_date <= ?",
        "complaint_date != ''",
        "COALESCE(withdraw_status,'')!='已撤诉'",
        "TRIM(COALESCE(withdrawn_at,''))=''",
    ]
    params = [start, end]
    if scope == "branch":
        where.append("organization_unit_type = '分校'")
    elif scope == "store":
        where.append("organization_unit_type = '分店'")
    q = "SELECT COUNT(*) FROM complaint_tickets WHERE " + " AND ".join(where)
    n = con.execute(q, params).fetchone()[0]
    con.close()
    return n


def expected_mom(start, end, scope="all"):
    if not start or not end:
        return None
    cur = eff_count(start, end, scope)
    ps, pe = shifted(start, end)
    prev = eff_count(ps, pe, scope)
    return round((cur - prev) / prev * 100, 1) if prev > 0 else None


def test_mom_is_window_aware(real_db):
    cases = [
        ("全部(无窗口)", "", "", "all", None),
        ("今年", "2026-01-01", "2026-09-02", "all", expected_mom("2026-01-01", "2026-09-02")),
        ("本月", "2026-09-01", "2026-09-02", "all", expected_mom("2026-09-01", "2026-09-02")),
        ("本周", "2026-08-31", "2026-09-02", "all", expected_mom("2026-08-31", "2026-09-02")),
        ("仅分校-本周", "2026-08-31", "2026-09-02", "branch", expected_mom("2026-08-31", "2026-09-02", "branch")),
    ]
    for name, s, e, scope, exp in cases:
        d = database.get_ticket_statistics(start_date=s, end_date=e, scope=scope)
        got = (d.get("monthly_compare") or {}).get("mom_change")
        assert got == exp, f"[{name}] mom_change 期望 {exp}，实际 {got}"
        # 全部必须显式 None（不显示无意义环比）
        if not s and not e:
            assert got is None, f"[{name}] 全部时间应返回 None，实际 {got}"
