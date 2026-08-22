# DESIGN.md — 学员投诉自动处理系统

> 版本：v1.0 · 2026-08-19
> 设计方向：**政务信赖蓝（Trust Blue）** —— 参考 Stripe 的色彩纪律与渐变签名、Linear 的高密度与极简克制，结合中文政务/驾校场景打磨。
> 强制原则：**所有视觉必须由本文件定义的 Token 驱动；禁止内联硬编码色值；禁止重复类定义；禁止裸 `.btn-*`（必须带 `.btn` 前缀）。**
> 适用：本系统是内部工单处理后台，用户为运营/法务人员，追求**专业、可信、高效、信息密度高且一目了然**。

---

## 1. Visual Theme & Atmosphere（视觉主题与氛围）

- **设计哲学**：以"可信赖的政务工具"为内核 —— 不花哨、不炫技，让数据说话。冷静的蓝色传递信任，克制的留白与清晰的分割线保证长时段操作的低疲劳。
- **视觉基调**：专业、稳重、清爽、秩序感。
- **核心视觉特征**：`信赖蓝主色` · `高密度信息布局` · `细分割线优先于阴影` · `强对比的状态语义色` · `一致的圆角节奏`。
- **光影与质感**：以 1px 细边框 + 极轻阴影为主（毛玻璃仅用于顶部导航/遮罩），避免重投影；表面以中性灰阶分层，而非靠深色块。

---

## 2. Color Palette & Roles（调色板与角色）

> 所有颜色必须用语义变量引用，**禁止**在 HTML 写死 HEX。

### Primary（信赖蓝）
| 角色 | HEX | CSS 变量 | 用途 |
|------|-----|----------|------|
| Primary 600（主色） | `#2563EB` | `--color-primary` | 主按钮、激活态、链接、重点数据 |
| Primary 700（深） | `#1D4ED8` | `--color-primary-dark` | 主按钮 hover、主色文字 hover |
| Primary 50（浅底） | `#EFF6FF` | `--color-primary-50` | 选中卡片底、聚焦光环、图标浅底 |

### Brand & Gradient（品牌渐变签名）
| 角色 | 值 | CSS 变量 | 用途 |
|------|-----|----------|------|
| 侧边栏/签名渐变 | `#1E3A8A → #2563EB` | `--brand-gradient` | 侧边栏背景、主视觉签名条 |
| 品牌强调（靛） | `#4F46E5` | `--color-brand-accent` | 渐变中段、特殊高亮 |

> 修正：原侧边栏末端 `#1e3a8a` 与主色 `#2563eb` 不连贯 → 统一为 `--brand-gradient`，保证品牌色前后一致。

### Semantic（语义色 —— 状态必须醒目）
| 角色 | HEX | CSS 变量 | 用途 |
|------|-----|----------|------|
| Success | `#16A34A` | `--color-success` | 已完成/成功/通过 |
| Success 50 | `#F0FDF4` | `--color-success-50` | 成功浅底 |
| Warning | `#D97706` | `--color-warning` | 待处理/警示/更正 |
| Warning 50 | `#FFFBEB` | `--color-warning-50` | 警示浅底 |
| Danger | `#DC2626` | `--color-danger` | 失败/过期/风险/删除 |
| Danger 50 | `#FEF2F2` | `--color-danger-50` | 危险浅底 |
| Info | `#2563EB` | `--color-info` | 信息提示（复用主色） |

### Neutral（中性灰阶 —— 以 slate 为基底）
| 角色 | HEX | CSS 变量 | 用途 |
|------|-----|----------|------|
| 文字主色 | `#0F172A` | `--color-text` | 正文、标题 |
| 文字次色 | `#64748B` | `--color-text-secondary` | 标签、辅助说明 |
| 边框 | `#E2E8F0` | `--color-border` | 分割线、卡片描边 |
| 表面底 | `#F8FAFC` | `--color-surface-50` | 卡片内浅底、斑马纹 |
| 页面底 | `#EEF3F8` | `--color-bg` | 页面背景 |
| 纯白表面 | `#FFFFFF` | `--color-surface` | 卡片/弹窗背景 |

### Shadow（阴影色）
- `--shadow-sm`: `0 1px 2px rgba(15,23,42,.06), 0 1px 3px rgba(15,23,42,.04)`
- `--shadow-md`: `0 4px 12px rgba(15,23,42,.08)`
- `--shadow-lg`: `0 12px 32px rgba(15,23,42,.12)`

