#!/usr/bin/env python3
"""原型 v4：运算符锚定 + 相对窗口 + 模板匹配（数字与运算符都按字形匹配）。
验证用 leave-one-out。"""
import re, json, cv2, numpy as np, warnings
from collections import Counter
from ddddocr import DdddOcr
warnings.filterwarnings("ignore")

OUT = "tmp/captcha_samples_2"
labels = json.load(open(f"{OUT}/labels.json", encoding="utf-8"))
eq_pat = re.compile(r"(\d+)\s*([+\-*/])\s*(\d+)")
ocr = DdddOcr(show_ad=False)

W_OP = (56, 95)
OPW = 38
SZ = 32
DW = 40  # 数字窗口半宽


def load_gray(path):
    a = np.frombuffer(open(path, "rb").read(), np.uint8)
    return cv2.imdecode(a, cv2.IMREAD_GRAYSCALE)


def norm_crop(im, win):
    x0, x1 = win
    if x1 <= x0:
        return np.zeros((SZ, SZ), np.uint8)
    sub = im[:, x0:x1]
    _, b = cv2.threshold(sub, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    rows = np.where(b.sum(axis=1) > 0)[0]
    y0, y1 = (rows[0], rows[-1]) if len(rows) else (0, b.shape[0] - 1)
    g = (b[y0:y1 + 1] > 0).astype(np.uint8)
    return cv2.resize(g, (SZ, SZ), interpolation=cv2.INTER_NEAREST)


def dist(a, b):
    return np.mean(a != b)


def match(crop, templates):
    best, bd = None, 1e9
    for t, lab in templates:
        d = dist(crop, t)
        if d < bd:
            bd, best = d, lab
    return best


def best_dist(crop, templates):
    return min((dist(crop, t) for t, _ in templates), default=1e9)


def locate_op_left(im, op_t):
    best_p, best_d = 56, 1e9
    for p in range(40, 92):
        c = norm_crop(im, (p, p + OPW))
        d = best_dist(c, op_t)
        if d < best_d:
            best_d, best_p = d, p
    return best_p


def extract(im, op_t):
    p = locate_op_left(im, op_t)
    op = norm_crop(im, (p, p + OPW))
    d1 = norm_crop(im, (max(0, p - DW), max(1, p - 2)))
    d2 = norm_crop(im, (min(im.shape[1], p + OPW + 2), min(im.shape[1], p + OPW + DW)))
    return d1, op, d2


# 固定窗口构建 op 模板（operator 位置稳定）
data = []
for it in labels:
    m = eq_pat.search(it.get("eq", ""))
    if not m:
        continue
    im = load_gray(f"{OUT}/{it['file']}")
    data.append({"im": im, "op_fixed": norm_crop(im, W_OP),
                 "d1": m.group(1), "op": m.group(2), "d2": m.group(3)})

digit_ok = op_ok = full_ok = 0
for i, s in enumerate(data):
    op_t = [(d["op_fixed"], d["op"]) for j, d in enumerate(data) if j != i]
    dig_t = []
    for j, d in enumerate(data):
        if j == i:
            continue
        d1, _, d2 = extract(d["im"], op_t)
        dig_t.append((d1, d["d1"]))
        dig_t.append((d2, d["d2"]))
    p = locate_op_left(s["im"], op_t)
    c1 = norm_crop(s["im"], (max(0, p - DW), max(1, p - 2)))
    co = norm_crop(s["im"], (p, p + OPW))
    c2 = norm_crop(s["im"], (min(s["im"].shape[1], p + OPW + 2), min(s["im"].shape[1], p + OPW + DW)))
    p1 = match(c1, dig_t)
    po = match(co, op_t)
    p2 = match(c2, dig_t)
    dok = (p1 == s["d1"] and p2 == s["d2"])
    ook = (po == s["op"])
    if dok: digit_ok += 1
    if ook: op_ok += 1
    if dok and ook: full_ok += 1

n = len(data)
print(f"[运算符锚定+模板匹配 leave-one-out] 样本={n}")
print(f"  数字正确: {digit_ok}/{n} = {digit_ok/n:.0%}")
print(f"  运算符正确: {op_ok}/{n} = {op_ok/n:.0%}")
print(f"  完整等式正确: {full_ok}/{n} = {full_ok/n:.0%}")
