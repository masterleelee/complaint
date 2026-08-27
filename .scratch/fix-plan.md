# 批次1修复方案（Agent-D · ISS-B-01~09）

> 原则：最小改动、每处独立可验证、未新增第三方依赖。
> 完整工作区 diff 存档：`.scratch/fix-d-batch1-full.diff`（注意：工作区含其他 Agent 未提交的 v2 改动，该 diff 为混合状态；本次修改范围以下表为准）。

## 修改总览

| 编号 | 级别 | 修改文件 | 落地思路 | 验证方式 | 结果 |
|---|---|---|---|---|---|
| ISS-B-01 | P1 | app.py `parse_deductions()`（新增，_err 之后）+ 5 处读取点 | save_analysis 整包 dict 存储保留不动（前端 GET /analysis 恢复流程依赖完整 dict：`ar.value.deductions/total_fee/...`），改为统一解析器兼容三种形态：list 直返 / str 反序列化 / dict 提取其 `deductions`（或 `deduction_detail`）数组。读取点：① register-form ② ticket/detail ③ `_official_case_payload`（回复函/归档载荷）④ 批量扣费明细路由 ⑤ Excel 导出 | 单测：save_analysis 整包 → register-form 返 200 且 detail.deductions 为数组；parse_deductions 5 组边界用例 | ✅ |
| ISS-B-02 | P1 | services/template_service.py `set_default_template` + app.py set-default 路由 | 先 SELECT 校验目标存在，不存在返 False；路由将失败语义改为 404「模板不存在」 | 单测：PUT 不存在 id → 404 且 reply_templates 无幽灵行 | ✅ |
| ISS-B-03 | P1 | app.py org-vehicle-counts PUT 校验段 | ① vehicle_count 仅接受 int 类型（bool 显式排除），浮点/字符串一律 400，杜绝静默截断；② items 为空时 400 拒绝，防止破坏性清空全表（接口保持全量替换语义不变） | 单测：1.5→400；items=[]→400；合法 int→200 | ✅ |
| ISS-B-04 | P2 | app.py POST /archive 更新语句 | 归档成功后同步写 `handle_status="已完结"` + `completed_at=now`，与 PUT 归档路径口径一致 | 代码比对 PUT 路径同款逻辑（app.py ~791）；由 T5 回归覆盖 N8 场景 | ✅ |
| ISS-B-05 | P2 | app.py 8 处 `get_json(force=True)` → `silent=True` + None 判空 400 | 覆盖 api_query / query_start / withdraw-status / auth_login / contract_download / contract_analyze / analyze_start / reply_generate。不采用全局 errorhandler(BadRequest)：路由内 try/except 会先吞掉异常导致全局 handler 失效 | 单测：三代表接口发 junk body → 均 400 且无 werkzeug 原文回显 | ✅ |
| ISS-B-06 | P2 | app.py tickets 列表 limit/offset 解析 | int() 包 try，ValueError 时静默回退默认值（10/0），与 complaints 接口口径一致 | 单测：limit=abc → 200 且响应不含 "invalid literal" | ✅ |
| ISS-B-07 | P2 | app.py `validate_id_card` 非18位分支 + 顶部 `import re` | 由"≥7位全放行"改为格式白名单：`^[A-Z]{0,3}\d{6,17}([（(]\d{1,4}[)）])?$`。依据代码内既有居留证约定（app.py 原 F1249468(8) 前缀补全逻辑）与外国人永久居留证（3字母+12数字）。乱码 ABC12345XYZ 被拦截，不再直打生产三系统 | 单测：ABC12345XYZ 拦截 / F1249468(8)、F12494680、USA123456789012 放行 / 错校验位身份证仍拦截 | ✅ |
| ISS-B-08 | P3 | app.py `_fee_plan_ticket_or_error` 改返回三元组 (ticket, err, status)；reply/generate 缺工单 → 404；GET contract/analysis 缺工单 → 404；database.py `delete_template` 用 cursor.rowcount 替代连接级累计的 total_changes + DELETE 路由不存在 → 404 | 三处资源不存在语义统一 404。前端 loadSavedAnalysis 对非 2xx 有 try/catch 兜底，行为兼容 | 单测：reply/generate、contract/analysis、templates DELETE 对不存在 id 均 404 | ✅ |
| ISS-B-09 | P3 | app.py 新增 `errorhandler(NotFound)` | `/api/*` 未知路由返回 JSON `{success:false,error:"接口不存在"}`；页面路由 return e 保持原 HTML 404 | 单测：/api/definitely-not-exist → 404 JSON | ✅ |

