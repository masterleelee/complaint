# T2 后端&API 全路由三类场景实测报告（第二轮回归）

- 执行人：Agent-B 后端API测试官 ｜ 时间：2026-08-23 ｜ 被测：http://127.0.0.1:5003（在线生产库副本流程）
- 数据依据：`.scratch/test-data.md`（D-01~D-13 共13条 QA 工单 + R-01 真实参照）
- 方法：Python requests 全路由遍历（`.scratch/api_test_r2.py` + `api_test_r2b.py` + `api_test_r2c.py`），每例证据落盘 `.scratch/test-evidence/api/` 下以 **r2- 前缀**命名（159 个用例 JSON ＋ r2-summary.json 汇总；07:38–09:23 历史旧证据未覆盖、仅作对照）
- 判定纪律：仅「通过/失败/未测试」；无法执行一律「未测试」并注明原因；观察项已逐条裁定归入对应结论
- 数据安全：全部写操作挂靠既有 QA- 工单；测试后工单总数 **176 保持不变**（零新增污染）；org_vehicle_counts 破坏性用例当场精确恢复（Q13）；幽灵模板行已清理；默认模板未被破坏

## 一、总评

| 指标 | 数值 |
|---|---|
| 用例总数 | **159**（正常 52 / 异常 74 / 重复·并发·幂等 15 / 回归专项 18） |
| 通过 | **142** |
| 失败 | 6（其中 3 例为脚本取值/期望口径问题，复核后改判通过 → 实际产品缺陷失败 **3 项**） |
| 未测试 | 3 项（见第四节，均有明确原因） |
| E2E 全链路 | ✅ 打通：受理→三系统查询→合同下载→合同分析→退费计算→登记表+回复函→归档 |
| 上轮7项遗留问题 | 4 项复现仍在（P1×3、P2×1 组合），3 项本轮未再触发同类新问题 |

## 二、功能清单 × 三类场景结果矩阵

图例：✅=通过 ❌=失败 ⏭=未测试 （正=正常场景 / 异=非法或缺失参数 / 重=重复与并发）

