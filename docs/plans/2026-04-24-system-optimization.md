# 投诉处理系统全面优化计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 全面优化投诉处理系统的登录效率、代码结构、页面体验，兼顾技术效率与用户体验

**Architecture:** 
- 引入登录态缓存机制（2小时有效期）
- 重构代码为分层架构（utils/services/crawlers/web）
- 前端改为分栏式布局，增加实时进度反馈
- 异步并行查询三系统，支持失败重试

**Tech Stack:** Python Flask + Vue 3 + Bootstrap 5 + SQLite

---

## 当前代码结构分析

```
/Users/master/Desktop/投诉处理系统/
├── app.py                  # Flask主程序（需拆分）
├── config.py               # 配置管理
├── database.py             # 数据库操作
├── requirements.txt        # 依赖
├── start.command           # 启动脚本
├── crawlers/
│   ├── __init__.py
│   ├── internal.py         # 内部系统爬虫
│   ├── third.py            # 第三系统爬虫
│   └── driving.py          # 东莞驾培爬虫
├── services/
│   ├── __init__.py
│   ├── query_service.py    # 查询编排
│   ├── contract_service.py # 合同分析
│   ├── reply_service.py    # 回复函生成
│   ├── feishu_service.py   # 飞书提交
│   └── file_service.py     # 文件处理
├── templates/
│   └── index.html          # 单页应用（需重构）
└── static/
    ├── css/style.css       # 样式（需优化）
    └── js/app.js           # 前端逻辑（需重构）
```

---

## Task 1: 创建新的目录结构

**目标：** 按照规范重构项目目录

**Files:**
- Create: `utils/__init__.py`
- Create: `utils/cache_manager.py`      # 登录态缓存管理
- Create: `utils/http_client.py`        # HTTP请求封装
- Create: `utils/logger.py`             # 日志管理
- Create: `utils/decorators.py`         # 装饰器（重试、计时等）
- Create: `core/__init__.py`
- Create: `core/auth_manager.py`        # 认证管理（含缓存）
- Create: `core/query_engine.py`        # 查询引擎（并行异步）
- Create: `api/__init__.py`
- Create: `api/routes.py`               # API路由（从app.py拆分）
- Create: `api/middleware.py`           # 中间件（异常处理、日志）

**Step 1: 创建目录结构**

```bash
mkdir -p /Users/master/Desktop/投诉处理系统/{utils,core,api}
touch /Users/master/Desktop/投诉处理系统/{utils,core,api}/__init__.py
```

**Step 2: 创建缓存管理器**

Create: `utils/cache_manager.py`

