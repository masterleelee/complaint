# 设置页 Round 2 · 布局重排（方案 A） + 自动保存 · spec

> 用户 2026-09-22 21:04 批准组合：**A（吸顶页头 + 左分区导航 + 右双列网格） + 自动保存 + 智能默认折叠**。
> demo 已评审：`demo/settings-layout-v6-demo.html`（含方案 A/B 对照、保存策略、折叠策略、深浅色）。
> 上一轮（折叠/分区/只读化）见 `.scratch/settings-redesign/spec.md`；本轮**不推翻**其红线，只在其上重排。
>
> **状态：已落地并通过全部验证**（见 §8）。代码评审（§11）又揪出 1 条真实竞态缺陷，已修并加护栏。

## 1. 用户诉求与判定

| # | 原话要点 | 判定 |
|---|---|---|
| U1 | 「上面两张卡的改动由这一个按钮一并保存」很不合理、一般系统不用这样复杂的单独保存按钮 | 改为**自动保存**：改动即写盘，页头常显状态。**删除**横跨两卡的常驻保存条 |
| U2 | 右栏排版反人类、要不断滚动、明明还有很多空间 | 内容区上限 860px → 1440px；单列堆叠 → **双列网格**；宽表格卡跨两列 |
| U3 | 新手一点不懂「能改什么」 | **左分区导航＝目录**（6 项全列出 + 状态徽章）+ **设置项搜索框** + 折叠态摘要常显 |
| U4 | 先做 demo 再改代码 | 已执行：demo 经用户确认后才动生产代码 |

**U1 的实证**：`data/config.json` 里**没有 `cloud_ocr` 段**，但用量台账显示百度成功调用过 1 次
→ 用户填的密钥只活在浏览器表单里，没点保存、刷新即丢。自动保存同时根治这个数据丢失路径。

## 2. 有意变更（非「零功能变更」，逐条列出）

1. **保存时机**：手动点「保存配置」→ 改动后 900ms 防抖自动 `PUT /api/config`；失败保留脏标记并提供重试。
2. **新增设置项搜索**（纯前端过滤卡片，不改任何配置）。
3. **默认折叠**：`llm` 展开 → 改为 `llm` + `ocr` 均展开（另 4 张折叠）。
4. **折叠偏好存储键** `settings.cards.collapsed.v1` → **`.v2`**（一次性重置旧偏好，否则老用户看不到新的默认态）。
5. **「全部展开 / 全部折叠」按钮**从页首工具条移到左导航底部（功能保留）。
6. **profile 卡改整宽横条**（`span2`，4 列只读字段），消除半栏空洞；仍是可折叠卡。
7. **文本/数字输入框的保存触发从 `@change` 改为 `@input`**（见 §5 说明）—— 落地时发现的真实丢数据路径。
8. **新增 `.save-pill` 组件**（页头保存状态胶囊），并在 DESIGN.md 显式登记为 `.btn` 体系之外
   的第二个合法例外（见 §11 与 DESIGN.md「Save Pill」节）。

## 3. 零功能变更保留清单（红线，逐条可验）

1. 字段全集不变：`cfg.llm.{model,api_url,api_key,max_tokens}`、`cfg.cloud_ocr.{enabled,order,providers.*}`。
2. 接口不变：本轮**未新增**任何后端接口（新增 `GET /api/config/llm/openrouter-models` 属**其他会话**在途改动，
   不在本轮范围，见 §12）。
3. 提交语义不变：仍是 **一次 PUT 提交整个 `cfg`**；密钥「值含 `•` 即未改动」的还原在前端保持不变；
   密钥框**不做 trim / 清空判断**。
4. 调度逻辑不变：`services/cloud_ocr/` 一行不改；导航徽章与链路预览仍是**前端只读派生**。
5. 条件可见性不变：`我的资料` 所有人可见；`用户管理`/`历史处理人映射` 仅 admin；`一键重挂` 对 viewer 隐藏。
6. 节流与守卫不变：`SMB_MANUAL_COOLDOWN=30`、`smbGuardNote` 四种文案、`toggleStatus`/`deleteAliasRow` 的原生 confirm。
7. 所有按钮的 `@click` 处理函数与 `:disabled` 条件逐条保留（含「测试连接」仍用当前表单值、不依赖保存）。
8. 上一轮既有的静态护栏必须继续全绿。

