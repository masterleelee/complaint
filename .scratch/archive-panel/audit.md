# 「打开归档」方案 A（网页内置归档面板）· 代码审计

日期：2026-09-10　状态：**审计完成，待用户审批计划**
范围：只读。本文件不涉及任何源码改动。

---

## 0. 前置风险（必须先处理，否则后面全部免谈）

### 0.1 【已实测】基线是红的：`7 failed, 603 passed`

```
env -u PYTHONPATH ./venv/bin/python3 -m pytest tests/ -q
→ 7 failed, 603 passed, 1 warning in 38.96s   （证据：evidence/00-baseline.txt）
```

**其中 6 例正好落在本方案的修改范围内**（归档域）：

```
test_archive_api.py::test_archive_success_copies_two_files
test_archive_api.py::test_archive_no_files_returns_400
test_archive_service.py::TestArchiveCase::test_no_existing_file_fails
test_doc_authority.py::test_withdraw_archived_case_writes_note_and_refreshes_form
test_doc_authority.py::test_archive_persists_archived_dir
test_doc_authority.py::test_archive_regen_failure_falls_back_with_warning
```

**已实验证实的根因（非推断）**：`app.py:32` 是 `from config import load_config, ...` —— **直接绑定**。
测试里 `monkeypatch.setattr(config_module, "load_config", ...)`（如 `tests/test_archive_api.py:121`）
只改 `config` 模块属性，**对 `app.load_config` 完全无效**：

```
A.load_config is C.load_config  → True   （初始同一对象）
patch C.load_config 后：
  A.load_config() → /Volumes/File/综合办公室/内部共用/投诉文件/投诉处理系统   ← 仍读真实配置
  C.load_config() → /tmp/PROBE
```

→ 归档路由读取**真实** `data/config.json` 的 `archive_root`，而该共享盘此刻未挂载 →
`PermissionError: [Errno 13] Permission denied: '/Volumes/File'` → 6 例失败。

**这两个结论合起来意味着**：`resolve_case_dir` 的 `root_unavailable` 不只是"某次配置问题"，
它是**当前生产环境的真实状态**（见 §2.1）。

**附带问题（数据卫生）**：因打桩无效，这些测试在**共享盘挂载时会把测试用的
`登记表_src.docx` / `回复函_src.docx` 真实写进生产归档目录**——traceback 已证实它操作的正是 `/Volumes/File/...`。

**处理**：立为 `ISS-AP-00`（P0，阻塞全部回测）。**基线不转绿，方案 A 的任何改动都无法证明"没改坏"。**

第 7 例 `test_contract_tiers.py::test_penalty_rate_conflict_warns` 属另一根因（疑似工作区未提交的
`services/contract_tiers.py` 改动），已在 ISS-AP-00 中归因，不并入本方案修复范围。

### 0.2 工作区不干净：11 个已跟踪文件未提交

`git status` 显示 **11 个已跟踪文件处于「已修改未提交」状态**，其中 4 个正是本次要改的文件：

```
M app.py
M templates/index.html
M static/js/app.js
M static/css/style.css
M static/js/composables/useComplaint.js
M core/query_engine.py
M services/contract_tiers.py
M services/upload_pipeline.py
M tests/test_clause_locate_fix.js
M .scratch/issue-list.md
M 回复模板/default_template.docx
```

**风险**：基线不干净 → ①无法区分「本次方案 A 的改动」与「历史遗留改动」；②一旦要回滚，会连历史改动一起回滚；③`code-review` 双轴审查会被无关 diff 淹没。

**处理选项（需用户裁决）**：
- (a) 先提交这批改动（按项目纪律「每处已验证改动单独 commit」拆成若干提交）→ 再开工；
- (b) 先 `git stash push -m "pre-archive-panel"` 备份 → 开工 → 事后恢复（风险：stash 期间其他人/进程动了文件会冲突）；
- (c) 确认这批改动无价值，直接丢弃（**不推荐**，需逐个确认）。

---

## 1. 现状资产盘点（按「方案 A 需要 / 需要改造 / 应删除」分类）

### 1.1 直接复用（方案 A 的核心资产，已存在且测试覆盖良好）