```python
"""
登录态缓存管理模块
支持内存缓存和文件持久化，有效期可配置
"""
import json
import time
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict

CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class AuthCache:
    """认证缓存数据结构"""
    system: str                           # 系统标识
    cookies: Dict[str, str]               # Cookie数据
    token: Optional[str] = None           # Token（如有）
    created_at: float = 0                 # 创建时间
    expires_at: float = 0                 # 过期时间
    username: str = ""                    # 用户名（用于标识）
    
    @property
    def is_valid(self) -> bool:
        """检查缓存是否有效"""
        return time.time() < self.expires_at
    
    @property
    def ttl(self) -> int:
        """剩余有效时间（秒）"""
        return max(0, int(self.expires_at - time.time()))


class CacheManager:
    """
    缓存管理器
    
    功能：
    1. 内存缓存（快速访问）
    2. 文件持久化（进程重启后恢复）
    3. 自动过期处理
    4. 手动清除接口
    """
    
    DEFAULT_TTL = 7200  # 默认2小时
    
    def __init__(self, ttl: int = DEFAULT_TTL):
        self._memory_cache: Dict[str, AuthCache] = {}
        self._ttl = ttl
        self._load_from_disk()
    
    def _get_cache_key(self, system: str, username: str) -> str:
        """生成缓存键"""
        return hashlib.md5(f"{system}:{username}".encode()).hexdigest()
    
    def _get_cache_file(self, key: str) -> Path:
        """获取缓存文件路径"""
        return CACHE_DIR / f"{key}.json"
    
    def _load_from_disk(self):
        """从磁盘加载缓存"""
        for cache_file in CACHE_DIR.glob("*.json"):
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    cache = AuthCache(**data)
                    if cache.is_valid:
                        self._memory_cache[cache_file.stem] = cache
                    else:
                        cache_file.unlink(missing_ok=True)
            except Exception:
                cache_file.unlink(missing_ok=True)
    
    def _save_to_disk(self, key: str, cache: AuthCache):
        """保存缓存到磁盘"""
        cache_file = self._get_cache_file(key)
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(asdict(cache), f, ensure_ascii=False, indent=2)
    
    def get(self, system: str, username: str) -> Optional[AuthCache]:
        """
        获取缓存
        
        Args:
            system: 系统标识（internal/third/driving）
            username: 用户名
            
        Returns:
            AuthCache或None（缓存不存在或已过期）
        """
        key = self._get_cache_key(system, username)
        
        # 先查内存
        if key in self._memory_cache:
            cache = self._memory_cache[key]
            if cache.is_valid:
                return cache
            else:
                del self._memory_cache[key]
        
        return None
    
    def set(self, system: str, username: str, cookies: Dict[str, str], 
            token: Optional[str] = None, ttl: Optional[int] = None):
        """
        设置缓存
        
        Args:
            system: 系统标识
            username: 用户名
            cookies: Cookie字典
            token: 可选的Token
            ttl: 自定义有效期（秒），默认2小时
        """
        key = self._get_cache_key(system, username)
        now = time.time()
        
        cache = AuthCache(
            system=system,
            cookies=cookies,
            token=token,
            created_at=now,
            expires_at=now + (ttl or self._ttl),
            username=username
        )
        
        # 写入内存
        self._memory_cache[key] = cache
        
        # 持久化到磁盘
        self._save_to_disk(key, cache)
    
    def clear(self, system: Optional[str] = None, username: Optional[str] = None):
        """
        清除缓存
        
        Args:
            system: 指定系统，None表示全部
            username: 指定用户，None表示全部
        """
        if system and username:
            # 清除指定缓存
            key = self._get_cache_key(system, username)
            self._memory_cache.pop(key, None)
            self._get_cache_file(key).unlink(missing_ok=True)
        elif system:
            # 清除指定系统所有缓存
            keys_to_remove = [
                k for k, v in self._memory_cache.items() 
                if v.system == system
            ]
            for key in keys_to_remove:
                del self._memory_cache[key]
                self._get_cache_file(key).unlink(missing_ok=True)
        else:
            # 清除全部缓存
            self._memory_cache.clear()
            for cache_file in CACHE_DIR.glob("*.json"):
                cache_file.unlink(missing_ok=True)
    
    def list_active(self) -> list[AuthCache]:
        """列出所有有效缓存"""
        return [cache for cache in self._memory_cache.values() if cache.is_valid]
    
    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        active = self.list_active()
        return {
            "total_cached": len(self._memory_cache),
            "active": len(active),
            "by_system": {}
        }


# 全局缓存管理器实例
cache_manager = CacheManager()
```

**Step 3: 创建HTTP客户端封装**

Create: `utils/http_client.py`

```python
"""
HTTP请求封装模块
提供统一的请求接口、自动重试、日志记录
"""
import time
import requests
from typing import Optional, Dict, Any, Callable
from functools import wraps
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class HTTPClient:
    """
    HTTP客户端封装
    
    特性：
    1. 自动重试机制
    2. 请求/响应日志
    3. 统一的超时和SSL配置
    4. Session管理
    """
    
    DEFAULT_TIMEOUT = 30
    MAX_RETRIES = 2
    
    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko)",
        })
    
    def request(self, method: str, url: str, 
                retries: int = MAX_RETRIES,
                timeout: int = DEFAULT_TIMEOUT,
                **kwargs) -> requests.Response:
        """
        发送HTTP请求（带重试）
        
        Args:
            method: HTTP方法
            url: 请求URL
            retries: 重试次数
            timeout: 超时时间
            **kwargs: 其他requests参数
            
        Returns:
            Response对象
            
        Raises:
            requests.RequestException: 请求失败
        """
        last_error = None
        
        for attempt in range(retries + 1):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    timeout=timeout,
                    verify=False,
                    **kwargs
                )
                response.raise_for_status()
                return response
                
            except requests.RequestException as e:
                last_error = e
                if attempt < retries:
                    wait_time = 2 ** attempt  # 指数退避
                    time.sleep(wait_time)
                    continue
        
        raise last_error
    
    def get(self, url: str, **kwargs) -> requests.Response:
        """GET请求"""
        return self.request("GET", url, **kwargs)
    
    def post(self, url: str, **kwargs) -> requests.Response:
        """POST请求"""
        return self.request("POST", url, **kwargs)
    
    def update_cookies(self, cookies: Dict[str, str]):
        """更新Session的Cookie"""
        self.session.cookies.update(cookies)
    
    def get_cookies_dict(self) -> Dict[str, str]:
        """获取当前Cookie字典"""
        return dict(self.session.cookies)


def retry_on_error(max_retries: int = 2, exceptions: tuple = (Exception,)):
    """
    重试装饰器
    
    用法：
        @retry_on_error(max_retries=3)
        def fetch_data():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_error = e
                    if attempt < max_retries:
                        time.sleep(2 ** attempt)
                        continue
            raise last_error
        return wrapper
    return decorator
```

