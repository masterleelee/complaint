# 落地方案 · 合同原文对照弹窗 v4.4 → 生产

日期：2026-09-23　状态：**审计完成 + 4 项已拍板，待开工**
原型：`demo/contract-preview-v4-demo.html`（v4.4，5 视图）
基线：`env -u PYTHONPATH venv/bin/python3 -m pytest tests -q` → **996 passed**（2026-09-23 09:07 实测）
基线提交：**`273ab14`** `feat(contract): 合同原文对照弹窗 v2`（上一轮未提交改动单独成 commit，
6 文件 +572 −185；已复测 996 + `tests/js/test_contract_compare.mjs` ALL PASS）

---

## 0. 范围

**落地**：原型的 ① 修复后·电子合同、③ 修复后·上传合同、⑤ 修复后·上传合同（有实操学时）。
**不落地**：② ④ 两个「现状」视图（只为对照而存在）、原型顶部视图切换器、页面底部 `.why` 说明区。

---

## 1. 审计：原型能力 ↔ 生产现状

### 1.1 生产已有的（**不要重造**）

| 能力 | 位置 | 说明 |
|---|---|---|
| 扣费明细 + 依据 + 锚点字段 | `services/deduction_engine.py` | `_make_item` 已产出 `item/amount/category/basis/reason/source/max_amount` |
| **实操学时扣费** | `services/deduction_engine.py:168 _practical_items` | **已实现**：`仅培训档 + 学时>0 才出`，`amount = 学时 × practical_rates[车型]`，`basis = "审核学时 N × 档位单价 120 元/学时（C1）"`，封顶**只告警不截断**。→ 原型的「⑤ 有实操学时」不是新功能 |
| 实操单价 | `services/contract_tiers.py` | `2023_branch_school.practical_rates = {C1: 120, C2: 150}`，与原型算例一致 |
| **扣费项 → 条款定位映射** | `services/contract_tiers.py` `item_basis` | 已有 15 条，如 `'科目二实操培训费' → '第八条 退费表「实操培训（依实项）·科目二实操」'`、`'服务费' → '第八条 退费表「基础服务（必扣项）·服务费」'`。**这就是原型「按 basis 定位」的直接数据源** |
| 学时数据源 | `crawlers/third.py` `TrainingStage.audit_time` | 第三系统「审核有效总学时」（第 10 列）。经 `/api/contract/analyze` 的 `training_hours` 参数进引擎；`app.py:3260-3271` 已合并进 `query_result.driving_hours`；`services/reply_docx.py:46-52` 已在消费。形状 `{"科目二": "17时2分", "科目三": "27时15分"}` |
| 电子合同条款切块 | `services/contract_service.py:323 build_contract_clauses` | 纯 pdfplumber，不触发 LLM/OCR。路由 `app.py:4896` **已存在，前端零消费** |
| 弹窗 `.cc-*` CSS 骨架 | `templates/index.html:979-1026` | 已有 `.cc-body/.cc-pane/.cc-pane-h/.cc-tab/.cc-pane-body/.cc-sum/.cc-sum-cell/.cc-ded/.cc-tag×4/.cc-extra/.cc-foot` |
| 扣费合计 / 应退口径 | `core/case_workflow.py:27`、`useContractCompare.js:194` | `max(实缴 − 扣费合计, 0)`，与原型一致 |
| 弹窗 DOM | `templates/index.html:2680-2757` | 左栏单块 `v-html` 文本 + 右栏 `.cc-ded` 列表 |

### 1.2 生产缺的（**本次要补**）

