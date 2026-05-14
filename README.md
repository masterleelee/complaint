# 学员投诉自动处理系统

Flask + Vue 3 的投诉工单处理后台：受理、三系统查询、合同分析、退费与文档归档等。

## 本地运行

1. Python 3.11+（建议与开发环境一致）。
2. 安装依赖：`pip install -r requirements.txt`
3. 在 `data/` 下自行准备 `config.json`（勿提交；按你方内部规范填写接口与密钥）。
4. 启动：`python app.py`（或项目内提供的 `start.command`）。

## 说明

- 数据库与缓存会在首次运行时写入 `data/`，默认已被 `.gitignore` 排除。
- `案件归档/`、`uploads/`、`回复函/` 为业务数据目录，不应进入版本库。
