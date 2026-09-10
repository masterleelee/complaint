"""第三系统图标三态（绿/橙/红）合并逻辑单元测试。

验证 core/query_engine.py::_merge_results 依据
「学员档案(学员申请登记) + 培训学时(阶段审核)」双维度计算 sources.third：
  绿 = 档案命中 且 学时命中
  橙 = 档案命中 但 学时查无
  红 = 档案与学时都无
  档案查询异常 → 回落学时状态（不误红）

纯逻辑测试，无网络请求。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.query_engine import (
    QueryEngine,
    QueryResult,
    QueryStatus,
    SystemType,
)


class _StubAuth:
    """避免 __init__ 注册真实爬虫，仅满足接口。"""

    def register(self, *a, **k):
        pass

    def get_status(self):
        return {}

    def login_all(self, **k):
        pass

    def clear_all_cache(self):
        pass


def _qr(system, status, profile_status=None):
    r = QueryResult(system=system, status=status)
    r.profile_status = profile_status
    return r


def _merged(third_status, third_pstat):
    """返回 (sources.third, sources.third_profile)。"""
    eng = QueryEngine(auth_mgr=_StubAuth())
    results = {
        SystemType.INTERNAL: _qr("internal", QueryStatus.NOT_FOUND),
        SystemType.THIRD: _qr("third", third_status, third_pstat),
        SystemType.DRIVING: _qr("driving", QueryStatus.NOT_FOUND),
    }
    m = eng._merge_results("dummy_id_card", results)
    return m.sources["third"], m.sources.get("third_profile")


def _check(name, got, expect):
    ok = got == expect
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got={got!r} expect={expect!r}")
    assert ok, f"{name} 失败: got={got!r} expect={expect!r}"


def test_third_source_status():
    print("第三系统三态合并逻辑:")

    # 1. 档案命中 + 学时命中 → 绿
    _check("档案+学时→绿", _merged(QueryStatus.SUCCESS, QueryStatus.SUCCESS)[0], "success")

    # 2. 档案命中 + 学时查无 → 橙（修复点：原实现学时查无会丢弃档案）
    _check("档案+无学时→橙", _merged(QueryStatus.NOT_FOUND, QueryStatus.SUCCESS)[0], "profile")

    # 3. 档案查无 + 学时命中（兜底，理论不发生）→ 绿
    _check("无档案+学时→绿", _merged(QueryStatus.SUCCESS, QueryStatus.NOT_FOUND)[0], "success")

    # 4. 档案查无 + 学时查无 → 红
    _check("无档案+无学时→红", _merged(QueryStatus.NOT_FOUND, QueryStatus.NOT_FOUND)[0], "not_found")

    # 5. 档案查询异常 + 学时命中 → 绿（不误红）
    _check("档案异常+学时→绿", _merged(QueryStatus.SUCCESS, QueryStatus.ERROR)[0], "success")

    # 6. 档案查询异常 + 学时查无 → 红
    _check("档案异常+无学时→红", _merged(QueryStatus.NOT_FOUND, QueryStatus.ERROR)[0], "not_found")

    # 7. third_profile 状态透传
    _check("档案命中透传", _merged(QueryStatus.NOT_FOUND, QueryStatus.SUCCESS)[1], "success")
    _check("档案查无透传", _merged(QueryStatus.NOT_FOUND, QueryStatus.NOT_FOUND)[1], "not_found")
    _check("档案异常透传", _merged(QueryStatus.NOT_FOUND, QueryStatus.ERROR)[1], "error")

    print("全部通过 ✅")


if __name__ == "__main__":
    test_third_source_status()
