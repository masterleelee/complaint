#!/usr/bin/env python3
"""离线回测：用 79 张标注样本验证 _solve_captcha 单张准确率。

用法: ./venv/bin/python scripts/backtest_captcha.py
"""
import os
import re
import json
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from crawlers.driving import DrivingCrawler

SAMPLES = os.path.join(ROOT, "tmp", "captcha_samples_2")
eq_pat = re.compile(r"(\d+)\s*([+\-*/])\s*(\d+)")

# 跳过 __init__（不需要 config），仅测试方法
crawler = DrivingCrawler.__new__(DrivingCrawler)


def main():
    labels = json.load(open(os.path.join(SAMPLES, "labels.json"), encoding="utf-8"))
    n = ok = 0
    wrong = []
    for it in labels:
        m = eq_pat.search(it.get("eq", ""))
        if not m:
            continue
        n += 1
        true = DrivingCrawler._eval_op(int(m.group(1)), m.group(2), int(m.group(3)))
        r = crawler._solve_captcha(open(os.path.join(SAMPLES, it["file"]), "rb").read())
        got = r[0] if r else None
        if got == true:
            ok += 1
        else:
            wrong.append((it["file"], m.group(0), got))
    print(f"单张准确率: {ok}/{n} = {ok / n:.1%}")
    if wrong:
        print(f"错误样本({len(wrong)}):")
        for f, eq, got in wrong:
            print(f"  {f} 真值={eq} 识别={got}")


if __name__ == "__main__":
    main()
