"""T0-B：用页面源码确认的真实参数名做组合验证。
确认: name / queryStartTime / queryEndTime(yyyy-MM-dd) / orgid
"""
import sys
import time

sys.path.insert(0, ".")
PROBE_NAME = "陈金生"


def ask(cw, url, extra, label):
    t0 = time.perf_counter()
    resp = cw.post(url, data={"page": 1, "rows": 50, "sort": "createtime",
                              "order": "desc", **extra}, timeout=10, retries=0)
    data = cw._response_json(resp)
    rows = data.get("rows") or []
    ms = int((time.perf_counter() - t0) * 1000)
    names = [r.get("name") for r in rows]
    bm = [r.get("bmrq") or r.get("createtime", "")[:10] for r in rows]
    og = [r.get("orgid") for r in rows]
    print(f"[{label:<38}] {ms:>5}ms total={data.get('total')} rows={len(rows)} 姓名={names[:4]} 报名日={bm[:4]} orgid={og[:4]}")
    return rows


def main():
    from crawlers.internal import InternalCrawler
    cw = InternalCrawler()
    url = f"{cw.api_base}/xyxxController/listXyxx.action"
    assert cw.ensure_login(), "登录失败"

    print("== 基线 ==")
    rows = ask(cw, url, {"name": PROBE_NAME}, "① name=陈金生")
    if not rows:
        print("!! 基线无结果，终止"); return
    row0 = rows[0]
    orgid, bmrq = row0.get("orgid", ""), (row0.get("bmrq") or "")[:10]

    print("\n== 时间范围 queryStartTime/queryEndTime ==")
    if bmrq:
        y, m, d = bmrq.split("-")
        ask(cw, url, {"name": PROBE_NAME, "queryStartTime": f"{y}-01-01",
                      "queryEndTime": f"{y}-12-31"}, f"② name+报名时间当年({y}) → 应命中")
        ask(cw, url, {"name": PROBE_NAME, "queryStartTime": "2030-01-01",
                      "queryEndTime": "2030-12-31"}, "③ name+2030年 → 应为空")

    print("\n== 分校 orgid 过滤 ==")
    if orgid:
        ask(cw, url, {"name": PROBE_NAME, "orgid": orgid}, f"④ name+orgid={orgid}(本人) → 应命中")
        ask(cw, url, {"name": PROBE_NAME, "orgid": orgid + "X"}, "⑤ name+错误orgid → 应为空")


if __name__ == "__main__":
    main()
