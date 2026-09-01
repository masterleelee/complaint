# 学员投诉自动处理系统

## 项目身份
Python Flask + Vue 3 的 Web 后台，覆盖学员投诉工单的自动受理、三系统查询、合同分析、退费计算、文档生成、飞书归档全流程。

## 工程流程（代码任务自动优先）
凡涉及「代码修改 / bug 修复 / 功能 / 需求 / 重构」，**自动优先**走 Matt Pocock 方法（技能均装在 `~/.workbuddy/skills/`，可直接加载）：
- 需求/功能开发：`/grill-with-docs`（留 `CONTEXT.md`/ADR）→ `/to-spec` → `/to-tickets`（写入 `.scratch/<feature>/`）→ `/implement`
- bug 修复：`/diagnosing-bugs`
- 大量缺陷堆积：`/triage` → `/implement`
- 大型/模糊工程：`/wayfinder` → `/to-spec`
- 不确定：`/ask-matt`
- 提交前必过：`/code-review`（Standards + Spec 双轴）
- 提交纪律：每改动单独 commit，message 说明「改了什么/为什么」
- 底层约束：`karpathy-guidelines` 全程适用（最小修改、暴露假设、可验证标准）

## 部署与启停（务必照此，否则会搞挂服务）
- 端口 **5003**（`app.py:5288` `app.run(host="0.0.0.0", port=5003)`）；浏览器访问 `http://<本机IP>:5003`（局域网多机共享同一台服务器）。
- 数据库：**`data/complaints.db`**（勿动根目录 `database.db` / `complaints.db`）。
- 服务由 **LaunchAgent** 守护（`~/Library/LaunchAgents/com.complaint.system.plist`，KeepAlive，日志 `/tmp/complaint_system.log`）。
- **重启正确姿势**：`kill <监听PID>` → 10 秒内自动以最新代码重启；或 `launchctl kickstart -k gui/$UID/com.complaint.system`。
- **禁止**：手动 `./venv/bin/python3 app.py`（抢端口，且 PYTHONPATH shim 会崩 `config.py`）；`nohup &`；`osascript` 开 Terminal。服务器端 OS 操作只作用于本机——远程访问者无法受益，必须走浏览器端方案。

## 关键雷区（踩过多次，先读再动）
- **前端白屏**：`app.js` 是 `type=module`，新增 ES module 必须在 importmap 登记裸模块名，否则整图解析失败 → 全站白屏（残留 `[[ ]]` 原文），错误不进 console。改完强刷（Cmd+Shift+R）。
- **`data-page-node-id` 注入污染**：某页面标注工具往 `index.html` 每个标签注入 22 位 ID，且误把属性值里的 `>` 当标签结束符，破坏 Vue 指令 → 白屏。注入器只插不删，原样 `re.sub(r'[ \t]*data-page-node-id="[A-Za-z0-9]{22}"', '', s)` 剥离即还原（先备份、先断言 count）。
- **第三系统 GBK**：车尚平台表单必须 GBK 提交，UTF-8 静默 0 命中；姓名全名精确匹配；分页 pagesize 硬上限 10，同名 >10 需翻页。
- **网点字典 58 条**（`services/org_unit_service.py` 的 `ORGANIZATION_UNITS`，`id` 恒定以保护历史工单）；`org_vehicle_counts` 同 58 条（正常 41 / 已注销 17，车辆合计 252）。`PUT /api/org-vehicle-counts` 为全量替换语义，缺失网点会被 DELETE。
- **归档三闸**（`archive_service.py`）：`handling_notes` + `branch_cooperation` 非空 + `fee_plan_status=='confirmed'`，缺一即 400。配合度只 `好/中/差`。

## Agent skills 配置
读取 `docs/agents/`：`issue-tracker.md`（本地 markdown 工单，`.scratch/`）、`triage-labels.md`（五默认角色标签，记 `Status:` 行）、`domain.md`（领域词典，single-context）。

## 验证
- 测试入口：`tests/`（pytest）。改动后跑相关用例，确认 green 再提交。
- 改 CSS/JS 必须 bump `index.html` 静态资源版本号（`?v=N`）并提醒强刷。

## 兄弟文档
`PLAN.md` / `CONTEXT.md` / `DESIGN.md` / `MEMORY.md` / `docs/`（36 篇）为补充设计与记录，需要时查阅。
