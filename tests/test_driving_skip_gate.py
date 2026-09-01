"""驾培跳过闸门扩展验证（方案 A）：

1. `_should_skip_driving` 报名日期来源优先级：内部系统 > 第三系统档案补查；
2. `_driving_task` 决策路径：内部查无时等第三档案结果再判，
   命中 2024-03-15 前报名则跳过（NO_CONTRACT），绝不发起驾培查询。
"""
import asyncio
import concurrent.futures
import unittest
from types import SimpleNamespace

from core.query_engine import QueryEngine, QueryResult, QueryStatus, SystemType


def _make_engine():
    """绕开 __init__（避免初始化真实爬虫），只装决策路径需要的部件。"""
    eng = object.__new__(QueryEngine)
    eng._executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    eng._system_executors = {
        st: concurrent.futures.ThreadPoolExecutor(max_workers=1) for st in SystemType
    }
    eng._crawlers = {}
    return eng


def _internal_not_found():
    return QueryResult(system="internal", status=QueryStatus.NOT_FOUND)


def _internal_success(reg_date=""):
    return QueryResult(
        system="internal",
        status=QueryStatus.SUCCESS,
        data=SimpleNamespace(registration_date=reg_date),
    )


def _future(value):
    fut = asyncio.get_event_loop().create_future() if False else asyncio.Future()
    fut.set_result(value)
    return fut


class ShouldSkipDrivingTest(unittest.TestCase):
    def test_internal_2017_skips(self):
        self.assertTrue(QueryEngine._should_skip_driving(_internal_success("2017-05-27")))

    def test_internal_2024_does_not_skip(self):
        self.assertFalse(QueryEngine._should_skip_driving(_internal_success("2024-05-01")))

    def test_internal_not_found_without_profile_does_not_skip(self):
        self.assertFalse(QueryEngine._should_skip_driving(_internal_not_found()))

    def test_profile_2017_supplements_internal_not_found(self):
        profile = SimpleNamespace(registration_date="2017-05-27")
        self.assertTrue(QueryEngine._should_skip_driving(_internal_not_found(), profile))

    def test_profile_2024_does_not_skip(self):
        profile = SimpleNamespace(registration_date="2024-05-01")
        self.assertFalse(QueryEngine._should_skip_driving(_internal_not_found(), profile))

    def test_profile_no_date_does_not_skip(self):
        profile = SimpleNamespace(registration_date="")
        self.assertFalse(QueryEngine._should_skip_driving(_internal_not_found(), profile))

    def test_internal_date_wins_over_profile(self):
        profile = SimpleNamespace(registration_date="2017-05-27")
        self.assertFalse(
            QueryEngine._should_skip_driving(_internal_success("2024-05-01"), profile)
        )

    def test_compact_and_timestamp_formats(self):
        self.assertTrue(QueryEngine._should_skip_driving(_internal_success("20170527")))
        self.assertTrue(
            QueryEngine._should_skip_driving(_internal_success(""), 
                                             SimpleNamespace(registration_date="1495843200000"))
        )


class DrivingTaskDecisionTest(unittest.TestCase):
    def _wire_crawler(self, eng, record):
        crawler = SimpleNamespace(
            ensure_login=lambda: True,
            query_student=lambda id_card, include_contract_check=True: record.append("driving_query") or None,
        )
        eng._crawlers[SystemType.DRIVING] = crawler

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_not_found_with_old_profile_skips_without_query(self):
        eng = _make_engine()
        record = []
        self._wire_crawler(eng, record)
        result = self._run(eng._driving_task(
            "x", _future(_internal_not_found()),
            _future(SimpleNamespace(registration_date="2017-05-27")),
        ))
        self.assertEqual(result.status, QueryStatus.NO_CONTRACT)
        self.assertEqual(record, [])

    def test_not_found_without_profile_queries_driving(self):
        eng = _make_engine()
        record = []
        self._wire_crawler(eng, record)
        result = self._run(eng._driving_task(
            "x", _future(_internal_not_found()),
            _future(None),
        ))
        self.assertEqual(result.status, QueryStatus.NOT_FOUND)
        self.assertEqual(record, ["driving_query"])

    def test_internal_old_date_skips_immediately(self):
        eng = _make_engine()
        record = []
        self._wire_crawler(eng, record)
        result = self._run(eng._driving_task(
            "x", _future(_internal_success("2017-05-27")),
            _future(None),
        ))
        self.assertEqual(result.status, QueryStatus.NO_CONTRACT)
        self.assertEqual(record, [])

    def test_internal_recent_date_ignores_profile(self):
        eng = _make_engine()
        record = []
        self._wire_crawler(eng, record)
        result = self._run(eng._driving_task(
            "x", _future(_internal_success("2024-05-01")),
            _future(SimpleNamespace(registration_date="2017-05-27")),
        ))
        self.assertEqual(result.status, QueryStatus.NOT_FOUND)
        self.assertEqual(record, ["driving_query"])

    def test_internal_error_with_old_profile_skips(self):
        eng = _make_engine()
        record = []
        self._wire_crawler(eng, record)
        err = asyncio.Future()
        err.set_exception(RuntimeError("内部系统登录失败"))
        result = self._run(eng._driving_task(
            "x", err,
            _future(SimpleNamespace(registration_date="2017-05-27")),
        ))
        self.assertEqual(result.status, QueryStatus.NO_CONTRACT)
        self.assertEqual(record, [])


if __name__ == "__main__":
    unittest.main()
