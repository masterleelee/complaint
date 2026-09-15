#!/usr/bin/env python3
"""把 complaint_tickets.source_channel 的存量脏值归一到规范渠道值域。

背景
----
「投诉列表 · 来源」列（templates/index.html）与「新增投诉 · 来源渠道」下拉
读写的是同一个字段 source_channel。受理页只允许 7 个规范值，但
2026-08-29 的历史资料导入（tmp/import_2026_final.json，81 条）沿用了原始
台账口径，把镇街名（东坑交通局、虎门交通局…）和「客服热线」直接写了进去，
于是 18 个不同值里有 15 个是非规范值，直接污染了三个下游：
  - 列表「来源」列内下拉（曾合并库内存量值）
  - 列表「来源」筛选下拉（曾取全量 distinct）
  - 看板「来源渠道 TOP5」（按该字段分组，前 5 名全是脏值）

映射规则（经确认）
  - "<镇街>交通局"  → 交通部门
  - "客服热线"      → 电话来访
  - 空白            → 电话来访
  - 其它未知值      → 报错退出，不静默吞（避免口径悄悄漂移）

行为
----
  - 默认 dry-run：只打印预览，不写库
  - --apply：写前用 SQLite backup() API 再备份一次（WAL 下裸 cp 会丢数据）
  - 按"不留痕"决策：不写 ticket_field_changes、不动 updated_at
  - 逐条 old→new 明细落盘 CSV，作为服务端留档（保留原镇街口径可追溯）
  - 幂等：已规范的工单不产生任何 UPDATE

运行：
    env -u PYTHONPATH ./venv/bin/python3 scripts/normalize_source_channel.py
    env -u PYTHONPATH ./venv/bin/python3 scripts/normalize_source_channel.py --apply
"""

import argparse
import csv
import os
import re
import sqlite3
import sys
import time

DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "complaints.db")
)
ROOT = os.path.dirname(os.path.dirname(DB_PATH))

# 唯一权威值域：必须与 templates/index.html 的 #f_source 下拉逐字一致，
# 也必须与 static/js/composables/useWorkbench.js 的 SOURCE_CHANNELS 一致。
CANONICAL_CHANNELS = ["12345", "交通部门", "电话来访", "信访", "邮件投诉", "驾培协会", "其他途径"]

# 镇街名 + "交通局"（如 东坑交通局 / 虎门交通局 / 大岭山交通局）
DISTRICT_DEPT_RE = re.compile(r"^[\u4e00-\u9fa5A-Za-z]{1,10}交通局$")

FALLBACK_DEPT = "交通部门"
FALLBACK_HOTLINE = "电话来访"


def classify(raw):
    """把单个来源渠道值映射到规范值。

    返回 (新值, 规则名)。已经是规范值时规则名为 None，表示无需改动。
    无法归一的值抛 ValueError —— 宁可中断也不静默吞掉。
    """
    v = (raw or "").strip()
    if v in CANONICAL_CHANNELS:
        return v, None
    if not v:
        return FALLBACK_HOTLINE, "空白 → 电话来访"
    if v == "客服热线":
        return FALLBACK_HOTLINE, "客服热线 → 电话来访"
    if DISTRICT_DEPT_RE.match(v):
        return FALLBACK_DEPT, "镇街交通局 → 交通部门"
    raise ValueError(f"无法归一的来源渠道值：{v!r}（请先确认口径，再扩充 classify 规则）")


def build_plan(conn):
    """扫描全表，返回需要改动的条目列表。"""
    rows = conn.execute(
        "SELECT id, ifnull(ticket_no,'') AS ticket_no, ifnull(student_name,'') AS student_name, "
        "ifnull(complaint_date,'') AS complaint_date, ifnull(source_channel,'') AS old_value "
        "FROM complaint_tickets ORDER BY complaint_date, id"
    ).fetchall()
    plan = []
    for r in rows:
        new_value, rule = classify(r["old_value"])
        if rule is None:
            continue
        plan.append(
            {
                "id": r["id"],
                "ticket_no": r["ticket_no"],
                "student_name": r["student_name"],
                "complaint_date": r["complaint_date"],
                "old_value": r["old_value"],
                "new_value": new_value,
                "rule": rule,
            }
        )
    return plan


