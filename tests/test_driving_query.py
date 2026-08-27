"""东莞驾培 query_student 顺序化验证：
1. 学员不存在时不调用交费订单接口（省一次 API 调用）；
2. 学员存在时交费订单在学员列表成功后恰好调用一次。
"""
import time
import unittest
from types import SimpleNamespace

from crawlers.driving import DrivingCrawler

ID_CARD = "110101199003070011"


def _make_crawler():
    crawler = object.__new__(DrivingCrawler)
    crawler.base_url = "http://test"
    crawler._query_metrics_lock = __import__("threading").Lock()
    crawler._last_query_metrics = {"phase_durations_ms": {}, "retry_count": 0}
    calls = []
    crawler.calls = calls
    return crawler


def _fake_post(crawler, rows):
    def post(url, **kwargs):
        crawler.calls.append("list")
        return SimpleNamespace(
            url="http://test/business/student/list",
            text="{}",
            json=lambda: {"code": 0, "rows": rows},
        )

    return post


class DrivingQueryOrderTest(unittest.TestCase):
    def _wire(self, crawler, rows, orders_result):
        crawler.post = _fake_post(crawler, rows)
        crawler.ensure_login = lambda: True

        def pay_orders(id_card):
            crawler.calls.append("orders")
            return orders_result

        crawler.query_pay_orders = pay_orders

    def test_not_found_skips_pay_orders(self):
        crawler = _make_crawler()
        self._wire(crawler, [], [])
        info = crawler.query_student(ID_CARD)
        self.assertIsNone(info)
        self.assertNotIn("orders", crawler.calls)

    def test_found_queries_orders_once_after_lookup(self):
        crawler = _make_crawler()
        row = {
            "identity": ID_CARD,
            "name": "测试学员",
            "id": 123,
            "contractFee": 3680,
            "payFee": 3680,
        }
        self._wire(crawler, [row], [{"order_no": "A1"}])
        info = crawler.query_student(ID_CARD, include_contract_check=False)
        self.assertEqual(info.name, "测试学员")
        self.assertEqual(info.pay_orders, [{"order_no": "A1"}])
        self.assertEqual(crawler.calls.count("orders"), 1)
        # 顺序：先列表，后订单
        self.assertLess(crawler.calls.index("list"), crawler.calls.index("orders"))


if __name__ == "__main__":
    unittest.main()
