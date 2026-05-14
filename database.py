"""SQLite 本地数据库 - 投诉工单 + 操作日志 + 回复模板"""
import math
import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent / "data" / "complaints.db"
_MIGRATED_KEY = "schema_migrated_to_tickets"

# 全局共享数据库连接（带检查+连接池效果）
_local = {}

def _get_conn():
    """线程安全的单连接，复用已有连接"""
    import threading
    tid = threading.current_thread().ident
    if tid not in _local or _local[tid] is None:
        DB_PATH.parent.mkdir(exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local[tid] = conn
    return _local[tid]

@contextmanager
def get_db():
    """获取数据库连接（上下文管理器，优先复用线程连接）"""
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db():
    """初始化数据库表（含自动迁移）"""
    with get_db() as conn:
        # ── 检查是否需要迁移 ──
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='complaints'")
        has_old_table = cursor.fetchone() is not None

        # 如果旧表存在且尚未迁移，则重命名
        if has_old_table:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='complaints_v1'"
            )
            if not cursor.fetchone():
                conn.execute("ALTER TABLE complaints RENAME TO complaints_v1")
                print("[数据库] 已迁移: complaints → complaints_v1")

        # ── 创建 complaint_tickets 表 ──
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS complaint_tickets (
                id TEXT PRIMARY KEY,
                ticket_no TEXT NOT NULL DEFAULT '',
                student_name TEXT NOT NULL DEFAULT '',
                id_card TEXT NOT NULL DEFAULT '',
                phone TEXT DEFAULT '',
                complaint_content TEXT DEFAULT '',
                complaint_demands TEXT DEFAULT '',
                source_channel TEXT DEFAULT '',
                forwarding_dept TEXT DEFAULT '',
                caller_number TEXT DEFAULT '',
                complaint_date TEXT DEFAULT '',
                complaint_type TEXT DEFAULT '',
                priority TEXT DEFAULT 'normal',

                -- 三系统查询结果（JSON）
                query_result TEXT DEFAULT '{}',

                -- 合同分析
                contract_path TEXT DEFAULT '',
                contract_code TEXT DEFAULT '',
                total_fee REAL DEFAULT 0,
                deduction_fee REAL DEFAULT 0,
                refund_fee REAL DEFAULT 0,
                deduction_detail TEXT DEFAULT '[]',

                -- 处理状态
                handle_status TEXT DEFAULT '待处理',
                handle_steps TEXT DEFAULT '[]',
                attachments TEXT DEFAULT '[]',

                -- 产出物
                reply_path TEXT DEFAULT '',
                feishu_record_id TEXT DEFAULT '',
                feishu_handle_no TEXT DEFAULT '',

                -- 学员信息缓存（三系统查询结果中常用的字段）
                license_type TEXT DEFAULT '',
                school_name TEXT DEFAULT '',
                school_short TEXT DEFAULT '',
                registration_date TEXT DEFAULT '',
                exam_stage TEXT DEFAULT '',
                student_status TEXT DEFAULT '',
                training_hours TEXT DEFAULT '{}',

                remarks TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_tickets_id_card ON complaint_tickets(id_card);
            CREATE INDEX IF NOT EXISTS idx_tickets_status ON complaint_tickets(handle_status);
            CREATE INDEX IF NOT EXISTS idx_tickets_source ON complaint_tickets(source_channel);
            CREATE INDEX IF NOT EXISTS idx_tickets_date ON complaint_tickets(complaint_date);
            CREATE INDEX IF NOT EXISTS idx_tickets_ticket_no ON complaint_tickets(ticket_no);

            -- 回复模板表
            CREATE TABLE IF NOT EXISTS reply_templates (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                description TEXT DEFAULT '',
                template_path TEXT DEFAULT '',
                variables TEXT DEFAULT '[]',
                is_default INTEGER DEFAULT 0,
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            );

            -- operation_logs 增加 ticket_id
            CREATE TABLE IF NOT EXISTS operation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                complaint_id TEXT DEFAULT '',
                ticket_id TEXT DEFAULT '',
                operation TEXT NOT NULL,
                detail TEXT DEFAULT '',
                success INTEGER DEFAULT 1,
                created_at TEXT DEFAULT ''
            );

            -- 兼容旧版 complaints_v1 表（用于 get_complaint_by_idcard）
            CREATE TABLE IF NOT EXISTS complaints_v1 (
                id TEXT PRIMARY KEY,
                id_card TEXT DEFAULT '',
                student_name TEXT DEFAULT '',
                registration_fee REAL DEFAULT 0,
                deduction_fee REAL DEFAULT 0,
                refund_fee REAL DEFAULT 0,
                deduction_detail TEXT DEFAULT '[]',
                contract_code TEXT DEFAULT '',
                contract_path TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
            );
        """)

        # 检查并添加 ticket_id 列（兼容旧表）
        cursor = conn.execute("PRAGMA table_info(operation_logs)")
        columns = [row[1] for row in cursor.fetchall()]
        if "ticket_id" not in columns:
            try:
                conn.execute("ALTER TABLE operation_logs ADD COLUMN ticket_id TEXT DEFAULT ''")
                print("[数据库] 已迁移: operation_logs 增加 ticket_id 列")
            except Exception:
                pass

        # ── 合同缓存表 ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS contract_cache (
                id_card TEXT PRIMARY KEY,
                student_name TEXT DEFAULT '',
                file_path TEXT NOT NULL,
                file_size INTEGER DEFAULT 0,
                downloaded_at TEXT DEFAULT ''
            );
        """)

        # ── 新增字段迁移（回访 + 文档 + 特殊退费检测）──
        new_fields = [
            ("visit_status", "TEXT DEFAULT ''"),
            ("visit_remark", "TEXT DEFAULT ''"),
            ("visit_time", "TEXT DEFAULT ''"),
            ("registration_form_path", "TEXT DEFAULT ''"),
            ("special_warnings", "TEXT DEFAULT '[]'"),
            ("completed_at", "TEXT DEFAULT ''"),
        ]
        cursor = conn.execute("PRAGMA table_info(complaint_tickets)")
        existing_columns = [row[1] for row in cursor.fetchall()]
        for col_name, col_def in new_fields:
            if col_name not in existing_columns:
                try:
                    conn.execute(f"ALTER TABLE complaint_tickets ADD COLUMN {col_name} {col_def}")
                    print(f"[数据库] 已迁移: complaint_tickets 增加 {col_name} 列")
                except Exception as e:
                    print(f"[数据库] 迁移 {col_name} 失败: {e}")


