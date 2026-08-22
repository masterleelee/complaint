# AI Refund Analysis Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `actual_paid` default to the contract total, remove duplicate analysis summaries, and implement the approved task-first responsive layout.

**Architecture:** Keep contract extraction and fee calculation in `services/contract_service.py`; the backend initializes the editable fee base from `total_fee`. Keep the existing Vue composable and template, but make the amount overview the single editing surface and move contract evidence into a collapsible review panel.

**Tech Stack:** Python 3, Flask, pytest, Vue 3 Composition API, CSS

---

### Task 1: Correct the actual-paid default

**Files:**
- Modify: `tests/test_contract_extraction.py`
- Modify: `tests/test_contract_pipeline.py`
- Modify: `services/contract_service.py`

- [ ] **Step 1: Change the standard PDF regression assertions**

Update the standard contract expectations from platform payment `1500` to contract total `3280`:

```python
assert result["total_fee"] == 3280
assert result["actual_paid"] == 3280
assert "actual_paid" not in fields
```

- [ ] **Step 2: Add an LLM response regression test**

```python
def test_actual_paid_defaults_to_total_fee_not_model_platform_payment():
    extracted = {
        "total_fee": 3880,
        "actual_paid": 1500,
        "penalty_rate": 0.2,
        "deduction_items": [],
    }
    result = _parse_ai_response(json.dumps(extracted), {}, {})
    assert result["actual_paid"] == 3880
```

- [ ] **Step 3: Run the tests and confirm RED**

Run:

```bash
pytest -q \
  tests/test_contract_extraction.py::test_standard_text_pdf_uses_local_rules_without_llm \
  tests/test_contract_extraction.py::test_standard_text_pdf_returns_normalized_contract_fields \
  tests/test_contract_pipeline.py::test_actual_paid_defaults_to_total_fee_not_model_platform_payment
```

Expected: failures showing `actual_paid` is `1500`.

- [ ] **Step 4: Implement the backend default**

In `_extract_standard_contract_data`, return:

```python
"actual_paid": total_fee,
```

In `_parse_ai_response`, ignore model-provided actual-paid values:

```python
result["actual_paid"] = result["total_fee"]
```

- [ ] **Step 5: Run the focused tests and confirm GREEN**

Run the same focused command. Expected: `3 passed`.

### Task 2: Implement the task-first analysis layout

**Files:**
- Modify: `tests/test_contract_extraction.py`
- Modify: `templates/index.html`
- Modify: `static/js/composables/useWorkflow.js`
- Modify: `static/css/style.css`

- [ ] **Step 1: Add static UI contract tests**

Add assertions that the analysis section:

```python
assert "analysis-amount-input" in analysis_section
assert "合同字段核对" in analysis_section
assert "退费基数认定" not in analysis_section
assert "分析参数" not in analysis_section
assert 'v-model.number="ar.contract_fields.actual_paid.value"' not in analysis_section
assert "summary_lines" not in analysis_section
```

Also assert the CSS contains a `390px` mobile layout for the amount grid and fee rows.

- [ ] **Step 2: Run the UI test and confirm RED**

Run:

```bash
pytest -q tests/test_contract_extraction.py::test_ai_analysis_uses_single_task_first_amount_surface
```

Expected: failure because the current template still contains duplicate summary and editing surfaces.

- [ ] **Step 3: Replace the amount overview**

Render four compact amount cells. Make only `actual_paid` editable:

```html
<input
  class="analysis-amount-input"
  type="number"
  v-model.number="ar.actual_paid"
  @change="recalc"
  aria-label="实际已交金额">
```

Remove the analysis-parameter block, summary strip, `summary_lines`, and actual-paid field from the contract review grid.

- [ ] **Step 4: Collapse contract review details**

Use a native `details` element:

```html
<details class="contract-fields-panel" :open="ar.can_confirm_fee_plan === false">
  <summary class="contract-fields-head">合同字段核对</summary>
  <div class="contract-fields-grid">...</div>
</details>
```

Keep recognition source and evidence snippets inside the panel.

- [ ] **Step 5: Stop generating duplicate summary text**

In `recalc()`, keep the authoritative numeric calculations and remove construction of `summary`, `summary_lines`, and repeated total-fee comparison prose.

- [ ] **Step 6: Add responsive CSS**

Use a four-column desktop amount grid, a `2 × 2` amount grid at `max-width: 640px`, and transform each fee row into a readable stacked grid on mobile without horizontal scrolling.

- [ ] **Step 7: Run the focused UI test and confirm GREEN**

Run:

```bash
pytest -q tests/test_contract_extraction.py::test_ai_analysis_uses_single_task_first_amount_surface
node --check static/js/composables/useWorkflow.js
```

Expected: test passes and JavaScript syntax check exits `0`.

### Task 3: Regression and rendered verification

**Files:**
- Verify only

- [ ] **Step 1: Run contract and workbench tests**

```bash
pytest -q tests/test_contract_extraction.py tests/test_contract_pipeline.py tests/test_case_workbench.py
```

Expected: all tests pass.

- [ ] **Step 2: Run the full suite**

```bash
pytest -q
```

Expected: all tests pass.

- [ ] **Step 3: Restart the Flask server**

Stop the stale process on port `5003`, start `python3 app.py`, and verify `http://127.0.0.1:5003/` serves the current code.

- [ ] **Step 4: Verify desktop**

Open the AI refund analysis result and check:

- Four core amounts are visible.
- Actual paid defaults to contract total.
- Editing actual paid updates refund.
- Duplicate summary prose is absent.
- Contract review is collapsed when no blockers exist.

- [ ] **Step 5: Verify mobile**

At `390px` width check:

- Amounts render as `2 × 2`.
- Fee items are readable and editable.
- No horizontal overflow.
- The confirm action does not cover content.

- [ ] **Step 6: Check browser console**

Verify there are no relevant Vue, JavaScript, or network errors in the target flow.
