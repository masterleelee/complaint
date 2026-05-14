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
    
    DEFAULT_TIMEOUT = 15
    MAX_RETRIES = 1
    
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
