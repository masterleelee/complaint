# 学员投诉自动处理系统

## 每次启动必须使用的技能

- **karpathy-guidelines**: 在编写、审查或重构代码时，必须遵循此行为准则，避免过度复杂化、做最小的修改、暴露假设、定义可验证的成功标准。

## 项目说明

本项目是一个驾校投诉处理系统的 Web 后台，使用 Python Flask + Vue 3 开发，用于处理学员投诉工单的自动受理、三系统查询、合同分析、退费计算、文档生成和飞书归档全流程。

## Agent skills

### Issue tracker

Local markdown issues tracked in `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical roles matching default label names. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context. See `docs/agents/domain.md`.

## 项目关键路径

- `app.py` — Flask 后端主路由
- `templates/index.html` — Vue 3 前端模板
- `static/js/composables/` — Vue 3 组合式函数
- `services/contract_service.py` — LLM 合同分析
- `crawlers/` — 三系统爬虫（内部/第三/驾培）
- `database.py` — SQLite 数据库
