# 学员投诉自动处理系统

Flask + Vue 3 的投诉工单处理后台：受理、三系统查询、合同分析、退费与文档归档等。

## v1 验收基线

项目功能边界、完成状态和验收门槛统一以
[`docs/acceptance/v1-requirements-matrix.md`](docs/acceptance/v1-requirements-matrix.md)
为准。旧 DRD 和历史计划仅作为背景资料，不再直接作为开发验收依据。

## 本地运行

1. Python 3.11+（建议与开发环境一致）。
2. 安装依赖：`pip install -r requirements.txt`
3. 在 `data/config.json` 配置三个系统的 `base_url`、`username`、`password`。
   使用合同 AI 时再配置 `llm_contract_vision` 和 `llm_contract_text`；
   字段结构以 [`config.py`](config.py) 的 `DEFAULT_CONFIG` 为准。配置文件含密钥，禁止提交。
4. 启动：`python app.py`（或项目内提供的 `start.command`）。

## 说明

- 数据库与缓存会在首次运行时写入 `data/`，默认已被 `.gitignore` 排除。
- `案件归档/`、`uploads/`、`回复函/` 为业务数据目录，不应进入版本库。
