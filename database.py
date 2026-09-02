"""SQLite 本地数据库 - 投诉工单 + 操作日志 + 回复模板"""
import math
import re
import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from services.org_unit_service import ORGANIZATION_UNITS, normalize_ticket_org_fields, resolve_org_unit
from utils.logger import system_logger

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
                system_logger.info("[数据库] 已迁移: complaints → complaints_v1")

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
                actual_paid REAL DEFAULT 0,
                deduction_fee REAL DEFAULT 0,
                refund_fee REAL DEFAULT 0,
                deduction_detail TEXT DEFAULT '[]',
                contract_set TEXT DEFAULT '{}',
                contract_manifest TEXT DEFAULT '{}',
                contract_text TEXT DEFAULT '{}',

                -- 处理状态
                handle_status TEXT DEFAULT '待处理',
                intake_type TEXT DEFAULT '',
                handle_steps TEXT DEFAULT '[]',
                attachments TEXT DEFAULT '[]',

                -- 产出物
                reply_path TEXT DEFAULT '',
                reply_outdated INTEGER DEFAULT 0,

                -- 学员信息缓存（三系统查询结果中常用的字段）
                license_type TEXT DEFAULT '',
                school_name TEXT DEFAULT '',
                school_short TEXT DEFAULT '',
                organization_unit_id TEXT DEFAULT '',
                organization_unit_type TEXT DEFAULT '',
                organization_unit_name TEXT DEFAULT '',
                organization_unit_code TEXT DEFAULT '',
                registration_date TEXT DEFAULT '',
                exam_stage TEXT DEFAULT '',
                student_status TEXT DEFAULT '',
                training_hours TEXT DEFAULT '{}',

                -- 案件闭环工作台
                handler_name TEXT DEFAULT '',
                complaint_summary TEXT DEFAULT '',
                final_outcome TEXT DEFAULT '',
                fee_plan_status TEXT DEFAULT '',
                fee_plan_version INTEGER DEFAULT 0,
                fee_plan_history TEXT DEFAULT '[]',
                fee_plan_snapshot TEXT DEFAULT '{}',
                fee_confirmed_by TEXT DEFAULT '',
                fee_confirmed_at TEXT DEFAULT '',
                fee_confirm_note TEXT DEFAULT '',
                branch_cooperation TEXT DEFAULT '',
                branch_cooperation_note TEXT DEFAULT '',
                archive_status TEXT DEFAULT '',
                handling_notes TEXT DEFAULT '',
                withdrawn_at TEXT DEFAULT '',
                withdraw_reason TEXT DEFAULT '',
                cancellation_date TEXT DEFAULT '',
                cancellation_source TEXT DEFAULT '',
                cancellation_note TEXT DEFAULT '',

                remarks TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_tickets_id_card ON complaint_tickets(id_card);
            CREATE INDEX IF NOT EXISTS idx_tickets_status ON complaint_tickets(handle_status);
            CREATE INDEX IF NOT EXISTS idx_tickets_source ON complaint_tickets(source_channel);
            CREATE INDEX IF NOT EXISTS idx_tickets_date ON complaint_tickets(complaint_date);
            CREATE INDEX IF NOT EXISTS idx_tickets_ticket_no ON complaint_tickets(ticket_no);

            CREATE TABLE IF NOT EXISTS contract_analysis_jobs (
                id TEXT PRIMARY KEY,
                ticket_id TEXT NOT NULL DEFAULT '',
                fingerprint TEXT NOT NULL DEFAULT '',
                filepath TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'queued',
                result TEXT DEFAULT '{}',
                error TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                started_at TEXT DEFAULT '',
                finished_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_contract_analysis_jobs_active
                ON contract_analysis_jobs(fingerprint, status);

            -- 沟通记录表已废弃（Wave2 迁移至 handling_notes 后由迁移函数 DROP）

            -- 网点车辆数配置表（投诉率分母，人工维护）
            CREATE TABLE IF NOT EXISTS org_vehicle_counts (
                unit_code TEXT PRIMARY KEY,
                unit_name TEXT NOT NULL DEFAULT '',
                vehicle_count INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT ''
            );

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

            -- 数据迁移标记表（避免一次性迁移重复执行）
            CREATE TABLE IF NOT EXISTS migration_flags (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );

            -- 用户表（账号体系：admin/handler/viewer）
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL DEFAULT '',
                real_name TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT 'handler',  -- 'admin' | 'handler' | 'viewer'
                phone TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '启用',    -- '启用' | '停用'
                session_version INTEGER NOT NULL DEFAULT 0,
                last_login_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
            CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

            -- 处理人历史名称 → 用户账号映射（admin 手工配）
            CREATE TABLE IF NOT EXISTS handler_name_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                old_name TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                created_by INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_aliases_user ON handler_name_aliases(user_id);

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
                system_logger.info("[数据库] 已迁移: operation_logs 增加 ticket_id 列")
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
            ("actual_paid", "REAL DEFAULT 0"),
            ("reply_outdated", "INTEGER DEFAULT 0"),
            ("contract_set", "TEXT DEFAULT '{}'"),
            ("contract_manifest", "TEXT DEFAULT '{}'"),
            ("contract_text", "TEXT DEFAULT '{}'"),
            ("visit_status", "TEXT DEFAULT ''"),
            ("visit_remark", "TEXT DEFAULT ''"),
            ("visit_time", "TEXT DEFAULT ''"),
            ("registration_form_path", "TEXT DEFAULT ''"),
            ("special_warnings", "TEXT DEFAULT '[]'"),
            ("completed_at", "TEXT DEFAULT ''"),
            ("processing_started_at", "TEXT DEFAULT ''"),
            ("handler_name", "TEXT DEFAULT ''"),
            ("complaint_summary", "TEXT DEFAULT ''"),
            ("final_outcome", "TEXT DEFAULT ''"),
            ("fee_plan_status", "TEXT DEFAULT ''"),
            ("fee_plan_version", "INTEGER DEFAULT 0"),
            ("fee_plan_history", "TEXT DEFAULT '[]'"),
            ("fee_plan_snapshot", "TEXT DEFAULT '{}'"),
            ("fee_confirmed_by", "TEXT DEFAULT ''"),
            ("fee_confirmed_at", "TEXT DEFAULT ''"),
            ("fee_confirm_note", "TEXT DEFAULT ''"),
            ("branch_cooperation", "TEXT DEFAULT ''"),
            ("branch_cooperation_note", "TEXT DEFAULT ''"),
            ("archive_status", "TEXT DEFAULT ''"),
            ("handling_notes", "TEXT DEFAULT ''"),
            ("withdrawn_at", "TEXT DEFAULT ''"),
            ("withdraw_reason", "TEXT DEFAULT ''"),
            ("organization_unit_id", "TEXT DEFAULT ''"),
            ("organization_unit_type", "TEXT DEFAULT ''"),
            ("organization_unit_name", "TEXT DEFAULT ''"),
            ("organization_unit_code", "TEXT DEFAULT ''"),
            ("cancellation_date", "TEXT DEFAULT ''"),
            ("cancellation_source", "TEXT DEFAULT ''"),
            ("cancellation_note", "TEXT DEFAULT ''"),
            ("withdraw_status", "TEXT DEFAULT '未撤诉'"),
            ("withdraw_updated_at", "TEXT DEFAULT ''"),
            ("negotiation_outcome", "TEXT DEFAULT ''"),
            ("archived_dir", "TEXT DEFAULT ''"),
            ("intake_type", "TEXT DEFAULT ''"),
            ("handler_user_id", "INTEGER DEFAULT NULL"),
            ("handler_external_name", "TEXT DEFAULT ''"),
        ]
        cursor = conn.execute("PRAGMA table_info(complaint_tickets)")
        existing_columns = [row[1] for row in cursor.fetchall()]
        for col_name, col_def in new_fields:
            if col_name not in existing_columns:
                try:
                    conn.execute(f"ALTER TABLE complaint_tickets ADD COLUMN {col_name} {col_def}")
                    system_logger.info("[数据库] 已迁移: complaint_tickets 增加 %s 列", col_name)
                except Exception as e:
                    system_logger.error("[数据库] 迁移 %s 失败: %s", col_name, e)

        # ── 网点车辆数表：增加 unit_type 列（空表时用经营单位字典初始化）──
        vc_columns = [row[1] for row in conn.execute("PRAGMA table_info(org_vehicle_counts)").fetchall()]
        if "unit_type" not in vc_columns:
            try:
                conn.execute("ALTER TABLE org_vehicle_counts ADD COLUMN unit_type TEXT NOT NULL DEFAULT ''")
                system_logger.info("[数据库] 已迁移: org_vehicle_counts 增加 unit_type 列")
            except Exception:
                pass
        if "is_active" not in vc_columns:
            try:
                conn.execute("ALTER TABLE org_vehicle_counts ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
                system_logger.info("[数据库] 已迁移: org_vehicle_counts 增加 is_active 列")
            except Exception:
                pass
        # 同步字典中的 active 状态到车辆表（初始回填/字典变更时保持一致）
        for u in ORGANIZATION_UNITS:
            conn.execute(
                "UPDATE org_vehicle_counts SET is_active=? WHERE unit_code=?",
                (1 if u.get("active", True) else 0, u["code"]),
            )
        for u in ORGANIZATION_UNITS:
            conn.execute(
                "UPDATE org_vehicle_counts SET unit_type=? WHERE unit_code=? AND unit_type=''",
                (u["type"], u["code"]),
            )
        vc_cnt = conn.execute("SELECT COUNT(*) AS cnt FROM org_vehicle_counts").fetchone()["cnt"] or 0
        if vc_cnt == 0:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.executemany(
                "INSERT OR IGNORE INTO org_vehicle_counts (unit_code, unit_name, unit_type, is_active, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                [(u["code"], u["name"], u["type"], 1 if u.get("active", True) else 0, now) for u in ORGANIZATION_UNITS],
            )
            system_logger.info("[数据库] 已初始化: org_vehicle_counts 载入 %d 个网点", len(ORGANIZATION_UNITS))

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("""
            UPDATE contract_analysis_jobs
            SET status='interrupted',
                error='服务重启，任务已中断，可重试',
                finished_at=?,
                updated_at=?
            WHERE status IN ('queued', 'running')
        """, (now, now))


def _ensure_default_admin() -> int:
    """users 表为空时建一个默认 admin/admin 账号（仅首次启动生效）。"""
    from werkzeug.security import generate_password_hash
    with get_db() as conn:
        cnt = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        if cnt > 0:
            return 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """INSERT INTO users (username, password_hash, real_name, role, phone, status,
                                  session_version, last_login_at, created_at, updated_at)
               VALUES (?, ?, ?, 'admin', '', '启用', 0, '', ?, ?)""",
            ("admin", generate_password_hash("admin"), "系统管理员", now, now),
        )
        system_logger.warning(
            "[账号] 已自动创建默认管理员 admin/admin，请登录后立即修改密码"
        )
        return 1


