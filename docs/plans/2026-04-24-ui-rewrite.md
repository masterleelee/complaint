# 投诉处理系统UI全面整改计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 按照 everything claude code 规范，全面整改系统UI/UX

**Architecture:** 统一设计规范、字体、颜色、间距、交互模式

**Tech Stack:** Vue 3 + Bootstrap 5 + 自定义CSS规范

---

## 设计规范 (Design System)

### 字体规范
```css
--font-base: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
--font-mono: "SF Mono", Monaco, "Cascadia Code", "Roboto Mono", Consolas, monospace;

/* 字号层级 */
--text-xs: 0.75rem;    /* 12px - 辅助文字 */
--text-sm: 0.875rem;   /* 14px - 正文 */
--text-base: 1rem;     /* 16px - 默认 */
--text-lg: 1.125rem;   /* 18px - 小标题 */
--text-xl: 1.25rem;    /* 20px - 标题 */
--text-2xl: 1.5rem;    /* 24px - 大标题 */
```

### 颜色规范
```css
/* 主色 */
--color-primary: #2563eb;      /* 蓝色 */
--color-primary-light: #3b82f6;
--color-primary-dark: #1d4ed8;

/* 功能色 */
--color-success: #10b981;      /* 绿色 */
--color-warning: #f59e0b;      /* 橙色 */
--color-danger: #ef4444;       /* 红色 */
--color-info: #6b7280;         /* 灰色 */

/* 中性色 */
--color-gray-50: #f9fafb;
--color-gray-100: #f3f4f6;
--color-gray-200: #e5e7eb;
--color-gray-300: #d1d5db;
--color-gray-400: #9ca3af;
--color-gray-500: #6b7280;
--color-gray-600: #4b5563;
--color-gray-700: #374151;
--color-gray-800: #1f2937;
--color-gray-900: #111827;
```

### 间距规范
```css
--space-1: 0.25rem;   /* 4px */
--space-2: 0.5rem;    /* 8px */
--space-3: 0.75rem;   /* 12px */
--space-4: 1rem;      /* 16px */
--space-5: 1.25rem;   /* 20px */
--space-6: 1.5rem;    /* 24px */
--space-8: 2rem;      /* 32px */
```

### 圆角规范
```css
--radius-sm: 4px;
--radius-md: 6px;
--radius-lg: 8px;
--radius-xl: 12px;
```

---

## Task 1: 修复查询进度条

**问题:** 进度条显示逻辑错误

**Files:**
- Modify: `static/js/app.js` - 进度条状态管理
- Modify: `templates/index.html` - 进度条UI

**Step 1: 分析当前问题**

查看 `queryAll` 函数，确认进度条更新时机

**Step 2: 修复进度条逻辑**

- 点击查询立即显示进度条（0%）
- 每个系统开始查询时更新状态为"查询中"
- 每个系统完成时更新进度百分比
- 查询完成时显示100%

**Step 3: 测试验证**

点击查询，观察进度条是否正确显示

**Step 4: Commit**

```bash
git add static/js/app.js templates/index.html
git commit -m "fix: 修复查询进度条显示逻辑"
```

---

## Task 2: 重构AI分析合同模块

**问题:** 布局不直观，操作复杂

**Files:**
- Modify: `templates/index.html` - AI分析合同区域

**新布局设计:**

```
┌─────────────────────────────────────────────────────────┐
│  📄 AI分析合同                                           │
├─────────────────────────────────────────────────────────┤
│  合同来源                                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐             │
│  │ 自动下载 │  │ 手动上传 │  │ 手动填写 │             │
│  └──────────┘  └──────────┘  └──────────┘             │
├─────────────────────────────────────────────────────────┤
│  [下载合同] 或 [选择文件] 或 [填写表单]                  │
├─────────────────────────────────────────────────────────┤
│  分析设置                                                │
│  已缴费用: [________] 元    投诉类型: [下拉选择___]      │
│  退费原因: [多行文本输入________________]               │
├─────────────────────────────────────────────────────────┤
│  [开始AI分析]                                           │
├─────────────────────────────────────────────────────────┤
│  分析结果（分析完成后显示）                              │
│  ┌─────────────────────────────────────────────────┐   │
│  │ 应退金额: ¥3,500                                │   │
│  │ 扣费明细: ...                                   │   │
│  │ [查看详情] [重新分析]                           │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

**Step 1: 重新设计HTML结构**

**Step 2: 简化操作流程**

**Step 3: 测试验证**

**Step 4: Commit**

```bash
git add templates/index.html
git commit -m "refactor: 重构AI分析合同模块布局"
```

---

## Task 3: 统一字体和样式规范

**Files:**
- Modify: `static/css/style.css` - 添加CSS变量和规范
- Modify: `templates/index.html` - 应用新样式

**Step 1: 定义CSS变量**

在 style.css 顶部添加设计系统变量

**Step 2: 统一字体**

- 所有文字使用 --font-base
- 代码/数字使用 --font-mono

**Step 3: 统一字号**

- 标题: --text-xl
- 正文: --text-base
- 辅助: --text-sm
- 标签: --text-xs

**Step 4: 统一颜色**

- 主操作: --color-primary
- 成功: --color-success
- 警告: --color-warning
- 错误: --color-danger

**Step 5: 统一间距**

- 卡片内边距: --space-4
- 元素间距: --space-3
- 区块间距: --space-6

**Step 6: Commit**

```bash
git add static/css/style.css templates/index.html
git commit -m "style: 统一字体、颜色、间距规范"
```

---

## Task 4: 优化整体布局

**Files:**
- Modify: `templates/index.html` - 整体布局结构

**优化点:**

1. **步骤指示器** - 更清晰的1-2-3步骤流程
2. **信息卡片** - 统一的卡片样式
3. **标签页** - 更明显的激活状态
4. **按钮** - 统一的大小和颜色
5. **表单** - 统一的输入框样式

**Step 1: 优化步骤指示器**

**Step 2: 优化信息展示卡片**

**Step 3: 优化标签页样式**

**Step 4: 优化按钮样式**

**Step 5: 优化表单样式**

**Step 6: Commit**

```bash
git add templates/index.html static/css/style.css
git commit -m "style: 优化整体布局和组件样式"
```

---

## Task 5: 验证和测试

**Step 1: 功能测试**

- 查询流程正常
- AI分析合同正常
- 生成回复函正常
- 提交飞书正常

**Step 2: 视觉检查**

- 字体统一
- 颜色协调
- 间距一致
- 响应式正常

**Step 3: Commit**

```bash
git add -A
git commit -m "chore: UI整改完成，全面测试通过"
```

---

## 验收标准

- [ ] 查询进度条正确显示（0% → 33% → 66% → 100%）
- [ ] AI分析合同布局直观易用
- [ ] 所有字体统一（无混杂字号）
- [ ] 所有颜色统一（符合设计规范）
- [ ] 所有间距统一（符合设计规范）
- [ ] 整体视觉风格一致
- [ ] 所有功能正常工作
