# Contract Analysis Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AI refund analysis reliable for both Dongguan driving-system text PDFs and uploaded photo/paper contracts.

**Architecture:** Text-layer PDFs stay on local deterministic rules because that is fastest and least error-prone. Image contracts and scanned PDFs try vision extraction first, then fall back to local OCR only when vision returns no usable text. Later phases add field-level confirmation, async processing, provider separation, and stricter workflow gates.

**Tech Stack:** Python Flask, Vue 3, pytest, pdfplumber, optional PaddleOCR/EasyOCR, OpenAI-compatible vision API.

---

## Phase Overview

1. **Phase 1: Image contract extraction routing**
   - Image files and scanned PDFs should try multimodal vision extraction before slow local OCR.
   - If vision extraction fails or returns too little text, local OCR remains the fallback.
   - Verification: unit tests prove image contracts prefer vision and fall back to OCR.

2. **Phase 2: Contract field confirmation**
   - Add a normalized contract field model for total fee, actual paid, service fee, theory fee, subject 2/3 unit prices, refund clauses, uncertain fields, and evidence snippets.
   - The handler confirms fields before a formal fee plan can be confirmed.

3. **Phase 3: Async contract analysis**
   - Move slow image/vision analysis into a background job with progress state, retry, and non-blocking UI.

4. **Phase 4: Provider configuration split**
   - Separate text model, vision model, OCR strategy, timeout, and max token settings.

5. **Phase 5: Workflow gates and E2E tests**
   - Enforce that reply letters and formal communication conclusions only use confirmed fee plans.
   - Add photo contract, scanned PDF, state transition, and reply generation tests.

---

### Task 1: Route Image Contracts Through Vision First

**Files:**
- Modify: `services/contract_service.py`
- Test: `tests/test_contract_extraction.py`

- [x] **Step 1: Add failing tests for image extraction routing**

Add two tests:

```python
def test_image_contract_prefers_vision_before_local_ocr(monkeypatch, tmp_path):
    from services import contract_service

    image_path = tmp_path / "contract.jpg"
    image_path.write_bytes(b"fake image bytes")
    vision_text = (
        "合同编号DGJP202606260001\n"
        "培训费用合计人民币 3280 元。\n"
        "平台支付金额为 1500 元。\n"
        "综合服务费 1100 元，理论培训费 600 元。\n"
        "第七条 退学退费相关约定。"
    )

    monkeypatch.setattr(contract_service, "extract_contract_text_vision", lambda paths: vision_text, raising=False)
    monkeypatch.setattr(
        contract_service,
        "extract_contract_text_ocr",
        lambda paths: (_ for _ in ()).throw(AssertionError("vision 成功时不应调用本地 OCR")),
        raising=False,
    )

    result = contract_service.extract_contract_text_from_file(str(image_path))

    assert result["source"] == "vision_text"
    assert result["text"] == vision_text
    assert result["can_confirm_fee_plan"] is True
```

```python
def test_image_contract_falls_back_to_local_ocr_when_vision_empty(monkeypatch, tmp_path):
    from services import contract_service

    image_path = tmp_path / "contract.jpg"
    image_path.write_bytes(b"fake image bytes")
    ocr_text = (
        "合同编号DGJP202606260002\n"
        "培训费用合计人民币 3980 元。\n"
        "平台支付金额为 3980 元。\n"
        "第七条 退学退费相关约定。"
    )

    monkeypatch.setattr(contract_service, "extract_contract_text_vision", lambda paths: "", raising=False)
    monkeypatch.setattr(contract_service, "extract_contract_text_ocr", lambda paths: ocr_text, raising=False)

    result = contract_service.extract_contract_text_from_file(str(image_path))

    assert result["source"] == "local_ocr"
    assert result["text"] == ocr_text
```