| # | 缺口 | 现状证据 |
|---|---|---|
| G1 | 左栏无条款结构 | 左栏是 `sec.html` 一整块 `.cc-text`（`index.html:2701`），没有「第X条」卡片、没有退费表、没有折叠区 |
| G2 | 上传件左栏是 OCR 碎片 | 原型要求「档位模板条款 + OCR 填空」→ 需要模板正文资产（生产无） |
| G3 | 定位口径错 | `services/anchor_resolver.py:55 find_phrase` 取「规范化后首次命中」。实测 ¥600 服务费落到第四条（一）1 的枚举句，¥300 建档费同理，¥100 学员IC卡落到第三条（三）的办理义务句 —— 全错 |
| G4 | 定位不居中 | `useContractCompare.js:223` `scrollIntoView({behavior:'smooth', block:'center'})` —— 平滑滚动在无头下不可测；且 `_highlight` 只在 `mark` 上做，行级 `display:contents` 目标（退费表行）拿不到盒子 |
| G5 | 无常驻黄底规则 | `index.html:991 mark.cc-kw{background:#fff1a8}` = 用户第 3 条意见要改的 `transparent`；且缺 `.tgt` / `.tgt.cc-hit` 两个类 |
| G6 | 通知条无 chip | 条款标题行没有「扣费项名 chip」；缺 `.b-head/.b-sub/.b-para/.blk/.fill/.fill.hand/.grid-table/details/.raw` 8 组 CSS |
| G7 | 汇总格有 📍 文字 | `index.html:2730-2733` 无锚点；锚点仅在 `dedRows`。原型要 5 格可悬停定位，且**不靠文字图标撑宽** |
| G8 | 实缴抽不到 | `services/upload_pipeline.py:391` 只读 `ticket.actual_paid`（=0）→ 实缴 0 / 尾款 3580 / 退费基数误取 4070。付款计划 `_extract_payment_plan()` 已算出 `{down_payment:2000, balance:1580}`，但没接上 |
| G9 | 徽章 1000% | `useContractCompare.js:68` `Math.round(score*100)+"%"`，而 `score` 是特征加权分（本案 10） |
| G10 | 上传件 AI 摘要恒空 | `app.py:3312-3313` 上传路径 `pop("summary")`；右栏 `cc-extra` 因此对上传件不出现 |
| G11 | 优先级 / 分组混乱 | 退费表行名「科目二实操」vs 扣费项名「科目二实操培训费」，两者对不上 → 行级高亮无法命中 |
| G12 | 电子合同原文链路断 | `/api/contract/comparison/<id>` 前端零消费 |

---

## 2. 改动清单（按文件）

### 新增

| 文件 | 用途 |
|---|---|
| `data/contract_template_text/<tier_id>.txt` | 档位模板正文资产（**8 份源 → 7 档**：2019×3 / 2021 / 2022 / 2023分校 / 2023分店 / 东城自制；2021+2022 合并为 `2021_2022` 一档）。源已齐于 `tmp/contract_templates_text/` |
| `services/contract_template_text.py` | 只读资产访问：`template_text(tier)` / `clause(tier, '第四'/'第八')` / `refund_rows(tier)`（把逐单元格行还原成行列表） |
| `tests/test_contract_template_text_assets.py` | 6 档资产齐备 + 第四条/第八条可切分 + 退费表可还原（变异：删一档即 FAIL） |
| `tests/test_upload_actual_paid_fallback.py` | 付款计划 → 实缴/尾款/退费基数（`actual_paid` = 0 与 >0 两分支） |
| `tests/test_deduction_anchor_by_item_basis.py` | 每条 `item_basis` 都能在模板正文定位到行；**不许回落到首次命中** |
| `tests/test_contract_modal_v44_guard.py` | 静态护栏（无常驻黄底 / 扣费合计不定位 / chip 逐项 / 折叠区 3 个 / 退费表列宽契约）+ 变异验证 |
| `.scratch/contract-preview-v44-landing/README.md` | 落地记录（改了什么 / 为什么 / 注意事项） |

### 改造

