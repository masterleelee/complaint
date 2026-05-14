"""
认证管理器 - 统一管理三系统登录态
集成缓存机制，支持自动刷新和后台登录
"""
import time
import threading
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum

from utils.cache_manager import cache_manager
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
    
    def _try_restore_session(self) -> bool:
        """尝试从缓存恢复会话"""
        cache = cache_manager.get(self.system_type.value, self.username)
        if cache and cache.is_valid:
            self.http.update_cookies(cache.cookies)
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
            token=None
        )
    
    @abstractmethod
    def _do_login(self) -> LoginResult:
        """执行实际登录逻辑 - 子类必须实现"""
        pass
    
    def login(self, force: bool = False, background: bool = False) -> LoginResult:
        """
        登录入口
        
        Args:
            force: 强制重新登录，忽略缓存
            background: 后台执行，不阻塞当前线程
        """
        if background:
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
        """确保已登录"""
        if self._logged_in:
            return True
        
        # 尝试恢复缓存
        if self._try_restore_session():
            return True
        
        # 执行登录
        result = self.login()
        return result.success
    
    def logout(self):
        """登出并清除缓存"""
        self._logged_in = False
        cache_manager.clear(self.system_type.value, self.username)
        # 清除 HTTP session 中的过期 cookies
        self.http.session.cookies.clear()
    
    def request(self, method: str, url: str, **kwargs) -> Any:
        """发送请求（自动确保登录）"""
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
    """认证管理器 - 统一管理三系统认证"""
    
    def __init__(self):
        self._crawlers: Dict[SystemType, BaseCrawler] = {}
    
    def register(self, crawler: BaseCrawler):
        """注册爬虫实例"""
        self._crawlers[crawler.system_type] = crawler
    
    def get_crawler(self, system_type: SystemType) -> Optional[BaseCrawler]:
        """获取爬虫实例"""
        return self._crawlers.get(system_type)
    
    def login_all(self, background: bool = True) -> Dict[SystemType, LoginResult]:
        """登录所有系统"""
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
