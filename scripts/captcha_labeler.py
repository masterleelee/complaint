#!/usr/bin/env python3
"""东莞驾培 验证码标注 + 规律分析工具

用法:
  python scripts/captcha_labeler.py fetch [--out DIR] [--n N]
      下载 N 张验证码到 DIR/，生成 review.html（请标注完整等式，如 7+3 / 9*2 / 4/1）
  python scripts/captcha_labeler.py summarize [--out DIR]
      读 DIR/labels.json，重新 OCR 同批图片，输出 数字/运算符 准确率 与混淆矩阵
"""
import os
import re
import json
import random
import argparse
import warnings
import requests
from collections import Counter
from ddddocr import DdddOcr

warnings.filterwarnings("ignore")

BASE = "https://www.guanjiaxie.com:8089"
OCR_CHARS = "+-*/txhkyijla"
ocr = DdddOcr(show_ad=False)


def fetch(out_dir, n):
    os.makedirs(out_dir, exist_ok=True)
    sess = requests.Session()
    items = []
    for i in range(1, n + 1):
        r = sess.get(
            f"{BASE}/captcha/captchaImage?type=math&s={random.random()}",
            timeout=10,
            verify=False,
        )
        if r.status_code != 200 or len(r.content) < 100:
            continue
        fn = f"{i:04d}.png"
        with open(os.path.join(out_dir, fn), "wb") as f:
            f.write(r.content)
        items.append({"file": fn, "ocr_guess": ocr.classification(r.content), "eq": ""})
    lab = os.path.join(out_dir, "labels.json")
    with open(lab, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    cards = "\n".join(
        f'<div class="card">'
        f'<img src="{it["file"]}">'
        f'<div class="meta">OCR猜测: <b>{it["ocr_guess"]}</b></div>'
        f'<input type="text" name="{it["file"]}" placeholder="完整等式 如 7+3">'
        f'</div>'
        for it in items
    )
    html = f"""<html><head><meta charset="utf-8"><style>
      body{{font-family:sans-serif}}
      .card{{border:1px solid #ccc;margin:8px;padding:8px;display:inline-block;vertical-align:top}}
      img{{width:160px;height:60px;display:block}}
      .meta{{font-size:12px;margin:4px 0}}
      input{{width:150px}}
      #bar{{position:fixed;top:0;left:0;right:0;background:#550efc;color:#fff;padding:10px;z-index:9}}
    </style></head>
    <body>
    <div id="bar"><b>东莞驾培验证码标注（完整等式）</b> — 共 {len(items)} 张，逐张在框内输入完整等式（如 <b>7+3</b> / <b>9*2</b> / <b>4/1</b>），点导出覆盖 labels.json</div>
    <div style="margin-top:60px">{cards}</div>
    <button onclick="exp()" style="position:fixed;bottom:20px;left:20px;font-size:18px;padding:10px 20px">导出 labels.json</button>
    <script>
    function exp(){{
      const out=[];
      document.querySelectorAll('input[type=text]').forEach(e=>{{ if(e.value.trim()) out.push({{file:e.name, eq:e.value.trim()}}); }});
      const b=new Blob([JSON.stringify(out,null,2)],{{type:'application/json'}});
      const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='labels.json';a.click();
    }}
    </script>
    </body></html>"""
    html_path = os.path.join(out_dir, "review.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"已下载 {len(items)} 张 -> {out_dir}/")
    print(f"请打开 {html_path} 逐张输入完整等式，导出后覆盖 {lab}")
    print(f"完成后运行: python scripts/captcha_labeler.py summarize --out {out_dir}")


def _ocr_digits(text):
    return [c for c in text if c.isdigit()]


def _resolved_op(text):
    """复刻 driving.py 的运算符解析逻辑，返回解析出的运算符字符（用于对照）。"""
    _op_map = {'t': '+', 'x': '+', 'y': '-', 'i': '+', 'j': '+', 'h': '*', 'k': '*', 'l': '/'}
    _NOISE = set('a')
    chars = [c for c in text
             if (c.isdigit() or c in '+-*/' or c in _op_map) and c not in _NOISE]
    digits = [c for c in chars if c.isdigit()]
    if len(digits) < 2:
        return None
    ops = [_op_map.get(c, c) for c in chars if c in '+-*/' or c in _op_map]
    if len(chars) >= 3 and chars[0].isdigit() and chars[2].isdigit() \
            and (chars[1] in '+-*/' or chars[1] in _op_map):
        return _op_map.get(chars[1], chars[1])
    if ops:
        return ops[0]
    for op in '+-*/':
        return op
    return None


def summarize(out_dir):
    lab = os.path.join(out_dir, "labels.json")
    if not os.path.exists(lab):
        print(f"找不到 {lab}")
        return
    items = json.load(open(lab, encoding="utf-8"))
    eq_pat = re.compile(r"(\d+)\s*([+\-*/])\s*(\d+)")

    digit_ok = 0
    op_ok = 0
    full_ok = 0
    total = 0
    no_eq = 0
    conf = Counter()  # (ocr_op_char_tuple, true_op)
    for it in items:
        eq = it.get("eq")
        if not eq:
            no_eq += 1
            continue
        m = eq_pat.search(eq)
        if not m:
            no_eq += 1
            continue
        true_d1, true_op, true_d2 = m.group(1), m.group(2), m.group(3)
        true_digits = f"{true_d1}{true_d2}"
        total += 1
        path = os.path.join(out_dir, it["file"])
        text = ocr.classification(open(path, "rb").read())
        ocr_digits = "".join(_ocr_digits(text))
        # 数字准确率：OCR 读出的两位数字 与 真值数字（不计顺序？计顺序）
        d_ok = (ocr_digits == true_digits) or (sorted(ocr_digits) == sorted(true_digits) and len(ocr_digits) == 2)
        r_op = _resolved_op(text)
        o_ok = (r_op == true_op)
        if d_ok:
            digit_ok += 1
        if o_ok:
            op_ok += 1
        if d_ok and o_ok:
            full_ok += 1
        # 混淆矩阵：OCR 运算符字符 -> 真运算符
        oc = tuple(c for c in text if c in OCR_CHARS)
        conf[(oc, true_op)] += 1

    print(f"已标注(含完整等式): {total}（跳过无等式: {no_eq}）\n")
    print(f"数字准确率 (OCR两位==真值两位): {digit_ok}/{total} = {digit_ok/total:.0%}")
    print(f"运算符准确率(解析运算符==真运算符): {op_ok}/{total} = {op_ok/total:.0%}")
    print(f"完整正确(数字且运算符都对): {full_ok}/{total} = {full_ok/total:.0%}\n")
    print("=== OCR字符 -> 真运算符 混淆矩阵 ===")
    for (oc, true), c in conf.most_common():
        print(f"  {''.join(oc)!r:10} -> {true}  x{c}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "summarize"])
    ap.add_argument("--out", default="tmp/captcha_samples")
    ap.add_argument("--n", type=int, default=80)
    args = ap.parse_args()
    if args.cmd == "fetch":
        fetch(args.out, args.n)
    else:
        summarize(args.out)
