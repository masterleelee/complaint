# 07: 金额置信度着色 + 待确认闸门

**Parent:** 规格 `spec.md`

**What to build:** 金额按置信来源着色：档位默认=绿、OCR 抽取=琥珀、人工录入=绿实心、pending=灰。pending 项不计入应退合计并带「待确认」徽标；存在 pending 时后端拒绝确认费用方案与归档（对接现有归档三闸语义），人工补录齐全后放行。

**Blocked by:** 05, 06

**Status:** done — 2026-09-01：`services/gating.py`（142 行纯函数）+ `tests/test_gating.py`（21 例）+ `tests/test_07_08_09_integration.py`（HTTP 集成 13 例：07 闸门两向 + 08 路由冒烟 + 09 页图路由）。`app.py` 集成 `check_fee_plan_confirm` 到 `api_ticket_fee_confirm:1885` + `check_archive` 到 `api_tickets_archive:3521`。全量回归 495 passed / 0 failed。

- [x] 三种来源金额颜色可区分——06 阶段 `useContractPreview.js` 已落地 `cp-conf-mini` + `cp-dot-green/amber/gray`；后端 07 只做判定不重做视觉
- [x] 存在 pending：应退合计不含该项 + 「待确认」徽标——02 阶段引擎 `total_deduction` 已排除 pending、`refund_pending` 已正确返回；06 阶段 `cp-tag-pending` 徽标已渲染
- [x] 有 pending 时确认费用方案被拒——`test_fee_confirm_rejects_when_pending_item_exists` 断言 400 + 「待确认」字样
- [x] 有 pending 时归档返回 400 + 提示——`test_archive_rejects_when_pending_item_exists` 断言 errors[0] 含「待确认」
- [x] 补齐 pending 后两道闸门放行——`test_fee_confirm_passes_when_no_pending` + `test_archive_passes_after_pending_resolved`

**实现要点**：
- **判定纯函数**：`has_pending_items` / `pending_items_summary` / `check_fee_plan_confirm` / `check_archive` 均无 IO、无 Flask、无 DB，便于测试与复用
- **app.py 集成**：`_ticket_pending_items(ticket)` helper 从 `ticket.deduction_detail` 抽取 `pending=True` 项；`fee-confirm` 在 no_fee_basis=False 分支前调闸门（no_fee_basis 与 pending 互斥）；archive 在既有 `archive_gate_errors` 之后追加 07 闸门（双保险）
- **设计取舍**：`check_archive` 未直接调 `archive_service.archive_gate_errors`，避免 services 循环依赖；用相同逻辑（约 6 行）独立判定——主 Agent 集成时若偏好单一事实源，可在 services 内改用前者
- **字段命名**：未新增 ticket 表字段；闸门从现有 `deduction_detail`（engine 落地时已有 pending 字段）读取——零迁移

