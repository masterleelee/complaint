# 问题清单（T2 后端API回归轮 · Agent-B 登记）

> 级别口径：P0=主流程阻断/数据破坏且无规避 ｜ P1=重要功能错误或数据风险 ｜ P2=行为不一致/健壮性缺陷 ｜ P3=低危体验问题
> 证据根目录：`.scratch/test-evidence/api/r2-`（每例一个 JSON：请求/响应/耗时）

## 本轮新登记

### ISS-B-01 ｜ P1 ｜ save_analysis 来源工单生成登记表必 500
- **现象**：`POST /api/tickets/<id>/register-form` 对 deduction_detail 为「整包 dict JSON」的工单抛 TypeError→500，登记表无法生成，主流程中断。
- **根因**：app.py:2181 `save_analysis` 把整个 analysis_data dict 存入 deduction_detail；app.py:2023-2024 register-form 用 `json.loads()` 还原后得到 dict（非 list）传入 visit_service。
- **复现**：① `POST /api/contract/save_analysis {"ticket_id":"QA-API-003","analysis_data":{...}}` → ② `POST /api/tickets/QA-API-003/register-form` → 500。
- **证据**：api/r2-L12.json（http=500）；上轮同问题 b-api-results.md#81。
- **修复建议**：save_analysis 只存 deductions 数组（与 analyze 链路对齐），或 register-form 兼容 dict（取 deduction_detail 字段）。

### ISS-B-02 ｜ P1 ｜ 模板 set-default 可制造幽灵默认模板行
- **现象**：`PUT /api/templates/QA-GHOST-R2/default`（任意不存在 id）返回 200，并在 reply_templates 插入 name/path 全空、is_default=1 的幽灵行，可与真实默认并存导致回复函选板错乱。
- **根因**：template_service.set_default_template 以 upsert 方式先置位再写 id，未校验目标存在性。
- **复现**：`PUT /api/templates/ANY-ID/default` → 查 reply_templates 出现幽灵行。测试已当场清理。
- **证据**：api/r2-O6.json（ghost_row_exists=True, name 为空）；上轮 H6/H10 复现一致。
- **修复建议**：UPDATE 前 SELECT 校验存在性，不存在返回 404。

### ISS-B-03 ｜ P1 ｜ org-vehicle-counts 浮点静默截断 + 破坏性全表替换
- **现象**：`PUT /api/org-vehicle-counts {"items":[{"unit_code":"QA-FLOAT","vehicle_count":1.5}]}` 返回 200；1.5 被 int() 截断为 1，且**全表替换**致原 38 条网点车辆数配置瞬间只剩 1 条（投诉率分母被清空），无任何告警。
- **根因**：app.py:2320 `int(item.get("vehicle_count"))` 接受浮点静默截断；save_org_vehicle_counts 为全量替换语义。
- **复现**：见上；测试已当场恢复 38 条/204 台并与基线逐条比对一致。
- **证据**：api/r2-Q12.json、Q13.json（截断成立=True 恢复一致=True）、Q7.json 基线快照。
- **修复建议**：拒绝非整数（400）；或提供增量模式；替换前校验 items 非空。

### ISS-B-04 ｜ P2 ｜ POST /archive 归档后 handle_status 不联动
- **现象**：archive 成功后 archive_status=已归档，但 handle_status 仍"处理中"、completed_at 为空；而 PUT 路径归档会强置"已完结"+completed_at（app.py:791-793）。两条归档路径状态不一致，影响完结统计与时长统计口径。
- **复现**：补齐三闸门后 `POST /api/tickets/QA-FLOW-001/archive`（200）→ GET 工单：handle_status=处理中。
- **证据**：api/r2-N4.json、N8.json、N8b.json（archive_status=已归档 handle_status=处理中 completed_at=False）。
- **修复建议**：archive 路由内同步写入 handle_status=已完结 + completed_at。

### ISS-B-05 ｜ P2 ｜ 多接口非法 JSON 返回 500 而非 400
- **现象**：`POST /api/auth/login`、`POST /api/query` 等以 get_json(force=True) 解析体的接口，收到非法 JSON 时 BadRequest 未捕获，统一兜底 except 转 500 并回显 werkzeug 原文。
- **复现**：`curl -X POST :5003/api/auth/login -H 'Content-Type: application/json' -d 'junk'` → 500 "400 Bad Request: The browser…"。
- **证据**：api/r2-H3.json、C4.json；上轮 F3 同问题。
- **修复建议**：force=True 改 silent=True+判空返 400，或全局 errorhandler(BadRequest)。

