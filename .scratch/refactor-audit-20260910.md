# 重构审计清单（R 轮 · 2026-09-10）

> 触发：用户请求「重构代码提升可读性与可维护性」，未指定范围 → 按项目铁律先出审计清单。
> 本轮**只读不写**，未改动任何源码。
> 级别口径（重构轮专用，与 bug 轮 P0–P3 不同轴）：
> - **P0 = 正确性隐患**：存在静默数据/金额误差风险，或两套实现分叉
> - **P1 = 结构性阻塞**：改动牵一发动全身，无法安全增量修改
> - **P2 = 可维护性债务**：不影响当前运行，但显著拉高后续成本
> - **P3 = 一致性/体验**：低收益或改动风险高于收益
> 采集方法：AST 静态分析（函数体量 / 导入图 / 异常处理 / 路由映射）+ 人工抽读关键路径。

---

## 一、量化总览

| 指标 | 数值 | 判读 |
|---|---|---|
| `app.py` 行数 | **5,428** | 上帝文件 |
| `app.py` 路由数 | **85** 条 / **25** 个业务域 | 单文件承载全部 API |
| `app.py` 路由跨域散落 | `/api/tickets` 20 条散布 L1333–L4268（跨度 **2,936 行**） | 非按域聚集，无法整块搬移 |
| `app.py` 模块级全局变量 | **41** 个（含 4 组线程池/任务表/全局锁） | 拆分时的隐式耦合点 |
| `app.py` 函数内局部 import | **82** 处（全项目 131 处） | 真实依赖图失真 |
| ≥80 行函数 | **29** 个，最长 **254** 行（`app.py:3107 _run_contract_analysis`） | 需拆分 |
| 宽泛 `except Exception` | **138** 处，其中 **35** 处 `pass` 吞异常 | 异常策略缺失 |
| 零引用模块 | **2** 个（`core/refund_engine.py` 890行、`core/case_workflow.py` 44行） | 死代码 |
| 路由直连 DB 比例 | **3 / 85** | ✅ 正向：分层已初步形成，不需要推倒重来 |
| 完全同构的重复函数 | **0** 组 | ✅ 正向：无复制粘贴式重复 |
| 测试文件数 | **53** | ✅ 回归网足以支撑重构 |

---

## 二、问题清单

### ISS-R-01 ｜ P0 ｜ 费用计算双实现分叉，且精度更优的一版是死代码

- **现象**：`core/refund_engine.py`（890 行）实现了完整的扣费/退费规则引擎，使用 `Decimal + ROUND_HALF_UP`；但它在全项目**零引用**。线上实际走的是 `services/contract_service.py` 的 float 实现。
- **根因**：Decimal 版做完了但接线时未启用；两版并行演进后已产生口径分叉。
- **证据**：
  - 依赖图：`refund_engine` 入度为 0（无任何模块 import）。
  - `core/refund_engine.py:3` `from decimal import Decimal, InvalidOperation, ROUND_HALF_UP`；`:106`/`:118` `quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`。
  - `services/contract_service.py:622` `round(sum(service_breakdown.values()), 2)`；`:630`/`:631` 同为 `round(...)`（Python 内建为银行家舍入）。
- **影响**：金额恰为 .5 分边界时，两版结果可能相差 **0.01 元**。对「退费金额需与学员对账」的业务场景属实质隐患。
- **⚠️ 强制约束**：这条**不是纯技术重构**，切换实现会改变线上金额。必须先用对比脚本量化两版差异（跑全量历史工单），再由业务确认口径，**不得静默切换**。
- **建议**：先做「差异量化脚本」，零引用状态下删除或接线，二选一。

### ISS-R-02 ｜ P0 ｜ `core/case_workflow.py` 零引用，与 app.py 内联逻辑重复

- **现象**：`calculate_saved_fee_plan()` 定义了「已保存扣费行 → 可确认方案 + blockers」这一业务策略，但无人 import；`app.py` 内联实现了同一策略。
- **证据**：依赖图零引用；`core/case_workflow.py:18`（全文仅 44 行）。
- **建议**：确认后删除，或把 `app.py` 内联版接线到它。属「同一策略两份实现」的典型债务。