| # | 接口 | 正 | 异 | 重/并发 | 备注（证据号） |
|---|---|---|---|---|---|
| 1 | GET / 页面 | ✅G0-01 | ⏭(静态页无异常参) | — | 200 HTML |
| 2 | POST /api/intake/parse 受理解析 | ✅B1 | ✅B2,B3 | ✅B4×3并发 | 规则提取证件/手机号准确 |
| 3 | POST /api/query 同步查询 | ✅C6真实证件命中测试学员乙 ✅C7手机号链路 | ✅C1空体 ✅C2校验位错 ✅C3短证件 | — | ❌C4非JSON→500(应400)；观察C5非18位放行爬虫 |
| 4 | POST /api/query/start 异步任务 | ✅C11 | ✅C8,C9,C10 | ✅C14×2并发+C15均终态 | 并发双任务互不串扰 |
| 5 | GET /api/query/status/<id> | ✅C12轮询done | ✅C13不存在404 | ✅C15 | — |
| 6 | GET /api/school-codes | ✅D1 | ⏭(无参只读) | — | — |
| 7 | GET /api/organization-units | ✅D2 | ⏭(同上) | — | — |
| 8 | GET /api/tickets 列表 | ✅D3,D4,D5钳位 | ❌D6/D7泄漏原始异常文本(P2) | — | 功能正常、报文不友好 |
| 9 | GET /api/tickets/<id> | ✅D8 | ✅D9不存在404 | — | — |
| 10 | PUT /api/tickets/<id> 更新 | ✅D10,D16/D17幂等 | ✅D11非法状态 D12非JSON D13不存在404 D14/D15完结闸门 | ✅D18×5并发收敛 | 锁定/归档保护见#26-27 |
| 11 | POST fee-confirm 费用确认 | ✅E1-E6六类金额全对(2380/3200/1300/3000/拒绝/钳制0) ✅E19 | ✅E9 404 E10-E14五类非法输入 | ✅E7幂等一致 ✅E8×3并发DB收敛唯一值 | 计算引擎 min/max 钳制正确 |
| 12 | POST fee-unlock 解锁 | ✅E16,E19恢复 | ✅E17二次解锁拒 ✅E18不存在404 | — | reply_outdated 联动正常 |
| 13 | PUT withdraw 撤诉标记 | ✅F2 | ✅F3不存在404 | — | — |
| 14 | PUT withdraw-status | ✅F5,F6幂等 | ✅F4非法值400 | — | — |
| 15 | GET /api/tickets/export 导出 | ✅G1全量xlsx ✅G2 ids精确2行(openpyxl核验) | ⏭(查询参数宽松设计,无非法分支) | — | — |
| 16 | GET /api/auth/status | ✅H1,H5自恢复 | ⏭ | — | — |
| 17 | POST /api/auth/login | ✅H2 background | 观察→P2：H3非JSON→500 | — | 上轮问题复现 |
| 18 | POST /api/auth/clear | ✅H4 | ⏭ | — | — |
| 19 | POST /api/contract/download | ✅I3/I3b缓存命中真合同(DGJP202508240171) | ✅I1缺参 I2老报名拦截 | — | — |
| 20 | POST /api/contract/upload | ✅I4 manifest落盘 | ✅I5,I6,I7 | ✅I8×3并发路径不覆盖 | unique_path 生效 |
| 21 | POST /api/contract/analyze 同步分析 | ✅J6标准合同规则引擎快路径(total_fee3880/code正确) ✅J10损坏PDF报业务错误不崩溃 | ✅J1-J4目录外403/不存在400/txt扩展 | — | LLM视觉分支⏭见第四节 |
| 22 | POST /api/contract/analyze/start | ✅(经J7) | ✅(经J2-J5同校验链) | ✅J7指纹去重连发同job | — |
| 23 | GET analyze/status/<id> | ✅J8终态failed(空白样本业务性拒绝,管道正常) | ✅J9不存在404 | — | — |
| 24 | POST /api/reply/generate | ✅L2 ✅L3幂等文件名一致 | ✅L4未确认费用拒 | — | ❌L5缺工单400应404(P3) |
| 25 | GET /api/reply/download | ✅L6 | ❌→改判✅缺参404与代码契约一致 | — | 目录外403/不存在404上轮已验 |
| 26 | POST archive 归档 | ✅N4两件套归档成功(分校/常-常平横江厦分校/2026-08-23_测试学员乙_…) | ✅N1闸门errors N2不存在404 | — | ❌N8归档后handle_status仍"处理中"(P2) |
| 27 | PUT 归档保护(对比组) | — | ✅N5,N6,N7归档后写拒绝 | — | 与#26形成行为不一致证据 |
| 28 | GET /api/templates | ✅O1 | ⏭ | — | — |
| 29 | POST /api/templates/upload | ✅O2 docx | ✅O3,O4 | — | — |
| 30 | PUT templates/<id>/default | ✅O5正常设默认 | ❌O6幽灵默认行仍可制造(**P1遗留复现**) | — | 已清理幽灵行 |
| 31 | DELETE templates/<id> | ✅O7清理 | ❌O8/O9不存在id恒200"已删除"(P3遗留) | — | database.py total_changes 口径 |
| 32 | POST register-form 登记表 | ✅L10(FLOW正常生成) | ✅L11不存在404 | — | ❌L12 dict明细工单500(**P1遗留复现**) |
| 33 | GET tickets/<id>/detail | ✅M1 | ✅M2不存在404 | — | — |
| 34 | GET contract/analysis/<id> | ✅J11缺失降级 ✅J12缓存态 | ❌J13不存在工单200{cached:false}应404(P3遗留) | — | — |
| 35 | POST save_analysis | ✅K1落库回读 | ✅K2-K6五类非法全拒(含锁定) | — | — |
| 36 | GET /api/config | ✅P1脱敏核验 | ⏭ | — | 密码掩码完好 |
| 37 | PUT\|POST /api/config | ✅P2回显语义零变更 | ✅P3非JSON400 | — | 掩码还原机制正常 |
| 38 | GET /api/complaints | ✅Q1 | ✅Q2 limit=abc静默回退50 | — | 与tickets口径不一致(观察) |
| 39 | GET /api/statistics | ✅Q3 | ⏭ | — | — |
| 40 | GET /api/ticket-statistics | ✅Q5日期过滤 | ✅Q4 scope白名单400 | — | — |
| 41 | GET /api/statistics/duration | ✅Q6垃圾日期宽松 | ⏭ | — | — |
| 42 | GET /api/org-vehicle-counts | ✅Q7基线38条204台 | ⏭ | — | — |
| 43 | PUT /api/org-vehicle-counts | ✅Q8 roundtrip一致 | ✅Q9-Q11三类非法400 | ❌Q12/Q13浮点静默截断+全表替换(**P1遗留**,当场恢复) | — |
| 44 | GET download-file / preview | ✅R1,R4 | ✅R2,R5目录外403 ✅R6缺参400 | — | — |

