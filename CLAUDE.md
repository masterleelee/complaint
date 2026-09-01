# 学员投诉自动处理系统

## 每次启动必须使用的技能

- **karpathy-guidelines**: 在编写、审查或重构代码时，必须遵循此行为准则，避免过度复杂化、做最小的修改、暴露假设、定义可验证的成功标准。该准则在以下所有 Matt Pocock 流程中同样适用。
- **Matt Pocock 工程技能（代码任务自动优先使用）**: 凡是涉及「代码修改 / bug 修复 / 功能开发 / 需求开发 / 重构」等工程任务，**无需用户每次显式提出**，必须自动优先采用 Matt Pocock 工程方法。按任务类型路由：
  - **需求 / 功能开发（需先明确设计与范围）**: `/grill-with-docs`（在工作目录留下 `CONTEXT.md` / ADR 设计轨迹）→ `/to-spec`（产出规格）→ `/to-tickets`（拆为可追踪工单，写入 `.scratch/<feature>/`）→ `/implement`（逐工单实现，内部驱动 `/tdd` 与 `/code-review`）。
  - **bug 修复（难定位 / 间歇 / 回归）**: `/diagnosing-bugs`（先建可复现反馈环，再修复并补回归测试）。
  - **大量来件缺陷或需求堆积**: 先 `/triage` 产出 agent-ready 工单，再进入 `/implement`。
  - **大型 / 模糊工程（超出单会话）**: `/wayfinder` 先绘制决策地图，再并入主流程 `/to-spec`。
  - **代码健康度提升（有余力时）**: `/improve-codebase-architecture` 发现深化机会并落地。
  - **不确定走哪条流程**: 加载 `/ask-matt` 路由器判定。
  - **任何代码改动提交前**: 必须 `/code-review`（Standards + Spec 双轴）通过后再提交。
  - 以上技能默认从 `docs/agents/`（issue-tracker / triage-labels / domain）读取本项目配置；issue tracker 为本地 markdown（`.scratch/`）。

## 项目说明

本项目是一个驾校投诉处理系统的 Web 后台，使用 Python Flask + Vue 3 开发，用于处理学员投诉工单的自动受理、三系统查询、合同分析、退费计算、文档生成和飞书归档全流程。

## Agent skills

### Issue tracker

Local markdown under `.scratch/` — aggregate register `issue-list.md`（`ISS-XXX` + P0–P3）plus per-feature `spec.md` / `issues/NN-*.md`. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical roles matching default label names, recorded as a `Status:` line. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context; `CONTEXT.md` / `docs/adr/` not yet created, `CLAUDE.md` is the interim domain reference. See `docs/agents/domain.md`.

## 项目关键路径

- `app.py` — Flask 后端主路由
- `templates/index.html` — Vue 3 前端模板
- `static/js/composables/` — Vue 3 组合式函数
- `services/contract_service.py` — LLM 合同分析
- `crawlers/` — 三系统爬虫（内部/第三/驾培）
- `database.py` — SQLite 数据库