# ═══════════════════════════════════════════════════
#  投诉工单 CRUD
# ═══════════════════════════════════════════════════

def save_ticket(data: dict) -> str:
    """保存或更新投诉工单，返回 id"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record_id = data.get("id") or str(uuid.uuid4())

    # 序列化嵌套字段
    for key in ("deduction_detail", "training_hours", "query_result", "handle_steps", "attachments"):
        val = data.get(key)
        if isinstance(val, (list, dict)):
            data[key] = json.dumps(val, ensure_ascii=False)

    # 白名单防护：只允许表结构中存在的列名
    ALLOWED_COLUMNS = {
        "id", "ticket_no", "student_name", "id_card", "phone",
        "complaint_content", "complaint_demands", "source_channel",
        "forwarding_dept", "caller_number", "complaint_date",
        "complaint_type", "priority", "query_result",
        "contract_path", "contract_code", "total_fee",
        "deduction_fee", "refund_fee", "deduction_detail",
        "handle_status", "handle_steps", "attachments",
        "reply_path", "feishu_record_id", "feishu_handle_no",
        "license_type", "school_name", "school_short",
        "registration_date", "exam_stage", "student_status",
        "training_hours", "remarks", "created_at", "updated_at",
    }

    with get_db() as conn:
        # 检查同一天同一人是否已有记录（去重逻辑）
        complaint_date = data.get("complaint_date", "")
        id_card = data.get("id_card", "")
        if complaint_date and id_card:
            date_only = complaint_date[:10]  # 取日期部分 YYYY-MM-DD
            existing_same_day = conn.execute(
                "SELECT id FROM complaint_tickets WHERE id_card=? AND date(complaint_date)=? ORDER BY created_at ASC LIMIT 1",
                (id_card, date_only),
            ).fetchone()
            if existing_same_day and existing_same_day[0] != record_id:
                # 同一天已有记录，更新最早的记录
                record_id = existing_same_day[0]
                data["id"] = record_id

        existing = conn.execute("SELECT id FROM complaint_tickets WHERE id=?", (record_id,)).fetchone()
        if existing:
            fields = []
            values = []
            for key, val in data.items():
                if key == "id" or key not in ALLOWED_COLUMNS:
                    continue
                fields.append(f"{key}=?")
                values.append(val)
            fields.append("updated_at=?")
            values.append(now)
            values.append(record_id)
            conn.execute(f"UPDATE complaint_tickets SET {','.join(fields)} WHERE id=?", values)
        else:
            data["id"] = record_id
            data["created_at"] = now
            data["updated_at"] = now
            columns = [c for c in data.keys() if c in ALLOWED_COLUMNS]
            placeholders = ",".join(["?"] * len(columns))
            conn.execute(
                f"INSERT INTO complaint_tickets ({','.join(columns)}) VALUES ({placeholders})",
                [data.get(c, "") for c in columns],
            )
    return record_id


def get_ticket(record_id: str) -> dict | None:
    """按 ID 获取投诉工单"""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM complaint_tickets WHERE id=?", (record_id,)).fetchone()
        if row:
            return _row_to_dict_ticket(row)
    return None


def get_ticket_by_idcard(id_card: str) -> list[dict]:
    """按身份证号获取所有投诉工单"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM complaint_tickets WHERE id_card=? ORDER BY created_at DESC",
            (id_card,),
        ).fetchall()
        return [_row_to_dict_ticket(r) for r in rows]


