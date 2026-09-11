# ISS-AP-08 · 前端：面板样式对齐 demo 基准（用户上报「做得太丑」）

**Status:** done —— 2026-09-11 主 Agent 实现
**Priority:** P2（不影响功能与数据，纯视觉；但用户明确上报，且影响「按钮按下去必有反应」的可信度感知）
**依赖:** ISS-AP-04 ✅ / ISS-AP-05 ✅（本单只换皮，不动已冻结的接口契约与状态机）
**并行:** ❌ 与任何触碰 `templates/index.html` 的工单互斥（该文件有 mtime 竞态坑）

## 现象（用户 2026-09-11 原话）

> 「样式应该按照这个 demo 的来吧。怎么现在做成这么丑」
> —— 附两张对照截图：图 1 = `demo/archive-panel-redesign-demo.html` 的效果（用户认可用），
> 图 2 = ISS-AP-04 落地后的实际效果。

## 根因（差距不是「调几个数值」，是两套设计语言）

ISS-AP-04 的落地版把 demo 的**结构**抄了，但样式是另起一套 `.ap-*`：

| 维度 | demo（基准） | 落地版（丑） | 差异性质 |
|---|---|---|---|
| 失败态 | 居中：56px 圆形图标 + 标题 + 正文 + 「该找谁」徽标 + 主按钮 | 左对齐红底块 + 一行加粗红字 | **信息层级完全不同** |
| 文件图标 | 30×30 彩色圆角色块（Word 蓝 / PDF 红 / 图片橙 / 文本绿） | 14px 灰色 `bi` 字体图标 | 无色彩编码，扫不出类型 |
| 按钮 | demo 通用皮肤 `6px 12px` / 圆角 8 | 全局 `.btn btn-secondary btn-sm` | 与 demo 的字号/内边距不一致 |
| 表格 | th `9px 20px` / td `10px 20px`、行 hover 高亮 | th `6px 4px` / td `7px 4px` | 过挤，行高 41px |
| 头部 | 「标题 + 副标题」两行 | 单行 + 灰色小尾巴 | 层级弱 |
| 视图切换 | 选中态填充主色 | 同左（但**选中态被 `.ghost` 洗掉**，见下） | 缺陷 |

## 改动点

### A. 样式（`templates/index.html` 内联 `<style>`）

把 `demo/archive-panel-redesign-demo.html` 的样式 **1:1 移植**，只做两处必要适配：

1. 选择器一律冠 `.ap-modal` 前缀（demo 用裸 `.modal/.state/.files/.grid`，直接搬会污染全局，
   例如 demo 的 `button{}` 会改掉全站按钮）。
2. demo 里硬编码的三个灰阶（`#f8fafc` / `#f1f5f9`）收成局部变量 `--ap-chrome` /
   `--ap-hairline` / `--ap-hover`，并在 `html.dark .ap-modal` 下重映射。

> **关键发现**：demo 的调色板变量（`--primary` / `--primary-light` / `--danger-light` /
> `--surface` / `--text-secondary` / `--border` / `--radius` / `--shadow-lg`…）在项目
> `static/css/style.css:1-22` 的 `:root` 里**同名同值**，且 `templates/index.html:32-34`
> 进一步把 `--primary/--primary-dark/--success/--warning/--danger/--shadow` 别名成了
> `var(--color-*)`（后者由 `html.dark` 重定义）。因此：
> - 浅色下 demo 样式**逐像素可复现**；
> - 深色下**自动跟随**，无需写任何前景色覆盖（初版写了 5 个覆盖，实测全部冗余，已删）。

### B. 状态层（`static/js/composables/useWorkbench.js`）

新增（不改动 ISS-AP-04 冻结的 `ap*` 状态机）：

