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
    
    def list_active(self) -> list:
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
