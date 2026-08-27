# -*- coding: utf-8 -*-
"""ISS-B-01~09 修复快速验证脚本（Agent-B）
用法：python3 .scratch/verify-b-fixes.py
前提：被测服务 http://127.0.0.1:5003 在线；D 已完成修复并重启服务。
原则：只读校验优先；破坏性用例沿用 T2 当场恢复模式；QA 工单挂靠不新增数据。
输出：逐项结论 + 汇总 .scratch/test-evidence/api/r2-verify-result.json
"""
import json, os, sqlite3, sys, time
import requests

BASE="http://127.0.0.1:5003"
ROOT="/Users/master/Desktop/投诉处理系统"
DB=os.path.join(ROOT,"data/complaints.db")
OUT=os.path.join(ROOT,".scratch","test-evidence","api","r2-verify-result.json")
S=requests.Session()
R=[]

def check(vid,title,fixed,note="",detail=None):
    R.append({"id":vid,"title":title,"verdict":fixed,"note":note,"detail":detail})
    tag={"修复确认":"✅","未修复":"❌","跳过":"⏭"}.get(fixed,"·")
    print(f"{tag} {vid} [{fixed}] {title} {('- '+note[:100]) if note else ''}",flush=True)

def call(m,p,timeout=60,**kw):
    try:
        r=S.request(m,BASE+p,timeout=timeout,**kw)
        try: b=r.json()
        except Exception: b=r.text[:800]
        return r.status_code,b
    except Exception as e:
        return -1,{"client_error":str(e)}

# 前置健康检查
st,_=call("GET","/api/tickets?limit=1")
if st!=200:
    print(f"❌ 服务不可达(http={st})，验证中止"); sys.exit(1)

conn=sqlite3.connect(DB); conn.row_factory=sqlite3.Row

# ── V01 ISS-B-01 P1: save_analysis 来源工单生成登记表不再 500 ──
# 步骤：先用 save_analysis 重写 QA-API-003（走修复后的落库格式）→ 生成登记表断言非500且成功
p={"ticket_id":"QA-API-003",
   "analysis_data":{"total_fee":3000,"actual_paid":2000,"total_deduction":200,"refund":1800,
                    "deduction_detail":[{"item":"违约金","amount":200}],"contract_code":"QA-V1"}}
st1,b1=call("POST","/api/contract/save_analysis",json=p)
st2,b2=call("POST","/api/tickets/QA-API-003/register-form",timeout=180)
ok = st1==200 and st2==200 and isinstance(b2,dict) and b2.get("success")
check("V01","B-01 登记表在save_analysis来源工单上正常生成",
      "修复确认" if ok else "未修复",
      f"save={st1} form={st2}" + (f" err={str(b2)[:120]}" if st2>=500 else ""),
      {"save_status":st1,"form_status":st2})

# ── V02 ISS-B-02 P1: set-default 幽灵行 ──
st,b=call("PUT","/api/templates/QA-VERIFY-GHOST/default")
row=conn.execute("SELECT * FROM reply_templates WHERE id='QA-VERIFY-GHOST'").fetchone()
ghost=bool(row)
if ghost:
    conn.execute("DELETE FROM reply_templates WHERE id='QA-VERIFY-GHOST'"); conn.commit()  # 当场清理
check("V02","B-02 不存在id设默认不再产生幽灵行",
      "修复确认" if (not ghost and st==404) else ("部分修复" if not ghost else "未修复"),
      f"http={st} ghost_row={'仍创建(已清理)' if ghost else '无'}",
      {"status":st,"ghost_row":ghost})

# ── V03 ISS-B-03 P1: ovc 浮点截断 + 破坏性全表替换 ──
st,b=call("GET","/api/org-vehicle-counts")
orig=b["data"]["items"]; otot=b["data"]["total_vehicles"]
st2,b2=call("PUT","/api/org-vehicle-counts",json={"items":[{"unit_code":"QA-VERIFY-FLOAT","vehicle_count":1.5}]})
st3,b3=call("GET","/api/org-vehicle-counts")
after=b3["data"]["items"]
rejected = (st2==400)
intact_or_fixed = (st3==200 and len(after)==len(orig))
call("PUT","/api/org-vehicle-counts",json={"items":orig})  # 当场恢复
st4,b4=call("GET","/api/org-vehicle-counts")
restored = b4["data"]["items"]==orig and b4["data"]["total_vehicles"]==otot
verdict="修复确认" if (rejected and restored) else ("部分修复" if restored and not rejected and intact_or_fixed else "未修复")
check("V03","B-03 车辆数浮点输入被拒绝且配置无损",
      verdict,
      f"float_put={st2}(期望400) 替换发生={not intact_or_fixed} 恢复一致={restored}",
      {"put_status":st2,"items_after":len(after),"restored":restored})