- `apLastDate` / `apMeta`（computed）—— 头部副标题「学员：X · 投诉日期：Y」，缺字段则整段省略。
- 视觉层：`AP_ICONS`（16 种内联 SVG，与 demo 的 `icon()` 同一套 path）+ `apIcon(kind,size)`、
  `apFtypeCls(name)`、`apFileSvg(name)`。**不用 Bootstrap Icons**：字形/字重与 demo 不同，
  且字体加载失败会退化成方框。
- 失败态视觉三分（均按后端结构化 `code` 分级）：`apErrTitle` / `apErrIconName` / `apErrStyle` /
  `apErrWho` / `apErrPrimaryText` / `apErrorPrimary()`。

### C. 同步

`static/js/app.js` 两处解构注入（L293-298 / L954-959）+ `useWorkbench.js` 的 return；
`useWorkbench.js?v=33→34`、`app.js?v=46→47`。

## 验收标准

- [x] 面板宽度 760px（不再被 `style.css:1670` 的 `.modal-content{max-width:600px}` 锁死）
- [x] `.m-head` padding `16px 20px`；`.m-foot` padding `11px 20px` + `#f8fafc` 底 + 左对齐
- [x] 工具栏按钮 `6px 12px`/圆角 8/13px；右侧视图切换为 ghost
- [x] 表格 th `9px 20px`、td `10px 20px`、行高统一 51px、四列溢出 0
- [x] 文件类型徽标 30×30 圆角 8、四色正确（docx `#e0ecff/#1d4ed8`、pdf `#fef2f2/#dc2626`、
      img `#fffbeb/#d97706`、txt `#ecfdf5/#16a34a`）
- [x] 失败态居中：56×56 圆形图标（`#fffbeb/#d97706`）+ h3 15.5px/600 + `.who` 徽标 +
      `.act` 居中 + `.err-detail` max-width 520px 左对齐
- [x] 深色主题下面板为深色（`--surface` 等自动跟随），非白板
- [x] 浏览器 E2E：`errors` 与 `console` 全空（干净会话从零计数）
- [x] 全量回归 `pytest tests/ -q` → **661 passed**
- [x] `node --input-type=module --check` 两个 JS 文件语法通过
- [x] `tmp/verify_frontend.mjs` 四项全绿（模板编译 0 error / 注入残留 0 / importmap 闭包完整 / 版本号已 bump）

## 顺带修掉的 2 个真缺陷

1. **视图切换选中态失效**：`.ap-modal button.ghost` 与 `.ap-modal button.on` 特异性相同
   （0,2,1），demo 里 `.on` 写在 `.ghost` **之前** → 选中态被洗成透明 + 次级灰，
   两个按钮长得一模一样，用户看不出当前在哪个视图。修法：把 `.ghost` 两行挪到 `.on` 之前。
   （demo 自身也有这个 bug，此处是有意偏离 demo。）
2. **失败态的「复制路径」是死按钮**：`app.py:_err_resolved()` 的失败响应只含
   `code`/`message`/`detail`，**不含 `dir`**，前端 `apDir` 在失败态恒为空 → 按钮恒置灰。
   修法：失败态只保留主按钮；路径仍在折叠的「技术细节」里（`pre` 带 `user-select:all` 可整体复制）。

## 与 demo 的有意偏差（3 处，均已在上文说明理由）

| # | 偏差 | 理由 |
|---|---|---|
| 1 | 圆角 16px 而非 demo 的 14px | 项目 `index.html:29` 把 `--radius-lg` 覆写为 16px，全站弹窗一致；2px 不可辨 |
| 2 | 状态块图标 24px（demo 为 16px）| demo 的 16px 落在 56px 圆里偏小；同体系内等比放大 |
| 3 | 视图切换选中态**可见**（demo 不可见）| demo 的 CSS 顺序 bug，见上 |

## 风险

- **低**：纯样式 + 新增只读 helper，不改接口契约、不改状态机。
- **低**：内联 `<style>` 体量由 2.5KB 增至 8.5KB（`index.html` 211KB → 219KB），可接受。
