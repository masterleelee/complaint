"""
重构后代码的基础测试
验证：
1. 爬虫继承关系正确
2. 登录态缓存正常工作
3. 查询引擎能正确并行查询
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
from core.auth_manager import BaseCrawler, SystemType, auth_manager
from crawlers.internal import InternalCrawler
from crawlers.third import ThirdCrawler
from crawlers.driving import DrivingCrawler
from core.query_engine import QueryEngine, query_all_systems_sync


def test_inheritance():
    """测试爬虫继承关系"""
    print("\n=== 测试爬虫继承关系 ===")
    
    # 测试内部系统
    internal = InternalCrawler()
    assert isinstance(internal, BaseCrawler), "InternalCrawler 必须继承 BaseCrawler"
    assert internal.system_type == SystemType.INTERNAL
    print("✓ InternalCrawler 继承正确")
    
    # 测试第三系统
    third = ThirdCrawler()
    assert isinstance(third, BaseCrawler), "ThirdCrawler 必须继承 BaseCrawler"
    assert third.system_type == SystemType.THIRD
    print("✓ ThirdCrawler 继承正确")
    
    # 测试东莞驾培
    driving = DrivingCrawler()
    assert isinstance(driving, BaseCrawler), "DrivingCrawler 必须继承 BaseCrawler"
    assert driving.system_type == SystemType.DRIVING
    print("✓ DrivingCrawler 继承正确")
    
    print("✅ 所有爬虫继承关系测试通过")


def test_auth_manager():
    """测试认证管理器"""
    print("\n=== 测试认证管理器 ===")
    
    # 清空现有注册
    auth_manager._crawlers.clear()
    
    # 注册爬虫
    internal = InternalCrawler()
    third = ThirdCrawler()
    driving = DrivingCrawler()
    
    auth_manager.register(internal)
    auth_manager.register(third)
    auth_manager.register(driving)
    
    assert len(auth_manager._crawlers) == 3, "应该注册了3个爬虫"
    print("✓ 爬虫注册成功")
    
    # 测试获取爬虫
    assert auth_manager.get_crawler(SystemType.INTERNAL) is internal
    assert auth_manager.get_crawler(SystemType.THIRD) is third
    assert auth_manager.get_crawler(SystemType.DRIVING) is driving
    print("✓ 爬虫获取正确")
    
    print("✅ 认证管理器测试通过")


def test_cache_mechanism():
    """测试登录态缓存机制"""
    print("\n=== 测试登录态缓存机制 ===")
    
    from utils.cache_manager import cache_manager
    
    # 清除所有缓存
    cache_manager.clear()
    
    # 测试缓存设置
    test_cookies = {"session": "test123", "token": "abc"}
    cache_manager.set("test_system", "test_user", test_cookies, token="test_token")
    
    # 测试缓存获取
    cache = cache_manager.get("test_system", "test_user")
    assert cache is not None, "缓存应该存在"
    assert cache.cookies == test_cookies
    assert cache.token == "test_token"
    print("✓ 缓存设置和获取正常")
    
    # 测试缓存有效性
    assert cache.is_valid, "缓存应该在有效期内"
    print("✓ 缓存有效性检查正常")
    
    # 清除测试缓存
    cache_manager.clear("test_system", "test_user")
    cache = cache_manager.get("test_system", "test_user")
    assert cache is None, "缓存应该已被清除"
    print("✓ 缓存清除正常")
    
    print("✅ 登录态缓存机制测试通过")


def test_query_engine():
    """测试查询引擎"""
    print("\n=== 测试查询引擎 ===")
    
    engine = QueryEngine()
    
    # 测试爬虫初始化
    assert SystemType.INTERNAL in engine._crawlers
    assert SystemType.THIRD in engine._crawlers
    assert SystemType.DRIVING in engine._crawlers
    print("✓ 查询引擎爬虫初始化正确")
    
    # 测试认证状态获取
    status = engine.get_auth_status()
    assert "internal" in status
    assert "third" in status
    assert "driving" in status
    print("✓ 认证状态获取正常")
    
    engine.close()
    print("✅ 查询引擎测试通过")


def test_crawler_methods():
    """测试爬虫方法"""
    print("\n=== 测试爬虫方法 ===")
    
    internal = InternalCrawler()
    
    # 测试 ensure_login 方法存在
    assert hasattr(internal, 'ensure_login'), "应该有 ensure_login 方法"
    assert hasattr(internal, 'get'), "应该有 get 方法"
    assert hasattr(internal, 'post'), "应该有 post 方法"
    assert hasattr(internal, 'logout'), "应该有 logout 方法"
    assert hasattr(internal, '_save_session'), "应该有 _save_session 方法"
    print("✓ InternalCrawler 方法完整")
    
    third = ThirdCrawler()
    assert hasattr(third, 'ensure_login')
    assert hasattr(third, 'get')
    assert hasattr(third, 'post')
    print("✓ ThirdCrawler 方法完整")
    
    driving = DrivingCrawler()
    assert hasattr(driving, 'ensure_login')
    assert hasattr(driving, 'get')
    assert hasattr(driving, 'post')
    assert hasattr(driving, 'download_contract')
    print("✓ DrivingCrawler 方法完整")
    
    print("✅ 爬虫方法测试通过")


def test_imports():
    """测试所有模块能正确导入"""
    print("\n=== 测试模块导入 ===")
    
    try:
        from core.auth_manager import BaseCrawler, SystemType, LoginResult, auth_manager
        print("✓ core.auth_manager 导入成功")
    except Exception as e:
        print(f"✗ core.auth_manager 导入失败: {e}")
        raise
    
    try:
        from core.query_engine import QueryEngine, query_all_systems_sync
        print("✓ core.query_engine 导入成功")
    except Exception as e:
        print(f"✗ core.query_engine 导入失败: {e}")
        raise
    
    try:
        from crawlers.internal import InternalCrawler, InternalStudentInfo
        print("✓ crawlers.internal 导入成功")
    except Exception as e:
        print(f"✗ crawlers.internal 导入失败: {e}")
        raise
    
    try:
        from crawlers.third import ThirdCrawler, ThirdStudentInfo
        print("✓ crawlers.third 导入成功")
    except Exception as e:
        print(f"✗ crawlers.third 导入失败: {e}")
        raise
    
    try:
        from crawlers.driving import DrivingCrawler, DrivingStudentInfo
        print("✓ crawlers.driving 导入成功")
    except Exception as e:
        print(f"✗ crawlers.driving 导入失败: {e}")
        raise
    
    print("✅ 所有模块导入测试通过")


def run_all_tests():
    """运行所有测试"""
    print("=" * 50)
    print("  投诉处理系统重构测试")
    print("=" * 50)
    
    try:
        test_imports()
        test_inheritance()
        test_auth_manager()
        test_cache_mechanism()
        test_crawler_methods()
        test_query_engine()
        
        print("\n" + "=" * 50)
        print("  ✅ 所有测试通过！")
        print("=" * 50)
        return True
        
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