## 4. 布局规约（实现口径）

```
section.fade-in
├─ .set-head                    吸顶(top:0)，高 ~56px：h1 + 搜索框 + 保存状态 pill
└─ .set-wrap                    grid: 212px | 1fr，max-width:1440px
   ├─ nav.set-nav               吸顶(top:74px)，分区组 + 6 个导航项（图标/短名/状态徽章）+ 全部展开|折叠
   └─ div
      ├─ h2.set-sec ×3          分区标题（在网格之外，整行）
      └─ .set-grid ×3           grid: repeat(2,minmax(0,1fr))；.span2 跨两列
```

- 分组：AI 能力 = `llm(1) | ocr(1)`；存储与归档 = `smb(2)`；账号与权限 = `profile(2) / users(2) / alias(2)`。
- 卡片不再有 `margin-top`（间距交给 grid `gap:16px`）；`scroll-margin-top:88px` 抵消吸顶遮挡。
- 卡片头保持上一轮结构：`<button class="ch-toggle" aria-expanded aria-controls>` + `.ch-actions`；**不嵌按钮**。
- 折叠按钮内新增 `.savedot`（仅该卡有未保存改动时出现），非交互元素、非 button。
- ≤1180px 退化：单列 + 导航横排换行 + `span2` 失效（`grid-column:span 1`）。

## 5. 自动保存规约

| 事件 | 行为 |
|---|---|
| 文本/数字输入框 `@input` | `mark('llm'|'ocr')`：置脏 + 记改动序号 + 重置 900ms 防抖 |
| `<select>` / `<input type=checkbox>` `@change` | 同上 |
| OCR 厂商上移/下移 `@click` | 同上（改写 `providers` 数组顺序） |
| 防抖到期 | `PUT /api/config`（整个 cfg）→ 成功清脏、记录 `hh:mm` |
| 成功 | pill = 绿「已保存 · hh:mm」；**只清已被本次请求覆盖的键**（见下） |
| 失败 | pill = 红「保存失败，点此重试」（唯一可点击态）+ toast 一次；脏标记保留 |
| 进入页面 `loadCfg()` | pill = 灰「配置已加载」（本会话尚未保存过）；清脏 |
| 切换视图离开设置页 | 若有脏项 → **立即 flush**（不等防抖到期），然后解绑 scroll-spy |
| 测试连接 / 刷新用量 / 刷新状态 / 一键重挂 / 用户增删改 | **不**改 cfg，**不**参与自动保存 |

设计要点：保存是**延迟读取整个 cfg**，所以 v-model 与 mark 的执行先后不影响提交内容；
`mark` 只置脏与起定时器，不做 diff。

**为什么文本输入框用 `@input` 而不是 `@change`**（落地修正）：
`@change` 只在**失焦**时触发。「填完 API Key → 直接 `Cmd+R` 刷新 / 关标签页」这条路径上
blur 不保证发生，change 也就不触发 → 自动保存从未排队 → 改动静默丢失。
这正是 §1 里那个「填了密钥刷新即丢」的原始症状，只靠 `@change` 并没有真正堵住。
`mark()` 自带 900ms 防抖，所以 `@input` 不会把每次击键变成一个 PUT。
`<select>` / checkbox 保留 `@change`：点选即触发，不存在未失焦空窗。

**成功时只能清「已被本次请求覆盖」的键**（评审修正，见 §11）：
每次 `mark()` 自增全局序号 `_rev`，并记 `dirtyRev[key] = _rev`；`saveCfg()` 发请求前快照
`myRev = _rev`，成功时只清 `dirtyRev` 里序号 ≤ `myRev` 的键。否则「请求在途时又改动」
会连同新改动一起被清脏 —— 页头显示「已保存」而改动尚未落盘，用户切走再进来即被
`loadCfg()` 覆盖，**永久丢失**。

## 6. 验收指标（E2E 量测，`tmp/verify_settings_v6.mjs`）