---

## 3. Typography Rules（排版规则）

### Font Family
- `--font-sans`: `"PingFang SC", "Microsoft YaHei", "Hiragino Sans GB", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`
- 等宽（身份证/单号）：`--font-mono`: `"SF Mono", "JetBrains Mono", Menlo, Consolas, monospace`

> 修正：原字体栈无中文优化 → 首位加入 `PingFang SC` / `Microsoft YaHei`，中文渲染更扎实。

### Type Scale（从 Display 到 Nano）
| 级别 | 字号 | 字重 | 行高 | 字距 | 用途 |
|------|------|------|------|------|------|
| Display | 28px | 700 | 1.25 | -0.01em | 页面主标题（可选） |
| H1 | 20px | 700 | 1.3 | 0 | 区块大标题 |
| H2 | 16px | 600 | 1.4 | 0 | 卡片标题、页标题 |
| H3 | 14px | 600 | 1.45 | 0 | 小标题、表头分组 |
| Body | 13px | 400 | 1.6 | 0 | 正文（默认） |
| Body-sm | 12px | 400 | 1.5 | 0 | 辅助说明、空状态 |
| Label | 12px | 600 | 1.4 | 0 | **表单标签（中文不加 uppercase）** |
| Nano | 11px | 600 | 1.3 | 0.02em | 徽章、角标 |

> 修正：原 `.form-label` 使用 `text-transform:uppercase` —— **中文标签禁止大写**，仅英文/缩写保留。全局行高 1.6，数据表内用 1.45 紧凑化。

---

## 4. Component Stylings（组件样式）

### Buttons（**单一体系，唯一真相**）
> 强制：所有按钮 = `.btn` + 变体。**禁止**裸 `.btn-primary` / `.btn-outline`（原两套冲突的根源）。圆角统一 `--radius`（8px），padding 统一 `8px 16px`（sm `6px 12px`，lg `10px 22px`）。

```css
.btn {
  display: inline-flex; align-items: center; gap: 6px;
  font: 600 13px/1 var(--font-sans);
  padding: 8px 16px; border-radius: var(--radius);
  border: 1px solid transparent; cursor: pointer;
  transition: background .15s, box-shadow .15s, transform .1s;
}
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; }

.btn-primary   { background: var(--color-primary); color:#fff; border-color: var(--color-primary); }
.btn-primary:hover:not(:disabled){ background: var(--color-primary-dark); box-shadow: var(--shadow-md); }
.btn-secondary { background:#fff; color: var(--color-text); border-color: var(--color-border); }
.btn-secondary:hover:not(:disabled){ background: var(--color-surface-50); border-color: var(--color-text-secondary); }
.btn-ghost     { background: transparent; color: var(--color-primary); }
.btn-ghost:hover:not(:disabled){ background: var(--color-primary-50); }
.btn-danger    { background: var(--color-danger); color:#fff; border-color: var(--color-danger); }
.btn-danger:hover:not(:disabled){ background:#B91C1C; }
.btn-sm { padding: 6px 12px; font-size: 12px; }
.btn-lg { padding: 10px 22px; font-size: 14px; }
```

### Cards
```css
.card { background: var(--color-surface); border: 1px solid var(--color-border);
        border-radius: var(--radius-lg); box-shadow: var(--shadow-sm); }
.card-header { padding: 12px 16px; border-bottom: 1px solid var(--color-border);
               font: 600 14px/1.4 var(--font-sans); display:flex; align-items:center; gap:8px; }
.card-body { padding: 16px; }
```

### Inputs
```css
.form-label { display:block; font: 600 12px/1.4 var(--font-sans); color: var(--color-text-secondary); margin-bottom:6px; }
.form-control { width:100%; padding:8px 12px; font-size:13px; color: var(--color-text);
                border:1.5px solid var(--color-border); border-radius: var(--radius); background:#fff;
                transition: border-color .15s, box-shadow .15s; }
.form-control:focus { outline:none; border-color: var(--color-primary); box-shadow: 0 0 0 3px var(--color-primary-50); }
```