**Step 4: 创建日志管理器**

Create: `utils/logger.py`

```python
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
              limit: int = 100) -> list[Dict[str, Any]]:
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
```

**Step 5: Commit**

```bash
cd /Users/master/Desktop/投诉处理系统
git add utils/ docs/
git commit -m "feat: add utils layer - cache_manager, http_client, logger"
```

---

## Task 2: 重构爬虫基类和认证管理

**目标：** 创建统一的爬虫基类，集成登录态缓存

**Files:**
- Create: `core/auth_manager.py`
- Modify: `crawlers/internal.py`
- Modify: `crawlers/third.py`
- Modify: `crawlers/driving.py`

**Step 1: 创建认证管理器**

Create: `core/auth_manager.py`

```python
"""
认证管理器 - 统一管理三系统登录态
集成缓存机制，支持自动刷新和后台登录
"""
import time
import threading
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Callable
from dataclasses import dataclass
from enum import Enum

from utils.cache_manager import cache_manager, AuthCache
from utils.http_client import HTTPClient
from utils.logger import operation_logger, LogType


class SystemType(Enum):
    """系统类型"""
    INTERNAL = "internal"
    THIRD = "third"
    DRIVING = "driving"


@dataclass
class LoginResult:
    """登录结果"""
    success: bool
    message: str
    cookies: Optional[Dict[str, str]] = None
    token: Optional[str] = None
    duration_ms: int = 0


class BaseCrawler(ABC):
    """
    爬虫基类
    
    特性：
    1. 自动登录态管理（缓存/恢复）
    2. 后台登录不阻塞
    3. 统一的请求接口
    4. 自动重试机制
    """
    
    def __init__(self, system_type: SystemType, username: str, password: str):
        self.system_type = system_type
        self.username = username
        self.password = password
        self.http = HTTPClient()
        self._logged_in = False
        self._login_lock = threading.Lock()
        self._background_login_thread: Optional[threading.Thread] = None
    
    @property
    def cache_key(self) -> str:
        """缓存键"""
        return f"{self.system_type.value}:{self.username}"
    
    def _try_restore_session(self) -> bool:
        """尝试从缓存恢复会话"""
        cache = cache_manager.get(self.system_type.value, self.username)
        if cache and cache.is_valid:
            self.http.update_cookies(cache.cookies)
            if cache.token:
                # 设置Token到headers
                pass
            self._logged_in = True
            return True
        return False
    
    def _save_session(self):
        """保存会话到缓存"""
        cookies = self.http.get_cookies_dict()
        cache_manager.set(
            self.system_type.value,
            self.username,
            cookies,
            token=None  # 如有token可传入
        )
    
    @abstractmethod
    def _do_login(self) -> LoginResult:
        """
        执行实际登录逻辑
        
        子类必须实现此方法
        """
        pass
    
    @abstractmethod
    def _check_session_valid(self) -> bool:
        """
        检查当前会话是否有效
        
        子类可实现此方法用于会话健康检查
        """
        return True
    
    def login(self, force: bool = False, background: bool = False) -> LoginResult:
        """
        登录入口
        
        Args:
            force: 强制重新登录，忽略缓存
            background: 后台执行，不阻塞当前线程
            
        Returns:
            LoginResult
        """
        if background:
            # 后台登录
            if self._background_login_thread and self._background_login_thread.is_alive():
                return LoginResult(False, "登录正在进行中")
            
            self._background_login_thread = threading.Thread(
                target=self._do_login_sync,
                kwargs={'force': force}
            )
            self._background_login_thread.daemon = True
            self._background_login_thread.start()
            return LoginResult(True, "后台登录已启动")
        
        return self._do_login_sync(force)
    
    def _do_login_sync(self, force: bool = False) -> LoginResult:
        """同步执行登录"""
        start_time = time.time()
        
        with self._login_lock:
            # 先尝试恢复缓存
            if not force and self._try_restore_session():
                # 检查会话是否仍然有效
                if self._check_session_valid():
                    duration = int((time.time() - start_time) * 1000)
                    return LoginResult(True, "从缓存恢复登录", duration_ms=duration)
            
            # 执行实际登录
            result = self._do_login()
            
            if result.success:
                self._logged_in = True
                self._save_session()
            
            # 记录日志
            operation_logger.log(
                LogType.LOGIN,
                f"{self.system_type.value}_login",
                {"username": self.username, "force": force},
                status="success" if result.success else "failed",
                duration_ms=result.duration_ms
            )
            
            return result
    
    def ensure_login(self) -> bool:
        """
        确保已登录
        
        如果未登录，尝试从缓存恢复或执行登录
        """
        if self._logged_in:
            if self._check_session_valid():
                return True
        
        # 尝试恢复缓存
        if self._try_restore_session():
            if self._check_session_valid():
                return True
        
        # 执行登录
        result = self.login()
        return result.success
    
    def logout(self):
        """登出并清除缓存"""
        self._logged_in = False
        cache_manager.clear(self.system_type.value, self.username)
    
    def request(self, method: str, url: str, **kwargs) -> Any:
        """
        发送请求（自动确保登录）
        
        如果未登录，会自动尝试登录
        """
        if not self.ensure_login():
            raise Exception(f"{self.system_type.value} 系统登录失败")
        
        return self.http.request(method, url, **kwargs)
    
    def get(self, url: str, **kwargs) -> Any:
        """GET请求"""
        return self.request("GET", url, **kwargs)
    
    def post(self, url: str, **kwargs) -> Any:
        """POST请求"""
        return self.request("POST", url, **kwargs)


class AuthManager:
    """
    认证管理器
    
    统一管理三系统的认证状态
    """
    
    def __init__(self):
        self._crawlers: Dict[SystemType, BaseCrawler] = {}
    
    def register(self, crawler: BaseCrawler):
        """注册爬虫实例"""
        self._crawlers[crawler.system_type] = crawler
    
    def get_crawler(self, system_type: SystemType) -> Optional[BaseCrawler]:
        """获取爬虫实例"""
        return self._crawlers.get(system_type)
    
    def login_all(self, background: bool = True) -> Dict[SystemType, LoginResult]:
        """
        登录所有系统
        
        Args:
            background: 是否后台执行
            
        Returns:
            各系统登录结果
        """
        results = {}
        for system_type, crawler in self._crawlers.items():
            results[system_type] = crawler.login(background=background)
        return results
    
    def clear_all_cache(self):
        """清除所有系统缓存"""
        cache_manager.clear()
    
    def get_status(self) -> Dict[str, Any]:
        """获取认证状态概览"""
        status = {}
        for system_type, crawler in self._crawlers.items():
            cache = cache_manager.get(system_type.value, crawler.username)
            status[system_type.value] = {
                "logged_in": crawler._logged_in,
                "cached": cache is not None,
                "cache_ttl": cache.ttl if cache else 0
            }
        return status


# 全局认证管理器
auth_manager = AuthManager()
```