| 资产 | 位置 | 说明 |
|---|---|---|
| 列目录 API | `app.py:3942-3976` `GET /api/tickets/<id>/archive-files` | 返回 `{dir, files:[{name,size,mtime}]}`，按 mtime 倒序 |
| 单文件下载 API | `app.py:3979-4005` `GET .../archive-files/download?name=` | 防目录穿越（basename 归一 + 原串比对），`as_attachment=True` |
| 案件夹定位 | `services/archive_service.py:194-212` `resolve_case_dir()` | 返回结构化 code：`root_unavailable` / `dir_missing` |
| 归档路径构建 | `services/archive_service.py:118-147` `build_archive_dir()` | 统一路径规则，`_assert_within` 防越界 |
| 面板前端逻辑 | `static/js/composables/useWorkbench.js:538-587` | `openKjFiles` / `kjFileUrl` / `fmtKjSize` + `kjFiles*` 状态 |
| 面板模板 | `templates/index.html:2588-2610` | 简易表格弹窗（仅下载，无打包/无预览） |
| 面板 API 测试 | `tests/test_archive_files_api.py`（**9 例**） | 列目录/空目录/夹子缺失/工单 404 + 下载/穿越/缺文件 |

### 1.2 需要改造

| 资产 | 位置 | 改造点 |
|---|---|---|
| 列表行按钮 | `templates/index.html:989` | `title="打开该学员的归档文件夹"` + 图标 `bi-folder2-open` → 文案与图标都要改成「查看归档文件」语义 |
| 批量条按钮 | `templates/index.html:1012` | 同上，且 title 里写着「在系统文件管理器中打开」 |
| `.row-open` 样式 | `templates/index.html:597-598` | 类名语义已不符（不再是 "open folder"） |
| `POST /open-archive` | `app.py:3842-3880` | 现在做 local/remote 分流；方案 A 下应简化为**仅本机可用**（远程返回 403 或直接不注册该按钮） |
| `_request_from_server_host()` | `app.py:3824-3839` | 保留，但只为「本机直开」这一个用途服务 |
| `openArchive()` | `useWorkbench.js:589-620` | 重写：不再请求 `open-archive`、不再 blur 猜测，直接开面板 |
| 面板模板 | `templates/index.html:2588-2610` | 重做：图标/大小/时间/下载/**打包 zip**/**预览**/复制路径/错误分级展示；列表↔图标视图切换 |
| `tests/test_open_archive_api.py`（**6 例**） | — | 随 `open-archive` 语义变更重写 |

### 1.3 方向错 / 死代码（方案 A 下无用）

#### 后端

| 资产 | 位置 | 行数 | 判定 |
|---|---|---|---|
| `HELPER_VBS` | `archive_service.py:311-397` | ~87 | ❌ kjfolder 助手 vbs 常量 |
| `_HELPER_BAT_TEMPLATE` | `archive_service.py:399-437` | ~39 | ❌ 安装脚本模板 |
| `generate_helper_bat()` | `archive_service.py:440-446` | 7 | ❌ 仅被 `/kopen-helper` 调用 |
| `_DIAGNOSE_BAT_TEMPLATE` | `archive_service.py:449-524` | ~76 | ❌ 诊断脚本模板 |
| `generate_diagnose_bat()` | `archive_service.py:527-536` | 10 | ❌ 仅被 `/kopen-diagnose` 调用 |
| `GET /kopen-helper` | `app.py:3883-3902` | 20 | ❌ 免登录路由，安全面冗余 |
| `GET /kopen-helper.vbs` | `app.py:3905-3917` | 13 | ❌ 同上 |
| `GET /kopen-diagnose` | `app.py:3920-3939` | 20 | ❌ 同上 |
| `open_case_dir()` | `archive_service.py:215-242` | 28 | ⚠️ 仅被 `/open-archive` 本机分支调用；若保留本机直开则**保留** |
| `get_smb_mappings()` / `to_unc_path()` / `_SMB_CACHE` | `archive_service.py:245-304` | ~60 | ⚠️ UNC 转换；方案 A 下若还想要「复制网络路径」按钮则保留，否则删 |
| **`archive_case(open_folder=...)` 的 open_folder 分支** | `archive_service.py:178-190` | 13 | ❌ **确证死代码**：唯一调用点 `app.py:3774` 恒传 `open_folder=False`，仅测试 `test_archive_service.py:122` 在跑这条分支 |

**后端 helper 链路小计：约 226 行模板常量 + 3 个免登录路由 + 4 个函数。**

#### 前端