# ── V04 ISS-B-04 P2: archive 归档后 handle_status 联动 ──
# 用 QA-FLOW-001（闸门字段齐备、两件套已在）重新归档验证联动
st,b=call("POST","/api/tickets/QA-FLOW-001/archive",timeout=120)
st2,b2=call("GET","/api/tickets/QA-FLOW-001")
d=b2["data"] if st2==200 else {}
ok = st==200 and d.get("handle_status")=="已完结" and bool(d.get("completed_at")) and d.get("archive_status")=="已归档"
check("V04","B-04 归档后 handle_status=已完结 且 completed_at 落值",
      "修复确认" if ok else "未修复",
      f"archive={st} handle_status={d.get('handle_status')} completed_at={bool(d.get('completed_at'))}",
      {"archive_status_code":st,"handle_status":d.get("handle_status")})

# ── V05 ISS-B-05 P2: 非法JSON 应 400 非 500 ──
h={"Content-Type":"application/json"}
st1,_=call("POST","/api/auth/login",data="junk",headers=h)
st2,_=call("POST","/api/query",data="junk",headers=h)
ok = st1<500 and st2<500
check("V05","B-05 非法JSON返回4xx而非500",
      "修复确认" if ok else "未修复",
      f"login={st1} query={st2}",
      {"login":st1,"query":st2})

# ── V06 ISS-B-06 P2: 分页参数非法值不泄漏异常原文 ──
st,b=call("GET","/api/tickets?limit=abc&offset=xyz")
body=json.dumps(b,ensure_ascii=False) if not isinstance(b,str) else b
leak="invalid literal" in body or "Traceback" in body
ok = st in (200,400) and not leak
check("V06","B-06 limit/offset非法值报错友好化",
      "修复确认" if ok else "未修复",
      f"http={st} 泄漏原文={leak}",
      {"status":st,"body_snippet":body[:150]})

# ── V07 ISS-B-07 P2: 非18位垃圾证件号入参拦截 ──
# 期望：格式白名单校验拒绝(400)；若产品决策改为文档口径放行需人工复核
t0=time.monotonic()
st,b=call("POST","/api/query",json={"id_card":"ABC12345XYZ","ticket_id":"QA-API-004"},timeout=300)
el=int((time.monotonic()-t0)*1000)
ok = st==400
check("V07","B-07 ABC12345XYZ 被入参校验拦截(不再直打三系统)",
      "修复确认" if ok else "未修复",
      f"http={st} 耗时{el}ms"+("" if ok else "（若按'文档口径放行'决策则本条预期需人工复核）"),
      {"status":st,"elapsed_ms":el})

# ── V08 ISS-B-08 P3: 不存在资源 404 语义统一 ──
st1,b1=call("POST","/api/reply/generate",json={"ticket_id":"QA-NOT-EXIST"})
st2,b2=call("GET","/api/contract/analysis/QA-NOT-EXIST")
st3,b3=call("DELETE","/api/templates/QA-VERIFY-NOPE")
ok = st1==404 and st2==404 and st3==404
check("V08","B-08 三处不存在资源统一返404",
      "修复确认" if ok else ("部分修复" if 404 in (st1,st2,st3) else "未修复"),
      f"reply={st1}(原400) analysis={st2}(原200) template_del={st3}(原200)",
      {"reply":st1,"analysis":st2,"template_delete":st3})

# ── V09 ISS-B-09 P3: 未知API路由返回JSON 404 ──
r=S.get(BASE+"/api/__nope_verify__",headers={"Accept":"application/json"},timeout=30)
try:
    rb=r.json(); is_json=True
except Exception:
    rb=r.text[:100]; is_json=False
check("V09","B-09 未知API路由返回JSON格式404",
      "修复确认" if (r.status_code==404 and is_json) else "未修复",
      f"http={r.status_code} json={is_json}",
      {"status":r.status_code,"is_json":is_json})

conn.close()
n_fix=sum(1 for x in R if x["verdict"]=="修复确认")
summary={"verified_at":time.strftime("%Y-%m-%d %H:%M:%S"),"base":BASE,
         "total":len(R),"fixed":n_fix,
         "pending":sum(1 for x in R if x["verdict"]=="未修复"),
         "partial":sum(1 for x in R if x["verdict"]=="部分修复"),
         "results":R}
os.makedirs(os.path.dirname(OUT),exist_ok=True)
json.dump(summary,open(OUT,"w",encoding="utf-8"),ensure_ascii=False,indent=1)
print(f"\n==== 快速验证: {n_fix}/{len(R)} 修复确认，待修 {summary['pending']}，部分 {summary['partial']} ====")
print(f"明细: {OUT}")
for x in R:
    if x["verdict"]!="修复确认": print(f"  ❌ {x['id']} {x['title']}")
