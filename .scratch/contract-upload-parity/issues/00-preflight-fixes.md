# 00: P0 前置修复（阻塞 03 验收的三件事）

**Parent:** spec.md；来源：2026-09-01 进度审计（基线 342 passed / 3 failed，3 条全在 `tests/test_contract_set_storage.py`）

**What to build:**

1. **img2pdf 依赖缺失**（已上线功能缺口，非新工单）：`requirements.txt` 增加 `img2pdf`，venv 安装。现状：多张合同照片合并 PDF 在 `app.py:2765` 抛 `ModuleNotFoundError` 被吞，静默退化成「只留第一张图作代表」，`manifest.merged_pdf_path` 为空。
2. **测试跨用例污染**：`fresh_db` 只 `init_db()` 不清库，而 `database.save_ticket` 默认走「同日同人合并」→ 后续用例 `_make_ticket()` 复用上一用例工单，合同分条累积（单独跑全绿、整文件跑 2 条变 3 条）。修法：fixture 真正清库（每用例独立 DB 文件）或 `_make_ticket` 传 `force_new=True`，二选一并说明取舍。
3. **护栏换对象**（ADR-0003 拍板）：`tests/test_contract_set_storage.py:26` 的 `engine_normalise` 引用零引用死代码 `core/refund_engine._normalise_contract_set` 当「读取方兼容」契约。改为断言真实读取方：`app._ticket_contract_paths`、requery 读合同集合、manifest 读取。refund_engine 本体不删（去留另案）。

**Blocked by:** None（拍板已完成，2026-09-01）

**Status:** done — 2026-09-01 完成：img2pdf 0.6.3 安装并入 requirements.txt；fresh_db 改每用例独立 DB（tmp_path）；PNG 夹具改 PIL 生成 64×64（1×1 会触发 img2pdf 页面尺寸下限）；护栏从 refund_engine 换为 normalize 幂等 + `_ticket_contract_paths` 稳定。全量回归 345 passed / 0 failed。

- [x] 全量 pytest 0 failed（基线 342 + 3 条转绿，实为 345 passed）
- [x] `test_upload_pdf_plus_images_creates_two_entries` 的 `manifest.merged_pdf_path` 非空（真实 img2pdf 合并）
- [x] 逐条隔离运行与整文件运行结果完全一致（无跨用例污染）
- [x] `core/refund_engine` 不再被任何测试当作「读取方兼容」依据；护栏断言对象为真实读取方
- [x] `requirements.txt` 与已装 venv 一致（新环境可复现）