def migrate_handler_names_to_user() -> int:
    """把 aliases 表里配好的映射回写到 complaint_tickets.handler_user_id（幂等）。

    执行规则：
    1. 找到所有 handler_user_id IS NULL 且 handler_name != '' 的工单
    2. 在 aliases 表里按 old_name = handler_name 查映射
    3. 命中则写回 handler_user_id
    4. 不命中则保留原 handler_name 用于「未关联账号」展示

    返回本次回写的工单数。
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT t.id AS ticket_id, t.handler_name, a.user_id
               FROM complaint_tickets t
               JOIN handler_name_aliases a ON a.old_name = t.handler_name
               WHERE t.handler_user_id IS NULL AND t.handler_name != ''"""
        ).fetchall()
        if not rows:
            return 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for r in rows:
            conn.execute(
                "UPDATE complaint_tickets SET handler_user_id=?, updated_at=? WHERE id=?",
                (r["user_id"], now, r["ticket_id"]),
            )
        system_logger.info("[数据库] 历史处理人映射回写：%d 个工单", len(rows))
        return len(rows)


# ═══════════════════════════════════════════════════
#  投诉工单 CRUD
# ═══════════════════════════════════════════════════

def save_ticket(data: dict, force_new: bool = False) -> str:
    """保存或更新投诉工单，返回 id

    force_new=True 时跳过「同日同人」去重合并，强制新建一条工单。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record_id = data.get("id") or str(uuid.uuid4())
    data = normalize_ticket_org_fields(dict(data))

    # 自动设置处理时长相关时间戳
    if data.get("handle_status") == "处理中" and not data.get("processing_started_at"):
        data["processing_started_at"] = now
    if data.get("handle_status") == "已完结" and not data.get("completed_at"):
        data["completed_at"] = now

    # 序列化嵌套字段
    for key in ("deduction_detail", "training_hours", "query_result", "handle_steps", "attachments", "special_warnings", "fee_plan_history", "fee_plan_snapshot", "contract_set", "contract_manifest"):
        val = data.get(key)
        if isinstance(val, (list, dict)):
            data[key] = json.dumps(val, ensure_ascii=False)

    # complaint_date 规范化：非空必须为 YYYY-MM-DD（正则+strptime 双验），
    # 空串/脏格式一律回退当天，杜绝脏日期流入归档目录名与去重 key
    if "complaint_date" in data:
        raw_date = str(data.get("complaint_date") or "").strip()
        date_ok = False
        if raw_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
            try:
                datetime.strptime(raw_date, "%Y-%m-%d")
                date_ok = True
            except ValueError:
                date_ok = False
        if not date_ok:
            data["complaint_date"] = now[:10]

    # 白名单防护：只允许表结构中存在的列名
    ALLOWED_COLUMNS = {
        "id", "ticket_no", "student_name", "id_card", "phone",
        "complaint_content", "complaint_demands", "source_channel",
        "forwarding_dept", "caller_number", "complaint_date",
        "complaint_type", "priority", "query_result",
        "contract_path", "contract_code", "total_fee", "actual_paid",
        "deduction_fee", "refund_fee", "deduction_detail", "contract_set", "contract_manifest", "contract_text",
        "handle_status", "handle_steps", "attachments",
        "reply_path", "reply_outdated",
        "license_type", "school_name", "school_short",
        "organization_unit_id", "organization_unit_type",
        "organization_unit_name", "organization_unit_code",
        "registration_date", "exam_stage", "student_status",
        "training_hours", "remarks", "created_at", "updated_at",
        "visit_status", "visit_remark", "visit_time",
        "registration_form_path", "special_warnings",
        "processing_started_at", "completed_at",
        "handler_name", "complaint_summary", "final_outcome",
        "fee_plan_status", "fee_plan_version", "fee_plan_history", "fee_plan_snapshot",
        "fee_confirmed_by", "fee_confirmed_at", "fee_confirm_note",
        "branch_cooperation",
        "branch_cooperation_note", "archive_status",
        "handling_notes",
        "cancellation_date", "cancellation_source",
        "cancellation_note",
        "withdraw_status", "withdraw_updated_at",
        "withdrawn_at", "withdraw_reason",
        "negotiation_outcome",
        "archived_dir",
        "intake_type",
        "handler_user_id",
        "handler_external_name",
    }

    with get_db() as conn:
        # 去重逻辑：仅在「新建工单」（data 未显式携带 id 且未指定 force_new）时生效，
        # 避免更新既有工单时把改动静默串写到同日最早工单。
        if not data.get("id") and not force_new:
            # 检查同一天同一人是否已有记录（去重逻辑）
            complaint_date = data.get("complaint_date", "")
            id_card = data.get("id_card", "")
            if complaint_date and id_card:
                date_only = complaint_date[:10]  # 取日期部分 YYYY-MM-DD
                existing_same_day = conn.execute(
                    "SELECT id, complaint_content, source_channel FROM complaint_tickets WHERE id_card=? AND date(complaint_date)=? ORDER BY created_at ASC LIMIT 1",
                    (id_card, date_only),
                ).fetchone()
                if existing_same_day and existing_same_day[0] != record_id:
                    # 同一天已有记录，更新最早的记录
                    record_id = existing_same_day[0]
                    data["id"] = record_id
                    # 保底：原记录已有投诉内容/来源渠道时不覆盖，防止二次提交清掉人工补录信息
                    if existing_same_day["complaint_content"]:
                        data.pop("complaint_content", None)
                    if existing_same_day["source_channel"]:
                        data.pop("source_channel", None)

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


def find_same_day_ticket(id_card: str, complaint_date: str) -> dict | None:
    """按 save_ticket 去重同一口径查找「同日同人」既有工单，供受理页预检提示。"""
    id_card = (id_card or "").strip()
    complaint_date = (complaint_date or "").strip()[:10]
    if not id_card or not complaint_date:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM complaint_tickets WHERE id_card=? AND date(complaint_date)=? ORDER BY created_at ASC LIMIT 1",
            (id_card, complaint_date),
        ).fetchone()
    return _row_to_dict_ticket(row) if row else None


def find_open_ticket_by_idcard(id_card: str) -> dict | None:
    """按身份证号反查「未结案」工单（最早的），供受理页跨日并入预检。

    业务语义：投诉处理是长期过程，学员一周前的工单今天仍可能在处理；
    只要该工单尚未完结、未撤诉、未归档，就视为同一件事的延续。
    返回最早创建的一条（created_at ASC），保证多人接手时仍是同一个工单。
    """
    id_card = (id_card or "").strip()
    if not id_card:
        return None
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT * FROM complaint_tickets
            WHERE id_card=?
              AND COALESCE(handle_status,'') != '已完结'
              AND COALESCE(withdraw_status,'') != '已撤诉'
              AND COALESCE(archive_status,'') != '已归档'
            ORDER BY created_at ASC LIMIT 1
            """,
            (id_card,),
        ).fetchone()
    return _row_to_dict_ticket(row) if row else None


