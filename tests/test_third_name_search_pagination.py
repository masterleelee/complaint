# -*- coding: utf-8 -*-
"""第三系统按姓名搜索的翻页取全逻辑验证（离线，mock 网络）。

背景：2026-08-31 勘定——第三系统「阶段审核管理」有分页，翻页参数是
URL query string 的 `currentpage`（页码）+ `pagesize`，不是 POST body 的
`pageNumber`；pagesize 服务端硬上限 10。search_by_name 现改为按 currentpage
翻页取全所有同名学员，去除「单页 10 行截断」误判。
"""
import re
import unittest
from unittest import mock

from crawlers.third import ThirdCrawler


def _make_row(id_card, name="张振"):
    """构造一条阶段数据行（6 个 td：checkbox/姓名/身份证/学员编号/车型/培训部分）。"""
    return (
        '<tr onclick="trCbClick(,\'cbx\')">'
        '<td align="center"><input name="cbx" type="checkbox" xytybh="S1"/></td>'
        f'<td>{name}</td>'
        f'<td>{id_card}</td>'
        '<td>S123</td>'
        '<td>C1</td>'
        '<td>2</td>'
        '</tr>'
    )


def _make_page(total, pages, rows):
    """构造一页 HTML：数据行 + 分页条「共 total 条记录 pages 页」。「张振」作姓名。"""
    bar = (
        f'共<span class="td10">{total}</span>条记录&nbsp;'
        f'<span class="td10">{pages}</span>页&nbsp;&nbsp;第<span class="td10">1</span>页'
    )
    body = "".join(_make_row(*r) for r in rows)
    return f'<table><tbody>{body}</tbody></table>{bar}'


def _make_crawler():
    """绕开 __init__（避免真实登录与网络），只装 search_by_name 需要的部件。"""
    c = object.__new__(ThirdCrawler)
    c.base_url = "http://jppt.example"
    c.ensure_login = mock.Mock(return_value=True)
    c.post = mock.Mock()
    c._increment_query_retry = mock.Mock()
    c.logout = mock.Mock()
    return c


class TestParsePageMeta(unittest.TestCase):
    def test_multi_page(self):
        html = '共<span class="td10">56</span>条记录&nbsp;<span class="td10">6</span>页&nbsp;&nbsp;第<span class="td10">1</span>页'
        self.assertEqual(ThirdCrawler._parse_page_meta(html), (56, 6))

    def test_single_page(self):
        html = '共<span class="td10">10</span>条记录&nbsp;<span class="td10">1</span>页&nbsp;&nbsp;第<span class="td10">1</span>页'
        self.assertEqual(ThirdCrawler._parse_page_meta(html), (10, 1))

    def test_missing_bar(self):
        self.assertEqual(ThirdCrawler._parse_page_meta("<html></html>"), (0, 1))


class TestParseNameCandidates(unittest.TestCase):
    def test_dedup(self):
        c = _make_crawler()
        html = _make_page(3, 1, [
            ("110101199003070011", "张振"),
            ("110101199003070011", "张振"),  # 重复身份证，应去重
            ("110101199003070011", "张振"),
        ])
        res = c._parse_name_candidates(html, "张振")
        self.assertEqual(
            [x.id_card for x in res.candidates],
            ["110101199003070011", "110101199003070011"],
        )
        self.assertEqual(res.data_rows, 3)
        self.assertFalse(res.truncated)


class TestSearchByNamePagination(unittest.TestCase):
    def test_paginates_and_merges(self):
        """3 页、共 25 条阶段行、5 名不同学员：翻页取全 + 跨页去重 + truncated=False。"""
        c = _make_crawler()
        pages = {
            1: _make_page(25, 3, [("110101199003070011", "张振"), ("110101199003070011", "张振")]),
            2: _make_page(25, 3, [("110101199003070011", "张振"), ("110101199003070011", "张振")]),
            3: _make_page(25, 3, [("110101199003070011", "张振")]),
        }

        def fake_post(url, **kwargs):
            page = int(re.search(r'currentpage=(\d+)', url).group(1))
            r = mock.Mock()
            r.status_code = 200
            r.url = url
            r.text = pages[page]
            return r

        c.post.side_effect = fake_post
        res = c.search_by_name("张振")

        self.assertEqual(
            [x.id_card for x in res.candidates],
            [
                "110101199003070011", "110101199003070011", "110101199003070011",
                "110101199003070011", "110101199003070011",
            ],
        )
        self.assertFalse(res.truncated)
        self.assertEqual(res.data_rows, 25)
        self.assertEqual(c.post.call_count, 3)

    def test_single_page_no_extra_request(self):
        """只有 1 页时只发 1 次请求。"""
        c = _make_crawler()
        r = mock.Mock()
        r.status_code = 200
        r.url = "http://x"
        r.text = _make_page(3, 1, [
            ("110101199003070011", "张振"),
            ("110101199003070011", "张振"),
            ("110101199003070011", "张振"),
        ])
        c.post.return_value = r
        res = c.search_by_name("张振")
        self.assertEqual(
            [x.id_card for x in res.candidates],
            ["110101199003070011", "110101199003070011"],
        )
        self.assertEqual(res.data_rows, 3)
        self.assertEqual(c.post.call_count, 1)

    def test_truncated_when_pages_exceed_cap(self):
        """总页数超过安全上限（NAME_SEARCH_MAX_PAGES）时置 truncated=True。"""
        c = _make_crawler()
        cap = c.NAME_SEARCH_MAX_PAGES

        def fake_post(url, **kwargs):
            r = mock.Mock()
            r.status_code = 200
            r.url = url
            r.text = _make_page(600, cap + 10, [("110101199003070011", "张振")])
            return r

        c.post.side_effect = fake_post
        res = c.search_by_name("张振")
        self.assertTrue(res.truncated)
        self.assertEqual(c.post.call_count, cap)

    def test_login_failed_raises(self):
        """登录失败必须抛异常（上游据此记为 error 而非 not_found）。"""
        c = _make_crawler()
        c.ensure_login.return_value = False
        with self.assertRaises(RuntimeError):
            c.search_by_name("张振")


if __name__ == "__main__":
    unittest.main()
