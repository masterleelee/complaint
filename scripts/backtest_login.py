#!/usr/bin/env python3
"""线上回测：对东莞驾培真实登录做 N 次，统计成功率（含内部 6 次重试）。

用法: ./venv/bin/python scripts/backtest_login.py [次数]
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from crawlers.driving import DrivingCrawler

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20


def main():
    crawler = DrivingCrawler()
    ok = 0
    for i in range(1, N + 1):
        res = crawler._do_login()
        if res.success:
            ok += 1
        print(f"[{i:02d}/{N}] {'OK' if res.success else 'FAIL'}  {res.message}"
              + (f"  ({res.duration_ms}ms)" if res.duration_ms else ""))
    print(f"\n登录成功率: {ok}/{N} = {ok / N:.0%}")


if __name__ == "__main__":
    main()