- [x] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_contract_extraction.py::test_image_contract_prefers_vision_before_local_ocr tests/test_contract_extraction.py::test_image_contract_falls_back_to_local_ocr_when_vision_empty -q
```

Expected before implementation: first test fails because image extraction currently calls local OCR first.

- [x] **Step 3: Implement minimal routing change**

In `extract_contract_text_from_file`, when `ocr_inputs` exists, call `extract_contract_text_vision(ocr_inputs)` first. If returned text length is at least 20 characters, set `source = "vision_text"` and skip OCR. Otherwise, call `extract_contract_text_ocr(ocr_inputs)` and keep `source = "local_ocr"`.

- [x] **Step 4: Run routing tests**

Run:

```bash
python3 -m pytest tests/test_contract_extraction.py::test_image_contract_prefers_vision_before_local_ocr tests/test_contract_extraction.py::test_image_contract_falls_back_to_local_ocr_when_vision_empty -q
```

Expected: both tests pass.

- [x] **Step 5: Run contract extraction regression tests**

Run:

```bash
python3 -m pytest tests/test_contract_extraction.py -q
```

Expected: all contract extraction tests pass.

- [x] **Step 6: Run full test suite**

Run:

```bash
python3 -m pytest -q
```

Expected: all tests pass.

---

## Later Phase Acceptance Criteria

- Photo contracts must not rely on slow EasyOCR as the first path.
- Text PDFs must not call a large model when local rules can parse the standard contract.
- Formal fee confirmation must be blocked when contract fields are uncertain.
- Reply letters must read only confirmed fee plans.
- Slow contract analysis must not freeze the main complaint page.

---

### Task 2: Return Normalized Contract Fields

**Files:**
- Modify: `services/contract_service.py`
- Test: `tests/test_contract_extraction.py`

- [x] **Step 1: Add tests for normalized contract fields**

Added tests for:

```bash
python3 -m pytest tests/test_contract_extraction.py::test_standard_text_pdf_returns_normalized_contract_fields tests/test_contract_extraction.py::test_unclear_contract_analysis_returns_review_contract_fields -q
```

Expected before implementation: both fail because `contract_fields` is not returned.

- [x] **Step 2: Implement field model**

Implemented `contract_fields` with these stable keys:

```text
status
total_fee
actual_paid
service_fee
theory_fee
subject2_unit_price
subject2_cap
subject3_unit_price
subject3_cap
penalty_rate
refund_clause
uncertain_fields
blockers
evidence_snippets
extraction_source
```

- [x] **Step 3: Attach extraction review state**

All analysis paths now attach the same field model:

```text
standard text PDF -> status=draft
LLM/vision text -> status=draft unless extraction flags uncertainty
unclear contract -> status=needs_review with blockers and uncertain_fields
```

- [x] **Step 4: Verify tests**

Run:

```bash
python3 -m pytest tests/test_contract_extraction.py -q
python3 -m pytest tests/test_case_workbench.py tests/test_contract_pipeline.py -q
python3 -m pytest -q
```

Actual result:

```text
12 passed
4 passed
30 passed
```

---

### Task 3: Show Contract Field Review Panel

**Files:**
- Modify: `templates/index.html`
- Modify: `static/css/style.css`
- Test: `tests/test_contract_extraction.py`

- [x] **Step 1: Add static UI regression test**

Added a test that asserts the AI analysis section includes:

```text
合同字段核对
ar.contract_fields
contract-fields-panel
```

- [x] **Step 2: Add field review panel**

The AI refund analysis section now shows a compact review panel when `ar.contract_fields` exists:

```text
合同总培训费
实际已交
综合服务费
理论培训费
科二单价/上限
科三单价/上限
违约金比例
退费条款
```

- [x] **Step 3: Add responsive styles**

Added compact grid styles for desktop and small screens using `auto-fit` and short field cards.

- [x] **Step 4: Verify**

Run:

```bash
python3 -m pytest tests/test_contract_extraction.py -q
python3 -m pytest -q
NO_PROXY=127.0.0.1,localhost curl -s http://127.0.0.1:5003/ | rg -n "合同字段核对|contract-fields-panel|vision_text"
```

Actual result:

```text
13 passed
31 passed
server template contains the new panel
browser console errors: []
```

---

### Task 4: Edit Contract Fields And Sync Fee Calculation

**Files:**
- Modify: `templates/index.html`
- Modify: `static/css/style.css`
- Modify: `static/js/composables/useWorkflow.js`
- Test: `tests/test_contract_extraction.py`

- [x] **Step 1: Add regression test**

Added a static test that verifies:

```text
contract field inputs use v-model.number
field changes call applyContractFields
workflow exports applyContractFields
fixed fee sync uses upsertDeductionFromField
```

- [x] **Step 2: Make contract fields editable**

The field review panel now allows editing:

```text
合同总培训费
实际已交
综合服务费
理论培训费
科二单价/上限
科三单价/上限
违约金比例
```

- [x] **Step 3: Sync edits into fee calculation**

Implemented `applyContractFields()`:

```text
updates ar.total_fee
updates ar.actual_paid
updates ar.penalty_rate
upserts 综合服务费 and 理论培训费 deductions
updates 科目二/科目三实操费 unit price and cap when items exist
recalculates total deduction and refund through existing recalc()
marks fee confirmation as stale
```

- [x] **Step 4: Remove duplicate basic fee inputs**

The old total fee / actual paid inputs now show only when `contract_fields` is absent.

- [x] **Step 5: Verify**

Run:

```bash
python3 -m pytest tests/test_contract_extraction.py -q
python3 -m pytest -q
NO_PROXY=127.0.0.1,localhost curl -s http://127.0.0.1:5003/ | rg -n "applyContractFields|合同字段核对|contract-fields-panel"
```

Actual result:

```text
14 passed
32 passed
server template contains editable contract fields
browser page loads with no console errors
```
