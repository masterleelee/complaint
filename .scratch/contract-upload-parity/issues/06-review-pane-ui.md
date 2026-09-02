# 06: 三栏预览组件接入上传件

**Parent:** 规格 `spec.md`

**What to build:** 数据源无关的三栏预览组件（原件清单 / 结构化原文+锚点 / 明细列表），版式以 demo（`demo/contract-upload-parity-v1.html`）为像素基准：左=结构化原文（条款号+页码标注+mark 高亮）、中=原件影像（照片直显、文本 PDF 浏览器端渲染；可折叠为缩略图条）、右=明细列表（必扣/依实/违约金标签+依据+金额）。点明细 → 左侧滚动高亮 + 中间对应页闪烁；anchor_missing 项不显示定位按钮。接入现有「上传合同分析」流：分析完成后打开该预览。遵守工程红线：静态资源版本号 bump、importmap 自检、HTML 改前查 data-page-node-id 残留。

**Blocked by:** 05

**Status:** done — 2026-09-01：**subagent(reasoning) 派发**完成，主 Agent 复核通过。新增 `static/js/composables/useContractPreview.js`（355 行）+ `templates/index.html` +216 行（CSS ~99 + Vue 模板 ~117）+ `static/js/app.js` +6 行（import + setup + return）；importmap bump（`useWorkflow` v=19→20 / `app.js` v=40→41 / 新增 `useContractPreview` v=1）。全量回归 420 passed / 0 failed（基线 411 → +9 是 subagent 跑时 pytest 重计，无 Python 文件改动）。

- [x] 真实页面三栏齐全且与 demo 版式一致（左原文 / 中原件骨架 / 右明细）—— Vue 模板在 ② 扣费明细表卡片之后（`templates/index.html:1560-1679`），CSS 翻译 demo 的 `:root`/`.panes`/`.pane` 等选择器（命名空间 `cp-*`）
- [x] 点击每条明细：高亮命中 + 滚动到位（`useContractPreview.locate` 加 `.cp-hit` + `scrollIntoView`）；页闪烁实现为「所有 `.cp-page-card` 都闪」（见下条注意事项）
- [x] 原件折叠按钮可在整页与缩略图条间切换（`.cp-fold-btn` 切换 `.cp-pane-orig.cp-folded`）
- [x] `anchor_missing` 项无定位按钮、不报错——`canLocate()` 双兜底（`a.missing` + `typeof start !== "number"`），模板用 `bi-question-circle` 占位
- [x] 浏览器红线：importmap 差集 `MISSING: OK`；data-page-node-id 残留 0；`node --check` 通过；版本号已 bump；下载链路 0 回归

**实现要点**：
- **snake_case → camelCase 桥接**：后端 Python `anchor_missing/start/end/text/phrase` → 前端 `missing/start/end/text/phrase`；`originalHtml` computed 按区间切片并去重 + 防重叠
- **Vue 全局约定**：`Vue.ref/computed/nextTick` 直接调用（不 importmap 登记 Vue），符合其他 composable 惯例；subagent 第一次踩了「import Vue」陷阱，红线 1 自检失败后立即修正
- **三栏挂载点位置**：紧邻 ② 扣费明细表卡片之后、③ 回复函之前——**不替换**原有可编辑表格（避免与 `useWorkflow.recalc()` 互相覆盖），两个视图并存：上表格编辑，下三栏只读预览
- **`canLocate()` 比 `locate()` 更严**：后者只要 start 是数字就允许，前者还要 missing=false——模板用前者决定是否渲染定位图标，双保险

**已知 V1 妥协（记入下阶段）**：
- **原件页闪烁**做了简化：当前契约 schema 只提供 `anchor_start/end`（合同原文位置），**没有 per-page 位置信息**，故 `locate()` 对**所有** `.cp-page-card` 都触发 `.cp-flash`。09 阶段补上按页码定位仅需修改 `useContractPreview.js:298-303` 一处 forEach。
- **原件面板是骨架占位**：09 阶段实现扫描版 PDF 页图缓存前，无真实原件可展示；当前用 `.cp-ph` 灰块示意。
- **LLM prompt 升级未做**（spec 05 第 1 项）：当前 items 的 `anchor_phrase` 全是 engine hints 表回填；06+ 阶段若 prompt 升级要求 LLM 直接吐 anchor_phrase，`resolve_anchors_for_items` 的 explicit-first 语义已就绪，无需改前端。