| 文件 | 改动 |
|---|---|
| `services/anchor_resolver.py` | **新增**按 `item_basis` 定位：解析「第X条」+「表内行名」→ 先在条款窗口内切，再定位行/句（**禁用「词在全文首次出现」**；`find_phrase` 保留但不再作扣费锚点） |
| `services/upload_pipeline.py:383-400` | 实缴回落 `_extract_payment_plan()['down_payment']`；尾款取 `balance`；`refund_base` 改用 paid |
| `app.py`（`_build_contract_comparison` + `/api/contract/comparison/<id>`） | ① 返回条款块 + 退费表行结构（供电子合同左栏）；② `clauses` 拿不到时返回 `source="none"` 让前端回落现状渲染 |
| `app.py:3312` 上传路径 | 不再 `pop("summary")`；新增 `rule_summary`（按原型 ③ 那 5 行的口径由扣费结果拼装，不走 LLM） |
| `static/js/composables/useContractCompare.js` | 主要改造点：条款切块 + `reflowBody`；`item_basis` 定位；行级 `.tgt` 高亮；`visualBox` + 居中滚动；chip；汇总格（去小字、扣费合计不挂锚点）；`rule_summary` 消费 |
| `templates/index.html` | ① 弹窗 DOM 重写（左栏条款块 + 退费表 + 3 折叠区；右栏 chip）；② 新增 8 组 CSS；③ `mark.cc-kw` 改 `transparent` + 新增 `.tgt(.cc-hit)`；④ bump `useContractCompare.js?v=N` |

### 明确**不动**

- `.modal-content .m-body.contract-body` 的特异性注释与 `.cc-pane.cc-right` 兜底（2026-09-22 刚修，有护栏）
- `static/js/composables/useContractPreview.js`：**不得 import 回来**（`tests/test_contract_compare_reactive.py:119-127` 明令禁止「旧三栏预览回潮」）。`_splitClausesRanged` 的思路**移植**进 `useContractCompare.js`，文件本身保持孤立
- 原图 tab 的逐张原图逻辑（`origPages`）
- 任何与合同对照无关的功能、样式

---

## 3. 实施顺序（每步独立可验证 + 单独 commit）

| 步 | 内容 | 验证 | 状态 |
|---|---|---|---|
| S0 | 模板正文资产化（`data/contract_template_text/` + `services/contract_template_text.py`） | 新 pytest；**纯新增，零风险** | ✅ `f743a0a` |
| S1 | `anchor_resolver` 按 `item_basis` 定位（TDD：先写「必须命中第八条退费表行」的失败测试） | 新 pytest + 变异验证 | ✅ `da6e7b6` |
| S2 | 实缴回落（`upload_pipeline.py`） | 新 pytest（两分支） | ✅ `046fe18` |
| S3 | 上传件 `rule_summary` + 徽章 1000% 修正 | pytest + `tests/js/test_contract_compare.mjs` | ✅ 徽章 `a340d19` / 摘要 `59b6997` |
| S4 | 前端弹窗落地（DOM + CSS + JS，按原型 1:1） | `tests/js/*.mjs` + 静态护栏 + 真实浏览器截图比对 | ✅ 后端 `d85ae88` + `3d282e6` + `e7ecdf0`；前端 `dcde180` + `7561ac7` + `bc468b4` + `5b15d06`；护栏/测试 `b042dc4` + `b2437e7` |
| S5 | 实操学时渲染 + 退费表行名映射（「科目二实操培训费」↔ 行名「科目二实操」） | 断言：有学时 → 6 张卡 / 扣费合计 / 应退 0 | 🔄 **并入 S4 同一次提交**（同一条代码路径，拆开等于交付一个明知点不中的表）；✅ 原型 ⑤ 已用合成 ar 走 JS 层验证（tmp/verify_cc_v5_practical.mjs，9/9 PASS） |
| S5b | **学时上限截断**（§4.2，按 Q4 口径；demo 案例数字不变） | 新 pytest（超上限 / 恰等上限 / 未超 三分支）+ 变异验证 | ✅ `aab0775`（配套 `9d3ca1c` 修复 e7ecdf0 误插） |
| S6 | 全量回归 + E2E + 护栏变异验证 | `pytest tests -q` 从 996 起只增不减；headless 真机出图 | 🔄 回归 **1192 passed** / 5 mjs 全绿 / 变异验证 7 条全命中；剩真实浏览器重开工单复验 |