### Navigation（侧边栏）
```css
.sidebar { width: 240px; background: var(--brand-gradient); color:#fff; }
.sidebar .nav-link { color: rgba(255,255,255,.78); padding:10px 16px; border-radius:8px; }
.sidebar .nav-link.active { background: rgba(255,255,255,.16); color:#fff; box-shadow: inset 3px 0 0 var(--color-brand-accent); }
.sidebar .brand { font: 700 15px/1.3 var(--font-sans); }
.sidebar .footer { color: rgba(255,255,255,.62); }  /* 修正：原 .4 对比度不足 */
```

### Badges / Tags
```css
.badge { display:inline-flex; align-items:center; gap:4px; padding:3px 10px;
         font: 600 11px/1.3 var(--font-sans); border-radius: 999px; border:1px solid transparent; }
.badge-pending   { background: var(--color-warning-50); color: var(--color-warning); border-color:#FDE68A; }
.badge-processing{ background: var(--color-primary-50); color: var(--color-primary); border-color:#BFDBFE; }
.badge-completed { background: var(--color-success-50); color: var(--color-success); border-color:#A7F3D0; }
```

### Tables
```css
.table { width:100%; border-collapse:collapse; font-size:13px; }
.table th { padding:10px 14px; text-align:left; font:600 12px/1.4 var(--font-sans);
            color: var(--color-text-secondary); background: var(--color-surface-50);
            border-bottom: 2px solid var(--color-border); }
.table td { padding:10px 14px; border-bottom:1px solid var(--color-border); }
.table tbody tr:hover { background: var(--color-surface-50); }
```

### Modal / Dialog
```css
.modal-overlay { background: rgba(15,23,42,.5); }
.modal-content { background:#fff; border-radius: var(--radius-lg); box-shadow: var(--shadow-lg); }
```

### Empty State（**统一组件，消除 4 处手写重复**）
```css
.empty-state { display:flex; flex-direction:column; align-items:center; justify-content:center;
               gap:8px; padding:32px; color: var(--color-text-secondary); }
.empty-state .icon { font-size:28px; color: var(--color-border); }
.empty-state .text { font-size:12px; }
```

### Stepper（流程步进器 —— 5 步工单流）
```css
.stepper { display:flex; align-items:center; gap:8px; }
.step .dot { width:36px; height:36px; border-radius:50%; display:flex; align-items:center; justify-content:center;
             background:#fff; border:2px solid var(--color-border); color: var(--color-text-secondary); font-weight:700; }
.step.active .dot { background: var(--color-primary); border-color: var(--color-primary); color:#fff; }
.step.done .dot { background: var(--color-success); border-color: var(--color-success); color:#fff; }
.step-line { flex:1; height:2px; background: var(--color-border); }
.step-line.done { background: var(--color-primary); }
```

### Toast（反馈）
```css
.toast { padding:12px 16px; border-radius:10px; box-shadow: var(--shadow-lg); color:#fff; font-size:13px; }
.toast-success { background: var(--color-success); }
.toast-danger  { background: var(--color-danger); }
.toast-info    { background: var(--color-primary); }
```

---

## 5. Layout Principles（布局原则）

- **Spacing System（4px 基数）**：`--sp-1:4px --sp-2:8px --sp-3:12px --sp-4:16px --sp-5:20px --sp-6:24px --sp-8:32px`。禁止使用未列于此的随意数值。
- **Grid**：12 列，列间距 `--sp-4`（16px）。
- **Container**：`max-width: 1440px`；主区 `padding: 24px 32px`。
- **Sidebar**：固定 `240px`（原 220px 略增，容纳中文导航）。
- **Section Spacing**：区块间距 `--sp-6`（24px）。
- **留白哲学**：以 1px 细分割线划分信息组，避免大面积色块；卡片内边距统一 16px，保证密集数据下的呼吸感。

---

## 6. Depth & Elevation（深度与层级）

- **Shadow System**：`--shadow-sm`（卡片静止）/ `--shadow-md`（hover、主按钮）/ `--shadow-lg`（弹窗、toast）。
- **Surface Layers**：`--color-bg`（页面）→ `--color-surface-50`（组内浅底）→ `--color-surface`（卡片）→ overlay（遮罩）。
- **Z-index Scale**：`sidebar:1000` / `modal:2000` / `toast:3000`。
- **Backdrop**：遮罩 `rgba(15,23,42,.5)`；导航可保留轻渐变，不额外加毛玻璃以免杂乱。

