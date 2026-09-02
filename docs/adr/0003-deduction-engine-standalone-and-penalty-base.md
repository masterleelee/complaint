# 扣费引擎另起炉灶（float 口径）；违约金基数=各份手写总额之和、代缴份不计入

工单 02 要新建「档位+阶段+进度 → 扣费明细」的纯函数引擎。仓库里已有一个 890 行的 `core/refund_engine.py`（Decimal 精确分账、`contract_id` 必填），但**全项目零 import**——生产退费一直走 `services/contract_service.py` 的 float 版本，两者逻辑已分叉。决定：新引擎**另起炉灶**（纯函数、沿用现网 float 金额口径、不要求 `contract_id`），不继承 refund_engine 的契约；refund_engine 的去留是独立的架构决策（`docs/优化报告-2026-08-29.md` 第 21 项），不因本功能续命或提前删除。同时，「读取方兼容」类回归护栏必须断言**真实读取方**（归档清单 `_ticket_contract_paths`、requery、manifest），禁止再引用零引用模块当契约依据。

Considered Options：继承 refund_engine 契约（拒绝：需给上传分条补造 `contract_id`、金额切 Decimal 是语义变更，超出本功能范围）；生产切换 Decimal 版（拒绝：回归面最大，与 spec「本期不碰下载链路」承诺冲突）。

Consequences：多份并行合同时，**违约金基数 = 该套合同各份手写总额之和，代缴份不计入（服务份计入）**，与「违约金基数=全部培训费用（不含代收代缴）」的词典口径一致。当前六档矩阵中「多份并行 × 违约金非零」组合不存在（2019 并行三份均为 0%，2021 起单一合同），此规则是未来防御而非今日金额变更；若日后出现该组合，按此基数一行判断即可，不再现场拍板。金额精度争议（float vs Decimal）遗留为独立事项，由 refund_engine 去留拍板一并裁决。
