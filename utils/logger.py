"""
日志管理模块
统一日志格式，支持操作日志和系统日志分离
"""
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
from enum import Enum

LOG_DIR = Path(__file__).parent.parent / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


class LogType(Enum):
    """日志类型"""
    SYSTEM = "system"      # 系统日志
    OPERATION = "operation" # 操作日志
    LOGIN = "login"        # 登录日志
    QUERY = "query"        # 查询日志
    CALC = "calculation"   # 计算日志


class OperationLogger:
    """
    操作日志管理器
    
    功能：
    1. SQLite存储，支持按时间/类型查询
    2. 自动归档旧日志
    3. 结构化日志数据
    """
    
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or (LOG_DIR / "operations.db")
        self._init_db()
    
    def _init_db(self):
        """初始化数据库表"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS operation_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    log_type TEXT NOT NULL,
                    action TEXT NOT NULL,
                    user_id TEXT,
                    details TEXT,
                    status TEXT,
                    duration_ms INTEGER,
                    ip_address TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp 
                ON operation_logs(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_type 
                ON operation_logs(log_type)
            """)
    
    def log(self, 
            log_type: LogType,
            action: str,
            details: Optional[Dict[str, Any]] = None,
            user_id: Optional[str] = None,
            status: str = "success",
            duration_ms: Optional[int] = None):
        """
        记录操作日志
        
        Args:
            log_type: 日志类型
            action: 操作名称
            details: 详细数据（会被JSON序列化）
            user_id: 用户标识
            status: 操作状态
            duration_ms: 操作耗时（毫秒）
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO operation_logs 
                   (timestamp, log_type, action, user_id, details, status, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now().timestamp(),
                    log_type.value,
                    action,
                    user_id,
                    json.dumps(details, ensure_ascii=False) if details else None,
                    status,
                    duration_ms
                )
            )
    
    def query(self,
              start_time: Optional[datetime] = None,
              end_time: Optional[datetime] = None,
              log_type: Optional[LogType] = None,
              limit: int = 100) -> list:
        """
        查询日志
        
        Returns:
            日志记录列表
        """
        conditions = []
        params = []
        
        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time.timestamp())
        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time.timestamp())
        if log_type:
            conditions.append("log_type = ?")
            params.append(log_type.value)
        
        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                f"""SELECT * FROM operation_logs 
                    {where_clause}
                    ORDER BY timestamp DESC 
                    LIMIT ?""",
                params + [limit]
            )
            return [dict(row) for row in cursor.fetchall()]


# 配置系统日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "system.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)

system_logger = logging.getLogger("complaint_system")
operation_logger = OperationLogger()
