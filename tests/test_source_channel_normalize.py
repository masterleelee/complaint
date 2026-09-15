"""来源渠道值域归一：映射规则 + 幂等 + 三处值域一致性守卫。

背景
----
「投诉列表 · 来源」列与「新增投诉 · 来源渠道」下拉读写同一个字段 source_channel，
但 2026-08-29 的历史资料导入沿用了原始台账口径，把镇街名（东坑交通局、虎门交通局…）
与「客服热线」直接写了进去 —— 18 个不同值里 15 个是非规范值、共 80 条工单。
这些值顺着三个下游冒出来：列表「来源」列内下拉、列表筛选下拉、看板「来源渠道 TOP5」。

2026-09-15 处置分两层：
  1. 数据层：scripts/normalize_source_channel.py 归一存量（本文件覆盖）
  2. 选项层：前端把值域收口为单一常量 SOURCE_CHANNELS，受理页与列表页同源渲染
     （tests/js/test_source_channel_options.mjs 覆盖）

本文件的第 5 节是**防漂移守卫**：脚本常量、前端常量、模板渲染三处必须指向同一份
值域。这次 bug 的根因就是同一个概念在两个文件里各硬编码了一份。
"""
import importlib.util
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "normalize_source_channel.py"

_spec = importlib.util.spec_from_file_location("normalize_source_channel", SCRIPT_PATH)
nsc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nsc)


