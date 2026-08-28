"""账号体系业务服务 - users + handler_name_aliases CRUD"""
import re
import sqlite3
from datetime import datetime
from typing import Optional

import database  # 用 database.DB_PATH 动态引用（测试时会改）
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_db


def _write_conn() -> sqlite3.Connection:
    """写操作专用短连接：自连接自关闭，避免和 get_db 复用同一线程连接导致死锁。"""
    conn = sqlite3.connect(str(database.DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


from contextlib import contextmanager

@contextmanager
def _write():
    conn = _write_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

VALID_ROLES = ("admin", "handler", "viewer")
ROLE_LABEL = {"admin": "管理员", "handler": "投诉专员", "viewer": "只读审计"}


def _row_to_user(row: sqlite3.Row) -> dict:
    d = dict(row)
    d.pop("password_hash", None)
    return d


def get_user_by_id(user_id: int) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_username(username: str) -> Optional[dict]:
    """返回带 password_hash 的完整行（仅供登录校验用）。"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username=?", (username,)
        ).fetchone()
    return dict(row) if row else None


def list_users(role: str = "", status: str = "") -> list[dict]:
    conditions, params = [], []
    if role:
        conditions.append("role=?")
        params.append(role)
    if status:
        conditions.append("status=?")
        params.append(status)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM users {where} ORDER BY id ASC", params
        ).fetchall()
    return [_row_to_user(r) for r in rows]


def list_assignable_users() -> list[dict]:
    """处理人下拉候选：admin + handler，排除 viewer 和停用账号。"""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, username, real_name, role FROM users
               WHERE status='启用' AND role IN ('admin','handler')
               ORDER BY (role='admin') DESC, real_name ASC"""
        ).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["role_label"] = ROLE_LABEL.get(d["role"], d["role"])
        items.append(d)
    return items


def verify_password(user: dict, password: str) -> bool:
    if not user or not user.get("password_hash"):
        return False
    return check_password_hash(user["password_hash"], password)


def create_user(
    username: str,
    password: str,
    real_name: str,
    role: str,
    phone: str = "",
) -> dict:
    """创建账号，返回带 id 的 user 字典。"""
    username = (username or "").strip()
    real_name = (real_name or "").strip()
    phone = (phone or "").strip()
    if not username or not real_name:
        return {"success": False, "error": "用户名和真实姓名不能为空"}
    if role not in VALID_ROLES:
        return {"success": False, "error": f"角色必须是 {VALID_ROLES} 之一"}
    if not password:
        return {"success": False, "error": "初始密码不能为空"}
    if phone and not re.fullmatch(r"1[3-9]\d{9}", phone):
        return {"success": False, "error": "手机号格式不正确，应为1开头的11位数字"}
    with _write() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE username=?", (username,)
        ).fetchone()
        if existing:
            return {"success": False, "error": "用户名已存在"}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur = conn.execute(
            """INSERT INTO users (username, password_hash, real_name, role, phone, status,
                                  session_version, last_login_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, '启用', 0, '', ?, ?)""",
            (username, generate_password_hash(password), real_name, role, phone, now, now),
        )
        new_id = cur.lastrowid
    user = get_user_by_id(new_id)
    return {"success": True, "user": user}


def update_user_profile(user_id: int, real_name: str = "", phone: str = "") -> dict:
    """改真实姓名/手机号（不改 username/role/password）。"""
    real_name = (real_name or "").strip()
    phone = (phone or "").strip()
    if not real_name:
        return {"success": False, "error": "真实姓名不能为空"}
    if phone and not re.fullmatch(r"1[3-9]\d{9}", phone):
        return {"success": False, "error": "手机号格式不正确"}
    with _write() as conn:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE users SET real_name=?, phone=?, updated_at=? WHERE id=?",
            (real_name, phone, now, user_id),
        )
    return {"success": True, "user": get_user_by_id(user_id)}


def change_user_role(user_id: int, new_role: str) -> dict:
    if new_role not in VALID_ROLES:
        return {"success": False, "error": f"角色必须是 {VALID_ROLES} 之一"}
    with _write() as conn:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE users SET role=?, session_version=session_version+1, updated_at=? WHERE id=?",
            (new_role, now, user_id),
        )
    return {"success": True, "user": get_user_by_id(user_id)}