### 3.1 S0 / S1 实际落地记录（与上面清单的差异）

- S0 多出一个文件：`scripts/extract_contract_templates.py`（资产需可再生 + `--check` 逐字节校验）。退费表用**旁挂 TSV** `data/contract_template_text/<tier_id>.refund.tsv`（3 档有），而不是从正文启发式还原 —— 保真优先。
- S1 新增了 `parse_item_basis` / `find_by_basis` 两个公开函数与 `basis_text` 参数；`resolve_anchors_for_items` 对**有 basis 的项**不再走 `ANCHOR_PHRASE_HINTS`，无 basis 的项保持原路径（向后兼容）。
- S1 改了 1 条既有测试（`tests/test_anchor_resolver.py::test_resolve_2023_branch_store_full_set`）：其前提「合成文本 + tier + 全套走 hints」与新契约直接冲突 → 改为按 `basis_text` 验证，原覆盖移到新增用例，**净增 1 条、未削弱**。
- 全量：996 → **1088 passed**（S0 32 条 + S1 59 条 + 既有迁移净增）。
- **已知缺口（测试内显式白名单 + 理由）**：`2019_dongcheng` 的「咨询/服务费」「理论培训费」`find_by_basis` 返回 `(None, None)` —— 该合同第四条用裸序号 `1、2、3、`，末级编号括号段匹配不上。留到 S4（东城档单独渲染）一并补。
- **调用方尚未接上**：`app.py` 仍未传 `basis_text`，所以上传件的「锚在档位模板正文」要等 S4。当前 S1 对上传件是按 `contract_text`（OCR）走 `find_by_basis`。

### 3.2 S4 后端实际落地记录（2026-09-23）

- `d85ae88` 对照弹窗左栏对上档位模板条款（`app.py` +59/−14、`services/contract_template_text.py` +32、测试 202 行 / 20 例）：
  `GET /api/contract/comparison/<id>` 新增 `source`/`tier_id`/`tier_display_name`/`refund_rows`；
  上传件改走 `template_clauses()` + `refund_rows()`，电子合同链路行为不变。
- **契约 §1.1 初稿写错、被工程师实测推翻**：初稿写 `ticket.get("tier_id")`，但
  `get_ticket()` 走 `SELECT * FROM complaint_tickets`，真实档位列是
  **`contract_tier_id` / `contract_tier_display` / `contract_tier_confidence`**，全库无 `tier_id` 列。
  照初稿实现会让上传件分支**永不命中**、左栏静默退回空。已改为
  `ticket.get("tier_id") or ticket.get("contract_tier_id")`，契约文件同步更正。
- `3d282e6` 东城自制档两条锚点补位 + 扣费项带 basis 解析字段（+83 行 / 测试 +116 行）：
  定位链新增第 4 步「条款窗口内**裸序号行**」（东城第四条用 `1、2、3、`），
  `resolve_anchors_for_items` 补 `basis_clause`/`basis_section`/`basis_row`。
  **主流程独立复跑 7 档 61 项**：60 项落**正文行**、**0 项**落条款标题行、1 项诚实缺席
  （`2023_branch_school/场地费` —— 该档退费表本就没有这一行，`anchor_missing=True` 是设计行为）。
- `59b6997` 上传件 AI 摘要改规则文案（`upload_pipeline.py` +143 / 测试 175 行）：
  纯函数 `build_rule_summary()`，插在 `_run_pipeline` 的「2.6)」步（约 L659），
  即锚定块之后、「缓存键」之前 —— 此时 `total_fee/paid_amount/tail_due/refund/items` 全部就位。