def _make_db(path, rows):
    """建一个最小 complaint_tickets 表并塞入 (student_name, source_channel) 行。"""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE complaint_tickets (
            id TEXT PRIMARY KEY,
            ticket_no TEXT DEFAULT '',
            student_name TEXT DEFAULT '',
            complaint_date TEXT DEFAULT '',
            source_channel TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        );
        """
    )
    conn.executemany(
        "INSERT INTO complaint_tickets (id, ticket_no, student_name, complaint_date, source_channel, updated_at)"
        " VALUES (?,?,?,?,?,'2026-01-01 00:00:00')",
        [(f"id{i}", f"T{i:03d}", name, "2026-04-20", ch) for i, (name, ch) in enumerate(rows)],
    )
    conn.commit()
    conn.close()


def _distinct(path):
    conn = sqlite3.connect(path)
    try:
        return {
            r[0]
            for r in conn.execute("SELECT DISTINCT ifnull(source_channel,'') FROM complaint_tickets")
        }
    finally:
        conn.close()


def _run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["normalize_source_channel.py", *argv])
    return nsc.main()


# ── 1. 映射规则 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected,rule",
    [
        ("东坑交通局", "交通部门", "镇街交通局 → 交通部门"),
        ("虎门交通局", "交通部门", "镇街交通局 → 交通部门"),
        ("大岭山交通局", "交通部门", "镇街交通局 → 交通部门"),
        ("客服热线", "电话来访", "客服热线 → 电话来访"),
        ("", "电话来访", "空白 → 电话来访"),
        ("   ", "电话来访", "空白 → 电话来访"),
        ("  客服热线  ", "电话来访", "客服热线 → 电话来访"),
    ],
)
def test_classify_rules(raw, expected, rule):
    assert nsc.classify(raw) == (expected, rule)


@pytest.mark.parametrize("raw", ["12345", "交通部门", "电话来访", "信访", "邮件投诉", "驾培协会", "其他途径"])
def test_classify_canonical_is_noop(raw):
    """已规范的值必须原样返回、规则为 None（否则会白白产生 UPDATE）。"""
    assert nsc.classify(raw) == (raw, None)


@pytest.mark.parametrize("raw", ["某镇交通分局", "12345热线", "微信小程序反馈"])
def test_classify_unknown_value_raises(raw):
    """未知口径宁可中断，也不静默吞掉 —— 静默吞会让口径悄悄漂移。"""
    with pytest.raises(ValueError):
        nsc.classify(raw)


# ── 2. 幂等 ────────────────────────────────────────────────


def test_build_plan_empty_when_already_canonical(tmp_path):
    db = tmp_path / "clean.db"
    _make_db(db, [("甲", "交通部门"), ("乙", "电话来访"), ("丙", "信访")])
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        assert nsc.build_plan(conn) == []
    finally:
        conn.close()


def test_apply_is_idempotent(tmp_path, monkeypatch, capsys):
    db = tmp_path / "dirty.db"
    _make_db(db, [("甲", "虎门交通局"), ("乙", "客服热线")])

    assert _run_main(monkeypatch, ["--db", str(db), "--ledger-dir", str(tmp_path), "--apply"]) == 0
    assert _distinct(db) == {"交通部门", "电话来访"}

    # 第二次跑：无待改记录，不备份、不写库
    capsys.readouterr()
    assert _run_main(monkeypatch, ["--db", str(db), "--ledger-dir", str(tmp_path), "--apply"]) == 0
    assert "无需改动" in capsys.readouterr().out


# ── 3. dry-run 不写库 ──────────────────────────────────────


def test_dry_run_does_not_write(tmp_path, monkeypatch, capsys):
    db = tmp_path / "dirty.db"
    _make_db(db, [("甲", "虎门交通局"), ("乙", "客服热线"), ("丙", "交通部门")])

    assert _run_main(monkeypatch, ["--db", str(db), "--ledger-dir", str(tmp_path)]) == 0
    assert _distinct(db) == {"虎门交通局", "客服热线", "交通部门"}
    assert not list(tmp_path.glob("*.bak-*presrcclean")), "dry-run 不得产生备份"
    assert not list(tmp_path.glob("source_channel_normalize_*.csv")), "dry-run 不得产生明细"
    assert "[dry-run]" in capsys.readouterr().out


# ── 4. 端到端：值域收敛 + 总数不变 + 备份与明细落盘 ─────────


def test_apply_converges_and_keeps_row_count(tmp_path, monkeypatch, capsys):
    rows = (
        [("甲", "虎门交通局")] * 2
        + [("乙", "东坑交通局")] * 3
        + [("丙", "客服热线")] * 4
        + [("丁", "")]
        + [("戊", "交通部门"), ("己", "电话来访")]
    )
    db = tmp_path / "dirty.db"
    _make_db(db, rows)

    assert _run_main(monkeypatch, ["--db", str(db), "--ledger-dir", str(tmp_path), "--apply"]) == 0

    assert _distinct(db) == {"交通部门", "电话来访"}
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT count(*) FROM complaint_tickets").fetchone()[0] == len(rows)
    finally:
        conn.close()

    backups = list(tmp_path.glob("*.bak-*presrcclean"))
    assert len(backups) == 1, "写库前必须且只备份一次"
    assert len(sqlite3.connect(backups[0]).execute("SELECT count(*) FROM complaint_tickets").fetchone()) == 1

    ledgers = list(tmp_path.glob("source_channel_normalize_*.csv"))
    assert len(ledgers) == 1
    # 明细必须覆盖全部 10 条脏值（2+3+4+1），规范值不入账
    body = ledgers[0].read_text(encoding="utf-8-sig").strip().splitlines()
    assert len(body) - 1 == 10, f"明细行数应为 10，实际 {len(body) - 1}"
    assert "虎门交通局" in ledgers[0].read_text(encoding="utf-8-sig")


def test_backup_targets_given_db_not_module_constant(tmp_path, monkeypatch):
    """回归：--db 指向临时库时，备份必须落在临时库旁边，绝不能碰生产库。"""
    db = tmp_path / "other.db"
    _make_db(db, [("甲", "虎门交通局")])
    dst = nsc.backup_db(str(db))
    assert Path(dst).parent == tmp_path
    assert Path(dst).name.startswith("other.db.bak-")


# ── 5. 防漂移守卫：三处必须指向同一份值域 ──────────────────


def test_script_and_frontend_share_one_channel_list():
    """脚本常量 ≡ 前端 SOURCE_CHANNELS。

    这次 bug 的根因是「同一个概念在两个文件里各硬编码一份」，所以必须钉住。
    """
    src = (ROOT / "static" / "js" / "composables" / "useWorkbench.js").read_text(encoding="utf-8")
    m = re.search(r"const SOURCE_CHANNELS = \[(.*?)\];", src, re.S)
    assert m, "未在 useWorkbench.js 找到 SOURCE_CHANNELS 定义"
    frontend = json.loads("[" + m.group(1) + "]")
    assert frontend == nsc.CANONICAL_CHANNELS
    assert frontend == ["12345", "交通部门", "电话来访", "信访", "邮件投诉", "驾培协会", "其他途径"]


def test_intake_page_renders_options_from_shared_constant():
    """受理页两处「来源渠道」下拉必须由 SOURCE_CHANNELS 渲染，不得再硬编码。"""
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    rendered = html.count('v-for="c in SOURCE_CHANNELS"')
    assert rendered == 2, f"应有 2 处（受理页 + 人工建案弹窗）由 SOURCE_CHANNELS 渲染，实际 {rendered}"
    # 旧的硬编码选项必须已清除 —— 它正是「虎门交通局们」能冒出来的入口
    assert 'value="12345">12345' not in html
    assert "<option>电话来访</option>" not in html


def test_list_page_options_no_longer_merge_inventory_values():
    """列表侧选项不得再遍历 allTickets 合并存量值（本次 bug 的直接成因）。"""
    src = (ROOT / "static" / "js" / "composables" / "useWorkbench.js").read_text(encoding="utf-8")
    m = re.search(r"const channelOptions = Vue\.computed\((.*?)\);", src, re.S)
    assert m, "未找到 channelOptions 定义"
    body = m.group(1)
    assert "allTickets" not in body, "channelOptions 仍在合并库内存量值"
    assert "SOURCE_CHANNELS" in body
