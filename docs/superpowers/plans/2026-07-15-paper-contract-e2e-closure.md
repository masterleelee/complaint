# 纸质合同真实案件闭环修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让多页纸质合同在不依赖外部模型时也能完成“上传、识别失败降级、人工核对、规则引擎重算、确认、沟通、归档、重开更正”的可审计闭环。

**Architecture:** 上传阶段持久化有序页面 manifest，原件只读、OCR 使用高分辨率派生图、合并 PDF 只负责预览。识别结果是候选字段；人工核对后生成 canonical `contract_set`，费用确认只能由后端 `refund_engine` 重算。归档更正以费用版本为边界，旧沟通和旧回复函不能自动证明新版本已经沟通。

**Tech Stack:** Python Flask、SQLite、Vue 3、pytest、Pillow、img2pdf。

## Global Constraints

- 以 `docs/PRD.md` 和 `docs/acceptance/v1-requirements-matrix.md` 为准。
- 不把真实合同图片、身份证号、手机号、住址或 OCR 全文加入仓库、测试输出和日志。
- 不调用外部视觉模型或文本模型；本次真实样本只走本地 OCR 和人工核对降级。
- 原始合同文件不可覆盖；派生图和合并 PDF 必须与原件区分。
- AI/OCR 只提供候选字段；正式金额必须由后端规则引擎计算。
- 每个生产代码修改必须先有失败的回归测试，并记录 RED 与 GREEN 命令结果。
- 当前工作区已有大量用户修改；不得重置、格式化或提交无关文件，不创建 git commit。

---

### Task 1: 多页纸质合同上传、恢复与失败重试

**Files:**
- Modify: `services/image_compressor.py`
- Modify: `app.py`
- Modify: `database.py`
- Modify: `static/js/composables/useWorkflow.js`
- Modify: `static/js/app.js`
- Modify: `templates/index.html`
- Test: `tests/test_case_workbench.py`
- Test: `tests/test_contract_extraction.py`

**Interfaces:**
- Upload response produces `manifest.source_files[]`, `manifest.merged_pdf_path`, `manifest.analysis_image_paths[]`, `manifest.upload_count`.
- `complaint_tickets.contract_manifest` persists the JSON object at upload time when `ticket_id` exists.
- `filepath` is the merged PDF for multi-page previews and the original source for a single page.
- `image_paths` remains an ordered list of analysis copies.

- [x] **Step 1: Write failing upload tests**

  Add tests proving: two selected pages remain in request order; duplicate content is analysed once; `upload_count == 2`; merged PDF is not counted as a third upload; portrait analysis copies retain at least 1600 pixels on the long edge; upload with `ticket_id` immediately persists the manifest.

- [x] **Step 2: Run RED**

  Run: `pytest tests/test_case_workbench.py -k 'contract_upload and (manifest or portrait or order or duplicate)' -v`

  Expected: failures showing missing manifest, wrong count/order and 1080-pixel long edge.

- [x] **Step 3: Implement upload manifest**

  Preserve incoming page order. Hash original bytes with SHA-256 and omit later duplicate content from analysis while keeping the first page. Return source file path, analysis path, page index and hash. Use a document compression profile whose portrait long edge is at least 1920 pixels. Bind the manifest and preview path to the ticket before analysis starts.

- [x] **Step 4: Write failing restore/retry frontend tests**

  Assert restored tickets populate all manifest pages, history restore does not auto-start a new analysis, failed/interrupted analysis renders both “重试分析” and “返回修改合同”, and explicit retry reuses the manifest image paths.

- [x] **Step 5: Implement restore/retry UI**

  Remove the workflow-step watcher side effect that auto-runs analysis. Add an explicit analysis button, retry action, and always-visible return action. Restore `uploadedFiles` from `contract_manifest` and preview the merged PDF.

- [x] **Step 6: Run GREEN**

  Run: `pytest tests/test_case_workbench.py tests/test_contract_extraction.py -q`

---

### Task 2: OCR 不清时人工核对生成 canonical contract_set

**Files:**
- Modify: `services/contract_service.py`
- Modify: `app.py`
- Modify: `static/js/composables/useWorkflow.js`
- Modify: `templates/index.html`
- Test: `tests/test_contract_extraction.py`
- Test: `tests/test_case_workbench.py`

**Interfaces:**
- Add `contract_set_from_reviewed_fields(fields, source_file, contract_code="", evidence=None) -> dict`.
- Reviewed fields produce fixed rules for service/archive/IC-card fees, dynamic `exam_fee`/`makeup_fee`, `training_hour_fee`, and `percentage_penalty` rules.
- Frontend confirmation always sends a reviewed candidate `contract_set`; backend accepts it only when the ticket has no saved canonical contract set, then recalculates with `calculate_saved_fee_plan`.

