# 03: 合同集合分条落库 + 正文落库

**Parent:** 规格 `spec.md`

**What to build:** 工单的合同集合字段由「单份 dict」升级为「按份类型分条」：每份含份类型（kind：服务/代缴/培训/单一培训）、档位（分析时回填，可暂空）、文件引用、正文（含提取来源与置信度）、分析结果。旧的单份结构在读取时兼容为单条。上传接口保存每份文件时写入分条结构；分析产出的正文不再被剔除、随结果落库（ADR-0002 的硬前提）。归档、requery 等既有读取方不感知变化。

**Blocked by:** None (can start immediately，可与 01 并行)

**Status:** done — 服务层 normalize/attach/merge + 上传分条 + 真实读取方护栏全部到位；P0 前置修复见 `00-preflight-fixes.md`（2026-09-01）。回归基线 345 passed / 0 failed。

- [x] 一次上传 2 份 → 集合含 2 条，各自带文件引用与正文（`test_upload_two_pdfs_creates_two_entries` + `test_upload_pdf_plus_images_creates_two_entries`）
- [x] 旧结构工单读取后表现为单条（`test_legacy_ticket_contract_set_reads_as_single_entry`）
- [x] 正文带来源标记（pdf_text / vision_text / local_ocr）与置信度（`test_text_confidence_mapping` + `test_analysis_result_carries_text`）
- [x] 归档流程与 requery 读合同集合不报错——护栏从 refund_engine 换为 normalize 幂等 + `_ticket_contract_paths` 稳定（ADR-0003）；requery 不读 contract_set（grep 全项目零引用）
- [x] HTTP 层可验证：上传 → 读回分条结构（测试客户端，`test_archive_paths_reader_unaffected_by_new_structure`）