| 资产 | 位置 | 判定 |
|---|---|---|
| `_utf8ToBase64Url()` | `useWorkbench.js:493-498` | ❌ kjfolder 专用 |
| `_tryKjProtocol()` | `useWorkbench.js:500-506` | ❌ kjfolder 专用 |
| `_watchBlurThenResolve()` | `useWorkbench.js:508-521` | ❌ **误报成功的根源**：`window.blur` + 1.5s 定时器猜「助手是否打开成功」，2026-09-01 诊断报告 H2 已判定其「必然成立」地误报 |
| `copyKjUnc()` | `useWorkbench.js:523-536` | ⚠️ 复制 UNC，可保留改造 |
| `kjHelperModalOpen` / `kjUncPath` / `kjServerDir` | `useWorkbench.js:489-491` | ❌ 兜底弹窗状态 |
| `kjFilesDir` | `useWorkbench.js:547` | ❌ **死变量**：赋值于 564 行，`templates/index.html` 全文从未引用 |
| 兜底弹窗模板 | `templates/index.html:2550-2585` | ❌ 36 行，含「下载归档助手」「一键诊断」「kjfolder 授权说明」 |

#### 测试（将随功能删除而失效）

| 文件 | 用例数 | 处理 |
|---|---|---|
| `tests/test_remote_archive_helper.py` | **20** | 测 `_request_from_server_host` / `to_unc_path` / `generate_helper_bat` / `generate_diagnose_bat` → 随功能删除 |
| `tests/test_open_archive_api.py` | 6 | 其中 2 例测 `open_case_dir` 纯函数 → 随重构调整 |
| `tests/test_archive_service.py::test_open_folder_darwin` | 1 | 随 `open_folder` 参数删除 |

---

## 2. 关键发现（决定方案取舍）

### 2.1 「打开归档」当前必然失败，与客户端无关
实测（本机 Python 直调）：
```
archive_root = /Volumes/File/综合办公室/内部共用/投诉文件/投诉处理系统
mounted?    = False          ← /Volumes 下只有 Macintosh HD，无任何 smbfs 挂载
resolve_case_dir → {'success': False, 'code': 'root_unavailable',
                    'errors': ["归档根目录不可用: 无法创建归档根目录: …Permission denied: '/Volumes/File'"]}
```
- 报错文案**误导**：说「无法创建归档根目录」，实际是网络盘未挂载；
- `config.smb_share` 未配置 → UNC 只能靠解析 `mount` 输出，未挂载时 `to_unc_path` 返回 `None`。

**对方案 A 的影响（这是本审计最重要的一条）**：**网页面板同样依赖归档盘可访问**——盘没挂载时，面板也只会显示一个失败提示。所以方案 A 落地**必须同时做「错误分级文案」**，否则用户会把「盘没挂载」误读成「新功能坏了」。这不等于修挂载，但比修挂载更急。

### 2.2 ZIP 打包是全新能力，项目内无先例（**该能力已于 2026-09-10 被用户取消**）
全项目（`app.py` / `services/` / `core/` / `utils/`）**零** `zipfile` / `make_archive` / `ZipFile` 引用。
原计划方案 A 的「打包下载 zip」需从零实现，且要处理：大目录内存占用、文件名中文编码（`zipfile` 默认 UTF-8 flag）、空目录。

> **2026-09-10 更新**：用户明确要求「不用这个 zip，用户需要哪个文件自己打开，自己下载就行，不要压缩打包」
> → `ISS-AP-02` 已取消（见 `issues/02-后端ZIP打包.md`），本结论转为**留档**：若将来重启该需求，直接引用此处即可，无需重复调研。

### 2.3 已有一个可复用的「白名单内联预览」实现
`app.py:5265-5290` `GET /api/contract/preview` 已实现：白名单目录校验 + `mimetypes.guess_type` + `as_attachment=False`。方案 A 的「预览」可直接沿用这套模式，**不必新造轮子**且能保持安全口径一致。

### 2.4 用户已知的历史诊断
`.scratch/open-archive-diagnosis-20260901.md` 已判定：kjfolder 主路径同时依赖 5 个外部环节（名字解析→共享凭据→客户端装机→注册表→浏览器授权），任一环断即静默失败，且 blur 判据把失败报成成功。**方案 A 正是该报告推荐的主线**，本审计确认该结论成立。

---

## 3. 审计结论

1. 方案 A 的**后端读能力已具备 80%**（列目录 + 下载 + 夹子定位 + 9 例测试），落地成本主要在「打包 zip」「预览」「错误分级」三件新增项。
2. 前端面板**已写好但被埋错位置**（藏在兜底弹窗里当"退路"），只需提升为主路径 + 补能力。
3. **可安全清理的死代码约 300 行**（后端 226 行模板 + 前端 3 个函数 + 1 死变量 + 1 死分支），但**删除是不可逆能力移除**，需用户裁决保留 / 冻结 / 删除（见 spec.md 待决策 D1）。
4. **真正的风险不在代码，而在基线**：11 个未提交文件 + 归档盘未挂载，这两件事不处理，方案 A 上线后依然会「点了没反应」。