def find_latest_ticket_by_id_card(id_card: str) -> dict | None:
    """按身份证号反查最近一条工单（不受日期限制），用于 download/upload 等
    路径补全：前端未带 ticket_id 时，仍能拿到真实单位字段，避免归档目录退化
    为「未归属/未归属-未归属/...」。"""
    id_card = (id_card or "").strip()
    if not id_card:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM complaint_tickets WHERE id_card=? ORDER BY created_at DESC LIMIT 1",
            (id_card,),
        ).fetchone()
    return _row_to_dict_ticket(row) if row else None


def get_ticket(record_id: str) -> dict | None:
    """按 ID 获取投诉工单"""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM complaint_tickets WHERE id=?", (record_id,)).fetchone()
        if row:
            return _row_to_dict_ticket(row)
    return None


def _row_to_contract_analysis_job(row: sqlite3.Row | None) -> dict | None:
    if not row:
        return None
    job = dict(row)
    try:
        job["result"] = json.loads(job.get("result") or "{}")
    except json.JSONDecodeError:
        job["result"] = {}
    return job


def create_contract_analysis_job(data: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    job_id = data.get("id") or uuid.uuid4().hex
    with get_db() as conn:
        conn.execute("""
            INSERT INTO contract_analysis_jobs (
                id, ticket_id, fingerprint, filepath, status, result, error,
                created_at, started_at, finished_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job_id,
            data.get("ticket_id", ""),
            data.get("fingerprint", ""),
            data.get("filepath", ""),
            data.get("status", "queued"),
            json.dumps(data.get("result", {}), ensure_ascii=False),
            data.get("error", ""),
            data.get("created_at", now),
            data.get("started_at", ""),
            data.get("finished_at", ""),
            now,
        ))
    return job_id


def get_contract_analysis_job(job_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, ticket_id, fingerprint, filepath, status, result, error,"
            " created_at, started_at, finished_at, updated_at"
            " FROM contract_analysis_jobs WHERE id=?", (job_id,)
        ).fetchone()
    return _row_to_contract_analysis_job(row)


def find_active_contract_analysis_job(fingerprint: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("""
            SELECT id, ticket_id, fingerprint, filepath, status, result, error,
                   created_at, started_at, finished_at, updated_at
            FROM contract_analysis_jobs
            WHERE fingerprint=? AND status IN ('queued', 'running')
            ORDER BY created_at DESC LIMIT 1
        """, (fingerprint,)).fetchone()
    return _row_to_contract_analysis_job(row)


def update_contract_analysis_job(job_id: str, **updates) -> None:
    allowed = {"status", "result", "error", "started_at", "finished_at"}
    fields = []
    values = []
    for key, value in updates.items():
        if key not in allowed:
            continue
        if key == "result" and isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        fields.append(f"{key}=?")
        values.append(value)
    if not fields:
        return
    fields.append("updated_at=?")
    values.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    values.append(job_id)
    with get_db() as conn:
        conn.execute(
            f"UPDATE contract_analysis_jobs SET {','.join(fields)} WHERE id=?",
            values,
        )


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
    repeat_only: bool = False,
    limit: int = 10,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """查询投诉工单列表，返回 (records, total)

    repeat_only: 仅保留同一身份证出现多次的重复投诉工单
    """
    conditions = []
    params = []

    if status:
        conditions.append("handle_status=?")
        params.append(status)
    if source:
        conditions.append("source_channel=?")
        params.append(source)
    if school:
        # 下钻网点时同时匹配分店自身代号与所属分校代号
        like = f"%{school}%"
        conditions.append("(school_short LIKE ? OR organization_unit_code LIKE ?)")
        params.extend([like, like])
    if search:
        # 合一模糊搜索：学员姓名 / 身份证 / 工单号 / 网点代号
        like = f"%{search}%"
        conditions.append("(student_name LIKE ? OR id_card LIKE ? OR ticket_no LIKE ? OR school_short LIKE ?)")
        params.extend([like, like, like, like])
    if start_date:
        conditions.append("complaint_date>=?")
        params.append(start_date)
    if end_date:
        conditions.append("complaint_date<=?")
        params.append(end_date)
    if repeat_only:
        inner = " AND ".join(conditions) if conditions else "1=1"
        conditions.append(
            "id_card IN (SELECT id_card FROM complaint_tickets "
            f"WHERE {inner} AND id_card != '' GROUP BY id_card HAVING COUNT(*)>1)"
        )
        params = params + list(params)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with get_db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM complaint_tickets {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM complaint_tickets {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        records = [_row_to_dict_ticket(r) for r in rows]

    return records, total


def search_students(
    name: str = "",
    school_short: str = "",
    start_date: str = "",
    end_date: str = "",
    limit: int = 30,
) -> list[dict]:
    """统一智能查询：按学员姓名（模糊）+ 报名点 + 报名时间范围检索本地学员档案。

    返回按身份证号聚合（取最新一条）的去重学员列表，用于受理员只知道姓名/报名点/
    报名时间时模糊查找学员。
    """
    conditions = []
    params = []
    if name:
        like = f"%{name}%"
        conditions.append("(student_name LIKE ? OR id_card LIKE ?)")
        params.extend([like, like])
    if school_short and school_short != "全部":
        conditions.append("school_short=?")
        params.append(school_short)
    if start_date:
        conditions.append("registration_date>=?")
        params.append(start_date)
    if end_date:
        conditions.append("registration_date<=?")
        params.append(end_date)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT id_card, student_name, phone, school_short, license_type,
                       registration_date, exam_stage, student_status,
                       MAX(created_at) AS created_at
                FROM complaint_tickets {where}
                GROUP BY id_card
                ORDER BY created_at DESC
                LIMIT ?""",
            params + [limit],
        ).fetchall()
    return [_row_to_dict_ticket(r) for r in rows]


def update_ticket(record_id: str, data: dict) -> bool:
    """更新投诉工单指定字段"""
    return bool(save_ticket({**data, "id": record_id}))


def delete_ticket(record_id: str) -> bool:
    """删除投诉工单及其关联的合同分析任务记录"""
    with get_db() as conn:
        conn.execute("DELETE FROM contract_analysis_jobs WHERE ticket_id=?", (record_id,))
        cur = conn.execute("DELETE FROM complaint_tickets WHERE id=?", (record_id,))
        return cur.rowcount > 0


def _row_to_dict_ticket(row: sqlite3.Row) -> dict:
    """将数据库行转为字典，反序列化 JSON 字段"""
    d = dict(row)
    for key in ("deduction_detail", "training_hours", "query_result", "handle_steps", "attachments", "special_warnings", "fee_plan_history", "fee_plan_snapshot", "contract_set", "contract_manifest"):
        val = d.get(key, "")
        if isinstance(val, str) and val:
            try:
                d[key] = json.loads(val)
            except json.JSONDecodeError:
                pass
    d["reply_outdated"] = bool(d.get("reply_outdated", 0))
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
        if data.get("is_default"):
            conn.execute("UPDATE reply_templates SET is_default=0")
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
        rows = conn.execute(
            "SELECT id, name, description, template_path, variables, is_default,"
            " created_at, updated_at FROM reply_templates"
            " ORDER BY is_default DESC, created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_default_template() -> dict | None:
    """获取默认回复模板"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, name, description, template_path, variables, is_default,"
            " created_at, updated_at FROM reply_templates WHERE is_default=1 LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def delete_template(template_id: str) -> bool:
    """删除回复模板"""
    with get_db() as conn:
        cur = conn.execute("DELETE FROM reply_templates WHERE id=?", (template_id,))
        return cur.rowcount > 0


# ═══════════════════════════════════════════════════
#  网点车辆数配置（投诉率分母）
# ═══════════════════════════════════════════════════

def get_org_vehicle_counts() -> dict[str, int]:
    """获取所有网点的车辆数配置 {unit_code: count}"""
    with get_db() as conn:
        rows = conn.execute("SELECT unit_code, vehicle_count FROM org_vehicle_counts").fetchall()
        return {r["unit_code"]: r["vehicle_count"] for r in rows}


def get_org_vehicle_count_items() -> list[dict]:
    """获取所有网点的车辆数配置列表（类型/名称/代号/车辆数/状态）"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT unit_code, unit_name, unit_type, vehicle_count, is_active FROM org_vehicle_counts"
            " ORDER BY is_active DESC, unit_type DESC, unit_code"
        ).fetchall()
        return [
            {
                "unit_code": r["unit_code"],
                "unit_name": r["unit_name"],
                "unit_type": r["unit_type"],
                "vehicle_count": r["vehicle_count"] or 0,
                "is_active": 1 if r["is_active"] else 0,
            }
            for r in rows
        ]


def save_org_vehicle_count(
    unit_code: str,
    unit_name: str = "",
    unit_type: str = "",
    vehicle_count: int = 0,
    is_active: int = 1,
) -> bool:
    """保存单个网点车辆数（upsert）"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO org_vehicle_counts (unit_code, unit_name, unit_type, vehicle_count, is_active, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(unit_code) DO UPDATE SET
                unit_name=excluded.unit_name,
                unit_type=excluded.unit_type,
                vehicle_count=excluded.vehicle_count,
                is_active=excluded.is_active,
                updated_at=excluded.updated_at
            """,
            (unit_code, unit_name, unit_type, int(vehicle_count or 0), 1 if is_active else 0, now),
        )
        return True