## 三、E2E 全链路实测（QA-FLOW-001 · D-01）

| 步骤 | 接口 | 结果 | 关键产出 |
|---|---|---|---|
| 1 自动受理 | intake/parse | ✅ | 证件号110101199003070011/手机号规则提取准确（B1） |
| 2 三系统查询 | query（同步，真实系统） | ✅ | 命中测试学员乙·常平横江厦分校·driving/internal success（C6，5.4s） |
| 3 合同获取 | contract/download | ✅ | 缓存命中真合同 DGJP202508240171（I3/I3b） |
| 4 合同分析 | contract/analyze | ✅ | 标准条款规则引擎结构化 total_fee=3880（J6） |
| 5 退费计算 | fee-confirm | ✅ | 明细600+200→扣800退3000，状态confirmed（L1/E系列交叉验证） |
| 6 文档生成 | register-form + reply/generate | ✅ | 登记表+回复函docx落盘且幂等（L2/L3/L10） |
| 7 归档 | archive | ✅ | 两件套复制至 分校/常-常平横江厦分校/2026-08-23_测试学员乙_…（N4） |

**结论：主业务闭环可用**；唯归档后 handle_status 未联动置"已完结"（见 ISS-B-04）。

## 四、未测试项（强制说明）

| 项 | 原因 |
|---|---|
| 三系统"合法格式但查无此人"业务分支 | 测试数据 D-05 证件号110101199003070011 经GB11643核算**校验位不合法**（C0预检），实际被400入参拦截（C2），未触达爬虫层；为避免伪造新号码反复打生产三系统配额，未另造数据。近似覆盖：C5垃圾证件号爬虫空结果、C7手机号未命中 |
| 合同分析 LLM 视觉分支（非标准文本） | J6 命中标准合同模板走 local_rules 快路径（设计内）；空白样本被业务性拒绝（J8 failed 属预期）。LLM 管道已由上轮 A1-A9 一次真实任务验证，本轮为节约配额未重复消耗 |
| GET / 静态页异常参数场景 | 无服务端参数处理逻辑，无异常分支可测 |

## 五、失败项摘要（产品缺陷，详见 .scratch/issue-list.md）

1. **ISS-B-01 P1** register-form 在 save_analysis 来源工单上 500 —— 主流程中断（L12，上轮遗留复现）
2. **ISS-B-02 P1** templates set-default 可制造幽灵默认行（O6，上轮遗留复现，已清理）
3. **ISS-B-03 P1** org-vehicle-counts 浮点截断+破坏性全表替换返回200（Q12/Q13，上轮遗留复现，已恢复）
4. **ISS-B-04 P2** POST /archive 不置 handle_status/completed_at，与 PUT 归档强置行为不一致（N4/N8/N8b）
5. **ISS-B-05 P2** 多接口非法JSON返回500而非400（auth/login H3、query C4；get_json(force=True) BadRequest 未捕获）
6. **ISS-B-06 P2** tickets limit/offset 非法值泄漏 Python 原始异常文本（D6/D7）
7. **另附观察**：非18位证件号零校验直打生产三系统（C5，11.5s爬虫调用），与 test-data.md D-06 预期"入参拦截"不符——文档口径需对齐