### ISS-B-06 ｜ P2 ｜ tickets 分页参数非法值泄漏 Python 异常原文
- **现象**：`GET /api/tickets?limit=abc` → 400，body 直接回显 `invalid literal for int() with base 10: 'abc'`（内部实现细节外泄）。offset 同理。
- **根因**：app.py:721-722 int() 无 try 包裹，except str(e) 直出。
- **证据**：api/r2-D6.json、D7.json。
- **修复建议**：参照 complaints 接口静默回退默认值，或返回友好文案"分页参数必须为整数"。

### ISS-B-07 ｜ P2 ｜ 非18位证件号零格式校验直打生产三系统（文档口径不一致）
- **现象**：`POST /api/query {"id_card":"ABC12345XYZ"}` 通过校验（validate_id_card 对非18位证件仅要求≥7位），实际调用三系统爬虫 11.5 秒后才返回空结果并落库工单。test-data.md D-06 预期"入参校验拦截"，实现与文档不符。
- **复现**：见上；结果挂靠 QA-API-004。
- **证据**：api/r2-C5.json（query_durations_ms 有爬虫耗时）、C0.json（D-05 校验位预检亦不合法）。
- **修复建议**：明确居留证等格式白名单正则；或在文档确认现状为预期行为。

### ISS-B-08 ｜ P3 ｜ 不存在资源的返回码语义不统一
- reply/generate 缺工单 → 400 应 404（L5）；contract/analysis/<不存在id> → 200 {cached:false} 应 404（J13）；templates DELETE 不存在 id → 200 "已删除"应 404（O8/O9，database.py total_changes 连接级累计恒真）。
- **证据**：api/r2-L5.json、J13.json、O8.json、O9.json。

### ISS-B-09 ｜ P3 ｜ 未知路由返回 HTML 404 而非 JSON
- API 客户端无法程序化解析错误。证据：api/r2-G0-02.json。建议补 `/api/<path>` 404 JSON handler。

---

## 上轮遗留对照（本轮回归结论）

| 上轮编号 | 问题 | 本轮状态 |
|---|---|---|
| 上轮#88 | 幽灵默认模板 | ❌仍复现 → ISS-B-02 |
| 上轮#89 | register-form 500 | ❌仍复现 → ISS-B-01 |
| 上轮#90 | ovc 浮点+全表替换 | ❌仍复现 → ISS-B-03 |
| 上轮#91 | DELETE 恒200 | ❌仍复现 → ISS-B-08 |
| 上轮#92 | 400/404 语义 | ❌仍复现 → ISS-B-08 |
| 上轮#93 | analysis 缺工单200 | ❌仍复现 → ISS-B-08 |
| 上轮#94 | login 500 + limit 泄漏 | ❌仍复现 → ISS-B-05 / ISS-B-06 |

---

## 批次1快验补充登记（2026-08-23 14:15 · Agent-B）

### ISS-B-10 ｜ P1 ｜ set-default 正常路径产生双默认（D修复连带回归）
- **现象**：对「已存在」的模板调用 PUT /api/templates/<id>/default 返回200，但库内出现两行 is_default=1；回复函生成选板将出现歧义。
- **根因**：template_service.set_default_template 改为「先查存在→save_template({id,is_default:1})」后，save_template 对已存在行走 UPDATE 分支只置目标行，丢失了原实现中先清零全表 is_default 的步骤。
- **复现**：GET templates 取任一非默认id → PUT 设默认 → GET 可见两行 is_default=1。
- **证据**：test-evidence/api/vf-S4.json（DB 快照含双默认行，updated_at=14:10:58）。
- **状态**：测试已 SQL 恢复单默认（fc97dfe2）。修复建议：set_default_template 内先 `UPDATE reply_templates SET is_default=0 WHERE id!=?` 再置1。

### ISS-B-11 ｜ P2 ｜ 服务重启重复插入默认模板行
- **现象**：app.py 模块级 create_default_template() 在 14:08:26 重启时再次执行，插入第二行同名同路径"默认模板"（ec6df0fd），与历史默认行并存。
- **复现**：再次重启服务后 GET /api/templates 行数+1。
- **证据**：vf-S4.json（created_at=14:08:26 行）；T2 基线中该行不存在。
- **修复建议**：create_default_template 按 name 或 template_path 判重跳过。