def group_summary(plan):
    """按 (旧值 → 新值) 聚合，返回 [(旧值, 新值, 条数, 规则)]，条数降序。"""
    buckets = {}
    for item in plan:
        key = (item["old_value"], item["new_value"], item["rule"])
        buckets[key] = buckets.get(key, 0) + 1
    return sorted(
        [(old, new, n, rule) for (old, new, rule), n in buckets.items()],
        key=lambda x: (-x[2], x[0]),
    )


def backup_db(db_path):
    """SQLite backup() API 备份；WAL 下裸 cp 会丢数据。

    必须按调用方给的 db_path 备份（不能读模块级 DB_PATH），否则 --db 指向临时库时
    会去备份/污染真实生产库。
    """
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = f"{db_path}.bak-{ts}-presrcclean"
    src = sqlite3.connect(db_path)
    out = sqlite3.connect(dst)
    try:
        with out:
            src.backup(out)
    finally:
        out.close()
        src.close()
    src_n = sqlite3.connect(db_path).execute("SELECT count(*) FROM complaint_tickets").fetchone()[0]
    dst_n = sqlite3.connect(dst).execute("SELECT count(*) FROM complaint_tickets").fetchone()[0]
    if src_n != dst_n:
        raise RuntimeError(f"备份校验失败：源 {src_n} 行 ≠ 备份 {dst_n} 行，已中止")
    print(f"[backup] {dst}  （{src_n} 行，校验一致）")
    return dst


def write_ledger(plan, ledger_dir=None):
    """逐条 old→new 落盘 CSV 留档（DB 内不留痕，靠这份文件追溯）。"""
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(ledger_dir or os.path.join(ROOT, "tmp"),
                        f"source_channel_normalize_{ts}.csv")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=["id", "ticket_no", "student_name", "complaint_date", "old_value", "new_value", "rule"],
        )
        w.writeheader()
        w.writerows(plan)
    print(f"[ledger] {path}  （{len(plan)} 条）")
    return path


def verify(conn):
    """写后复验：所有值的必须落在规范集合内。"""
    values = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT ifnull(source_channel,'') FROM complaint_tickets ORDER BY 1"
        )
    ]
    bad = [v for v in values if v not in CANONICAL_CHANNELS]
    if bad:
        raise RuntimeError(f"复验失败，仍存在非规范值：{bad}")
    total = conn.execute("SELECT count(*) FROM complaint_tickets").fetchone()[0]
    print(f"[verify] 值域 = {values}")
    print(f"[verify] 规范值域内 · 工单总数 {total}")
    return values


def main():
    ap = argparse.ArgumentParser(description="归一 source_channel 存量脏值")
    ap.add_argument("--apply", action="store_true", help="真正写库（缺省仅预览）")
    ap.add_argument("--db", default=DB_PATH, help=f"数据库路径，默认 {DB_PATH}")
    ap.add_argument("--ledger-dir", default=os.path.join(ROOT, "tmp"),
                    help="逐条改动明细 CSV 的落盘目录，默认 <项目根>/tmp")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"[fatal] 数据库不存在：{args.db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        plan = build_plan(conn)
        summary = group_summary(plan)

        print("=== 归一预览 ===")
        if not summary:
            print("（无待归一记录，值域已干净）")
        for old, new, n, rule in summary:
            print(f"  {old or '(空白)':<14} → {new:<8} {n:>3} 条   [{rule}]")
        print(f"  合计待改：{len(plan)} 条")

        if not args.apply:
            print("\n[dry-run] 未写库。加 --apply 执行。")
            return 0
        if not plan:
            print("\n[skip] 无需改动。")
            return 0

        backup_db(args.db)

        # 乐观并发：仅当库内值仍等于读取到的旧值时才更新，
        # 避免覆盖扫描与写入之间由 UI 产生的合法改动。
        changed = 0
        with conn:
            for item in plan:
                cur = conn.execute(
                    "UPDATE complaint_tickets SET source_channel=? "
                    "WHERE id=? AND ifnull(source_channel,'')=?",
                    (item["new_value"], item["id"], item["old_value"]),
                )
                changed += cur.rowcount
        print(f"[apply] 已更新 {changed} 行（计划 {len(plan)} 行）")
        if changed != len(plan):
            print(f"[warn] 有 {len(plan) - changed} 行在写入前被改动，已跳过，请复查", file=sys.stderr)

        write_ledger(plan, args.ledger_dir)
        verify(conn)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
