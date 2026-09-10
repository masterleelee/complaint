# 文件与目录规范（投诉处理系统）

> 用途：把散落在 `AGENTS.md` / `PLAN.md` / `CONTEXT.md` / `services/file_service.py` / `.gitignore` / Matt Pocock 工程技能里的"文件怎么放、怎么命名、怎么保存、怎么测、怎么提交"约定汇总成一份长期依据。
> 配套诊断见 [`目录整理清单.md`](./目录整理清单.md)。
> 本文件是可执行规范的**唯一汇总点**；若某规则与上游源文件冲突，以上游源文件（AGENTS.md 等）为准并回改此处。

## 1. 目录结构总览（权威树）

```
投诉处理系统/
├── app.py database.py config.py requirements.txt   # 核心源码（勿动结构）
├── api/ core/ services/ crawlers/ utils/            # 后端模块
├── templates/ static/                               # 前端（Vue 用 [[ ]]，static 含 app.js/style.css）
├── tests/                                           # pytest 测试（单文件 pytest tests/test_xxx.py -v）
├── scripts/                                         # 运维/工具脚本（启动、备份、缓存清理等）
├── start.command                                    # 三级启动入口（放在 scripts/ 或根均可，仅留一份）
├── data/                                            # 权威数据区（AGENTS 硬约束）
│   ├── complaints.db (+ -shm/-wal + .bak-*)          # 唯一数据库
│   ├── config.json  contract_templates.yaml
│   ├── logs/  cache/                                # 运行时，gitignored
├── docs/                                            # 规范文档区（本文件所在）
│   ├── agents/  adr/                                # Matt Pocock 技能配置 / 架构决策记录
│   ├── 三系统API接口细节.md  页面源码类文档…
├── demo/                                            # 前端原型 HTML（AGENTS 约定留 demo/）
├── uploads/                                         # 业务收件（gitignored）
├── 回复函/ 回复模板/ 案件归档/ 合同文件/ 1合同种类/  # 业务产出/资料（多 gitignored）
├── .scratch/                                        # 本地 markdown 工单（Matt Pocock issue-tracker）
└── AGENTS.md PLAN.md CONTEXT.md DESIGN.md MEMORY.md README.md  # 规范入口
```

> 根目录**不应**出现：业务数据库、散落的需求/接口文档、临时堆。这些应分别进 `data/`、`docs/`、收纳或清理。

## 2. 文件命名约定

### 2.1 业务产出文件（出自 `services/file_service.py`）
- **合同副本**：`{日期}+{姓名}+{身份证}+{文件名}.pdf`
- **回复函**：`{日期}{姓名}{身份证}投诉回复函{网点}.docx`
- 命名含身份证号，便于"姓名/手机号 → 身份证 → 解锁合同"的身份解析主线。

### 2.2 案件归档四段式（出自 `PLAN.md` L262-273、`CONTEXT.md` 归档段）
```
{归档根}/{单位类型}/{代号}-{单位名}/{投诉日期}_{姓名}_{身份证}_{代号}/
```
子项含：登记表、回复函、合同副本。

### 2.3 工单 / 设计 / 决策（Matt Pocock，见第 4 节）
- 聚合清单：`.scratch/issue-list.md`，条目用 `ISS-<轮次>-<NN>`（如 `ISS-B-01`），带 P0–P3 严重度。
- 功能工单：`.scratch/<feature>/spec.md` + `issues/NN-<slug>.md`（自 `01` 编号，一票一文件）。
- 架构决策：ADR 命名 `docs/adr/0001-<decision>.md`。

## 3. 数据库规则（AGENTS 硬约束）

- **唯一数据库**：`data/complaints.db`。所有代码从 `database.py` 的 `DB_PATH` 读取。
- **严禁**根目录 `database.db` / `complaints.db`（历史空壳，属违规）。
- **备份**数据库必须用 SQLite `backup()` API（WAL 常含大半数据，裸 `cp .db` 会丢）。
- 不手动跑 `./venv/bin/python3 app.py`（抢端口 + PYTHONPATH shim 会崩 `config.py`）；服务由 LaunchAgent 守护，改码后 `kill <监听PID>` 让其自重启。

## 4. Matt Pocock 工作产物约定