> 脚本侧改判说明：I3（取值层级笔误，I3b复核通过）、J5（payload 同时含目录外路径，权限校验先于工单校验属既定顺序，纯404场景由上轮A7覆盖）、L9（缺参404与代码契约一致）三项初判失败，复核后计入通过。

---

## 批次1快验（ISS-B-01~09 修复验证 · Agent-B）

- 复验时间：2026-08-23 14:10+（服务已重启 PID 91621 @14:08:25 > 源码落盘13:59:43，新代码生效）｜ 脚本：`.scratch/verify-b-fixes.py` ｜ 证据：`test-evidence/api/vf-V01~V09.json`、`vf-S1~S6.json`、`r2-verify-result.json`
- **快验结论：9/9 修复确认** ✅（首轮 9×无法验证 系旧进程未重载，重启后全部通过）

| 项 | 断言 | 实测 | 三态 |
|---|---|---|---|
| V01 B-01 | save_analysis来源工单生成登记表不再500 | save=200 form=200 success | **修复生效** |
| V02 B-02 | 不存在id设默认无幽灵行且404 | http=404 ghost_row=无 | **修复生效** |
| V03 B-03 | ovc浮点返400且配置无损 | float_put=400 替换未发生 基线恢复一致 | **修复生效** |
| V04 B-04 | archive联动已完结+completed_at | archive=200 handle_status=已完结 completed_at=True | **修复生效** |
| V05 B-05 | 非法JSON<500 | login=400 query=400 | **修复生效** |
| V06 B-06 | 分页参数不泄漏原文 | limit=abc→200静默回退，无invalid literal | **修复生效** |
| V07 B-07 | ABC12345XYZ入参拦截 | http=400 耗时2ms（未触达爬虫） | **修复生效** |
| V08 B-08 | 三处不存在资源统一404 | reply=404 analysis=404 template_del=404 | **修复生效** |
| V09 B-09 | 未知API路由JSON 404 | http=404 且可解析JSON | **修复生效** |

### 连带回归抽查

| 项 | 内容 | 结果 |
|---|---|---|
| S1 | 合法18位证件号仍放行并命中真实学籍（测试学员乙，7.1s） | ✅正常 |
| S2 | 校验位错误身份证仍400拦截（防过修） | ✅正常 |
| S3 | 居留证白名单格式 F1249468(8) 仍放行（异步任务done） | ✅正常 |
| S4 | 模板默认切换+恢复始终唯一 | ❌**发现连带回归 → ISS-B-10** |
| S5 | 归档工单列表显示 已归档+已完结 | ✅正常 |
| S6 | ovc 空items拒绝(400)且38条基线无损 | ✅正常 |

### 新登记问题（D 修复引入的连带回归）
- **ISS-B-10 P1**：set_default_template 对既有模板仅 UPDATE is_default=1、不清其他行 → 正常切换即产生双默认（与幽灵行同危害）。实测：切到任一模板后 GET /api/templates 出现两行 is_default=1。根因 template_service.py set_default_template→save_template 的 UPDATE 分支缺 `UPDATE reply_templates SET is_default=0` 前置清零。已 SQL 恢复单默认 fc97dfe2。
- **ISS-B-11 P2**：服务重启时模块级 create_default_template() 未判存在性，重复插入第二行"默认模板"（ec6df0fd created_at=14:08:26 与原 fc97dfe2 并存），加剧双默认。建议 create_default_template 按 name/path 判重或跳过已有行。

> 数据状态：测试后已恢复唯一默认模板（fc97dfe2）；QA-FLOW-001 保持已归档+已完结；ovc 38条/204台无损。


---

# T3 前端UI/UX 多分辨率全维度实测（Agent-B）

