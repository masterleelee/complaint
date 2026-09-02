# 04: 单份分析管线串联（主干 tracer）

**Parent:** 规格 `spec.md`

**What to build:** 打通上传件主干：上传 1 份 → 现有提取降级链出正文 → 档位识别 → 关键字段抽取（LLM 以桩替换）→ 扣费引擎按档位+阶段+进度算明细 → 写回工单 → 预览 API 返回档位信息、正文、明细、原件清单与分析缓存键（键=文件指纹+档位+进度哈希，本票只产出、消费在 08）。同时加下载链路回归护栏：下载件分析输出结构不变（本期不碰下载链路的可验证承诺）。LLM 不可用/超时须降级为可见错误而非崩溃。

**Blocked by:** 01, 02, 03

**Status:** done — 2026-09-01：`services/upload_pipeline.py`（187 行）+ `tests/test_upload_pipeline.py`（18 例）全绿；`app.py:3092-3156` 接入 `is_upload_ticket` 判定 + 新管线 dispatch；下载链路零回归（388 passed / 0 failed 全量回归）。

- [x] 2023·分店特征文本 + registration_date=2023 + org_type=分店 → tier_id=2023_branch_store、必扣 4 项（服务费/建档费/IC卡/场地费）、考试费按已考门控（科一 70）、违约金 20%（6000×20%=1200）（`test_identifies_2023_branch_store_via_text` + `test_pipeline_runs_deduction_engine_with_resolved_tier`）
- [x] 2019·培训特征文本 + 已受理 → 理论费全额扣（3000）、无实操费、无违约金（`test_pipeline_2019_training_accepted_no_penalty`）
- [x] 提取失败（LLM 桩超时）→ 管线返回 `extraction_error` 字段，**不抛未捕获异常**；tier_id/cache_key 空、deductions_result=None（`test_pipeline_handles_extraction_error_gracefully`）
- [x] 下载链路：legacy `analyze_contract_from_file` 输出结构未动；新管线仅在 ticket 的 contract_set 含 sha256 标记时启用（`is_upload_ticket` 判定）；既有测试全绿
- [x] 缓存键产出：文件指纹 + 档位 + 进度哈希；同输入稳定、档位变 / 进度变即重算（`test_cache_key_changes_when_tier_changes` / `test_cache_key_changes_when_progress_changes`）

**实现要点**：
- 上传管线与 legacy `analyze_contract_from_file` **并存不替代**——`is_upload_ticket(contract_set)`（每条带 `sha256` 标记）决定走新管线；下载件继续走 legacy 输出
- 失败降级：提取失败 / 档位不可定 / 引擎异常 → 字段级表达，不抛；legacy 结果保留作为兜底
- 缓存键产出已包含文件指纹、档位、进度哈希——08 改档重算 / 09 页图缓存可直接消费（键相同则命中）
- 正文随结果落库（ADR-0002 硬前提）：测试覆盖 `pdf_text` / `vision_text` / `local_ocr` 三种 source
- LLM 抽锚点短语（spec 04 工单 5 描述）留 05 阶段处理；本期只做档位识别 + 引擎 + 缓存键 + 落库

**关键设计决策（与 ADR-0003 一致）**：
- 新管线的扣费明细 **覆盖** legacy deductions（spec「按档位+阶段+进度算明细」）；legacy 输出保留为未走新管线时的兜底
- `refund_pending` 字段新加——上游缺失时 refund=0、refund_pending=True；为 07 闸门（费用方案确认拒绝）做准备