## 验证记录

- 定向验证脚本（9 例）：全部通过（临时脚本，运行于 /var/folders/.../opencode/test_fixd_verify.py，不入库）
- 全量回归：`pytest tests/ -q` → **39 passed**（修复前后各跑一次）
- 语法检查：py_compile app.py / database.py / template_service.py 通过

## 假设与风险说明

1. ISS-B-01 选择"读取方兼容 dict"而非"只存数组"：后者会破坏前端分析恢复流程（ar.value 期望完整对象），属主流程回归风险，故按台账备选方案落地。save_analysis 存储格式未动，历史数据天然兼容。
2. ISS-B-07 白名单为保守推导（基于代码库自身居留证处理逻辑），若存在未覆盖的真实证件格式（如护照号录入场景），需产品侧确认后扩白名单——已在正则中预留字母前缀与括号尾注两类扩展位。
3. 工作区含他人未提交改动，T5 回归验收时请以本表所列函数/路由为界核对本次修复，勿以整树 diff 直接归因。

---

# 批次2修复方案（Agent-D · ISS-B-10/11 连带回归）

> 完整工作区 diff 已更新至 `.scratch/fix-d-batch1-full.diff`（混合态，区分依据仍以本文件登记为准）。

| 编号 | 级别 | 修改文件 | 修改的函数/行区域 | 落地思路 | 验证方式 | 结果 |
|---|---|---|---|---|---|---|
| ISS-B-10 | P1 | database.py | `save_template()`：将 `UPDATE reply_templates SET is_default=0` 清零语句从 INSERT 分支提升到事务开头（`if data.get("is_default")` 守卫），UPDATE/INSERT 两分支统一覆盖；同一 `get_db()` 事务内先清零后置位，commit 原子生效 | 修复"对既有模板仅 UPDATE 置位不清其他行→双默认"。覆盖三条置位路径：set-default 切换、上传即默认、create_default_template 插入。不涉及 app.py | 定向单测：①建 A/B 双模板反复切换默认→任意时刻全表仅一行 is_default=1；②连续两次 `save_template(is_default=1)` 不同 id（模拟上传即默认）→唯一；③批次1幽灵行回归用例复跑仍通过 | ✅ |
| ISS-B-11 | P2 | services/template_service.py | `create_default_template()` 重写判重逻辑 + 抽取 `_write_default_reply_doc(filepath)` 辅助函数（原函数体 docx 构建部分原样平移，无逻辑变更） | 原 guard 要求"默认行存在**且**文件在盘"才复用，文件丢失即穿透插新行且无同名判重。现改为：存在任一默认模板 **或** 同名「默认模板」记录 → 一律复用该行不再插入；仅当其文件缺失时按该行既有 template_path 重建文件。空表时才走插入分支（行为与原版一致） | 定向单测：①连续调用两次（模拟重启）→ 行数不变、默认唯一、路径一致；②删文件后再调 → 复用原行重建文件、不新增行；③表内仅有同名非默认行 → 复用不新增；④空表 → 正常建一条（原行为保留） | ✅ |

## 批次2验证记录

- 定向验证脚本 6 例（临时脚本 /var/folders/.../opencode/test_fixd_verify2.py，不入库）：全部通过
- 交叉回归：批次1验证脚本 9 例 + 仓库 tests/ 全量 → **48 passed**
- 语法检查：py_compile app.py / database.py / services/template_service.py 通过

## 批次2假设说明

- ISS-B-11 同名判重仅针对字面量「默认模板」（与插入行的 name 一致），未做模糊匹配；如产品侧允许用户自建同名模板并期望不同处理，需再确认口径。
- 文件缺失复用场景按"该行既有路径"重建（不迁移到规范路径），保持用户数据不动。

---

# 批次3修复方案（Agent-D · UI 问题 ISS-C-A01/A02 + ISS-U-01/02/03）

> 完整工作区 diff 已更新至 `.scratch/fix-d-batch1-full.diff`（混合态，区分依据仍以各批次登记为准）。
> 注：ISS-C-A03（查无此人仍自动建案）待产品确认，本批未处理。