def reset_password(user_id: int, new_password: str) -> dict:
    if not new_password:
        return {"success": False, "error": "新密码不能为空"}
    with _write() as conn:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """UPDATE users
               SET password_hash=?, session_version=session_version+1, updated_at=?
               WHERE id=?""",
            (generate_password_hash(new_password), now, user_id),
        )
    return {"success": True}


def change_own_password(user_id: int, old_password: str, new_password: str) -> dict:
    if not new_password:
        return {"success": False, "error": "新密码不能为空"}
    with _write() as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id=?", (user_id,)
        ).fetchone()
    if not row or not check_password_hash(row["password_hash"], old_password or ""):
        return {"success": False, "error": "原密码不正确"}
    with get_db() as conn:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE users SET password_hash=?, session_version=session_version+1, updated_at=? WHERE id=?",
            (generate_password_hash(new_password), now, user_id),
        )
    return {"success": True}


def set_user_status(user_id: int, status: str) -> dict:
    if status not in ("启用", "停用"):
        return {"success": False, "error": "状态必须是 启用/停用"}
    with _write() as conn:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """UPDATE users SET status=?, session_version=session_version+1, updated_at=?
               WHERE id=?""",
            (status, now, user_id),
        )
    return {"success": True, "user": get_user_by_id(user_id)}


def record_login(user_id: int) -> None:
    """登录成功时写入 last_login_at 并 bump session_version（踢掉旧 session）。"""
    with _write() as conn:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """UPDATE users SET last_login_at=?, session_version=session_version+1, updated_at=?
               WHERE id=?""",
            (now, now, user_id),
        )


def lookup_alias_user_id(old_name: str) -> Optional[int]:
    """查 handler_name → user_id 的别名映射。"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT user_id FROM handler_name_aliases WHERE old_name=?",
            (old_name,),
        ).fetchone()
    return row["user_id"] if row else None


def list_aliases() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT a.id, a.old_name, a.user_id, a.created_at,
                      u.real_name AS user_real_name, u.username AS username
               FROM handler_name_aliases a
               LEFT JOIN users u ON u.id = a.user_id
               ORDER BY a.id ASC"""
        ).fetchall()
    return [dict(r) for r in rows]


def create_alias(old_name: str, user_id: int, created_by: int) -> dict:
    old_name = (old_name or "").strip()
    if not old_name:
        return {"success": False, "error": "历史处理人名称不能为空"}
    with _write() as conn:
        existing = conn.execute(
            "SELECT id FROM handler_name_aliases WHERE old_name=?", (old_name,)
        ).fetchone()
        if existing:
            return {"success": False, "error": "该历史名称已存在映射"}
        target = conn.execute(
            "SELECT id FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not target:
            return {"success": False, "error": "目标用户不存在"}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """INSERT INTO handler_name_aliases (old_name, user_id, created_by, created_at)
               VALUES (?, ?, ?, ?)""",
            (old_name, user_id, created_by, now),
        )
    return {"success": True}


def delete_alias(alias_id: int) -> dict:
    """删除映射，返回被删的 old_name（供调用方回滚工单）。"""
    with _write() as conn:
        row = conn.execute(
            "SELECT old_name FROM handler_name_aliases WHERE id=?", (alias_id,)
        ).fetchone()
        if not row:
            return {"success": False, "error": "映射不存在"}
        old_name = row["old_name"]
        conn.execute("DELETE FROM handler_name_aliases WHERE id=?", (alias_id,))
    return {"success": True, "old_name": old_name}


def list_unmapped_handler_names() -> list[dict]:
    """扫描所有工单，列出 handler_name 出现 ≥1 次但未在 aliases 里的非空名称。"""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT t.handler_name AS old_name, COUNT(*) AS cnt
               FROM complaint_tickets t
               WHERE t.handler_name != '' AND t.handler_user_id IS NULL
               GROUP BY t.handler_name
               ORDER BY cnt DESC"""
        ).fetchall()
    return [dict(r) for r in rows]
