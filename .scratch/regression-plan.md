# T5 全量回归验收预案（Agent-E 回归验收官R）

> 编制时间：2026-08-23 · 阶段：T4 修复进行中，本阶段仅只读+规划
> 纪律：修复完成前不执行任何写操作测试；本预案所有步骤待 lead 正式派发 T5 后执行
> 判定纪律沿用：仅「通过/失败/未测试」，证据落盘 `.scratch/test-evidence/api-t5/`（t5- 前缀）

## 一、逐项回归验证步骤（ISS-B-01 ~ ISS-B-09）

> 每项含：复现路径 → 期望结果 → 验证命令/操作。
> 最终判定以 Agent-D `.scratch/fix-plan.md` 宣明的落地语义为准；下述期望为问题闭环的最低标准。

### ISS-B-01 ｜ P1 ｜ save_analysis 来源工单生成登记表必 500
- **复现路径**：
  1. `POST /api/contract/save_analysis` body：`{"ticket_id":"QA-API-003","analysis_data":{"total_fee":3800,"actual_paid":3800,"refund":3000,"deduction_detail":[{"item":"科目一培训费","amount":600}]}}`
  2. `POST /api/tickets/QA-API-003/register-form`
- **期望结果**：步骤2 返回 200 且 `success=true`，登记表文件生成（filepath 有值）；
- **连带校验**：正常链路工单（deduction_detail 为 list 的 QA-FLOW 类工单）register-form 仍 200 —— 确认修复未破坏原路径。
- **验证命令**：脚本用例 t5-RG01a/b（见 §三骨架）；证据 t5-L12*.json 对照上轮 r2-L12.json(http=500)。

### ISS-B-02 ｜ P1 ｜ 模板 set-default 幽灵默认行
- **复现路径**：`PUT /api/templates/QA-GHOST-T5/default`
- **期望结果**：返回 **404**（或按 fix-plan 的其他拒绝语义），且 `reply_templates` 表不产生新行。
  - ⚠️ 基线事实（2026-08-23 smoke 核实）：当前库内仅 1 条模板且 `is_default=0`（T2 的 O5→O7 序列遗留）。
  - ✅ 恢复已获 lead 授权（T5 开工窗口内执行，三约束：先备份 .scratch/backup/ → 优先走 API set-default 顺带验证 B-02 修复、否则直改 DB → 动作与核验留痕 regression-report.md「环境准备」节）。
- **DB 核验**：`SELECT COUNT(*) FROM reply_templates WHERE id='QA-GHOST-T5' AND is_default=1` 应为 0；测试后如产生残留立即清理并记录。
- **连带校验**：对真实存在的模板 id 设默认仍返回 200（O5 场景重跑），设完恢复原默认。

### ISS-B-03 ｜ P1 ｜ org-vehicle-counts 浮点截断 + 破坏性全表替换
- **前置**：GET `/api/org-vehicle-counts` 存基线快照（应为 38 条 / 204 台，与 r2-Q7.json 一致）。
- **复现路径**：PUT `/api/org-vehicle-counts` body：`{"items":[{"unit_code":"QA-FLOAT","vehicle_count":1.5}]}`
- **期望结果**：非整数 vehicle_count 被 **400 拒绝**（若 fix-plan 采用增量模式等替代语义，则以该语义为准，但必须满足两条硬断言）：① 1.5 不再被静默截断入库；② 基线 38 条网点配置不被无告警清空。
- **恢复核验**：测试后 GET 与前置快照逐条一致（total_vehicles 相同）。
- **连带校验**：合法 roundtrip（Q8 场景：原 items 原样 PUT 回读一致）仍通过。

### ISS-B-04 ｜ P2 ｜ POST /archive 归档后 handle_status 不联动
- **前置**：需要一条可归档的完整链路工单。注意 QA-FLOW-001 已在 T2 的 N4 归档为「已归档」终态，不可复用 → 由 §三 E2E 链路新生成一条 QA 工单（记入脏数据清单）走完全链后归档。
- **复现路径**：E2E 六步完成后补齐 handling_notes+branch_cooperation → `POST /api/tickets/<id>/archive`
- **期望结果**：归档 200 后 GET 工单满足：`archive_status=已归档` **且** `handle_status=已完结` **且** `completed_at` 非空 —— 与 PUT 归档强置行为（app.py:791-793 语义）一致。
- **对照校验**：PUT 归档路径行为不变（N5/N6/N7 归档保护三连仍拒写）。

### ISS-B-05 ｜ P2 ｜ 多接口非法 JSON 返回 500 应 400
- **复现路径**：
  1. `curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:5003/api/auth/login -H 'Content-Type: application/json' -d 'junk'`
  2. 同法打 `POST /api/query`（非法体在解析层即拒，不会触达爬虫）
- **期望结果**：两处均返回 **400**，响应体不含 werkzeug/BadRequest 原文堆栈字样。
- **连带校验**：合法 JSON 登录（background=true）仍 200；config PUT 非法 JSON（P3 场景）保持 400。

### ISS-B-06 ｜ P2 ｜ tickets 分页参数泄漏异常原文
- **复现路径**：
  1. `curl -s "http://127.0.0.1:5003/api/tickets?limit=abc"`
  2. `curl -s "http://127.0.0.1:5003/api/tickets?offset=abc"`