> 来源：`~/.workbuddy/skills/` 下 `to-spec` / `to-tickets` / `wayfinder` / `tdd` / `code-review` / `setup-matt-pocock-skills` / `ask-matt` / `issue-tracker`（docs/agents）。

### 4.1 需求 / 计划书（spec）流程与落点
- `to-spec`：对话综合成 spec，发到 issue tracker（本地 `.scratch/`），打 `ready-for-agent`；模板含 Problem Statement / Solution / User Stories / Implementation Decisions / Testing Decisions，**不写具体文件路径**。
- `to-tickets`：spec 拆成 tracer-bullet 工单，本地为 `.scratch/<feature>/issues/NN-<slug>.md`（一票一文件，绝不合并单文件）。
- `wayfinder`：超大模糊任务画 `wayfinder:map` issue + 子 decision ticket，产出 decisions 而非 deliverables。

### 4.2 目录 / 命名铺设（`setup-matt-pocock-skills`）
- 仓库文档布局：`docs/agents/{issue-tracker,domain,triage-labels}.md`、`docs/adr/`、`CONTEXT.md`、根 `AGENTS.md` 的 `## Agent skills` 块。
- 本地 issue 默认 `.scratch/<feature>/`。
- `code-review` 标准源：仓库 `CODING_STANDARDS.md` / `CONTRIBUTING.md`；spec 查找顺序 `docs/` → `specs/` → `.scratch/`。

### 4.3 测试（`tdd`）
- red → green → refactor；只测预约定 seam 的公共接口而非实现；垂直切片；警惕实现耦合 / 同义反复 / 横向切片。
- 本项目：`pytest tests/ -v`；每次改动都编/更新对应测试，交付前全部通过。

### 4.4 编码规范 / 提交纪律（`code-review` + `implement` + `ask-matt`）
- `code-review` Standards 轴：以仓库自文档标准为准 + 固定 Fowler 代码味道基线（Mysterious Name / Duplicated Code / Feature Envy…），"repo overrides"。
- 每个 issue 内部驱动 `/tdd` → 跑 `/code-review`（Standards + Spec 双轴）通过后才 commit；另配 `git-guardrails-claude-code` 拦截危险 git 命令。
- `ask-matt`：prototype 保留为 `prototype/<name>` 分支；research 在仓库留 cited Markdown；handoff 存 OS 临时目录而非 workspace。

> 注：Matt Pocock 方法规范的是"**工作产物**（工单 / 设计 / ADR）放哪、叫什么"，**不**规定源代码文件本身的命名；源码命名靠本项目的 `file_service.py` / `CONTEXT.md` / `.gitignore` 与既有模块约定。

## 5. `.gitignore` 排除清单

以下**不进版本库**（已在根 `.gitignore` 配置）：
- `.env`
- `data/cache` / `data/logs`
- `*.db*`（含 `-shm`/`-wal`/备份）
- `案件归档/` / `uploads/` / `回复函/` / `合同文件/` / `页面源码/`
- `*.doc` / `*.docx`（部分模板除外，按实际规则）
- `.DS_Store`

> 意味着：根目录的 `database.db` / `complaints.db`、`合同文件/`、`回复函/`、`案件归档/`、`页面源码/`、`tmp/` 等均为"物理存在但不该提交"的散落物，是整理重点。

## 6. 禁止项 / 红线

1. **根目录数据库**：严禁 `database.db` / `complaints.db`，一律在 `data/`。
2. **一次性大改**：遵循"先审计 → 出清单 → 设计 → 审批 → 再改"，增量逐项审批，不整批移动/删除。
3. **裸 `rm` / 批量删除**：删前必须备份 + 逐批（≤10 文件）确认 + 走回收站（无 trash CLI 时二次确认）。
4. **手动起服务**：不用 `./venv/bin/python3 app.py`、`nohup &`、`osascript` 开 Terminal；交给 LaunchAgent。
5. **服务器端 OS 操作只作用于本机**：远程访问者无法受益，须走浏览器端方案（复制路径 / 文件面板）。
6. **共享盘**：`/Volumes/File/...` 禁止递归遍历、禁止删除文件。

---
*维护：任何规则变更请同步回上游源文件（AGENTS.md / file_service.py / .gitignore）并回改本文件。*
