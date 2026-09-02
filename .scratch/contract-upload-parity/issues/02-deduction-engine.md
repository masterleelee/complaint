# 02: 扣费计算引擎（纯函数）

**Parent:** 规格 `spec.md`

**What to build:** 纯函数扣费引擎：输入（合同档位、阶段〔已受理/实操中〕、进度〔各科已考次数、审核有效学时〕、手写金额〔培训费总额、实操单价等〕），输出有序扣费明细。规则：必扣项全扣；考试费/补考费只计已考科目（已考次数 > 0）且金额取档位标准；实操费 = 审核学时 × 档位单价（封顶校验）；违约金 = 培训费总额 × 档位比例、恒排最后；**已受理（培训档）＝理论培训费视为已发生全额扣**，实操中另加已发生实操费；手写金额缺失 → 该项 pending 且不计入应退；每条明细带置信来源（tier_default / ocr / manual / pending）、金额、依据。无 IO、不依赖 Flask 与数据库。

**Blocked by:** 01（档位表）

**Status:** done — 2026-09-01：`services/deduction_engine.py`（225 行）+ `tests/test_deduction_engine.py`（16 例表驱动 + 单元）全绿；全量回归 361 passed / 0 failed；下载链路零回归。

- [x] 六档 × 场景矩阵表驱动：2019 三档无违约金；2021-2022 为 10%；2023 为 20%；分店档多扣场地费 700（7 条 parametrize 覆盖）
- [x] 2019·培训档：已受理 → 理论费全额扣且无实操费；实操中 → 理论费 + 已发生实操费
- [x] 科二学时 5、已考 0 次 → 明细中无科二考试费、有实操费 600（C1）
- [x] 培训费总额缺失 → 违约金与应退合计为 pending，不计入数字
- [x] 输出顺序稳定：必扣 → 考试费/补考费 → 实操费/理论费 → 违约金（恒排最后）
- [x] 每条含金额、依据、来源（tier_default/ocr/manual/pending）、置信度
- [x] 全部测试为表驱动；现有测试套件全绿（纯新增无回归）

**实现要点**：
- 纯函数 `calculate_deductions(tier, stage, progress, total_fee, manual_amounts)`，无 IO、无 Flask、无 DB；接受 `services.contract_tiers.TIERS_BY_ID[tier_id]` 作为档位输入
- ADR-0003：沿用 float 金额口径；不继承 refund_engine 契约（无 contract_id 要求、不分制）；pending 项占位 0 不计入合计
- 多份并行归并交给 04（管线串联）；本函数按单份合同输出明细，refund 字段仅在合同内自洽
- 封顶校验只告警不截断（人工核对优先级）