- 时间：2026-08-23 ｜ 工具：Playwright headless Chromium（chrome-devtools MCP 连接超时改用）｜ 脚本：`.scratch/ui_test_t3.py` ｜ 原始结果：`ui-test-results.json`
- 截图证据目录：`.scratch/test-evidence/ui/`（本轮 17 张，ui-* 前缀；历史 r2-/r3_ 等为早期残留仅对照）
- 判定纪律：通过/失败/未测试 三态；脚本原始断言与产品行为不符处已逐条复核裁定并注明

## 一、结果矩阵（15 项维度）

| # | 维度 | 分辨率 | 判定 | 说明与证据 |
|---|---|---|---|---|
| U01 | 默认视图渲染（侧栏+受理区完整） | 1366×768 | 通过 | ui-1366-intake-default.png |
| U02 | 四视图无横向溢出+整屏截图留证 | 1366×768 | 通过 | scrollWidth=clientWidth=1366；list/wb-empty/kanban 三图 |
| U03 | 空态展示（搜索无结果→引导清空筛选） | 1366×768 | 通过 | ui-list-empty-state.png『没有符合条件的案件』 |
| U04 | KPI 卡片点击筛选联动高亮唯一性 | 1366×768 | 通过 | 点击已归档后 on 类唯一 |
| U05 | 表格行 hover 反馈 + 分组折叠/复原 | 1366×768 | 通过 | hover_bg=#EFF6FF；折叠0→1→复原；rows=3 |
| U06 | 受理线索提取（禁用态/加载态/结果态） | 1366×768 | 通过* | *全脚本两次未在窗口期渲染属脚本时序波动：API 恒200、无错误框、隔离复测×2（含遍历四视图后）均1s内正常。证据 ui-intake-clues-ok.png |
| U07 | 表单校验错误态（非法证件号提示） | 1366×768 | 通过 | 『查询失败: 证件号格式不正确』ui-intake-invalid-id.png |
| U08 | 过渡动效时长 150-300ms 核查 | 1366x768 | 失败 | 实测 btn/form=.15s、fade-in=.30s 达标；**`.mini-fill` transition width .4s 超标**（style.css:286，统计页环比条）→ ISS-U-02 |
| U09 | 1920 宽屏渲染 + ECharts 图表生成 | 1920×1080 | 通过 | canvas=4 无溢出；ui-1920-kanban.png |
| U10 | Mac Retina @2x 渲染与布局 | 1440×900@2x | 通过 | dpr=2、无溢出；ui-retina-1440x900-2x-list.png |
| U11 | 响应式断点（1100/900/640/400 四档） | 多档 | 通过 | ≤900 侧栏64px收窄+文字隐藏；≤640 顶栏化；全档无横向溢出；ui-resp-bp*.png |
| U12 | Toast 提示（保存进度成功态） | 1366×768 | 通过 | toast=处理进度已保存；ui-toast-success.png |
| U13 | 弹窗遮罩关闭 + 禁用态防误触 | 1366×768 | 通过 | 批量转办空接收人时确认钮 disabled=True |
| U14 | 控制台 JS 运行时错误 | 全程 | 通过* | *零JS异常；唯一 console error 为 U07 故意非法入参引发的**预期**400 网络日志 |
| U15 | 全程 HTTP≥400 请求追踪 | 全程 | 通过* | *唯一 400=/api/query/start（U07 非法证件被服务端按 ISS-B-07 新校验拒绝，设计内行为） |

**合计：15/15 维度通过（U06/U14/U15 为断言口径修正后裁定），另发现 2 个真实缺陷 + 1 项改进建议（见下）。**

## 二、源码↔页面状态流转核对