def save_org_vehicle_counts(items: list[dict]) -> int:
    """全量保存网点车辆数配置（不在列表中的网点将被删除），返回成功条数"""
    saved = 0
    codes: set[str] = set()
    for item in items or []:
        code = str(item.get("unit_code") or "").strip()
        if not code or code in codes:
            continue
        codes.add(code)
        save_org_vehicle_count(
            code,
            str(item.get("unit_name") or ""),
            str(item.get("unit_type") or ""),
            int(item.get("vehicle_count") or 0),
            1 if item.get("is_active", True) else 0,
        )
        saved += 1
    with get_db() as conn:
        if codes:
            placeholders = ",".join("?" for _ in codes)
            conn.execute(
                f"DELETE FROM org_vehicle_counts WHERE unit_code NOT IN ({placeholders})",
                tuple(codes),
            )
        else:
            conn.execute("DELETE FROM org_vehicle_counts")
    return saved


# ═══════════════════════════════════════════════════
#  投诉统计
# ═══════════════════════════════════════════════════

def _previous_month(ym: str) -> str:
    """返回 YYYY-MM 的上一个月（如 2026-01 → 2025-12）"""
    try:
        year, month = int(ym[:4]), int(ym[5:7])
        month -= 1
        if month == 0:
            year -= 1
            month = 12
        return f"{year:04d}-{month:02d}"
    except (ValueError, IndexError):
        return ym