---

## 7. Do's and Don'ts（设计规范与禁忌）

### Do's
1. 所有颜色走语义变量（`--color-*`），不写死 HEX。
2. 按钮统一 `.btn` + 变体；聚焦态统一 `:focus-visible`。
3. 间距只用语令标尺（`--sp-*`）。
4. 表单标签中文不加 uppercase；字号/字重遵循 Type Scale。
5. 空状态用 `.empty-state` 组件，不手写。
6. 状态用语义色徽章，颜色与含义一一对应。
7. 中文优先 `PingFang SC / Microsoft YaHei` 字体栈。

### Don'ts
1. ❌ 禁止内联 `style="color:#xxx"` 等硬编码（原第 156/190/220 行等问题）。
2. ❌ 禁止裸 `.btn-primary` / `.btn-outline`（导致与原 `.btn` 体系冲突）。
3. ❌ 禁止同一类重复定义（原 `.btn-primary`×2、`.navbar-footer`×2）。
4. ❌ 禁止 `.form-label` 使用 `text-transform:uppercase` 作用于中文。
5. ❌ 禁止新增"补丁段"样式；新组件须归入对应模块注释区。
6. ❌ 禁止低对比度文本（侧边栏页脚不低于 `.62`）。
7. ❌ 禁止在数据表中使用过大行高（统一 1.45）。

---

## 8. Responsive Behavior（响应式行为）

| 断点 | 宽度 | 规则 |
|------|------|------|
| Mobile | ≤640px | 侧边栏转为顶部横向导航；主区 `padding:16px 12px`；栅格降为 1–2 列 |
| Tablet | 641–1024px | 侧边栏保留；查询表单 6 列 → 3 列 |
| Desktop | 1025–1440px | 标准布局 |
| Wide | >1440px | 内容居中 `max-width:1440px` |

- **Touch Targets**：最小 44×44px。
- **折叠策略**：tables 在 ≤640px 转为卡片式（data-label 重排）；`.query-form` 在 tablet 降列。
- **Font Scaling**：移动端 Body 保持 13px，标题 H2 16px 不变。

---

## 9. Agent Prompt Guide（AI 代理提示指南）

### Quick Reference
- 主色 `--color-primary:#2563EB`；中性 slate 基底；语义色 success/warning/danger 必须醒目。
- 按钮永远 `.btn .btn-*`；间距永远 `--sp-*`；颜色永远 `--color-*`。
- 中文标签不加 uppercase；字体栈首位中文。

### Component Prompts（可直接复制）
1. "生成一个 `.btn .btn-primary` 主操作按钮，文案『开始查询』，带 Bootstrap Icon `bi-search`。"
2. "用 `.card` + `.card-header` + `.card-body` 包裹一个查询结果区，内部用 `.info-grid`（4 列）展示学员字段。"
3. "渲染 `.table` 退费明细表，表头 sticky，hover 行高亮。"
4. "用 `.stepper` 展示 5 步工单流：合同获取→退费分析→沟通记录→结果归档/文档→飞书(可选)，当前在第 3 步。注：受理与「查询三系统」是受理页的独立前置动作，不计入此 stepper。"
5. "用 `.empty-state` 渲染『暂无培训记录』空状态，图标 `bi-inbox`。"
6. "用 `.badge .badge-warning` 标记『待处理』状态。"

### Iteration Guide（迭代建议）
1. 任何改动先确认是否复用现有 Token，缺 Token 先补到 `:root`，不临时写 HEX。
2. 新增组件必须带对应 CSS 注释分区，杜绝补丁段。
3. 每次提交前用 grep 检查内联 `style="color` / 裸 `.btn-` 是否出现。
4. 响应式先验证 ≤640px 与 1024px 两档。
5. 颜色对比度用 WCAG AA 自测（正文 ≥4.5:1，大字 ≥3:1）。
6. 状态语义色严格对应（绿=成功/通过，黄=待处理/警示，红=失败/风险）。
7. 保持信息密度：后台工具宁紧凑勿空旷，但留白靠分割线而非大片色块。
8. 字体栈首位中文，避免 Windows 下中文发虚。
9. 不要为了"好看"加重投影或渐变块，信赖感来自秩序而非装饰。
10. 改完用本文件第 7 节 Don'ts 做自检清单。