### ISS-R-03 ｜ P1 ｜ `app.py` 上帝文件：5,428 行 / 85 路由 / 25 业务域

- **现象**：所有 HTTP 端点、后台任务编排、LLM 提示词常量、启动逻辑混在一个文件。
- **根因**：历史增量叠加，无模块边界约定。
- **关键难点（必须正视）**：路由**不是按域连续排列**的。例如 `/api/tickets` 的 20 条散落 L1333–L4268，中间夹着 contract/reply/fs/kopen 的路由。这决定了**不能整块剪切**，只能逐条搬移。
- **建议策略**：见第四节「批次 1」，原则是**先搬不改**（纯位移，零逻辑变更），每域独立 commit + 全量 pytest。

### ISS-R-04 ｜ P1 ｜ 41 个模块级可变全局状态散在 app.py

- **现象**：`CONTRACT_ANALYSIS_JOBS` / `QUERY_JOBS` / `REPLY_GENERATE_LOCKS` / 4 个 `ThreadPoolExecutor` / 相关锁与事件，全部是 app.py 顶层的可变全局。
- **风险**：
  1. 拆 blueprint 后这些量成为跨模块隐式耦合点（谁都能改，无人拥有）。
  2. 若将来多 worker 部署（gunicorn >1 worker），内存态任务表直接失效。
- **证据**：`app.py:130–147` 集中定义；`_prune_contract_analysis_jobs()`（L173）等直接读写。
- **建议**：先抽 `core/job_registry.py` 统一持有 + 提供类型化访问函数，再拆路由。**顺序不能反**。

### ISS-R-05 ｜ P1 ｜ 82 处函数内局部 import 掩盖真实依赖关系

- **现象**：`app.py` 82 处函数内 import（全项目 131 处）。
- **性质需分类，不可一刀切**：
  - **合法**：重依赖延迟加载，如 `services/file_parser.py:34` 的 `pdfplumber`、`services/contract_service.py:90` 的 `paddleocr`（启动耗时代价大，懒加载是正确设计）。
  - **债务**：破环导入与随手写的 `import re`（如 `app.py:164 _safe_path_component`），使静态依赖图失真，重构时无法预判影响面，且环会在运行时才炸。
- **建议**：先分类清单（重依赖 / 破环 / 随手写），重依赖保留并加注释说明，破环类消除环后提为顶层导入。

### ISS-R-06 ｜ P2 ｜ 138 处宽泛 except，35 处 `pass` 完全吞异常

| 文件 | 宽泛 except | 其中 `pass` 吞掉 | 判读 |
|---|---|---|---|
| `app.py` | 75 | **15** | ⚠️ 主要处理对象，多为无注释静默失败 |
| `services/page_cache.py` | 10 | 9 | 多数带注释、属可选依赖防御，风险可控 |
| `services/contract_service.py` | 13 | 3 | 需逐条看 |
| `core/query_engine.py` | 13 | 0 | 有日志，可接受 |
| `services/file_parser.py` | 10 | 3 | 需逐条看 |
| `database.py` | 5 | 5 | 需逐条看 |
| 其它 | 12 | 0 | — |

- **判读**：不是「138 处都是问题」。`page_cache` 的 9 处多数带注释说明（如 `# pdfplumber 不可用 → 保守视作扫描版`），属**有意为之的防御**。真正需要处理的是 `app.py` 那 15 处**无注释**的静默吞异常。
- **建议**：分级处理，禁止无差别改写。

### ISS-R-07 ｜ P2 ｜ 29 个 ≥80 行函数，最长 254 行

Top 6：

