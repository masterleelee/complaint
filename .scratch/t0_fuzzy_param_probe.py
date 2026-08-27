"""T0 验证：内部系统/第三系统 列表接口是否支持按姓名筛选。
只读探测，不改任何现有代码。用法: venv/bin/python .scratch/t0_fuzzy_param_probe.py
"""
import sys
import time

sys.path.insert(0, ".")

PROBE_NAME = "陈金生"
FAKE_NAME = "查无此人测试XYZ"
CONTROL_ID = "110101199003070011"


def section(title):
    print(f"\n{'='*60}\n■ {title}\n{'='*60}")


def probe_internal():
    from crawlers.internal import InternalCrawler

    cw = InternalCrawler()
    url = f"{cw.api_base}/xyxxController/listXyxx.action"

    def ask(extra, label):
        t0 = time.perf_counter()
        resp = cw.post(url, data={"page": 1, "rows": 50, "sort": "createtime",
                                  "order": "desc", **extra}, timeout=10, retries=0)
        data = cw._response_json(resp)
        rows = data.get("rows") or []
        ms = int((time.perf_counter() - t0) * 1000)
        names = [r.get("name", "") for r in rows]
        total = data.get("total")
        print(f"[{label:<28}] {ms:>5}ms total={total} rows={len(rows)} 姓名样例={names[:5]}")
        return rows, data

    section("内部系统 listXyxx.action")

    ok = cw.ensure_login()
    print(f"登录: {'OK' if ok else 'FAIL'}")
    if not ok:
        return

    # 阳性对照：按身份证（现有能力）
    ask({"sfzh": CONTROL_ID}, "对照: sfzh=36242…3215")

    # 探测1: name 精确名
    rows_name, _ = ask({"name": PROBE_NAME}, "探测: name=陈金生")

    # 探测2: name 单姓（测 LIKE 模糊性）
    rows_xing, _ = ask({"name": PROBE_NAME[0]}, f"探测: name={PROBE_NAME[0]}(单姓)")

    # 判定
    filtered = bool(rows_name) and all(r.get("name") == PROBE_NAME for r in rows_name)
    like = len(rows_xing) > len(rows_name)
    print("\n>>> 内部系统判定:")
    if filtered:
        print(f"    ✅ 支持 name 服务端过滤（精确匹配）"
          f"{'，且单字返回更多 → LIKE 前缀匹配' if like else '；单姓行为待观察'}")
    elif rows_name:
        mixed = [n for n in {r.get('name') for r in rows_name}][:8]
        print(f"    ❌ name 参数疑似被忽略（返回混合姓名: {mixed}）→ 需抓包网页版确认真实参数名")
    else:
        print("    ⚠️ name=陈金生 返回空：可能该学员不存在或参数无效，请换一个真实存在的姓名重试")

    # 探测3: 若支持 name，试组合过滤 orgid / bmrq
    if filtered and rows_name:
        row0 = rows_name[0]
        ask({"name": PROBE_NAME, "orgid": row0.get("orgid", "")}, f"组合: name+orgid={row0.get('orgid')}")
        ask({"name": PROBE_NAME, "bmrq": row0.get("bmrq", "")}, f"组合: name+bmrq={row0.get('bmrq')}")
        ask({"name": PROBE_NAME, "orgdh": row0.get("orgdh", "")}, f"组合: name+orgdh={row0.get('orgdh')}")

    # 阴性对照
    ask({"name": FAKE_NAME}, "阴性对照: name=不存在")


def probe_third():
    from crawlers.third import ThirdCrawler

    cw = ThirdCrawler()
    url = f"{cw.base_url}/school/schoolOprAction!xsjdshList.action"

    def ask(extra, label):
        t0 = time.perf_counter()
        resp = cw.post(url, data={"pageNumber": 1, "pagesize": 20, **extra}, timeout=15)
        html = resp.text
        ms = int((time.perf_counter() - t0) * 1000)
        n_tr = html.count("<tr")
        has_ctrl = CONTROL_ID in html
        has_probe = PROBE_NAME in html
        has_fake = FAKE_NAME in html
        print(f"[{label:<26}] {ms:>5}ms len={len(html):>6} tr={n_tr:>3} 含对照证件={has_ctrl} 含'{PROBE_NAME}'={has_probe} 含假名={has_fake}")
        return html

    section("第三系统 xsjdshList.action")

    ok = cw.ensure_login()
    print(f"登录: {'OK' if ok else 'FAIL'}")
    if not ok:
        return

    base = ask({}, "基线: 无筛选")
    ctrl = ask({"xyxshzVo.sfzmhm": CONTROL_ID}, "对照: sfzmhm=证件号")
    by_name = ask({"xyxshzVo.xm": PROBE_NAME}, "探测A: xyxshzVo.xm=陈金生")
    fake = ask({"xyxshzVo.xm": FAKE_NAME}, "阴性A: xm=假名")
    alt = ask({"xyxshzVo.name": PROBE_NAME}, "探测B: xyxshzVo.name")

    print("\n>>> 第三系统判定:")
    name_supported = (by_name != base) and (PROBE_NAME in by_name) and (not fake or FAKE_NAME not in fake)
    if name_supported:
        print("    ✅ xyxshzVo.xm 支持姓名过滤")
    else:
        print("    ❌ 未见姓名过滤生效证据 → fallback：跳过第三系统模糊查询")


if __name__ == "__main__":
    try:
        probe_internal()
    except Exception as e:
        print(f"内部系统探测异常: {type(e).__name__}: {e}")
    try:
        probe_third()
    except Exception as e:
        print(f"第三系统探测异常: {type(e).__name__}: {e}")
