# 投诉处理系统UI/UX优化计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 优化查询交互体验，修复进度条逻辑、状态提示、培训时长显示等问题

**Architecture:** 保持现有功能不变，仅优化前端交互逻辑和显示细节

**Tech Stack:** Vue 3 + Bootstrap 5 + 现有后端API

---

## Task 1: 修复查询进度条逻辑

**问题:** 点击查询后进度条不出现，查询完成后才显示100%

**Files:**
- Modify: `templates/index.html` - 查询按钮和进度条区域
- Modify: `static/js/app.js` - 查询逻辑和状态管理

**Step 1: 分析当前逻辑**

查看 `app.js` 中的 `doQuery` 函数，确认进度条显示时机

**Step 2: 修复进度条显示时机**

在点击"查询三系统"按钮时立即显示进度条，而不是等数据返回后才显示

```javascript
// 修改前: 进度条在数据返回后更新
// 修改后: 点击查询立即显示进度条0%，然后逐步更新
```

**Step 3: 添加系统状态实时更新**

每个系统查询开始时更新状态为"查询中"，完成后更新为"成功/失败"

**Step 4: 测试验证**

点击查询按钮，确认：
- 进度条立即出现（0%）
- 各系统状态实时更新
- 查询完成后进度条100%

**Step 5: Commit**

```bash
git add templates/index.html static/js/app.js
git commit -m "fix: 修复查询进度条显示时机，点击立即显示"
```

---

## Task 2: 优化第三系统状态提示

**问题:** 第三系统显示黄色警告，但没有说明原因

**分析:** 
- 黄色表示该学员在第三系统不存在（not_found）
- 这不是错误，只是该学员没有培训记录
- 需要明确告知用户原因

**Files:**
- Modify: `templates/index.html` - 系统状态标签显示
- Modify: `static/js/app.js` - 状态提示逻辑

**Step 1: 添加状态说明提示**

当第三系统返回"not_found"时，显示提示："该学员在计时培训平台无记录"

**Step 2: 修改状态标签样式**

- 成功：绿色 ✓
- 无记录：灰色 ℹ（不是黄色警告）
- 错误：红色 ✗（并显示错误原因）

**Step 3: 添加悬停提示**

鼠标悬停在状态标签上时显示详细说明

**Step 4: 测试验证**

查询学员 `110101199003070011`（唐嘉文），确认：
- 第三系统显示灰色"无记录"标签
- 悬停显示说明"该学员在计时培训平台无记录"

**Step 5: Commit**

```bash
git add templates/index.html static/js/app.js
git commit -m "fix: 优化第三系统状态提示，无记录显示灰色而非警告"
```

---

## Task 3: 完善培训时长显示

**问题:** 培训时长需要显示总时长和共享时长

**分析:**
- 第三系统返回的字段：
  - `training_time` = 平台总时长（实际培训时间）
  - `platform_time` = 上传省平台总学时（共享时长）
- 需要同时显示这两个数据

**Files:**
- Modify: `services/query_service.py` - 数据合并逻辑
- Modify: `templates/index.html` - 培训时长显示区域

**Step 1: 检查当前数据结构**

查看 `query_service.py` 中 `_merge_results` 如何处理培训时长

**Step 2: 修改数据结构**

为每个科目添加两个字段：
- `total_time` - 平台总时长
- `shared_time` - 上传省平台学时

**Step 3: 修改前端显示**

培训时长卡片显示：
```
┌─────────────────┐
│ 17时8分         │  ← 平台总时长（大字体）
│ 科目二          │
│ 共享: 13时4分   │  ← 上传省平台学时（小字灰色）
└─────────────────┘
```

**Step 4: 测试验证**

查询有培训记录的学员（如 `110101199003070011` 张晶晶），确认：
- 显示平台总时长
- 显示共享时长

**Step 5: Commit**

```bash
git add services/query_service.py templates/index.html
git commit -m "feat: 培训时长显示总时长和共享时长"
```

---

## Task 4: 优化整体UI细节

**问题:** 页面体验粗糙，需要专业优化

**Files:**
- Modify: `templates/index.html` - 整体布局和样式
- Modify: `static/css/style.css` - 样式优化

**Step 1: 优化查询按钮状态**

- 查询中：显示加载动画 + "查询中..."
- 查询完成：恢复原状
- 禁用重复点击

**Step 2: 优化信息卡片样式**

- 统一间距和字体大小
- 添加悬停效果
- 重要信息高亮

**Step 3: 优化标签页切换**

- 添加平滑过渡动画
- 当前标签更明显

**Step 4: 测试验证**

整体浏览页面，确认视觉体验改善

**Step 5: Commit**

```bash
git add templates/index.html static/css/style.css
git commit -m "style: 优化整体UI细节和交互体验"
```

---

## 验证清单

- [ ] 点击查询按钮，进度条立即显示0%
- [ ] 各系统状态实时更新（查询中/成功/无记录/失败）
- [ ] 第三系统无记录时显示灰色标签，有说明提示
- [ ] 培训时长显示总时长和共享时长
- [ ] 查询按钮有加载状态，防止重复点击
- [ ] 整体UI风格统一，交互流畅

---

## 注意事项

1. **不要修改现有功能逻辑** - 只优化UI/UX
2. **保持后端API不变** - 只修改前端展示
3. **保持数据流不变** - 只优化显示时机和样式
4. **所有改动可回滚** - 频繁提交git
