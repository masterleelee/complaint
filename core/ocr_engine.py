"""全局共享 ddddocr 实例，避免多爬虫各自加载模型占用内存"""
import threading

_instance = None
_lock = threading.Lock()


def get_ocr():
    """获取全局唯一的 ddddocr 实例（延迟初始化，线程安全）"""
    global _instance
    if _instance is not None:
        return _instance

    with _lock:
        if _instance is None:
            import ddddocr
            _instance = ddddocr.DdddOcr(show_ad=False)
        return _instance