| 编号 | 级别 | 修改文件 | 修改位置 | 落地思路 | 验证方式 | 结果 |
|---|---|---|---|---|---|---|
| ISS-C-A01 | P1 | static/js/composables/useComplaint.js | `queryAll()` payload 构造（身份证查询分支） | `payload.id_card` 分支同时携带 `payload.phone = rawPhone`（一行），手工输入手机号不再丢失；后端 /api/query/start 已兼容双参（id_card 优先） | 代码走查：身份证分支 payload 现含 id_card+phone；后端 api_query_start 同时读取 id_card/phone；终验由 UI 实测覆盖 | ✅ |
| ISS-U-01 | P2 | static/css/style.css | `.btn-danger` / `.btn-danger:hover` 选择器 | 提升为双类名 `.btn.btn-danger`（特异性 0,2,0 > 内联 .btn 的 0,1,0），不受加载顺序影响；附一行注释说明缘由。`.btn-success` 同类隐患因模板中零使用未动（最小改动） | 特异性推演：内联 `<style>`（index.html L10-369）后于 style.css 加载，原同优先级被 `.btn{background:none}` 覆盖；双类名后必胜。UI 实测由终验覆盖 | ✅ |
| ISS-U-02 | P2 | templates/index.html + static/css/style.css | index.html L286 `.mini-fill` transition `width .4s→.3s`；style.css `.progress-bar-fill` `width .4s ease→.3s ease` | 两处超规范动效统一收敛至 300ms | 全局扫描 `transition` 中 ≥.4s 值：已无残留 | ✅ |
| ISS-C-A02 | P2 | templates/index.html | L378 侧栏导航按钮文案 | 「学员信息」→「案件工作台」（纯文案，view='workbench' 不变）；L610 注释为代码注释非用户可见，未动 | grep 确认用户可见文案唯一处已更名 | ✅ |
| ISS-U-03 | P3 | static/js/composables/useComplaint.js | `validateIdCard()` 非18位分支 + 函数 docstring | 非18位由"≥7位放行"改为与服务端一致的白名单正则 `^[A-Z]{0,3}\d{6,17}([（(]\d{1,4}[)）])?$`；乱码 ABC12345XYZ 前端即拦截，不再提交后端触发爬虫 | node 单测：ABC12345XYZ 拦截 / F1249468(8)、F12494680、USA123456789012 放行 / 错校验位与正确校验位身份证判定均正确；前后端正则逐字对齐 | ✅ |

## 批次3验证记录

- JS 语法：`node --check useComplaint.js` 通过
- 白名单口径一致性：node 脚本 7 组用例前后端行为一致
- 回归：仓库 tests/ 全量 **39 passed**；`python -c "import app"` 启动导入正常
- UI 视觉/交互项（U-01 按钮配色、U-02 动效、A02 文案）属浏览器实测范畴，留给终验确认

---

# 批次4修复方案（Agent-D · 用户视角走查 ISS-UJ-01~08）

> 完整工作区 diff 已更新至 `.scratch/fix-d-batch1-full.diff`（混合态，区分依据以各批次登记为准）。
> ISS-C-A03 仍未处理（待产品确认）。