| 行数 | 位置 | 函数 |
|---|---|---|
| 254 | `app.py:3107` | `_run_contract_analysis` |
| 251 | `app.py:2626` | `api_contract_upload` |
| 179 | `services/visit_service.py:555` | `generate_registration_form` |
| 174 | `services/contract_service.py:1141` | `_parse_ai_response` |
| 165 | `core/refund_engine.py:726` | `calculate_fee_plan`（死代码，随 R-01 处理） |
| 157 | `services/deduction_engine.py:385` | `calculate_dongcheng_refund` |

- **建议**：优先拆 app.py 的两个 200+ 行函数（R-03 拆域时顺带完成，不单独立项）。

### ISS-R-08 ｜ P2 ｜ 约 250 行 LLM 提示词硬编码在 app.py

- **现象**：`NOTES_POLISH_PROMPT`(L4862) / `COMPLAINT_EXTRACT_PROMPT`(L4922) / `COMPLAINT_POLISH_PROMPTS`(L4996) / `REPLY_POLISH_PROMPT`(L5068) 连续占据约 250 行。
- **风险**：提示词迭代需要改 Python 源代码 + 重启服务，无法热更新，也无法单独做 A/B 或单测。
- **建议**：外置为 `prompts/*.txt`（带版本号）或入库；加载层保持接口不变。

### ISS-R-09 ｜ P3 ｜ `templates/index.html` 单文件 2,771 行

- **现状**：JS 层已合理拆分（8 个 composables + 3 个 utils，走 importmap 裸模块名）；模板层全部内联，含 1 段内联 `<style>`。
- **判读**：**不建议本轮处理**。拆分需同步维护 importmap 映射与静态版本号 `?v=N`，而项目已两次踩过 importmap 白屏坑（新增 ES module 未登记 → 整图解析失败 → 全站白屏且 console 无报错）。收益低于风险。

### ISS-R-10 ｜ 正向发现（不需要重构）

- 路由直连 DB 仅 **3 / 85** → 分层（route → service → database）已初步成型，**不需要推倒重来**。
- 完全同构的重复函数 **0 组** → 无复制粘贴式重复代码。
- 无循环导入（AST 层面 0 个依赖环）。
- 53 个测试文件构成可用的回归网。

---

## 三、重构批次规划（待审批）

> 原则：**先搬不改**、**小步可回滚**、每批独立 commit + 全量 pytest 通过。

| 批次 | 内容 | 风险 | 验收标准 |
|---|---|---|---|
| **0** | 死代码处置：`refund_engine` 差异量化脚本 → 业务确认 → 删或接线；`case_workflow` 确认后删除 | 低（不动线上路径） | 差异量化报告产出；删除后 pytest 全绿 |
| **1** | `app.py` 逐域拆 blueprint（**纯位移，零逻辑变更**）：先 tickets → contract → users/auth → reply/fs/config → templates/statistics | 中（85 路由逐条搬） | 每域搬完立即跑全量 pytest；`git diff` 只应出现缩进与 decorator 变更；路由表 85 条数量不变 |
| **2** | 全局状态收敛：抽 `core/job_registry.py` | 中 | 任务提交/查询/清理全链路 E2E 通过（合同分析、三系统查询） |
| **3** | 长函数拆分（app.py 两个 200+ 行）+ 提示词外置 | 低-中 | 拆分前后同一输入的输出逐字段一致 |
| **4** | 异常策略收窄（仅处理 app.py 15 处无注释 `pass`） | 低 | 补充日志后 pytest 全绿，且失败路径日志可查 |

**批次 1 的硬性前置**：批次 2 的 `job_registry` **必须先于**路由拆分完成，否则全局状态会在拆分过程中被多处引用，反而加深耦合。

---

## 四、明确不动的部分（避免过度设计）

- `templates/index.html`（见 ISS-R-09）。
- 延迟加载重依赖的写法（`paddleocr` / `pdfplumber` / `pdf2image`）——这是**正确设计**，不是债务。
- 三个系统的查询口径与爬虫层的 GBK/分页处理（`crawlers/`）——业务语义密集，重构收益低、破坏风险高。
- 网点字典 58 条与 `org_vehicle_counts` 结构——受历史工单 `id` 保护。