**Step 2: 重构内部系统爬虫**

Modify: `crawlers/internal.py`

```python
"""
内部系统爬虫 - 重构后版本
集成登录态缓存，继承BaseCrawler
"""
import time
import ddddocr
from typing import Optional, Dict, Any
from dataclasses import dataclass

from core.auth_manager import BaseCrawler, SystemType, LoginResult
from utils.http_client import HTTPClient


@dataclass
class StudentInfo:
    """学员信息"""
    name: str = ""
    id_card: str = ""
    phone: str = ""
    license_type: str = ""
    registration_date: str = ""
    exam_stage: str = ""
    school_name: str = ""
    school_code: str = ""
    training_hours: Dict[str, str] = None
    timeline: list = None


class InternalCrawler(BaseCrawler):
    """内部系统爬虫（重构版）"""
    
    def __init__(self, base_url: str, username: str, password: str):
        super().__init__(SystemType.INTERNAL, username, password)
        self.base_url = base_url.rstrip("/")
        self.ocr = ddddocr.DdddOcr(show_ad=False)
    
    def _do_login(self) -> LoginResult:
        """执行登录"""
        start_time = time.time()
        
        try:
            # 1. 获取验证码
            captcha_url = f"{self.base_url}/servlet/validate_image"
            resp = self.http.get(captcha_url)
            captcha_text = self.ocr.classification(resp.content)
            
            # 2. 登录请求
            login_url = f"{self.base_url}/sypro_jm/login!doLogin.action"
            resp = self.http.post(
                login_url,
                data={
                    "userName": self.username,
                    "password": self.password,
                    "validate": captcha_text
                }
            )
            
            result = resp.json()
            if result.get("success"):
                duration = int((time.time() - start_time) * 1000)
                return LoginResult(True, "登录成功", duration_ms=duration)
            else:
                return LoginResult(False, result.get("msg", "登录失败"))
                
        except Exception as e:
            return LoginResult(False, str(e))
    
    def query_student(self, id_card: str) -> Optional[StudentInfo]:
        """查询学员信息"""
        try:
            # 查询接口
            url = f"{self.base_url}/sypro_jm/school/schoolOprAction!xsjdshList.action"
            resp = self.post(url, data={"sfzmhm": id_card})
            
            # 解析结果...
            # （保留原有解析逻辑，简化展示）
            
            return StudentInfo(
                id_card=id_card,
                # ... 填充字段
            )
            
        except Exception as e:
            # 查询失败，可能是会话过期，清除缓存后重试一次
            self.logout()
            if self.ensure_login():
                return self.query_student(id_card)
            raise
```

