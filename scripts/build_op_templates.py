#!/usr/bin/env python3
"""从标注样本构建运算符字形模板文件 crawlers/captcha_op_templates.npz。

用法: ./venv/bin/python scripts/build_op_templates.py
依赖: tmp/captcha_samples_2/{labels.json, *.png}（由 scripts/captcha_labeler.py 生成）
"""
import os
import re
import json
import io
import sys
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from crawlers.driving import DrivingCrawler  # 复用相同归一化，保证模板与运行时一致

SAMPLES = os.path.join(ROOT, "tmp", "captcha_samples_2")
OUT = DrivingCrawler._OP_TEMPLATES_PATH
eq_pat = re.compile(r"(\d+)\s*([+\-*/])\s*(\d+)")


def main():
    labels = json.load(open(os.path.join(SAMPLES, "labels.json"), encoding="utf-8"))
    templates, labs = [], []
    for it in labels:
        m = eq_pat.search(it.get("eq", ""))
        if not m:
            continue
        img = open(os.path.join(SAMPLES, it["file"]), "rb").read()
        im = DrivingCrawler._gray(img)
        c = DrivingCrawler._norm_crop(im, DrivingCrawler._OP_WIN)
        templates.append(c)
        labs.append(m.group(2))
    if not templates:
        raise SystemExit("未找到可用标注样本")
    np.savez(OUT, templates=np.stack(templates), labels=np.array(labs))
    print(f"已保存 {len(templates)} 个运算符模板 -> {OUT}")


if __name__ == "__main__":
    main()