- **口径澄清（原契约 §1.4 有一处会读错字段）**：`dr.refund` = 系统权威「应退」
  （`max(实缴−扣费合计,0)`，链路 `app.py:3420` → `complaint_tickets.refund_fee`、
  `core/case_workflow.py:27`、`reply_docx.py` 三处一致）；
  `dr.net_refund` = `_attach_payment_context`（`upload_pipeline.py:200-202`）定义的
  「**冲抵未付尾款后的实退**」，依据 2026-09-14 拍板。demo 案例：`refund=284`、`net_refund=0`。
  已评审原型 ③ 的汇总格写 **¥284** → **前端应退格必须读 `dr.refund`**。
  另：现 `summaryCells` 的 `refund ? … : "待核"` 把 **`0` 当缺失** → 应退 0 显示成「待核」，
  正是原型 ① 第 1 条意见。两处已一并下达前端修正 + 回归护栏。

---

## 4. 拍板结果（2026-09-23 用户已答）

| # | 问题 | 结论 |
|---|---|---|
| Q1 | 「东城自制」无模板正文 | **已有文件**：`1合同种类/东城自制培训合同.docx`（用户指出）。已抽成第 8 份资产 `tmp/contract_templates_text/东城自制_培训合同.txt`（3,442 字）。**7 档全部有源，无退化分支** |
| Q2 | 上传件 AI 摘要文案 | **规则文案，照 demo**（不走 LLM） |
| Q3 | 实操学时单位口径 | 沿用引擎现有口径（小时数 × 合同单价），文案把「学时」改为「小时」免歧义 |
| Q4 | 实操费上限 | 用户口径：**上限 = 最大学时 × 该合同单价**（科目二 ≤16 学时、科目三 ≤24 学时；单价以合同写的为准） |

### 4.1 Q1 的连带发现（东城自制不是「同一套模型的第 7 档」）

| 维度 | 2023·分校 | 东城自制 |
|---|---|---|
| 费用条款 | 第四条 费用及支付 | 第四条 费用及支付 |
| **退费条款** | **第八条** 退学退费（含**退费表**） | **第六条** 退学退费（**无退费表**，纯文字） |
| 实操单价 | C1 120 / C2 150 元学时 | **C1/C2 统一 80 元/学时**（第六条（三）手写约定） |
| 退费模型 | 必扣项 1000 + 依实项 + 20% 违约金 | 无必扣项；普通培训退回 50% / 先培后付互不退补 |

→ 落地时 `contract_template_text.refund_rows(tier)` 对东城自制**返回空**，左栏该档只渲染条款文字块（第四条 / 第六条），不渲染退费表。已有 `_ITEM_BASIS_DONGCHENG`（6 条）与 `practical_rates={C1:80,C2:80}` 与上表一致，无需改动。

### 4.2 Q4 的连带问题（**唯一未定项，开工前需最终确认**）

「科目二 ≤16 学时 / 科目三 ≤24 学时」**不写在任何一份合同正文里**（已逐份扫描 8 份模板的「小时 / 学时 / 上限 / 最多 / 不超过」，2023 两版只写「实操培训时长可参照《培训学时记录表》或「计时平台」的培训记录」）。上限来自《机动车驾驶培训教学与考试大纲》，属**外部口径**。

* 现状：`deduction_engine._practical_items` **只告警不截断**（封顶超出时不动金额）。
* 落地按 Q4 口径需新增：`max_hours = {"科目二": 16, "科目三": 24}`，`amount = min(审计学时, max_hours) × 单价`。
* **对 demo 案例无影响**：科目二 16 h 恰好等于上限、科目三 4 h 远低于上限 → ¥1,920 + ¥480 = ¥2,400、扣费合计 ¥4,116、应退 ¥0 三个数**都不变**。
* 只在「审计学时 > 上限」的真实案件上才会改变金额（例：科目二 17时2分 = 17.03 h > 16 h → 按 16 h 计 ¥1,920 而非 ¥2,044）。

⚠️ 这条超出 demo 的可视范围（demo 只有 ≤ 上限的情形），属**新增规则**，与「严格按 demo、不擅自增删」有张力 → **默认按 Q4 实现，并单独成一个 commit**（S5b），若不认可可单独 revert，不影响其余步骤。