---

## T3 前端UI问题登记（2026-08-23 · Agent-B）

### ISS-U-01 ｜ P2 ｜ 撤诉确认按钮危险色样式失效（CSS级联覆盖）【已修复验证 2026-08-23 批次3】
- **现象**：撤诉弹窗「确认撤诉」渲染为普通白底黑字按钮，无红色警示样式，危险操作辨识度缺失。
- **根因**：templates/index.html 内联 `<style>` 中 `.btn{background:none;color:var(--color-text);border-color:transparent}` 与 static/css/style.css:236 `.btn-danger` 同特异性(0,1,0)，但内联块在文档序更靠后被应用，逐属性覆盖之；内联块只补定义了 primary/secondary/ghost，漏掉 danger。
- **复现**：投诉列表→进入任一工单→「标记撤诉」→查看弹窗底部按钮计算样式（bg=rgba(0,0,0,0)）。
- **证据**：test-evidence/ui/ui-withdraw-modal-btndanger.png。
- **修复建议**：内联块补 `.btn-danger{...}`，或将撤诉确认改用内联已定义的 btn-ghost/danger 变体。

### ISS-U-02 ｜ P2 ｜ 统计页环比条动效 400ms 超出 150-300ms 标准【已修复验证 2026-08-23 批次3】
- **现象/根因**：style.css:286 `.mini-fill{transition:width .4s}`；DESIGN 规范要求标准过渡 150-300ms。同页其余交互（btn/form .15s、fade-in .3s）达标。
- **复现**：投诉统计→环比对比卡观察条形动画时长；或直接读计算样式。
- **修复建议**：改 `.3s` 以内。

### ISS-U-03 ｜ P3 ｜ 前端证件号校验未同步非18位白名单【已修复验证 2026-08-23 批次3：前端拦截+零请求】
- **现象**：输入 ABC12345XYZ 点「查询三系统并建案」，前端放行→服务端 400（ISS-B-07 新校验兜底生效）→前端展示错误。多一次无效往返。
- **建议**：useComplaint.validateIdCard 增加与服务端一致的居留证等格式白名单，前置拦截。

## 批次4终验标注（2026-08-23 18:30 · Agent-A · 用户视角复走）

> 来源：T8 用户走查登记的 ISS-UJ 系列（详见 .scratch/test-report-user-journey.md），本轮全部复验：

| 编号 | 问题 | 级别 | 终验结论 |
|---|---|---|---|
| ISS-UJ-03 | 确认明细时用户编辑的扣费行被静默丢弃 | P1 | ✅ 已修复（DB=[700,700]=1400与UI一致，退款2480正确） |
| ISS-UJ-02 | AI分析失败无手动兜底录入路径 | P2 | ✅ 已修复（「手动录入费用」空表可编辑+实时重算+确认归档全通） |
| ISS-UJ-05 | 费用确认后工作台闸门不同步 | P2 | ✅ 已修复（确认后闸门即时转绿） |
| ISS-UJ-06 | SPA列表数据陈旧 | P2 | ✅ 已修复（切列表即时显示¥2,480已确认） |
| ISS-UJ-01 | 三系统未命中无法补录学员姓名 | P2 | ✅ 已修复（姓名输入框→DB+列表同步） |
| ISS-UJ-08 | 粘贴区投诉描述不落库 | P2 | ✅ 已修复（complaint_content 随建案写入） |
| ISS-UJ-04 | 登记表功能无入口+文案失实 | P2 | ✅ 已修复（「生成登记表」按钮可用，内容/回写正确；遗留：成功后无Toast提示） |
| ISS-UJ-07 | 归档Toast虚称"两件套" | P2 | ✅ 已修复（改为如实的"案件已归档"） |

证据：test-evidence/user-journey/final4-01~07.png · 测试工单 e4be4325 已清场，基线158 ✓

## 登记表单页自适应（2026-09-01 · Agent-B · diagnosing-bugs 收尾）

