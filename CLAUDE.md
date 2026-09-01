# 学员投诉自动处理系统

> 项目规范文件；`AGENTS.md` 为跨工具同一份内容，两者保持同步。

## 技术栈与架构
Flask + Vue 3 的 Web 后台，覆盖学员投诉工单的自动受理、三系统查询、合同分析、退费计算、文档生成、飞书归档。后端入口 `app.py`，前端 `templates/index.html`（Vue 用 `[[ ]]`），数据在 `data/complaints.db`。更多设计见 `docs/`（36 篇）与 `PLAN.md`/`CONTEXT.md`/`DESIGN.md`/`MEMORY.md`。

## 命令（agent 会执行，务必准确）
- 重启服务：**不要**手动跑 `app.py`。服务由 LaunchAgent 守护（`~/Library/LaunchAgents/com.complaint.system.plist`）；改代码后 `kill <监听PID>`，10 秒内自动以最新代码重启；或 `launchctl kickstart -k gui/$UID/com.complaint.system`。
- 端口 **5003**（`app.py:5288`）；浏览器访问 `http://<本机IP>:5003`（局域网多机共享本机服务器）。
- 测试：`pytest tests/ -v`（单文件 `pytest tests/test_xxx.py -v`）。
- 改 CSS/JS 必 bump `index.html` 静态版本号 `?v=N`，提醒用户强刷（Cmd+Shift+R）。

## 工程流程（代码任务自动优先）
- 凡「改代码 / bug / 功能 / 需求 / 重构」自动走 Matt Pocock 方法（技能装在 `~/.workbuddy/skills/`，直接 `/技能名` 加载）：
  - 需求/功能：`/grill-with-docs` → `/to-spec` → `/to-tickets`（写 `.scratch/<feature>/`）→ `/implement`
  - bug：`/diagnosing-bugs`　大量堆积：`/triage` → `/implement`　大型/模糊：`/wayfinder` → `/to-spec`　不确定：`/ask-matt`
- 提交前必过 `/code-review`（Standards + Spec 双轴）。`karpathy-guidelines` 全程适用（最小修改、暴露假设、可验证标准）。
- **YOU MUST 每次改动都编写或更新对应测试；交付用户前，所有测试与验证（pytest + code-review + 构建）必须全部通过。**
- 提交纪律：每处已验证改动单独 commit，message 说明「改了什么/为什么」；禁止多件不相关改动混进一个大 commit。

## 硬约束
- 数据库只用 `data/complaints.db`，**绝不**动根目录 `database.db` / `complaints.db`。
- 禁止手动 `./venv/bin/python3 app.py`（抢端口且 PYTHONPATH shim 会崩 `config.py`）、`nohup &`、`osascript` 开 Terminal。
- 服务器端 OS 操作只作用于本机——远程访问者无法受益，必须走浏览器端方案（复制路径/文件面板）。
- 备份 DB 用 SQLite `backup()` API（WAL 常含大半数据，裸 cp 会丢）。

## 已知雷区（踩过多次，先读）
- 前端白屏：`app.js` 是 `type=module`，新增 ES module 必须在 importmap 登记裸模块名，否则整图解析失败（页面残留 `[[ ]]` 原文），错误不进 console。改完强刷。
- `data-page-node-id` 注入污染：某页面标注工具往 `index.html` 注入 22 位 ID 且误把属性值里的 `>` 当标签结束符 → 白屏；注入器只插不删，用 `re.sub(r'[ \t]*data-page-node-id="[A-Za-z0-9]{22}"', '', s)` 原样剥离（先备份、先断言 count）。
- 第三系统 GBK：车尚平台表单必须 GBK 提交，UTF-8 静默 0 命中；姓名全名精确匹配；分页 pagesize 硬上限 10，同名 >10 需翻页。
- 网点字典 58 条（`services/org_unit_service.py` 的 `ORGANIZATION_UNITS`，`id` 恒定保护历史工单）；`org_vehicle_counts` 同 58 条（正常 41 / 注销 17，车辆合计 252）。`PUT /api/org-vehicle-counts` 为全量替换，缺失网点会被 DELETE。
- 归档三闸（`archive_service.py`）：`handling_notes` + `branch_cooperation` 非空 + `fee_plan_status=='confirmed'`，缺一即 400。

## Agent skills 配置
读 `docs/agents/`：`issue-tracker.md`（本地 markdown 工单 `.scratch/`）、`triage-labels.md`（五默认角色标签记 `Status:` 行）、`domain.md`（领域词典）。遇到复杂用法或异常时再读对应文档。