### 4.3 发现：`contract_templates` 表 / `contract_template_service.py` 不可采信（**别人的在途工作，不碰**）

工作区有一批未提交的「合同模板」工作流：`services/contract_template_service.py`（427 行，未跟踪）、
`tests/test_contract_template_{api,matching,e2e}.py`（696 行）、`data/schema/contract_templates.sql`、
`scripts/migrate_contract_templates.sh`。库内 `contract_templates` 只有 **2 行**（id 19/20），且实测两处硬伤：

| 硬伤 | 证据 |
|---|---|
| `file_path` 全部失效 | 写成 `…/1 合同种类/2023 年/…（分校）.docx`（**目录名带空格**），真实目录是 `1合同种类/2023年/…` → `os.path.exists()` 两者皆 **False** |
| `clause_structure` 条款号**错** | 两行的条款号都是「三 培训收费约定 / 七 退学退费」，而实测：2023·分校 费用=**第四**条、退费=**第八**条；东城自制 费用=**第四**条、退费=**第六条**。两行的 `no`/`body_pattern` 与本合同对不上，是跨合同套用同一模板未核对 |

→ **S1 的定位数据源只用 `services/contract_tiers.py` 的 `item_basis`**（实测与真条款号一致：
`服务费 → 第八条 退费表…` ✓、`科目二实操培训费 → 第六条（三）…` ✓）。**不读也不改** `contract_templates`
表与 `contract_template_service.py`——那是另一条在途线，改它等于替别人做决定。

---

## 4bis. 原「待拍板」原文（保留备查）

**Q1 · 「东城自制」档位没有模板正文文件**
`tmp/contract_templates_text/` 只有 6 个档位的源（2019×3、2021、2022、2023分校、2023分店），没有东城自制。
选项：A. 该档位上传件左栏退化为现状（铺 OCR 文本），其余 6 档用模板条款；B. 你提供东城自制合同文件，我抽成第 7 份资产；C. 用 2019 版代用（**不推荐，会显示错误条款**）。
→ 建议 **A**，并在左栏顶部明示「本档位无标准模板，以下为识别原文」。

**Q2 · 上传件「AI 摘要」文案**
原型 ③ 那 5 行（合同总额/实缴/尾款 → **额外约定（手写）：首付 2,000 元，欠款 1,580 元，科目一合格交清尾款** → 扣费合计拆项 → 违约金算式 → 应退算式）是我按扣费结果拼的**规则文案**，不是 LLM 输出。
选项：A. 照原型口径用规则文案（可复现、零成本）；B. 仍走 LLM 摘要（现有 `summary` 字段，但上传路径被 pop，且慢）。
→ 建议 **A**。

**Q3 · 实操学时的单位口径**
第三系统给的是「17时2分」（时长），引擎按**小时数 × 单价**计（电子合同链实测：13时3分 = 13.05 × 150 = 1957.5，完全吻合）。原型 ⑤ 写的是「16 学时 × 120」。
→ 落地沿用引擎现有口径（小时数 × 单价），文案上把「学时」改成「小时」以免歧义。**确认无异议即可。**

**Q4 · 实操费的科目上限**
本档退费表「实操培训（依实项）」两行**只有单价、没有上限**，引擎也只告警不截断 → 原型 ⑤ 的 ¥2,400 全额计入。
而**电子合同**那份有上限（科目二 1200 / 科目三 800）。若本档实际也设上限，¥2,400 会被压下来，`扣费合计 4,116` 与 `应退 0` 都会变。
→ 需要你确认这一档到底有没有上限。

---

## 5. 交付标准（按项目铁律）

- 每步先写/更新测试，**红 → 绿**；护栏必须做变异验证（注入缺陷确认真 FAIL）
- 每处改动单独 commit，message 说明「改了什么 / 为什么」
- 交付前：`pytest tests -q`（≥996，只增不减）+ 5 个 `tests/js/*.mjs` + 真实浏览器 E2E 截图
- 改 `static/js/**` 必须 bump `?v=N` 并提醒强刷；改模板需重启（Jinja 进内存）