### ISS-C-01 ｜ P1 ｜ 投诉登记表内容超长会生成 2 页 A4【已修复 2026-09-01】
- **现象**：投诉内容/处理经过超长的工单（如梁思念 498 字投诉内容）生成的 docx 登记表撑破固定定高、掉到第 2 页，不符合「单页 A4 登记表」交付要求；用户现场反馈梁思念旧文件为 2 页。
- **根因**：分区行高采用固定定高（SECTION_HEIGHTS_CM），生成前未做页面垂直预算；内容超过定高时 AT_LEAST 行高规则把单元格撑高 → 整表超 27.5cm → 翻页。早期版本还叠加 `_spacer_count` 往已撑破的行里塞空段（张玉富 363 字→9 空段→行高 9.25cm/定高 7.0cm）。
- **修复**：`services/visit_service.py` 新增 `_fit_section_heights` 压缩链（档位 L0→L4：页边距→行距→字号→信息行高→标题字号），生成前按与 `_section_cell` 完全一致的渲染口径预算 Σ高度，超可用预算逐级收紧直到装下；返回 `page_fit:{level, used_cm, budget_cm}`。固定定高改为「理想上限」——内容不超时用理想定高，超则降到 need 下限；真实工单全 L0 单页，超长触发压缩兜底。
- **证据**：`tests/test_registration_form_onepage.py`（4 passed）；全量 86 工单生成 level 0、0 溢出、max_used 24.70/27.10cm；超长/极端模拟触发 L1~L4 且不抛异常。`tmp/diag_reg_pagefit.py` 单一真源校验。
- **遗留**：极端超长（>约 1200 字组合）物理上 1 页塞不下，降到 L4 仍可能 2 页——属内容超限非 bug，page_fit 已暴露 used>budget 供上层告警；app.js 预览在 L0 与 docx 一致，超长档为 L0 近似（预览为 HTML 浏览器分页，不影响生成的 docx 单页性）。

## 关联清单

- **重构审计轮（2026-09-10）**：`ISS-R-01` ~ `ISS-R-10`，见 `.scratch/refactor-audit-20260910.md`。
  级别口径不同轴：P0=正确性隐患 / P1=结构性阻塞 / P2=可维护性债务 / P3=低收益。当前状态：**清单已出，待用户圈定范围后进入设计批次**。
- **「打开归档」方案 A 轮（2026-09-10）**：`ISS-AP-00` / `01` / `03` / `04` / `05` / `06` / `07` / `08` / `09`（`ISS-AP-02` ZIP 打包**已取消**），见 `.scratch/archive-panel/`（`audit.md` / `spec.md` / `issues/` / `evidence/`）。
  主线：把「打开归档」从「尝试弹出资源管理器」改为「网页内置归档文件面板」（零安装、必有反应）。
  **范围决定（用户 2026-09-10）**：不做压缩打包，只做「需要哪个文件自己下载」+ 单文件预览。
  `ISS-AP-08`（2026-09-11 用户上报「做得太丑」）：把面板样式按 `demo/archive-panel-redesign-demo.html`
  **1:1 移植**（该 demo 的调色板变量与项目 `:root` 同名同值，故浅色逐像素可复现、深色自动跟随）；
  顺带修掉 demo 自身也有的「视图切换选中态被 `.ghost` 洗掉」与失败态「复制路径」死按钮两个真缺陷。
  `ISS-AP-09`（2026-09-11 用户上报复制路径格式错）：`/archive-files` 追加 `unc` 键
  （`\\192.0.2.199\File\...`），前端「复制路径」**优先复制 UNC**。⚠️ 连带修掉
  `config.load_config()` **丢弃非 DEFAULT_CONFIG 顶层键**的设计坑——否则往 config.json
  写 `smb_share` 根本读不到。server 段必须用固定 IP（挂载点主机名 `kj-server` 解析不了）。
  ⚠️ **审计实测发现基线是红的**：`pytest tests/ -q` → **7 failed / 603 passed**，其中 **6 例落在归档域**。
  根因（已实验证实）：`app.py:32` 用 `from config import load_config` 直接绑定，测试却 patch `config_module.load_config`
  → 打桩无效 → 归档路由读真实 `data/config.json` 的 `archive_root`（`/Volumes/File/...`，当前未挂载）→ `PermissionError`。
  即**这批归档测试从未隔离环境，通过与否取决于共享盘是否挂载**；且在共享盘挂载时会把测试文件真实写进生产归档目录。
  当前状态：**用户已批准"按建议执行"（D0~D5），已进入实施批次 1**。
  上游证据：`.scratch/open-archive-diagnosis-20260901.md`（kjfolder 方案 5 环外部依赖 + blur 误报的成功判据）。