| 编号 | 级别 | 修改文件 | 修改位置 | 落地思路 | 验证方式 | 结果 |
|---|---|---|---|---|---|---|
| ISS-UJ-03 | P1 | static/js/composables/useWorkflow.js | `handleSaveAndConfirm` fee-confirm payload | 补传 `deductions: ar.value.deductions`（recalc 后的最新编辑行）；后端本就优先取 body deductions（批次1已验证），此前前端未传致静默回退旧明细 | 代码走查 + 批次1单测已覆盖 body 优先逻辑；落库一致性由终验 uj-06 复测 | ✅ |
| ISS-UJ-02 | P2→P1 | useWorkflow.js + templates/index.html + app.js | 新增 `startManualFeeEntry()`（空表骨架 `{deductions:[],total_fee:0,actual_paid:0,source:'manual',...}`）；②扣费明细卡 !ar 分支新增「手动录入费用」按钮 + hint 引导文案；fee-total 栏 manual 模式下合同总额/实缴改为可编辑输入框（@input=recalc 实时重算） | 最小兜底方案：不重构 ar 语义，点击后初始化空骨架 → 既有表格/添加行/确认按钮因 v-if="ar" 全部自然可用；确认走既有 handleSaveAndConfirm（can_confirm_fee_plan 未定义不拦截）。归档链路死锁解除：用户可手动录入→确认→生成回复函→归档 | node --check；模板渲染探针含「手动录入费用」；流程连通性由终验复测（分析失败场景→手动录入→确认→归档） | ✅ |
| ISS-UJ-05/06 | P2 | useWorkflow.js + static/js/app.js | useWorkflow 增加第4参 `hooks={}`，确认成功末尾回调 `hooks.afterFeeConfirm(confirmed, ticketId)`；app.js 接线：更新 selectedTicket 快照（fee_plan_status/total_fee/actual_paid/deduction_fee/refund_fee 五字段）+ `await loadTickets()` 刷新列表分组与统计源 | 工作台闸门即时转绿、档案区金额同步、列表页「¥0 未核算」即时消失，无需整页刷新。回调式解耦，useWorkbench 无需感知 useWorkflow | app.js destructure/return 双处登记；node --check 通过；终验 UI 复测 | ✅ |
| ISS-UJ-07 | P2 | static/js/composables/useWorkbench.js | `doArchive` 成功 Toast 副文案 | `"已生成登记表与回复函两件套"` → 如实的 `"案件已归档"`（主文案仍显示归档目录路径）；不补齐登记表自动生成（保持最小改动，UJ-04 已提供手动入口使两件套可达成） | 模板渲染无异常；文案 grep 确认旧句已不存在 | ✅ |
| ISS-UJ-04 | P2 | templates/index.html + （复用既有函数） | 归档区右栏 path-tree 上方新增「生成登记表」按钮（绑定既有 `genRegistrationForm(selectedTicketId)`，formLoading 态防抖）；静态失实文案「✓ 登记时生成」→ 动态：`registration_form_path` 存在显「✓ 已生成」，否则显「未生成·可点上方按钮生成」 | 零新逻辑，仅绑定既有实现 + 文案如实化 | 模板渲染探针含「生成登记表」且旧文案已消失 | ✅ |
| ISS-UJ-01 | P2 | useWorkbench.js + templates/index.html + app.js | useWorkbench 新增 `studentNameEdit` ref（openTicket 时从 detail 初始化）；处理情况卡新增姓名输入框（标注"三系统未命中时可手动补录"）；`saveProgress` 在姓名有变更时附带 PUT student_name 并回写快照+刷新列表 | 复用既有 PUT /api/tickets/<id> 与 saveProgress 入口，零新接口；后端 ALLOWED_COLUMNS 本就含 student_name（定向单测验证 PUT 生效） | 定向单测：PUT student_name → 200 且落库生效 | ✅ |
| ISS-UJ-08 | P2 | useComplaint.js + app.py | queryAll payload 补传 `complaint_desc: intakeText.trim()`；后端 `_persist_query_result` ticket_data 增加 `"complaint_content": str(data.get("complaint_desc","") or "")`（/api/query 与 /api/query/start 两条建案链路共用该函数，均覆盖） | 一进一出两行修；ALLOWED_COLUMNS 已含 complaint_content | 定向单测：①带 desc 建案 → complaint_content 落库一致；②无 desc → 落空串 | ✅ |

## 批次4验证记录

- JS 语法：node --check 四个改动文件全过
- 后端定向单测 3 例（UJ-08×2 + UJ-01 前提）全过
- 全量回归：pytest tests/ **39 passed**
- Jinja 模板渲染探针：「手动录入费用」「生成登记表」「学员姓名」等新元素均在，旧失实文案「✓ 登记时生成」已消失

## 批次4假设说明

1. UJ-02 采用"空表骨架复用既有编辑链路"而非独立手动表单——ar.source='manual' 仅用于切换总额/实缴为输入框，其余逻辑与 AI 分析路径完全共享，回归面最小。
2. UJ-07 选择改文案而非强制补齐登记表：登记表生成本就是用户动作（UJ-04 已给入口），归档闸门仍由后端"无可归档文件"兜底。
3. UJ-05/06 的 afterFeeConfirm 回调在 loadCommunications 之后执行，若其失败不影响确认结果本身（toast 已先发出）。