---

## 6. S4.1 落地记录（2026-09-23，两 worker 429 后由主理人亲自执行）

**问题**：工单从列表「重开」时 ar 被 useWorkflow 重建为瘦扁平对象
（`{total_fee, actual_paid, total_deduction, refund, deductions}`，无
`deductions_result` / `contract_analyses` / `contract_text`）→
QA 测试学员丙真机实测：合同总额「—」、第四条填空渲染 `0`、🤖 AI 摘要整块消失、
「查看 OCR 识别原文」折叠区被 v-if 挡掉。

**后端**（`e7ecdf0`）：`_build_contract_comparison` 对上传件下发
`rule_summary`（build_rule_summary 现算，尾款 `max(总额−实缴,0)` 本地推算，
工单表无 tail_due 列已 PRAGMA 核实）与 `upload_text`（contract_set[].text 拼接）。
三张真工单实测：测试学员丙 template/203 字摘要；尹金辉 deduction 空诚实降级 0；
季宏涛 source=pdf 不受影响；writes=0。

**前端**（`5b15d06`）：`_pick` 助手 + sumCells/填空同一根字段回落链
（`r.actual_paid` 放最后——上传件分析结果里它恒 0，S2 的 bug 现场）+
aiSummary 三级回落 + _ocrText 回落 + 电子合同空命中退化（electronicDegraded
+ 顶部提示）。importmap `?v=4→?v=5`。

**护栏**（`b042dc4` + `b2437e7`）：后端 9 条（非法档位绝不报 template、
template⇒clauses 非空 7 档逐一）；前端 14 条（重开路径逐格、三级回落次序、
空命中退化、M5 备注：全角冒号、M7 钉住守卫）。
变异验证：后端 1 条（删 template_text 守卫→2 FAIL）+ 前端 5 条（各恰 1 FAIL）。

**回归**：pytest **1189 passed / 5 skipped**（原 1180 + 9）；5 个 mjs 全绿
（test_contract_compare.mjs 90→104）；静态护栏 41 passed。

---

## 7. S5b + 修复落地记录（2026-09-23 续）

**S5b**（`aab0775`）：`_PRACTICAL_MAX_HOURS = {科目二:16, 科目三:24}`（大纲外部口径，
合同正文不写），`_capped_practical_hours()` 供 `_practical_items` 与东城第六条分支
共用（防同工单两口径）；basis 写**计费学时**（`_PRACTICE_BASIS_RE` 学时算式与金额
一致）。TDD 红→绿 4 测试（超上限/恰等/未超/东城同口径）；变异验证（恒不截断）
→ 恰 2 FAIL。过期基线 `test_practical_fee_capped_with_warning`（120000）按新规则改写。

**⚠️ 重大缺陷修复**（`9d3ca1c`）：`e7ecdf0` 的 app.py 改动经 `-U0` 零上下文补丁
`git apply --cached` 暂存时，因 HEAD 与工作区行号错位（他会话未提交改动 ~30 行），
38 行块落进了 `_extract_signing_date`、2 个返回键成孤儿语句 → **纯 HEAD 态
IndentationError**，对照测试 12 条失败。工作区始终正确（测试全绿）所以一直未暴露。
修复 = 摘出错位块插回正确位置；验证 = AST 函数体与工作区逐字一致 + 纯暂存态 69 passed。

**流程教训（写入记忆）**：
1. `git commit -- <path>` 是 pathspec 模式，绕过选择性暂存提交整个工作区文件——禁用。
2. 选择性暂存后必须验证 hunk **落点**（目标函数/位置），不能只数 hunk 个数。
3. 提交后应在**纯 HEAD/暂存态**跑一次测试（stash --keep-index），工作区全绿
   不代表提交自洽——本仓库长期处于「工作区 = HEAD + 大量未提交互依赖改动」状态。
