"""T0-C：第三系统姓名检索实测（修正编码处理）。
端点: /xyxx/xyxxAction!list.action，姓名参数 ksyyXyxx.xm
"""
import sys
import time

sys.path.insert(0, ".")
PROBE_NAME = "陈金生"
FAKE_NAME = "查无此人测试XYZ"
CONTROL_ID = "110101199003070011"


def analyze(resp, label):
    # 第三系统 GBK 编码，必须显式设定后再读 text
    resp.encoding = "GBK"
    html = resp.text
    ms = int((time.perf_counter() - t0) * 1000) if False else None
    n_tr = html.count("<tr")
    has_name = PROBE_NAME in html
    has_ctrl = CONTROL_ID in html
    has_fake = FAKE_NAME in html
    print(f"[{label:<22}] len={len(html):>6} tr={n_tr:>3} 含'{PROBE_NAME}'={has_name} 含对照证件={has_ctrl} 含假名={has_fake}")
    return html


def main():
    from crawlers.third import ThirdCrawler
    cw = ThirdCrawler()
    url = f"{cw.base_url}/xyxx/xyxxAction!list.action"

    print("登录:", "OK" if cw.ensure_login() else "FAIL")
    base = cw.post(url, data={"pagesize": 20, "pageNumber": 1}, timeout=15)
    base.encoding = "GBK"; L0 = len(base.text)
    print(f"[基线: 无筛选         ] len={L0} tr={base.text.count('<tr')}")

    ctrl = cw.post(url, data={"ksyyXyxx.sfzmhm": CONTROL_ID, "pagesize": 20, "pageNumber": 1}, timeout=15)
    analyze(ctrl, "对照: sfzmhm=证件号")

    nm = cw.post(url, data={"ksyyXyxx.xm": PROBE_NAME, "pagesize": 20, "pageNumber": 1}, timeout=15)
    analyze(nm, "探测: ksyyXyxx.xm=陈金生")

    fk = cw.post(url, data={"ksyyXyxx.xm": FAKE_NAME, "pagesize": 20, "pageNumber": 1}, timeout=15)
    analyze(fk, "阴性: xm=假名")

    print("\n>>> 第三系统判定:")
    if PROBE_NAME in nm.text and CONTROL_ID in ctrl.text and FAKE_NAME not in fk.text and L0 != len(nm.text):
        print("    ✅ ksyyXyxx.xm 姓名检索可用（控制命中、姓名命中、假名无命中、长度有别于基线）")
    else:
        print("    ⚠️ 仍无法确认，详见上方数据")


if __name__ == "__main__":
    main()
