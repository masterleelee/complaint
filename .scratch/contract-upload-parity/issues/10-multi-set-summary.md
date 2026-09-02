# 10: 多份合同合并汇总呈现

**Parent:** 规格 `spec.md`

**What to build:** 前端汇总呈现：摘要条显示「已识别 N 份」与各份份类型标签；培训费总额 = 各份手写总额求和；明细跨份合并展示且每条标注所属合同份（服务/代缴/培训）；某份缺失时不猜测、只汇总已识别份数（2019 只传 2 份也能完整计算）。单份上传的行为与 06 保持一致（回归）。

**Blocked by:** 03, 06

**Status:** done — 2026-09-01/02 主 Agent 实现：`services/multi_contract.py`（185 行：逐份分析 + 归并纯函数）+ `app.py` 集成（`_run_contract_analysis` 多份分支 + 单份回传补齐 + 请求进度覆盖进 ticket 副本）+ 前端 `useContractPreview.js?v=2`（contractSections / contractCount / 份标签 + 左栏分节渲染修复条款重复缺陷）+ `templates/index.html`（摘要条「已识别 N 份」+ 明细份标签 + CSS；importmap useContractPreview v=1→v=2）。测试：`tests/test_multi_contract.py` 17 例 + `tests/test_10_multi_integration.py` 5 例（+2 真实样张回归）+ `tests/js/test_contract_preview.mjs` 17 例。全量回归 518 passed / 0 failed。E2E：Vue 挂载 ✓ / errors+console 干净 ✓ / importmap 差集 OK ✓ / data-page-node-id 残留 0 ✓ / node --check ✓。

**真实样张预验收（2026-09-02，Done 门⑥自动化前置，脚本 `tmp/real_sample_acceptance.py`）**：
- **A. 2019 三份真实 PDF（服务/代缴/培训）→ ✅ 全通过**：`多份合并（已识别 3 份）：服务(2019·服务)、代缴(2019·代缴)、培训(2019·培训)`；考试费归属代缴份（70 元）；理论培训费 pending（多份逐份总额未知，设计口径）；逐份真实提取 2137/2131/3340 字符
- **B. 2023 分店 docx → ✅ 档位识别正确**：`identify_tier(2023-12-27, 分店, 真文5386字) → 2023_branch_store 2023·分店 (high)`；完整管线呈「识别不完整→人工补录」为设计内 07 闸门 UX（docx 不在上传白名单，真实流程为扫描件上传）
- **C. 2021 .doc → 白名单 400 拒绝 ✓**（上传只收 pdf/图片，设计行为）
- **验收修复（P1）**：真实样张暴露 `_run_contract_analysis` 顺序缺陷——legacy 首份 `can_confirm_fee_plan=False`（2019 服务合同本就无费用字段，属预期）在多份归并**之前**早退整单报错 → 三栏面板永不渲染。修复：多份套件时 legacy 错误降级为面板 warnings（`cp.warnings` 渲染），继续归并；单份/下载件行为不变。回归：`test_multi_legacy_first_file_incomplete_demoted` + `test_single_legacy_error_still_early_return`。此前集成测试未暴露因 `_stub_legacy` 打了无错误桩

- [x] 上传 2 份（代缴+培训）→ 摘要条「已识别 2 份」，培训费总额按整套录入值展示（逐份手写总额 V1 未抽取，见注意事项 3）
- [x] 每条明细可见所属合同份——`contract_kind` 标签（明细行 chip + 左栏分节头）
- [x] 同名扣费项跨份不去重、各份独立列示——`test_same_name_items_not_deduped_across_entries` + `test_aggregate_analyses_standalone`
- [x] 单份上传行为与 06 一致（回归用例）——`test_analyze_single_contract_returns_deductions_result`：legacy 顶层字段不缩 + 理论费仍用 ticket.total_fee 非 pending

**实现要点**：
- **逐份分析**：`analyze_contract_set` 对 contract_set 每个有文件的条目跑 `analyze_upload_contract_file`；份类型 = 条目登记 kind > 档位表 kind > 空串
- **考试费归属**（多份归并口径，ADR-0003「调用方做多份归并」的落地）：多份并行时考试费/补考费只计「代缴 > 单一培训 > 培训 > 首份」第一份，其余份同名项剔除（代收代交考试费合同的职责；跨份不重复扣）
- **多份逐份手写总额未知** → 逐份 total_fee=None → 理论培训费/违约金按引擎规则置 pending（对接 07 闸门，不编造数字）；单份沿用 ticket.total_fee 原行为
- **P1 修复（06 遗留）**：`_run_contract_analysis` 此前从不回传 `deductions_result`（只写 `deductions` 列表），前端三栏面板 `v-if="ar && ar.deductions_result"` **在生产路径从不渲染**；06 阶段 E2E 用构造数据未暴露。本票统一回传 `deductions_result` + `contract_analyses`（多份和单份都回），下载件不含这些键（面板不渲染，承诺不破）
- **P2 修复（04 遗留）**：`exam_counts`/实操学时不是工单表字段，上传管线只从 ticket 读 → 生产上考试费/实操费永远为空；现把分析请求携带的进度覆盖进 ticket 副本（`app.py` `_run_contract_analysis`）
- **左栏缺陷修复**：06 版每条条款都 `v-html="cp.originalHtml"`（整篇原文重复 N 次）；改为逐条款区间切片注入 mark（`_splitClausesRanged` + `_injectMarks`），单份/多份共用

**注意事项（V1 妥协，待用户审批）**：
1. 逐份手写总额抽取未做（spec 05 的 LLM prompt 升级同批遗留）→ 多份模式下理论培训费/违约金呈 pending，需人工在明细表补录后才可确认费用方案
2. 改档重算（08 retier）仍是单份接口——多份工单逐份改档留二期
3. 摘要条「培训费总额」= 整套合同录入总额（用户权威录入，US18）；「各份之和」语义由用户录入值承载，逐份 OCR 求和待 prompt 升级
4. 多份逐份分析成本：N 份 = N 次提取（vision/OCR），缓存（08）可挡重复打开
5. E2E 登录态导致 admin session_version +1（脚本登录顶掉旧会话，一次）