| 指标 | 改前（Round 1.1 实测） | 目标 | 实测（2026-09-22 21:31） |
|---|---|---|---|
| 文档滚动倍数 1618×900（默认态） | 3.36×（≈3025px） | ≤ 1.40× | **1.39×**（1250px）✓ |
| 文档滚动倍数（全折叠） | 3.36× | — | **1.00×**（900px，一屏装下）✓ |
| 文档滚动倍数（全展开） | 3.36× | — | 2.06×（1850px） |
| 内容区宽度上限 | 860px（占屏 ~53%） | 1440px | **1330px / 上限 1440px** ✓ |
| 内容区列数 | 1 | 2 | **2 / 2 / 2**（每轨 541px）✓ |
| 4 张跨列卡宽度 | — | 撑满整行 | 1098/1098px ×4 ✓ |
| 改动 → 写盘（**未失焦**） | 需手动点按钮 | ≤ 2s 自动落盘 | 2.4s 内 `config.json` 已更新 ✓ |
| **保存在途时又改动** | — | 脏标记不得被误清 | PUT#1 落地后 pill 仍为「有未保存改动」+ 1 个 `.savedot` ✓（旧实现此处显示「已保存」/0 圆点，已 A/B 复现） |
| 设置项搜索 | 无 | 命中卡数 < 6 且命中卡强制展开 | 1/6，命中卡 `aria-expanded=true` ✓ |
| 左导航吸顶 | 无 | 滚动后 `top` 不变且仍在视口 | 24 → 0（钉在 sticky 偏移）保持到底 ✓ |
| 智能默认折叠态 | 仅 `llm` 展开 | `llm`+`ocr` 展开，其余 4 张折叠 | 实测一致 ✓ |
| ≤1180px 退化 | — | 单列 | 1100px 下 1 列 ✓ |
| console 错误 | 0 | 0 | **0** ✓ |
| 静态护栏 | — | 全绿 + 新增 | `tests/test_settings_page_guards.py` **22 passed**（本轮新增 9 条）✓ |

E2E 合计 **26/26 通过**。

## 7. 影响面

| 文件 | 改动 |
|---|---|
| `templates/index.html` | 设置页样式块整体替换（`/* ===== 系统设置页` … `/* 新增投诉 · 悬浮操作栏` 之间）；设置页 section 整段原子替换（走 `tmp/patch_settings_v6.py`）；5 个输入框 `@change`→`@input`（`tmp/patch_settings_v6b_input_mark.py`）；修 6 处顶层 ref 误写 `.value`（`tmp/patch_settings_v6c_toplevel_ref.py`）；`useSettings.js?v=31 → 32` |
| `static/js/composables/useSettings.js` | `useSettings` 增自动保存（`mark/dirtyKeys/saveState/dirtyRev/_rev/_clearDirty`）；`saveCfg` 按序号清脏；`useSettingsLayout` 增分区/搜索/scroll-spy/默认折叠 v2 + LS 键 bump |
| `static/js/app.js` | 增 `setNavBadge()`；setup return 补新符号（**每个必须独立成行**）；切到 settings 时 `layout.bindSpy()`、离开时 flush + `unbindSpy()` |
| `tests/test_settings_page_guards.py` | **本轮新增 9 条**（文件 13 → **22** 条） |
| `DESIGN.md` | 新增「Save Pill」节，登记 `.save-pill` 为 `.btn` 体系之外第二个合法例外（含 4 条硬约束）；「Disclosure Header」的"唯一例外"措辞改为"之一" |
| `demo/settings-layout-v6-demo.html` | 评审用原型（已交付用户确认） |
| `tmp/verify_settings_v6.mjs` | E2E 量测 26 项断言 + 截图 |
| `tmp/verify_settings_v6_mutations.py` | **16 个**变异用例（护栏有效性证明） |
| `tmp/make_settings_review_diff.py` | 从混有他会话改动的工作区里切出本轮范围的 diff，供 code-review 使用 |

**本轮未新增任何间距令牌消费**：`--sp-*` 在 `static/css/style.css` 里只定义 1 个、
使用 **0 次**，HEAD 版设置页 CSS 也是 0 次 —— DESIGN.md Do's #3「间距只用语令标尺」
事实上全仓库未执行。只改本轮这一块会造出孤岛，故**不单独改**，作为独立议题记录（§11）。

## 8. 验证计划（缺一不可）· 执行结果