def _shifted_range(start_str: str, end_str: str):
    """本期窗口等长向前平移得到上期窗口（与前端 shiftedRange 一致）。

    返回 (prev_start, prev_end)，均为 YYYY-MM-DD。用于环比的"上期"基准，
    保证分子（本期）与分母（上期）使用同一时间长度、同一范围/网点条件。
    """
    from datetime import datetime, timedelta
    try:
        s = datetime.strptime(start_str, "%Y-%m-%d")
        e = datetime.strptime(end_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None, None
    length = (e - s).days + 1
    p1 = s - timedelta(days=length)
    p2 = s - timedelta(days=1)
    return p1.strftime("%Y-%m-%d"), p2.strftime("%Y-%m-%d")


def get_ticket_statistics(
    start_date: str = "",
    end_date: str = "",
    scope: str = "all",
    unit_code: str = "",
) -> dict:
    """获取投诉工单统计数据，支持按时间筛选、网点类型（all/branch/store）与单网点过滤。

    scope: all=全部网点 / branch=仅分校 / store=仅分店
    unit_code: 指定单个网点代号时，只统计该网点（下钻详情用）
    """
    with get_db() as conn:
        # 时间条件 + 网点范围条件
        conditions = []
        params = []
        if start_date:
            conditions.append("complaint_date >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("complaint_date <= ?")
            params.append(end_date)
        if unit_code:
            conditions.append("(organization_unit_code = ? OR school_short = ?)")
            params.extend([unit_code, unit_code])
        elif scope == "branch":
            conditions.append("organization_unit_type = '分校'")
        elif scope == "store":
            conditions.append("organization_unit_type = '分店'")

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else "WHERE 1=1"

        # 一条 SQL 聚合：总数 + 各状态（三桶均排除已撤诉）+ 撤诉独立桶 + 有效投诉量（未撤诉）
        agg_row = conn.execute(f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN handle_status='待处理' AND COALESCE(withdraw_status,'')!='已撤诉' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN handle_status='处理中' AND COALESCE(withdraw_status,'')!='已撤诉' THEN 1 ELSE 0 END) AS processing,
                SUM(CASE WHEN (handle_status='已完结' OR archive_status='已归档') AND COALESCE(withdraw_status,'')!='已撤诉' THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN COALESCE(withdraw_status,'')='已撤诉' THEN 1 ELSE 0 END) AS withdrawn_bucket,
                SUM(CASE WHEN TRIM(COALESCE(withdrawn_at,''))!='' OR withdraw_status='已撤诉'
                         THEN 1 ELSE 0 END) AS withdrawn_total,
                SUM(CASE WHEN TRIM(COALESCE(withdrawn_at,''))='' AND COALESCE(withdraw_status,'')!='已撤诉'
                         THEN 1 ELSE 0 END) AS effective_total,
                SUM(CASE WHEN TRIM(COALESCE(intake_type,''))!='' THEN 1 ELSE 0 END) AS manual_no_record
            FROM complaint_tickets {where_clause}
        """, params).fetchone()

        integrity_row = conn.execute(f"""
            SELECT
                SUM(CASE WHEN archive_status='已归档' AND handle_status!='已完结' THEN 1 ELSE 0 END) AS archived_not_completed
            FROM complaint_tickets {where_clause}
        """, params).fetchone()

        # 一条 SQL 聚合：经营单位 / 渠道 / 类型 / 结果 / 配合度分组（撤诉量单独累计）
        group_rows = conn.execute(f"""
            SELECT school_short, organization_unit_name, organization_unit_code, organization_unit_type,
                   source_channel, complaint_type, final_outcome, branch_cooperation,
                   COUNT(*) as cnt,
                   SUM(CASE WHEN TRIM(COALESCE(withdrawn_at,''))!='' OR withdraw_status='已撤诉'
                            THEN 1 ELSE 0 END) as withdrawn_cnt,
                   SUM(CASE WHEN COALESCE(withdraw_status,'')!='已撤诉'
                            THEN 1 ELSE 0 END) as active_cnt,
                   SUM(CASE WHEN TRIM(COALESCE(intake_type,''))!='' THEN 1 ELSE 0 END) as manual_cnt
            FROM complaint_tickets {where_clause}
            GROUP BY school_short, organization_unit_name, organization_unit_code, organization_unit_type,
                     source_channel, complaint_type, final_outcome, branch_cooperation
        """, params).fetchall()

        # 重复投诉（一次查询）
        repeat_rows = conn.execute(f"""
            SELECT id_card, COUNT(*) as cnt
            FROM complaint_tickets {where_clause} AND id_card != ''
            GROUP BY id_card HAVING cnt > 1
            ORDER BY cnt DESC LIMIT 10
        """, params).fetchall()

        daily_rows = conn.execute(f"""
            SELECT date(complaint_date) AS complaint_day, COUNT(*) AS cnt
            FROM complaint_tickets {where_clause}
            AND complaint_date != ''
            AND COALESCE(withdraw_status,'')!='已撤诉'
            GROUP BY date(complaint_date)
            ORDER BY complaint_day ASC
        """, params).fetchall()

        # 月度聚合（YYYY-MM），供看板趋势图与环比/同比使用
        month_rows = conn.execute(f"""
            SELECT substr(complaint_date, 1, 7) AS ym, COUNT(*) AS cnt
            FROM complaint_tickets {where_clause}
            AND complaint_date != ''
            AND COALESCE(withdraw_status,'')!='已撤诉'
            GROUP BY ym
            ORDER BY ym ASC
        """, params).fetchall()

        # 环比基准（窗口感知）：上期窗口 = 本期窗口等长向前平移；
        # 分子（本期）与分母（上期）使用同一套 where_clause（时间 + 范围 + 网点）。
        # 全部时间（无起止）无"上一周期"可比，mom_change 置 None。
        last_ym = month_rows[-1]["ym"] if month_rows else ""
        prev_month_count = 0
        prev_window_start = ""
        prev_window_end = ""
        if start_date and end_date:
            p_start, p_end = _shifted_range(start_date, end_date)
            prev_window_start, prev_window_end = p_start, p_end
            prev_conditions = ["complaint_date >= ?", "complaint_date <= ?"]
            prev_params = [p_start, p_end]
            if unit_code:
                prev_conditions.append("(organization_unit_code = ? OR school_short = ?)")
                prev_params.extend([unit_code, unit_code])
            elif scope == "branch":
                prev_conditions.append("organization_unit_type = '分校'")
            elif scope == "store":
                prev_conditions.append("organization_unit_type = '分店'")
            prev_where = "WHERE " + " AND ".join(prev_conditions)
            prev_rows = conn.execute(f"""
                SELECT COUNT(*) AS cnt FROM complaint_tickets {prev_where}
                AND complaint_date != ''
                AND COALESCE(withdraw_status,'')!='已撤诉'
                AND TRIM(COALESCE(withdrawn_at,''))=''
            """, prev_params).fetchone()
            prev_month_count = prev_rows["cnt"] or 0

        # 车辆数配置（投诉率分母）
        vehicle_counts = get_org_vehicle_counts()

        total = agg_row["total"] or 0
        pending = agg_row["pending"] or 0
        processing = agg_row["processing"] or 0
        completed = agg_row["completed"] or 0
        effective_total = agg_row["effective_total"] or 0
        withdrawn_total = agg_row["withdrawn_total"] or 0
        archived_not_completed = integrity_row["archived_not_completed"] or 0

        # 按 complaint_type 分组计数（撤诉案件不计入有效，但单独列出撤诉量）
        type_group_rows = conn.execute(f"""
            SELECT complaint_type, COUNT(*) as cnt
            FROM complaint_tickets {where_clause}
            GROUP BY complaint_type
        """, params).fetchall()

        # 解析分组结果
        type_map = {"A": "退费纠纷", "B": "教学服务", "C": "考试安排", "D": "合同争议", "E": "其他"}
        school_counts: dict[str, dict] = {}
        source_counts: dict[str, int] = {}
        type_counts: dict[str, int] = {}
        outcome_counts: dict[str, int] = {}
        cooperation_counts: dict[str, int] = {}
        for row in group_rows:
            code = row["organization_unit_code"] or row["school_short"] or ""
            unit = resolve_org_unit(code, row["organization_unit_name"] or "")
            name = row["organization_unit_name"] or (unit or {}).get("name") or code or "未知"
            unit_type = row["organization_unit_type"] or (unit or {}).get("type") or "未知"
            key = (unit_type, name, code or (unit or {}).get("code", ""))
            school_counts.setdefault(key, {
                "school": name,
                "code": code or (unit or {}).get("code", ""),
                "unit_type": unit_type,
                "count": 0,
                "total_count": 0,
                "cancelled_count": 0,
                "manual_count": 0,
            })
            school_counts[key]["total_count"] += row["cnt"]
            withdrawn_cnt = row["withdrawn_cnt"] or 0
            school_counts[key]["cancelled_count"] += withdrawn_cnt
            school_counts[key]["count"] += row["cnt"] - withdrawn_cnt
            school_counts[key]["manual_count"] += row["manual_cnt"] or 0
            if (row["active_cnt"] or 0) > 0:
                src = row["source_channel"] or "未知"
                source_counts[src] = source_counts.get(src, 0) + row["active_cnt"]
                t = type_map.get(row["complaint_type"], row["complaint_type"] or "未知")
                type_counts[t] = type_counts.get(t, 0) + row["active_cnt"]
            outcome = row["final_outcome"] or "未定结果"
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + row["cnt"]
            cooperation = row["branch_cooperation"] or "未评价"
            cooperation_counts[cooperation] = cooperation_counts.get(cooperation, 0) + row["cnt"]

        # 给每个网点挂车辆数与投诉率（有效投诉 ÷ 车辆数）
        for item in school_counts.values():
            code = item["code"]
            vc = vehicle_counts.get(code) or 0
            item["vehicle_count"] = vc
            item["complaint_rate"] = round(item["count"] / vc * 100, 2) if vc > 0 else None
        school_items = list(school_counts.values())
        by_school = sorted(school_items, key=lambda x: x["count"], reverse=True)[:10]
        by_rate = sorted(
            [x for x in school_items if x["complaint_rate"] is not None],
            key=lambda x: x["complaint_rate"],
            reverse=True,
        )[:10]

        by_source = sorted([{"source": k, "count": v} for k, v in source_counts.items()], key=lambda x: x["count"], reverse=True)
        by_type = sorted([{"type": k, "count": v} for k, v in type_counts.items()], key=lambda x: x["count"], reverse=True)
        by_complaint_type = sorted(
            [
                {"complaint_type": row["complaint_type"] or "未知", "count": row["cnt"] or 0}
                for row in type_group_rows
            ],
            key=lambda x: x["count"],
            reverse=True,
        )
        by_outcome = sorted([{"outcome": k, "count": v} for k, v in outcome_counts.items()], key=lambda x: x["count"], reverse=True)
        by_branch_cooperation = sorted(
            [{"cooperation": k, "count": v} for k, v in cooperation_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )

        return {
            "this_total": total,
            "total": total,
            "pending": pending,
            "processing": processing,
            "completed": completed,
            "withdrawn": agg_row["withdrawn_bucket"] or 0,
            "effective_total": effective_total,
            "withdrawn_total": withdrawn_total,
            "cancelled_total": withdrawn_total,
            "manual_no_record_count": agg_row["manual_no_record"] or 0,
            "integrity_issues": {
                "archived_not_completed": archived_not_completed,
                "total": archived_not_completed,
            },
            "refund_sum": 0,
            "by_school": by_school,
            "by_rate": by_rate,
            "by_source": by_source,
            "by_type": by_type,
            "by_complaint_type": by_complaint_type,
            "by_outcome": by_outcome,
            "by_branch_cooperation": by_branch_cooperation,
            "monthly_trend": [
                {"month": row["ym"], "count": row["cnt"]}
                for row in month_rows
            ],
            "monthly_compare": {
                "current_month": start_date or last_ym,
                "current_month_count": effective_total,
                "previous_month": prev_window_start or (_previous_month(last_ym) if last_ym else ""),
                "previous_month_count": prev_month_count,
                "mom_change": (
                    round((effective_total - prev_month_count) / prev_month_count * 100, 1)
                    if start_date and end_date and prev_month_count > 0
                    else None
                ),
            },
            "total_vehicle_count": sum(vehicle_counts.values()),
            "total_complaint_rate": (
                round(effective_total / sum(vehicle_counts.values()) * 100, 2)
                if sum(vehicle_counts.values()) > 0
                else None
            ),
            "daily_trend": [
                {"date": row["complaint_day"], "count": row["cnt"]}
                for row in daily_rows
            ],
            "repeat_count": len(repeat_rows),
            "repeat_tickets": [{"id_card": r[0], "count": r[1]} for r in repeat_rows],
        }


# ═══════════════════════════════════════════════════
#  处理时长统计
# ═══════════════════════════════════════════════════

def get_processing_duration_stats(date_start: str = "", date_end: str = "", unit_code: str = "") -> dict:
    """获取处理时长统计"""
    with get_db() as conn:
        conditions = []
        params = []
        if date_start:
            conditions.append("complaint_date >= ?")
            params.append(date_start)
        if date_end:
            conditions.append("complaint_date <= ?")
            params.append(date_end)
        if unit_code:
            conditions.append("(organization_unit_code = ? OR school_short = ?)")
            params.extend([unit_code, unit_code])

        where = " AND ".join(conditions) if conditions else "1=1"

        row = conn.execute(f"""
            SELECT
                COUNT(*) as completed_count,
                AVG(
                    CASE
                        WHEN completed_at != '' AND created_at != ''
                        THEN (julianday(completed_at) - julianday(created_at)) * 24
                        ELSE NULL
                    END
                ) as avg_hours
            FROM complaint_tickets
            WHERE handle_status = '已完结' AND {where}
        """, params).fetchone()

        overdue = conn.execute(f"""
            SELECT COUNT(*) FROM complaint_tickets
            WHERE handle_status IN ('待处理', '处理中')
            AND COALESCE(withdraw_status,'')!='已撤诉'
            AND COALESCE(archive_status,'')!='已归档'
            AND complaint_date != ''
            AND julianday(date('now')) - julianday(date(complaint_date)) > 7
            AND {where}
        """, params).fetchone()[0]

        by_school = conn.execute(f"""
            SELECT school_short,
                COUNT(*) as count,
                AVG(
                    CASE
                        WHEN completed_at != '' AND created_at != ''
                        THEN (julianday(completed_at) - julianday(created_at)) * 24
                        ELSE NULL
                    END
                ) as avg_hours
            FROM complaint_tickets
            WHERE handle_status = '已完结' AND {where}
            GROUP BY school_short
            ORDER BY avg_hours DESC
        """, params).fetchall()

        return {
            "completed_count": row[0] or 0,
            "avg_processing_hours": round(row[1], 1) if row[1] else 0,
            "overdue_count": overdue,
            "by_school": [
                {"school": r[0], "count": r[1], "avg_hours": round(r[2], 1) if r[2] else 0}
                for r in by_school
            ],
        }


# ═══════════════════════════════════════════════════
#  （向下兼容）旧版 complaints CRUD - 只读
# ═══════════════════════════════════════════════════

def get_complaint_by_idcard(id_card: str) -> dict | None:
    """按身份证号获取旧版投诉记录"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, id_card, student_name, registration_fee, deduction_fee,"
            " refund_fee, deduction_detail, contract_code, contract_path, created_at"
            " FROM complaints_v1 WHERE id_card=? ORDER BY created_at DESC LIMIT 1",
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
            "SELECT id, complaint_id, ticket_id, operation, detail, success, created_at"
            " FROM operation_logs ORDER BY created_at DESC LIMIT ?",
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
        "actual_paid": data.get("actual_paid", 0),
        "deduction_fee": data.get("deduction_fee", 0),
        "refund_fee": data.get("refund_fee", 0),
        "deduction_detail": data.get("deduction_detail", []),
        "contract_code": data.get("contract_code", ""),
        "contract_path": data.get("contract_path", ""),
        "reply_path": data.get("reply_path", ""),
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
            "SELECT id_card, student_name, file_path, file_size, downloaded_at"
            " FROM contract_cache WHERE id_card=?",
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
            "SELECT id_card, student_name, file_path, file_size, downloaded_at"
            " FROM contract_cache ORDER BY downloaded_at DESC"
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


# ═══════════════════════════════════════════════════
#  回访记录（已弃用，统一为沟通记录 communication_records）
# ═══════════════════════════════════════════════════

def save_visit_record(ticket_id: str, visit_date: str, visit_status: str, visit_remark: str) -> int:
    """Deprecated: 回访记录模型已移除，统一使用 communication_records。保留签名避免外部误用。"""
    return None


def get_visit_records(ticket_id: str) -> list[dict]:
    """Deprecated: 回访记录模型已移除，返回空列表。"""
    return []


def delete_visit_record(record_id: int) -> bool:
    """Deprecated: 回访记录模型已移除。"""
    return False


# ═══════════════════════════════════════════════════
#  沟通记录 CRUD
# ═══════════════════════════════════════════════════

def save_communication_record(
    ticket_id: str,
    fee_plan_version: int = 0,
    contact_time: str = "",
    contact_method: str = "",
    summary: str = "",
    student_intention: str = "",
    next_follow_up: str = "",
) -> int:
    """保存一次学员沟通记录。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not contact_time:
        contact_time = now
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO communication_records
            (ticket_id, fee_plan_version, contact_time, contact_method, summary, student_intention, next_follow_up, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket_id, int(fee_plan_version or 0), contact_time, contact_method,
                summary, student_intention, next_follow_up, now,
            ),
        )
        conn.execute(
            "UPDATE complaint_tickets SET updated_at=? WHERE id=?",
            (now, ticket_id),
        )
        return cursor.lastrowid


def get_communication_records(ticket_id: str) -> list[dict]:
    """获取某个投诉案件的所有沟通记录。"""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, ticket_id, fee_plan_version, contact_time, contact_method, summary,
                   student_intention, next_follow_up, created_at
            FROM communication_records
            WHERE ticket_id=?
            ORDER BY contact_time ASC, id ASC
            """,
            (ticket_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def delete_communication_record(record_id: int, ticket_id: str = "") -> bool:
    """删除沟通记录，可限定所属案件。"""
    with get_db() as conn:
        if ticket_id:
            cursor = conn.execute(
                "DELETE FROM communication_records WHERE id=? AND ticket_id=?",
                (record_id, ticket_id),
            )
        else:
            cursor = conn.execute("DELETE FROM communication_records WHERE id=?", (record_id,))
        return cursor.rowcount > 0


# ═══════════════════════════════════════════════════
#  v2 迁移：多轮沟通记录 → handling_notes
# ═══════════════════════════════════════════════════

def migrate_communications_to_notes() -> int:
    """把每个工单的存量沟通记录摘要拼接为 handling_notes 初始文本（幂等）。

    格式：每条一行「[日期] 摘要；」。迁完即删除源记录并打标记，
    再次执行时无源记录可迁，直接返回 0，不会重复追加。
    """
    with get_db() as conn:
        marked = conn.execute(
            "SELECT value FROM migration_flags WHERE key='communications_to_notes'"
        ).fetchone()
        if marked and marked["value"] == "done":
            conn.execute("DROP TABLE IF EXISTS communication_records")
            return 0
        has_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='communication_records'"
        ).fetchone()
        rows = (
            conn.execute(
                "SELECT ticket_id, contact_time, created_at, summary"
                " FROM communication_records ORDER BY ticket_id, contact_time ASC, id ASC"
            ).fetchall()
            if has_table
            else []
        )
        grouped: dict[str, list[str]] = {}
        for r in rows:
            stamp = (r["contact_time"] or r["created_at"] or "")[:10]
            summary = (r["summary"] or "").strip()
            if not summary:
                continue
            grouped.setdefault(r["ticket_id"], []).append(f"[{stamp}] {summary}；")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        migrated = 0
        for ticket_id, lines in grouped.items():
            row = conn.execute(
                "SELECT handling_notes FROM complaint_tickets WHERE id=?", (ticket_id,)
            ).fetchone()
            if not row:
                continue
            existing = (row["handling_notes"] or "").strip()
            text = "\n".join(lines)
            new_notes = text if not existing else existing + "\n" + text
            conn.execute(
                "UPDATE complaint_tickets SET handling_notes=?, updated_at=? WHERE id=?",
                (new_notes, now, ticket_id),
            )
            migrated += 1
        conn.execute("DROP TABLE IF EXISTS communication_records")
        conn.execute(
            "INSERT OR REPLACE INTO migration_flags (key, value) VALUES ('communications_to_notes', 'done')"
        )
    system_logger.info("[数据库] 沟通记录已迁移至 handling_notes，源表已删除：%d 个工单", migrated)
    return migrated


# 模块加载时初始化
init_db()
_ensure_default_admin()
migrate_handler_names_to_user()