- [x] **Step 1: Write failing rule-conversion tests**

  Cover a PII-free paper-contract fixture: total 3680; service 600; archive 300; IC card 100; subject-2/3 hourly rates 120/150; exam fees 70/130/280; makeup fees 35/65/140; penalty 20%; refund clause present. Assert produced rule types and evidence, and assert no client-calculated total deduction is trusted.

- [x] **Step 2: Run RED**

  Run: `pytest tests/test_contract_extraction.py -k reviewed_fields -v`

  Expected: failure because `contract_set_from_reviewed_fields` does not exist.

- [x] **Step 3: Implement server conversion and UI fields**

  Expose archive fee, IC-card fee, exam/makeup tables and refund clause in the review panel. Generate the candidate `contract_set` from reviewed fields and original page evidence. Do not convert exam/training/penalty rows to fixed deductions.

- [x] **Step 4: Write failing real-frontend-payload confirmation test**

  Create a temporary ticket with `fee_plan_status=needs_review`, no `contract_set`, and zero progress. Submit exactly the JSON fields used by `handleSaveAndConfirm`; assert HTTP 200, persisted canonical contract facts, backend-calculated total deduction 1736 and refund 1944 when actual paid is explicitly set to 3680.

- [x] **Step 5: Implement minimal confirmation bridge**

  Persist the candidate only after schema validation. Recalculate with `refund_engine`; never use client `deductions` or client totals as the formal result.

- [x] **Step 6: Run GREEN**

  Run: `pytest tests/test_contract_extraction.py tests/test_case_workbench.py -q`

---

### Task 3: 归档后费用更正的版本化闭环

**Files:**
- Modify: `database.py`
- Modify: `app.py`
- Modify: `static/js/composables/useWorkflow.js`
- Modify: `templates/index.html`
- Test: `tests/test_case_workbench.py`
- Test: `tests/test_contract_extraction.py`

**Interfaces:**
- Communication records persist `fee_plan_version` at creation.
- Re-archive requires at least one communication whose version equals the current confirmed fee-plan version, except `投诉撤销`.
- Reconfirming a reopened plan does not clear `reply_outdated`; only generating a new reply for the current version clears it.
- Archived cases expose a “重新打开费用方案” action requiring a non-empty reason.

- [x] **Step 1: Write failing version-safety tests**

  Cover: archive v1 → reopen → confirm v2 → rearchive rejected with only v1 communication; add v2 communication → rearchive succeeds; old reply remains outdated after v2 confirmation; frontend exposes reopen action and reason input.

- [x] **Step 2: Run RED**

  Run: `pytest tests/test_case_workbench.py -k 'reopen or rearchive or communication_version or reply_outdated' -v`

- [x] **Step 3: Implement version rules and UI**

  Add the communication version column by migration, stamp new records from the current ticket version, update completion gate, preserve old reply invalidation, and route the UI back to contract/fee review after successful reopen. Remove the redundant second status PUT after archive.

- [x] **Step 4: Run GREEN**

  Run: `pytest tests/test_case_workbench.py tests/test_contract_extraction.py -q`

---

### Task 4: 真实学员本地闭环验收

**Files:**
- Modify: `docs/acceptance/v1-requirements-matrix.md`
- Do not add the real images to the repository.

- [x] **Step 1: Use the latest open ticket explicitly**

  Reuse the selected open ticket instead of creating a sixteenth implicit ticket. Mark all newly written notes as system test evidence, not a real complaint decision.

- [x] **Step 2: Upload both pages and verify manifest**

  Confirm two source pages, one merged preview, ordered hashes, persisted ticket binding, and no external model request.

- [x] **Step 3: Run local OCR and manual-review fallback**

  Confirm local OCR recognises two pages but leaves handwritten monetary fields for manual confirmation. Enter only contract-grounded values; do not invent actual paid amount, training hours or exam attempts. If those facts are unavailable, stop at `needs_input` and record the blocker rather than fabricate a formal refund.

- [x] **Step 4: Verify correction loop in a temporary cloned ticket**

  Use a PII-free temporary database copy to run confirmed v1 → communication v1 → archive → reopen → confirmed v2 → communication v2 → rearchive, including reply invalidation.

- [x] **Step 5: Fresh verification**

  Run: `pytest`

  Run: `python3 -m py_compile app.py database.py services/contract_service.py services/image_compressor.py`

  Run: `node --check static/js/app.js && node --check static/js/composables/useWorkflow.js && git diff --check`

  Run desktop and mobile browser smoke tests for upload, retry, manual review and reopen controls. Update the acceptance matrix with exact passed and externally blocked items.