def get_latest_ticket_by_idcard(id_card: str) -> dict | None:
    """按身份证号获取最新投诉工单"""
    tickets = get_ticket_by_idcard(id_card)
    return tickets[0] if tickets else None


def list_tickets(
    status: str = "",
    source: str = "",
    school: str = "",
    search: str = "",
    start_date: str = "",
    end_date: str = "",
    limit: int = 10,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """查询投诉工单列表，返回 (records, total)"""
    conditions = []
    params = []

    if status:
        conditions.append("handle_status=?")
        params.append(status)
    if source:
        conditions.append("source_channel=?")
        params.append(source)
    if school:
        conditions.append("school_short LIKE ?")
        params.append(f"%{school}%")
    if search:
        conditions.append("(student_name LIKE ? OR id_card LIKE ? OR ticket_no LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])
    if start_date:
        conditions.append("complaint_date>=?")
        params.append(start_date)
    if end_date:
        conditions.append("complaint_date<=?")
        params.append(end_date)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with get_db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM complaint_tickets {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM complaint_tickets {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        records = [_row_to_dict_ticket(r) for r in rows]

    return records, total


def update_ticket(record_id: str, data: dict) -> bool:
    """更新投诉工单指定字段"""
    return bool(save_ticket({**data, "id": record_id}))


def _row_to_dict_ticket(row: sqlite3.Row) -> dict:
    """将数据库行转为字典，反序列化 JSON 字段"""
    d = dict(row)
    for key in ("deduction_detail", "training_hours", "query_result", "handle_steps", "attachments"):
        val = d.get(key, "")
        if isinstance(val, str) and val:
            try:
                d[key] = json.loads(val)
            except json.JSONDecodeError:
                pass
    return d


# ═══════════════════════════════════════════════════
#  回复模板 CRUD
# ═══════════════════════════════════════════════════

def save_template(data: dict) -> str:
    """保存或更新回复模板，返回 id"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    template_id = data.get("id") or str(uuid.uuid4())

    if isinstance(data.get("variables"), list):
        data["variables"] = json.dumps(data["variables"], ensure_ascii=False)

    with get_db() as conn:
        existing = conn.execute("SELECT id FROM reply_templates WHERE id=?", (template_id,)).fetchone()
        if existing:
            fields = []
            values = []
            for key, val in data.items():
                if key == "id":
                    continue
                fields.append(f"{key}=?")
                values.append(val)
            fields.append("updated_at=?")
            values.append(now)
            values.append(template_id)
            conn.execute(f"UPDATE reply_templates SET {','.join(fields)} WHERE id=?", values)
        else:
            if data.get("is_default"):
                conn.execute("UPDATE reply_templates SET is_default=0")
            data["id"] = template_id
            data["created_at"] = now
            data["updated_at"] = now
            columns = list(data.keys())
            placeholders = ",".join(["?"] * len(columns))
            conn.execute(
                f"INSERT INTO reply_templates ({','.join(columns)}) VALUES ({placeholders})",
                [data.get(c, "") for c in columns],
            )
    return template_id


def list_templates() -> list[dict]:
    """列出所有回复模板"""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM reply_templates ORDER BY is_default DESC, created_at DESC").fetchall()
        return [dict(r) for r in rows]


def get_default_template() -> dict | None:
    """获取默认回复模板"""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM reply_templates WHERE is_default=1 LIMIT 1").fetchone()
        return dict(row) if row else None


def delete_template(template_id: str) -> bool:
    """删除回复模板"""
    with get_db() as conn:
        conn.execute("DELETE FROM reply_templates WHERE id=?", (template_id,))
        return conn.total_changes > 0


# ═══════════════════════════════════════════════════
#  投诉统计
# ═══════════════════════════════════════════════════

def get_ticket_statistics(start_date: str = "", end_date: str = "") -> dict:
    """获取投诉工单统计数据，支持按时间筛选（合并查询减少数据库调用）"""
    with get_db() as conn:
        # 时间条件
        conditions = []
        if start_date:
            conditions.append(f"complaint_date >= '{start_date}'")
        if end_date:
            conditions.append(f"complaint_date <= '{end_date}'")

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else "WHERE 1=1"

        # 一条 SQL 聚合：总数 + 各状态 + 退费总额 + 重复投诉数
        agg_row = conn.execute(f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN handle_status='待处理' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN handle_status='处理中' THEN 1 ELSE 0 END) AS processing,
                SUM(CASE WHEN handle_status IN ('已完结','已归档') THEN 1 ELSE 0 END) AS completed,
                COALESCE(SUM(refund_fee), 0) AS refund_sum
            FROM complaint_tickets {where_clause}
        """).fetchone()

        # 一条 SQL 聚合：驾校 / 渠道 / 类型分组
        group_rows = conn.execute(f"""
            SELECT school_short, source_channel, complaint_type,
                   COUNT(*) as cnt
            FROM complaint_tickets {where_clause}
            GROUP BY school_short, source_channel, complaint_type
        """).fetchall()

        # 重复投诉（一次查询）
        repeat_rows = conn.execute(f"""
            SELECT id_card, COUNT(*) as cnt
            FROM complaint_tickets {where_clause} AND id_card != ''
            GROUP BY id_card HAVING cnt > 1
            ORDER BY cnt DESC LIMIT 10
        """).fetchall()

        total = agg_row["total"] or 0
        pending = agg_row["pending"] or 0
        processing = agg_row["processing"] or 0
        completed = agg_row["completed"] or 0
        refund_sum = round(agg_row["refund_sum"], 2)

        # 解析分组结果
        type_map = {"A": "退费纠纷", "B": "教学服务", "C": "考试安排", "D": "合同争议", "E": "其他"}
        school_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        type_counts: dict[str, int] = {}
        for row in group_rows:
            s = row["school_short"] or "未知"
            school_counts[s] = school_counts.get(s, 0) + row["cnt"]
            src = row["source_channel"] or "未知"
            source_counts[src] = source_counts.get(src, 0) + row["cnt"]
            t = type_map.get(row["complaint_type"], row["complaint_type"] or "未知")
            type_counts[t] = type_counts.get(t, 0) + row["cnt"]

        by_school = sorted([{"school": k, "count": v} for k, v in school_counts.items()], key=lambda x: x["count"], reverse=True)[:10]
        by_source = sorted([{"source": k, "count": v} for k, v in source_counts.items()], key=lambda x: x["count"], reverse=True)
        by_type = sorted([{"type": k, "count": v} for k, v in type_counts.items()], key=lambda x: x["count"], reverse=True)

        return {
            "this_total": total,
            "total": total,
            "pending": pending,
            "processing": processing,
            "completed": completed,
            "refund_sum": refund_sum,
            "by_school": by_school,
            "by_source": by_source,
            "by_type": by_type,
            "repeat_count": len(repeat_rows),
            "repeat_tickets": [{"id_card": r[0], "count": r[1]} for r in repeat_rows],
        }


# ═══════════════════════════════════════════════════
#  （向下兼容）旧版 complaints CRUD - 只读
# ═══════════════════════════════════════════════════

def get_complaint_by_idcard(id_card: str) -> dict | None:
    """按身份证号获取旧版投诉记录"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM complaints_v1 WHERE id_card=? ORDER BY created_at DESC LIMIT 1",
            (id_card,),
        ).fetchone()
        if row:
            d = dict(row)
            for key in ("deduction_detail", "training_hours"):
                val = d.get(key, "")
                if isinstance(val, str) and val:
                    try:
                        d[key] = json.loads(val)
                    except json.JSONDecodeError:
                        pass
            return d
    return None


# ═══════════════════════════════════════════════════
#  操作日志
# ═══════════════════════════════════════════════════

def add_log(operation: str, detail: str = "", ticket_id: str = "", success: bool = True):
    """添加操作日志"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.execute(
            "INSERT INTO operation_logs (ticket_id, operation, detail, success, created_at) VALUES (?,?,?,?,?)",
            (ticket_id, operation, detail, 1 if success else 0, now),
        )


def get_recent_logs(limit: int = 20) -> list[dict]:
    """获取最近的操作日志"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM operation_logs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════
#  向下兼容的旧版函数封装
# ═══════════════════════════════════════════════════

def save_complaint(data: dict) -> str:
    """旧版 save_complaint → 调用新版 save_ticket，字段名映射"""
    mapped = {
        "id_card": data.get("id_card", ""),
        "student_name": data.get("name", data.get("student_name", "")),
        "phone": data.get("phone", ""),
        "complaint_date": data.get("complaint_date", ""),
        "complaint_type": data.get("complaint_type", ""),
        "source_channel": data.get("complaint_channel", ""),
        "total_fee": data.get("registration_fee", 0) or data.get("total_fee", 0),
        "deduction_fee": data.get("deduction_fee", 0),
        "refund_fee": data.get("refund_fee", 0),
        "deduction_detail": data.get("deduction_detail", []),
        "contract_code": data.get("contract_code", ""),
        "contract_path": data.get("contract_path", ""),
        "reply_path": data.get("reply_path", ""),
        "feishu_record_id": data.get("feishu_record_id", ""),
        "handle_status": data.get("handle_status", "待处理"),
        "license_type": data.get("license_type", ""),
        "school_name": data.get("school_name", ""),
        "school_short": data.get("school_short", ""),
        "registration_date": data.get("registration_date", ""),
        "exam_stage": data.get("exam_stage", ""),
        "student_status": data.get("student_status", ""),
        "training_hours": data.get("training_hours", {}),
    }
    if data.get("id"):
        mapped["id"] = data["id"]
    return save_ticket(mapped)


def list_complaints(status="", limit=50, offset=0, search="") -> tuple[list, int]:
    """旧版 list_complaints → 调用新版 list_tickets"""
    return list_tickets(status=status, search=search, limit=limit, offset=offset)


def get_statistics() -> dict:
    """旧版 get_statistics → 调用新版 get_ticket_statistics"""
    return get_ticket_statistics()


# ═══════════════════════════════════════════════════
#  合同缓存管理
# ═══════════════════════════════════════════════════

def contract_cache_get(id_card: str) -> dict | None:
    """获取合同缓存，返回 dict 或 None"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM contract_cache WHERE id_card=?",
            (id_card.upper(),)
        ).fetchone()
        if row:
            return {
                "id_card": row["id_card"],
                "student_name": row["student_name"],
                "file_path": row["file_path"],
                "file_size": row["file_size"],
                "downloaded_at": row["downloaded_at"],
            }
    return None


def contract_cache_save(id_card: str, student_name: str, file_path: str, file_size: int) -> None:
    """保存合同缓存（覆盖写入）"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.execute("""
            INSERT INTO contract_cache (id_card, student_name, file_path, file_size, downloaded_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id_card) DO UPDATE SET
                student_name=excluded.student_name,
                file_path=excluded.file_path,
                file_size=excluded.file_size,
                downloaded_at=excluded.downloaded_at
        """, (id_card.upper(), student_name, file_path, file_size, now))


def contract_cache_delete(id_card: str) -> bool:
    """删除合同缓存"""
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM contract_cache WHERE id_card=?",
            (id_card.upper(),)
        )
        return cursor.rowcount > 0


def contract_cache_list() -> list[dict]:
    """列出所有缓存的合同"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM contract_cache ORDER BY downloaded_at DESC"
        ).fetchall()
        return [
            {
                "id_card": row["id_card"],
                "student_name": row["student_name"],
                "file_path": row["file_path"],
                "file_size": row["file_size"],
                "downloaded_at": row["downloaded_at"],
            }
            for row in rows
        ]


def get_distinct_school_short() -> list[str]:
    """获取所有不重复的代号（校区简称）列表"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT school_short FROM complaint_tickets WHERE school_short != '' ORDER BY school_short"
        ).fetchall()
        return [row["school_short"] for row in rows]


# 模块加载时初始化
init_db()