1. ✅ `env -u PYTHONPATH venv/bin/python3 -m pytest tests -q` → **994 passed**（基线 972）。
2. ✅ 重启 5003（PID 60719 → 61502，LaunchAgent 自愈）→ E2E 量测 **26/26 通过** + 截图 `tmp/settings_v6_1618.png`。
3. ✅ 护栏变异验证 `tmp/verify_settings_v6_mutations.py` → **16/16 全部拦截**，跑完自动还原并复验基线。
4. ✅ `/code-review` Standards + Spec 双轴 → 结论与处置见 §11。
5. ⏳ 单独 commit —— **遇阻，待用户决策**（§12）。

## 9. 与上一轮文档的关系

- 上一轮 §3「默认态 = 只展开大模型」的结论**被本轮取代**：当时为满足「≤1.4 屏」而收缩默认态；
  双列布局后内容高度大幅下降，展开 `llm` + `ocr` 实测仍达标（见 §6），故恢复「智能默认」。
- 上一轮列为 Round 2+ 的 **A4「未保存改动提示与离开拦截」**：本次以自动保存落地其前半段；
  **不做**离开拦截（自动保存后无「未保存就离开」的窗口，拦截反而多余）。

## 10. 落地过程中发现的 P0：模板对 setup 顶层 ref 误写 `.value`

**症状**：点「系统设置」后内容区**直接消失**，无任何提示。console 里是
`TypeError: Cannot read properties of undefined (reading 'llm')`，来自 Vue 的 `Proxy.render`。

**根因**：setup 返回对象里的**顶层** ref/computed 在模板中会被 Vue **自动解包**，
所以模板里的 `dirtyKeys.value` 求值为 `undefined`，紧接着的 `.llm` 抛错 →
该 `<section>` 的 render 整体失败。本轮共引入 6 处（`dirtyKeys.value.llm/ocr`、
`saveCls.value`、`saveRetryable.value`、`saveIcon.value`、`saveText.value`）。

**为什么单测抓不到**：pytest 全绿、`node --check` 通过、静态正则全绿 —— 旧断言甚至
把 `saveText.value` 当成规范钉住了（"页头保存状态没有接上 saveText.value"）。
**只有真实浏览器渲染会炸**，是 E2E 抓到的。

**与既有坑的关系**：项目记忆里「普通对象内嵌 computed 不解包 → 整页白屏」是同一类，
区别只在嵌套位置：
- 顶层返回的 ref → 模板里**直接写名字**（`dirtyKeys` / `saveCls` / `saveText`）
- 挂在普通对象上的 ref → 模板里**必须写 `.value`**（`smb.smbLoading.value`、`users.list.value`）

**护栏**：`test_template_never_dot_values_a_top_level_setup_symbol` —— 解析 `app.js`
setup 返回块的全部 476 个顶层符号，断言模板里没有 `sym.value`。实测在 233k 字符模板上
**零误报**（`layout.query.value` 这类合规写法不受影响，因为正则要求 `.value` 紧跟符号）。
变异用例 M9/M10 证明它确实会 FAIL。

## 11. `/code-review` 双轴结论与处置

### Standards 轴

| 发现 | 判定 | 处置 |
|---|---|---|
| `.save-pill` 不是 `.btn`，违反 DESIGN.md §4「所有按钮 = `.btn` + 变体」 | **硬违规成立** | **已在 DESIGN.md 登记为例外**（新增「Save Pill」节 + 4 条硬约束），照 `.ch-toggle` 的先例；`html_source`/`DESIGN.md` 双向绑定护栏 `test_save_pill_is_registered_and_readable` |
| 新增样式块零 `var(--sp-*)`，违反 §5「间距只用语令标尺」 | 硬违规成立，但**全仓库一致如此** | **不单独改**：`style.css` 定义 1 处、使用 0 次，HEAD 版设置页 CSS 也是 0 次。只改一块会造孤岛 → 记为独立议题（建议单独一次全仓库间距令牌化） |
| `.chip` 与 `.badge` 同类重复定义（§7 Don'ts #3） | **误报** | `.chip` 在 HEAD 已存在 8 处，属既有问题，非本轮引入 |
| `.ocr-toggle{min-width:150px}` 与 DESIGN.md 登记的 `200px` 不符 | 判断性 | 与本轮无关（上一轮已有），记录待清 |
| smell：`saveText/saveCls/saveIcon/saveRetryable` 四处对 `saveState` 重复 switch | 判断性 | **暂不改**：合并成映射表要动 4 个已被护栏断言独立成行的符号，收益（~6 行）小于改动面。记为待办 |
| smell：`setNavBadge()` 的 `switch(key)` 与 `SETTINGS_CARDS` 键集重复（加卡要改三处） | 判断性 | **暂不改**：徽章要读 `cfg/cloudOcr/smb/users` 四个来源，塞进注册表会让展示层反向依赖业务 composable（见 app.js 注释的设计取舍）。已把注册表注释里的"只改这里"改为准确表述 |
| smell：`mark(key)` 命名含糊 | 判断性 | **不改**：`mark` 已在 20+ 处模板与 9 条护栏中引用，改名收益不抵改动面 |
| smell：`saveErr` 已 return 但 app.js 未解构、模板未用 | 判断性 | **保留**：失败态的 toast 内容取自 `saveErr`（`saveCfg` 内部消费），导出供后续统一错误面板用；若确认无用可删 |