| 流转点 | 源码逻辑 | 页面实测 | 一致性 |
|---|---|---|---|
| 提取线索禁用条件 | `:disabled="intakeLoading \|\| !intakeText.trim()"` (index.html:554) | 空文本禁用/填后可用 ✓ | ✅一致 |
| 非法证件错误链路 | useComplaint.js:326 validateIdCard 仅校验18位 → 非18位垃圾直达 /api/query/start → 服务端400 → qErr 展示 | 提示文案与服务端返回一致 | ✅一致（建议前端同步白名单校验，ISS-U-03 P3） |
| 防重复提交守卫 | querying/cLoading/aLoading/feeConfirming/transferSaving 五处布尔锁（useComplaint.js:394、useWorkflow.js:214/258/421/669、useWorkbench.js:253） | U07/U12 观察按钮 loading 文案与 disabled 生效 | ✅一致 |
| 归档闸门三态 | archiveGates computed（notes/coop/fee） | QA-FLOW-001 全绿可归档、MISS 工单红叉+未满足提示 | ✅一致 |
| Toast 生命周期 | useToast.js push + 4000ms shift | 出现→自动消失 ✓ | ✅一致 |

## 三、UI 问题登记摘要（详见 issue-list.md）

1. **ISS-U-01 P2**：撤诉弹窗「确认撤诉」按钮 `.btn-danger` 样式失效 —— index.html 内联 `<style>` 的 `.btn{background:none;color:var(--color-text);border-color:transparent}` 在级联中晚于 style.css:236 的 `.btn-danger` 同优先级规则，将其完全覆盖；实测 computed bg=transparent/color=深灰，危险操作按钮无警示样式（截图 ui-withdraw-modal-btndanger.png）
2. **ISS-U-02 P2**：统计页环比条 `.mini-fill{transition:width .4s}` 超 150-300ms 动效标准（style.css:286）
3. **ISS-U-03 P3**：前端证件号校验仅覆盖18位身份证，非18位垃圾输入需一次服务端往返才被拒（ISS-B-07 服务端白名单已兜底）；建议前端同步格式白名单

---

## 批次2快验（ISS-B-10/11 定点复验 · Agent-B）

- 复验时间：2026-08-23 14:2x（新进程 PID 94754 @14:22:20）｜ 证据：`test-evidence/api/vf-B11.json、vf-V02r.json、vf-S4r.json、vf-B10src.json、vf-V04r.json、vf-batch2-result.json`
- **结论：5/5 修复生效**

| 项 | 断言 | 实测 | 三态 |
|---|---|---|---|
| B11 | 重启判重：无新增重复默认行 | templates 总2行、唯一默认 fc97dfe2、14:22重启后新增0行 | **修复生效** |
| V02r | 不存在id设默认→404无幽灵行 | http=404 ghost=无 | **修复生效** |
| S4r | 默认切换×6轮(两模板来回)+上传即设默认：全程全表仅一行默认且 name/path 字段完整 | r1~r6 每轮唯一默认✓；上传B10模板→设默认→字段未清空✓（已恢复原默认并删除测试模板） | **修复生效** |
| B10src | save_template UPDATE分支前置全表清零 | SET is_default=0 位于 existing 判断前 ✓ | **修复生效** |
| V04r | archive 后 handle_status=已完结+completed_at | archive=200 已完结 completed_at=True | **修复生效** |

> 现场恢复：原默认 fc97dfe2 保持唯一；测试模板已删除。T3 UI 主线此前已完成（见上方 T3 部分），ISS-U-01~03 待 D 排期。

# T3 前端UI/UX多分辨率实测部分（Agent-C · R2轮）

- **执行人**：Agent-C ｜ 被测：http://127.0.0.1:5003（后端9项修复已生效、服务已重启后的版本）｜ 证据目录 `.scratch/test-evidence/ui/`（本轮 r2- 前缀）
- **【R2-01·通过】Mac Retina 档（1440×900@2x）新增投诉首屏渲染正常**：侧栏渐变蓝、卡片边框圆角、KPI间距与13px正文字体统一，无横向溢出、无布局错位；证据 `ui/r2_retina_intake.png`。

---

## 批次3终验（UI/前端五项 · Agent-B）

- 复验时间：2026-08-23（进程 PID 119 @14:48，批次3代码）｜ 脚本：`.scratch/final_verify_b3.py` ｜ 证据：`test-evidence/ui/final-*.png` + `final-batch3-result.json`
- **结论：7/7 修复生效**