**Step 3: Commit**

```bash
git add core/ crawlers/
git commit -m "feat: refactor crawlers with auth manager and cache support"
```

---

## Task 3: 重构前端页面（分栏式布局）

**目标：** 改为左侧导航+右侧工作区的分栏布局

**Files:**
- Modify: `templates/index.html`
- Modify: `static/css/style.css`
- Modify: `static/js/app.js`

（由于篇幅限制，前端重构代码将在实施时详细编写，此处为规划）

---

## Task 4: 异步并行查询引擎

**目标：** 三系统并行查询，实时展示进度

**Files:**
- Create: `core/query_engine.py`
- Modify: `api/routes.py`

---

## Task 5: 优化退费计算逻辑

**目标：** 提升计算效率，保留计算明细

**Files:**
- Modify: `services/contract_service.py`

---

## Task 6: 集成测试和优化说明文档

**Files:**
- Create: `docs/optimization-report.md`
- Create: `tests/test_cache.py`
- Create: `tests/test_crawlers.py`

---

## 优化前后对比预期

| 指标 | 优化前 | 优化后 | 提升 |
|------|--------|--------|------|
| 登录耗时（三系统） | 3-5秒×3 = 9-15秒 | 首次5秒，后续0秒（缓存） | 100%（后续） |
| 查询耗时 | 串行10-15秒 | 并行3-5秒 | 60-70% |
| 页面加载 | 单页全量加载 | 分栏按需加载 | 50% |
| 代码复用率 | 30% | 80% | 166% |
| 异常处理 | 直接崩溃 | 自动重试+友好提示 | - |

---

## 执行建议

这是一个大型重构任务，建议分阶段执行：

**阶段1（Task 1-2）：** 后端基础设施（缓存、HTTP封装、爬虫重构）
**阶段2（Task 3）：** 前端重构（分栏布局、进度反馈）
**阶段3（Task 4-5）：** 异步查询和计算优化
**阶段4（Task 6）：** 测试和文档

每个阶段完成后进行验证测试，确保功能正常后再进入下一阶段。