### Spec 轴

| 发现 | 判定 | 处置 |
|---|---|---|
| **保存成功无条件清脏** → 「请求在途时又改动」会被误标为已保存，切走再进来即被 `loadCfg` 覆盖，永久丢失 | **真实缺陷（确认）** | **已修**：引入 `_rev`/`dirtyRev` 按序号清脏 + `_clearDirty()` 收口；护栏 `test_save_success_only_clears_keys_the_request_covered`；E2E 新增确定性竞态用例（路由挂住 PUT）+ A/B 反证旧实现必失败 |
| §6 实测列写「20 条（19 passed 文件内）」与 §7「文件共 19 条」自相矛盾 | 文档错误（确认） | **已修**：统一为「文件 22 条 / 本轮新增 9 条」 |
| 新增 `GET /api/config/llm/openrouter-models` + `#s_or_q` OpenRouter UI，违反 §3.2/§3.3「接口不变」 | **误报** | 属**其他会话**在途改动（`app.py` 本轮未改；`openrouter-models` 在 HEAD 不存在）。被 hunk 过滤 diff 误收，见 §12 |
| API Key 的「显示/隐藏」按钮不在 §2 变更清单内 | **误报** | 同上，既有功能（HEAD 已有 `keyVisible`），仅被 section 整段替换顺带带出 |
| 一键重挂按钮由 `smbRemounting` 改为 `!smbCanRemount`、文案加「请稍候 Ns」/「间隔 30 秒」 | **误报** | 属**其他会话**的 SMB 冷却改动（`smbCanRemount` 在 HEAD 不存在） |

## 12. 待用户决策：commit 边界

工作区里 `templates/index.html` / `static/js/app.js` / `static/js/composables/useSettings.js`
**同时含有其他会话的在途改动**，且与本轮改动**同一文件、部分同一 hunk**：

| 证据 | 说明 |
|---|---|
| `static/js/app.js` diff 共 23 个 hunk，其中 `@@ -595,200 +654,0 @@` 是一段约 **200 行的删除** | 非本轮所为 |
| `grep -c smbCanRemount` → HEAD **0** / 工作区 **有** | SMB 冷却改动（他会话） |
| `grep -c openrouter-models app.py` → HEAD **0** / 工作区 **1** | OpenRouter 后端路由（他会话） |
| `git status` 另有 `app.py` / `config.py` / `database.py` / `services/*` / `tests/conftest.py` 等 20+ 文件改动 | 均非本轮 |

**本轮实际改动 = 4 个文件**：`templates/index.html`、`useSettings.js`、`app.js`、`tests/test_settings_page_guards.py`（+ 文档 `DESIGN.md`、spec/demo/tmp 脚本）。
但前 3 个文件**无法整文件 `git add`**，否则会把其他会话未完成的改动一并提交。
按 hunk 切分则因「设置页 section 是整段替换」而把该段内他会话的改动（如 `smbCanRemount`、
OpenRouter UI）一起带出 —— 无法机械分离。

**故不擅自提交**，等用户选：是否接受把这三个文件的当前状态一并提交、还是先让其他会话
收敛/提交后再切分、或暂时不提交。