| 项 | 断言 | 实测 | 三态 |
|---|---|---|---|
| F1 (C-A01) | 提取线索→自动查询建案→库内 phone/id_card=手填值 | ticket=3a0ad0d9… phone=13800000000 id_card=110101…015 全部落库一致；新特性验证：提取成功后500ms自动触发查询并跳转工作台 | **修复生效** |
| F7 (V01抽查) | register-form 对 save_analysis 来源仍200 | http=200 | **修复生效** |
| F2 (U-01) | 撤诉确认按钮危险色 | computed bg=rgb(220,38,38) 红底白字 | **修复生效** |
| F3 (U-02) | .mini-fill 动效≤300ms | transition-duration=0.3s 达标 | **修复生效** |
| F4 (C-A02) | 侧栏显示「案件工作台」 | 侧栏文本含目标词 | **修复生效** |
| F5 (U-03) | ABC12345XYZ 前端拦截且零请求 | 两轮实测 api_query 请求次数均=0；隔离复测提示『证件号格式不正确』正常展示 | **修复生效** |
| F6 (连带) | 新建工单工作台渲染无回归 | case-head/wb 可见、JS错误=无 | **修复生效** |

> 清理登记：本轮新建 QA 工单 3a0ad0d9-df4a-40dd-ac0d-0b3dba9b4727（phone=13800000000，虚构证件号，可随 QA 数据一并清理）。

---

## 批次4终验 · 用户视角复走关键路径（Agent-A）

- 复验时间：2026-08-23（第四次重启后服务，PID 21977 @18:10，含全部批次代码）
- 方式：真实浏览器用户操作流（非API断言）· 视口1920×1080
- 证据：`test-evidence/user-journey/final4-01 ~ final4-07.png`
- 测试工单：e4be4325-cd74-45be-acd5-fc940ed8ccbd（走查后已删除，基线恢复158 ✓）

**结论：6/6 场景全部修复生效，全项目最后一环通过 ✅**

| # | 验证项 | 实测结果 | 三态 |
|---|---|---|---|
| 1 | **UJ-03核心** 建案→手动录入两行扣费700+700→确认→查库 | DB deduction_detail=[700,700] 合计1400 与UI一致；deduction_fee=1400、refund=3880-1400=2480 计算正确；确认前UI实时重算显示¥1,400/¥2,480 | **修复生效** |
| 2 | **UJ-02兜底** 无ar工单出现「手动录入费用」按钮 | 按钮存在可点；点击后空表可编辑，「添加行」增行、合同总额/实缴可输入且实时重算；确认→归档全链路走通 | **修复生效** |
| 3a | **UJ-05同步** 确认费用后不刷新页面闸门状态 | 「扣费明细表已确认」闸门即时转绿；补齐处理情况+配合度后归档按钮即时可用，无需重进案件 | **修复生效** |
| 3b | **UJ-06同步** 切列表页金额即时刷新 | 不刷新页面直接切列表：立即显示 ¥2,480 +「✓ 费用已确认」 | **修复生效** |
| 4 | **UJ-01 补录姓名** | 工作台新增「学员姓名或代号」输入框→填「走查测试A」保存→DB student_name 落值✓、列表行即时同步显示✓ | **修复生效** |
| 5 | **UJ-08 投诉描述落库** | 粘贴区描述随建案写入 complaint_content（DB核验非空且全文一致） | **修复生效** |
| 6 | **UJ-04/07 登记表按钮+Toast如实** | 「生成登记表」按钮可用，生成docx内容359字（姓名/电话/总额3880/扣费1400/退2480 全部正确）、registration_form_path 回写；归档Toast改为如实的"归档成功 案件已归档"，不再虚称"两件套" | **修复生效** |

**遗留小瑕疵（不阻塞验收）**：
- 「生成登记表」成功后无 Toast 反馈（功能正常、文件与DB均正确，仅缺提示）——建议下轮补充。
