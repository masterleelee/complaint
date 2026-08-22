"""下载东莞驾培合同PDF并提取文本，用于验证费用字段映射 / 生成测试夹具。

用法: venv/bin/python scripts/download_contract_fixtures.py [身份证1] [身份证2] ...
不带参数时使用内置示例列表。

输出: tests/fixtures/contracts/<身份证>.txt
"""
import io
import os
import re
import sys
import time

import ddddocr
import pdfplumber
import requests
import urllib3

urllib3.disable_warnings()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config import load_config

OUT_DIR = os.path.join(ROOT, "tests", "fixtures", "contracts")
DEFAULT_ID_CARDS = [
    "110101199003070011",  # 明海川
    "110101199003070011",  # 刘郴
    "110101199003070011",  # 何屹杰
    "110101199003070011",  # 袁杰茹
    "110101199003070011",  # 测试学员丙
    "110101199003070011",  # 郑英鹏
    "110101199003070011",  # 赵雨莹
]


def solve_captcha(ocr, img):
    text = ocr.classification(img)
    chars = [c for c in text if c.isdigit() or c in "+-*/"]
    for i in range(len(chars) - 2):
        if chars[i].isdigit() and chars[i + 2].isdigit() and chars[i + 1] in "+-*/":
            try:
                value = eval(f"{int(chars[i])}{chars[i+1]}{int(chars[i+2])}")
                if isinstance(value, int) and 0 <= value <= 81:
                    return value
            except Exception:
                pass
    return None


def login(session, base_url, cfg):
    ocr = ddddocr.DdddOcr(show_ad=False)
    for _ in range(12):
        try:
            session.get(f"{base_url}/schoolLogin", timeout=8)
            img = session.get(
                f"{base_url}/captcha/captchaImage?type=math&s={time.time()}", timeout=8
            ).content
            if len(img) < 100:
                continue
            value = solve_captcha(ocr, img)
            if value is None:
                continue
            resp = session.post(
                f"{base_url}/schoolLogin",
                data={
                    "schoolType": "1",
                    "username": cfg["username"],
                    "password": cfg["password"],
                    "validateCode": str(value),
                    "rememberMe": "false",
                },
                timeout=8,
            )
            if resp.json().get("code") == 0:
                return True
        except Exception:
            time.sleep(0.5)
    return False


def contract_text(session, base_url, student_id):
    resp = session.get(f"{base_url}/business/student/viewContract/{student_id}", timeout=15)
    paths = re.findall(r"[\"']([^\"']*\.pdf[^\"']*)[\"']", resp.text)
    if not paths:
        return ""
    path = paths[0]
    url = path if path.startswith("http") else base_url + path
    resp = session.get(url, timeout=30)
    if resp.status_code != 200 or not resp.content:
        return ""
    try:
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            pages = [pg.extract_text() or "" for pg in pdf.pages]
    except Exception as exc:
        print(f"  pdf parse fail: {exc}")
        return ""
    return "\n\n===PAGE===\n\n".join(pages)


def main():
    id_cards = sys.argv[1:] or DEFAULT_ID_CARDS
    cfg = load_config()["driving_system"]
    base_url = cfg["base_url"].rstrip("/")

    session = requests.Session()
    session.verify = False
    session.headers.update(
        {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    )
    if not login(session, base_url, cfg):
        print("登录失败")
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    for id_card in id_cards:
        resp = session.post(
            f"{base_url}/business/student/list",
            data={"pageNum": 1, "pageSize": 10, "identity": id_card},
            timeout=15,
        )
        rows = resp.json().get("rows") or []
        if not rows:
            print(f"{id_card}: 未找到学员")
            continue
        sid = rows[0]["id"]
        text = contract_text(session, base_url, sid)
        if not text:
            print(f"{id_card}: 无合同或无文本层")
            continue
        out = os.path.join(OUT_DIR, f"{id_card}.txt")
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        fee_line = [ln for ln in text.splitlines() if "培训费用合计" in ln]
        print(f"{id_card}: {os.path.basename(out)} ({len(text)} chars) {fee_line[:1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())