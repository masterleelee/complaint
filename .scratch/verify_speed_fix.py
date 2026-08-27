# -*- coding: utf-8 -*-
"""复测：提取线索提速 + 二次提取姓名更新回归（/api/query/* 已拦截避免脏数据）"""
import os, time, json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5003"
ROOT = "/Users/master/Desktop/投诉处理系统"
EV = os.path.join(ROOT, ".scratch", "test-evidence", "ui")
R = []

TEXT_PHONE = "学员王小明手机13800000000投诉驾校乱收费，报名后未安排上车训练，要求退还全部费用，请核实处理。"
TEXT_NAME = "（李开莹）退款不合理，我没练过他们的车，没享受过他们的服务，考试也没考过一次，就只挂了个名字交了钱在那里，现在办理退费，他们说按照合同，要扣掉我的服务费500块钱，还有建档费600块，违约金338元，我总共才交3380元，我不认可他们的扣费"

def rec(vid, title, ok, note):
    R.append({"id": vid, "title": title, "ok": ok, "note": note})
    print(("✅" if ok else "❌") + f" {vid} {title} :: {note[:150]}", flush=True)

def extract(pg, text):
    before = pg.locator(".clues").inner_text() if pg.locator(".clues").count() else None
    pg.fill("#intake-paste-input", text)
    t0 = time.time()
    pg.locator("button:has-text('提取线索')").click()
    # 等横幅出现且内容变化（快路径下横幅可能在首轮轮询前就已重现，不能按消失→重现判定）
    for _ in range(150):
        pg.wait_for_timeout(200)
        c = pg.locator(".clues")
        if c.count():
            if before is None or c.inner_text() != before:
                break
    return time.time() - t0

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
    pg = ctx.new_page()
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.route("**/api/query/**", lambda r: r.abort())

    pg.goto(BASE, wait_until="networkidle", timeout=30000)
    pg.wait_for_timeout(1200)

    # T1: 含手机号材料 → 提取秒回
    dt1 = extract(pg, TEXT_PHONE)
    phone_val = pg.locator("#f_phone").input_value()
    t1_ok = dt1 < 1.5 and phone_val == "13800000000"
    pg.screenshot(path=os.path.join(EV, "speed-1-phone.png"))
    rec("T1", "含手机号材料：提取秒回+手机号回填", t1_ok,
        f"耗时={dt1:.2f}s #f_phone={phone_val!r}")

    # T2: 换纯姓名材料（李开莹）→ 提取 <4s 且姓名更新（回归上轮修复）
    dt2 = extract(pg, TEXT_NAME)
    name_val = pg.locator("#f_name").input_value()
    banner = pg.locator(".clues").inner_text() if pg.locator(".clues").count() else ""
    t2_ok = dt2 < 4.0 and name_val == "李开莹" and "李开莹" in banner
    pg.screenshot(path=os.path.join(EV, "speed-2-name.png"))
    rec("T2", "纯姓名材料：提取<4s+姓名更新为李开莹", t2_ok,
        f"耗时={dt2:.2f}s #f_name={name_val!r} 横幅含李开莹={'李开莹' in banner}")

    js_err = [e for e in errs if "jsdelivr" not in e and "ERR_FAILED" not in e]
    rec("T3", "全程无 JS 错误", not js_err, f"错误={js_err[:2] or '无'}")

    ctx.close(); browser.close()

json.dump(R, open(os.path.join(EV, "speed-fix-result.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
n = sum(1 for r in R if r["ok"])
print(f"\n==== UI 复测: {n}/{len(R)} 通过 ====")