- **期望结果**：状态码 400（或按 fix-plan 静默回退默认值为 200），响应体**不含** `invalid literal for int()` 等 Python 内部文本；若回退方案则与 complaints 接口口径一致。
- **连带校验**：`limit=10&offset=0`、`limit=99999&offset=-5` 钳位行为不变（D3/D5 重跑）。

### ISS-B-07 ｜ P2 ｜ 非18位证件号零校验直打生产三系统（口径问题）
- **双分支判定**（取决于 D 修复方向）：
  - 若代码修复（格式白名单正则）：`POST /api/query {"id_card":"ABC12345XYZ"}` 应 **400** 且响应耗时 <100ms（不再调用爬虫）；对照证据 t5-C5*.json vs 上轮 r2-C5.json（11.5s）。
  - 若文档对齐（确认现状为预期）：核验 test-data.md / 相关文档已更新口径说明，标记该项「以文档方式关闭」，报告遗留风险区注明现状。
- **数据纪律**：无论哪支，本轮不再向生产三系统发送垃圾证件号查询（配额保护），仅在代码修复分支做一次拦截性验证。

### ISS-B-08 ｜ P3 ｜ 不存在资源返回码语义统一
- **复现与期望（三点合一）**：
  1. `POST /api/reply/generate {"ticket_id":"QA-NOT-EXIST-T5"}` → **404**（上轮 L5 为 400）
  2. `GET /api/contract/analysis/QA-NOT-EXIST-T5` → **404**（上轮 J13 为 200{cached:false}）
  3. `DELETE /api/templates/QA-NOPE-T5` → **404**（上轮 O8/O9 恒 200）
- **注意**：若 fix-plan 仅修其中部分子项，逐点标注「通过/未修复」，不允许合并模糊判定。

### ISS-B-09 ｜ P3 ｜ 未知 API 路由返 HTML 应 JSON
- **复现路径**：`curl -s -i http://127.0.0.1:5003/api/__nope_t5__`
- **期望结果**：HTTP 404 且 `Content-Type` 含 `application/json`，body 可 `json.loads`（结构含 error/success 字段即可）。

## 二、ISS-C 批次预留位（UI 问题，T3 定稿后由 lead 下发追加）

| 编号 | 级别 | 复现路径 | 期望结果 | 验证操作 | （待填） |
|---|---|---|---|---|---|

## 三、自动化回归脚本方案

- **骨架**：`.scratch/api_regression_t5.py`（已建好，风格对齐 api_test_r2.py 的 case/rec/call 三件套）
- **安全开关**：全局 `EXECUTE_WRITE=False`——所有含写操作的用例在该开关关闭时自动标「未测试（写操作锁）」跳过；lead 派发 T5 后置 True 执行。
- **当前可用模式**：`python3 .scratch/api_regression_t5.py --smoke` 仅跑只读预检（服务存活、关键 GET 接口、基线快照采集），修复完成前即可运行，用于验证脚本本身可执行 + 采集修复前基线。
- **覆盖范围**（对应核心业务链路六步）：
  1. 自动受理 intake/parse → 2. 三系统查询 query → 3. 合同下载/分析 contract/download+analyze → 4. 退费计算 fee-confirm（六类金额矩阵复跑）→ 5. 文档生成 register-form+reply/generate → 6. 归档 archive（含 B-04 联动断言）
- **执行顺序设计**：R0 只读预检 → RG 各问题专项回归 → E2E 全链路（一条新 QA 工单贯穿）→ 连带回归抽样（费用矩阵/模板/导出/配置脱敏）→ 汇总 JSON。

## 四、最终交付报告五要素框架（regression-report.md 骨架已建）

| # | 要素 | 内容来源 |
|---|---|---|
| ① | 整体测试覆盖率 | 已测项/总项数：专项回归 9 项（B-01~09）+ 连带回归抽样 + E2E 7 步，对照 T2 159 用例矩阵 |
| ② | 问题分级分布统计 | P0/P1/P2/P3 数量（台账 issue-list.md 全量，含 ISS-C 追加批次）|
| ③ | 修复前后对比证据位 | 每问题一对证据：上轮 r2-*.json（修复前）vs 本轮 t5-*.json（修复后），表格逐行给出双路径 |
| ④ | 遗留风险说明 | 未修复项/未测试项及原因、ISS-B-07 口径类处理方式、脏数据清单 |
| ⑤ | 验收结论 | 通过/有条件通过/不通过 三选一硬结论 + 循环迭代建议 |

## 五、执行纪律备忘

1. 修复前只跑 `--smoke` 只读模式；EXECUTE_WRITE 解锁需 lead 明示；
2. 所有写操作挂靠 QA- 工单，新增脏数据实时登记到报告④；
3. 幽灵模板行、ovc 全表替换两类破坏性用例必须带「当场恢复」步骤；
4. 每个用例证据独立落盘，命名 `t5-<case_id>.json`，与上轮 r2- 证据一一对应便于前后对比；
5. smoke 预检已于预案阶段执行通过（3/3：服务存活、ovc 基线 38条/204台、模板基线核实），脚本可执行性已验证。
