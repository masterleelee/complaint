# -*- coding: utf-8 -*-
"""复测：二次粘贴新材料后，姓名/候选列表/提示是否正确更新（修复 applyIntakeResult 残留问题）"""
import os, time, json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5003"
ROOT = "/Users/master/Desktop/投诉处理系统"
EV = os.path.join(ROOT, ".scratch", "test-evidence", "ui")
os.makedirs(EV, exist_ok=True)
R = []

TEXT_A = "学员刘乐怡投诉驾校乱收费，报名缴费后一直未安排上车训练，多次沟通无果，要求尽快退还全部费用，请核实处理。"
TEXT_B = "（李开莹）退款不合理，我没练过他们的车，没享受过他们的服务，考试也没考过一次，就只挂了个名字交了钱在那里，现在办理退费，他们说按照合同，要扣掉我的服务费500块钱，还有建档费600块，违约金338元，我总共才交3380元，第一大问题就是这个扣费问题，我不认可他们的扣费"
TEXT_C = "投诉学员陈晓明，身份证号码110101199003070011 手机号13800000000，报名后未培训要求退费。"
FAKE_ID = "110101199003070011"

def rec(vid, title, ok, note):
    R.append({"id": vid, "title": title, "ok": ok, "note": note})
    print(("✅" if ok else "❌") + f" {vid} {title} :: {note[:150]}", flush=True)

def extract(pg, text):
    pg.fill("#intake-paste-input", text)
    pg.locator("button:has-text('提取线索')").click()
    # 等绿色线索横幅或错误提示出现（AI 提取可能较慢）
    for _ in range(60):
        pg.wait_for_timeout(1000)
        if pg.locator(".clues").count() or pg.locator(".alert-danger").count():
            break
    pg.wait_for_timeout(800)  # 等 500ms 自动查询定时器与回填脉冲结束

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
    pg = ctx.new_page()
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    # 拦截三系统查询，避免真实爬虫/建案产生脏数据
    pg.route("**/api/query/**", lambda r: r.abort())

    pg.goto(BASE, wait_until="networkidle", timeout=30000)
    pg.wait_for_timeout(1200)

    # T1: 第一次提取（刘乐怡）→ 姓名回填
    extract(pg, TEXT_A)
    name1 = pg.locator("#f_name").input_value()
    t1_ok = name1 == "刘乐怡"
    pg.screenshot(path=os.path.join(EV, "reextract-1-liuleyi.png"))
    rec("T1", "首次提取：姓名回填为 刘乐怡", t1_ok, f"#f_name={name1!r}")

    # T2: 替换为新材料（李开莹）再提取 → 姓名必须更新（原 bug：保留刘乐怡）
    extract(pg, TEXT_B)
    name2 = pg.locator("#f_name").input_value()
    banner = pg.locator(".clues").inner_text() if pg.locator(".clues").count() else ""
    t2_ok = name2 == "李开莹"
    pg.screenshot(path=os.path.join(EV, "reextract-2-likaiying.png"))
    rec("T2", "二次提取新材料：姓名更新为 李开莹", t2_ok,
        f"#f_name={name2!r} 横幅含李开莹={'李开莹' in banner}")

    # T3: 再提取含证件号材料 → 姓名更新 + 旧候选区清空（queryAll 路径不残留旧列表）
    extract(pg, TEXT_C)
    name3 = pg.locator("#f_name").input_value()
    cand_gone = pg.locator(".cand-wrap").count() == 0
    id_filled = FAKE_ID in (pg.locator("#f_id_card").input_value() or "")
    t3_ok = (name3 == "陈晓明") and cand_gone and id_filled
    pg.screenshot(path=os.path.join(EV, "reextract-3-idpath.png"))
    rec("T3", "三次提取(证件号路径)：姓名更新+旧候选清空+证号回填", t3_ok,
        f"#f_name={name3!r} 候选区已清={cand_gone} 证号回填={id_filled}")

    js_err = [e for e in errs if "jsdelivr" not in e and "ERR_FAILED" not in e]
    rec("T4", "全程无 JS 错误", not js_err, f"错误={js_err[:2] or '无'}")

    ctx.close(); browser.close()

json.dump(R, open(os.path.join(EV, "reextract-fix-result.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
n = sum(1 for r in R if r["ok"])
print(f"\n==== 复测: {n}/{len(R)} 通过 ====")
