# PRD v1 Code Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the current Flask + Vue complaint system with `docs/PRD.md` v1 for organization-unit statistics, fee-plan states, contract parsing rules, reply gates, and three-system authority.

**Architecture:** Keep the current Flask monolith and Vue composables, but extract small domain helpers where they remove real duplication. Use database migrations that preserve existing `school_short` behavior while adding canonical organization-unit fields. Keep AI as extraction fallback and put formal money decisions behind explicit fee-plan states.

**Tech Stack:** Python Flask, SQLite, pytest, Vue 3 browser globals, python-docx, pdfplumber.

## Global Constraints

- Do not implement payment execution, finance approval, account/role permissions, or refund amount statistics.
- Do not let AI草案 generate external money conclusions.
- Keep changes surgical; do not rewrite the app shell or refactor unrelated crawler code.
- Existing dirty worktree changes must not be reverted.
- Verify each task with focused pytest commands before continuing.

---

## File Structure

- Create `services/org_unit_service.py`: canonical organization-unit dictionary and normalization helpers.
- Modify `database.py`: schema fields, JSON serialization, organization-unit normalization, effective complaint statistics.
- Modify `app.py`: fee-plan state gates, cancellation archive exception, provisional/formal reply rules.
- Modify `services/contract_service.py`: no default penalty, old/new Dongguan template parsers, clearer fee-plan extraction status.
- Modify `core/query_engine.py`: stop inferring primary exam stage from third-system training hours.
- Modify `static/js/composables/useWorkflow.js`: frontend status checks for provisional/formal fee plans and progress replies.
- Modify `static/js/composables/useHistory.js`: read new statistics fields without breaking current charts.
- Modify tests in `tests/test_case_workbench.py`, `tests/test_contract_extraction.py`, `tests/test_contract_pipeline.py`, and `tests/test_driving_query_performance.py`.

---

### Task 1: Organization Unit Dictionary and Effective Complaint Statistics

**Files:**
- Create: `services/org_unit_service.py`
- Modify: `database.py`
- Modify: `app.py`
- Modify: `static/js/composables/useHistory.js`
- Test: `tests/test_case_workbench.py`

**Interfaces:**
- Produces `services.org_unit_service.ORGANIZATION_UNITS: list[dict]`
- Produces `services.org_unit_service.resolve_org_unit(code: str = "", name: str = "") -> dict | None`
- Produces `services.org_unit_service.normalize_ticket_org_fields(data: dict) -> dict`
- Updates `database.get_ticket_statistics(start_date="", end_date="") -> dict` with `effective_total`, `cancelled_total`, `by_school[].count` as effective count, and no meaningful refund aggregation.

Steps:

- [ ] Add failing tests for dictionary normalization and撤销排除.
- [ ] Run focused tests and verify failure.
- [ ] Add the organization-unit service.
- [ ] Add database columns `organization_unit_id`, `organization_unit_type`, `organization_unit_name`, `organization_unit_code`, `cancellation_date`, `cancellation_source`, `cancellation_note`.
- [ ] Normalize org fields inside `save_ticket()`.
- [ ] Rewrite `get_ticket_statistics()` so `by_school` uses effective complaints and reports cancellation totals.
- [ ] Add `/api/organization-units` for frontend dropdowns.
- [ ] Update history chart mapping to prefer `item.name || item.school`.
- [ ] Run focused tests and verify pass.

Verification:

```bash
python3 -m pytest tests/test_case_workbench.py::test_ticket_normalizes_organization_unit_from_school_short tests/test_case_workbench.py::test_statistics_use_effective_complaint_count_and_exclude_cancelled -q
```

---

### Task 2: Fee Plan States, Version History, and Output Gates

**Files:**
- Modify: `database.py`
- Modify: `app.py`
- Modify: `services/reply_service.py`
- Modify: `static/js/composables/useWorkflow.js`
- Test: `tests/test_case_workbench.py`

**Interfaces:**
- Fee plan statuses are `draft`, `needs_review`, `provisional`, and `confirmed`.
- `confirmed` means formal fee plan.
- `provisional` means stage fee plan, allowed only for progress replies.
- `database` stores `fee_plan_version` and `fee_plan_history`.
- `/api/tickets/<ticket_id>/fee-confirm` accepts `plan_status: "provisional" | "confirmed"`.
- `/api/reply/generate` accepts `document_type: "formal" | "progress"`.

Steps:

- [ ] Add failing tests for provisional confirmation, formal reply rejection with provisional status, progress reply allowance, reconfirm history, and撤销归档 without fee plan.
- [ ] Run focused tests and verify failure.
- [ ] Add fee-plan version columns and JSON serialization.
- [ ] Update fee-confirm API to support provisional/formal confirmation and append history when replacing a confirmed/provisional plan.
- [ ] Update reply gates so formal replies require `confirmed`, progress replies accept `provisional` or `confirmed`.
- [ ] Update completion gate so `投诉撤销` can be archived without fee plan and communication requirement.
- [ ] Update frontend fee status checks and progress reply payload.
- [ ] Run focused tests and verify pass.

Verification:

```bash
python3 -m pytest tests/test_case_workbench.py::test_fee_confirm_supports_provisional_plan tests/test_case_workbench.py::test_formal_reply_rejects_provisional_plan_but_progress_reply_allows_it tests/test_case_workbench.py::test_reconfirm_fee_plan_requires_reason_and_keeps_history tests/test_case_workbench.py::test_cancelled_ticket_can_archive_without_fee_plan -q
```

---

### Task 3: Contract Parser PRD Corrections

**Files:**
- Modify: `services/contract_service.py`
- Test: `tests/test_contract_pipeline.py`
- Test: `tests/test_contract_extraction.py`

**Interfaces:**
- `_extract_penalty_rate(text: str) -> float` returns `0` when no explicit penalty exists.
- `_parse_ai_response(content, exam_counts=None, training_hours=None) -> dict` never invents 20% penalty.
- `_extract_standard_contract_data(text: str) -> dict` supports both old 9-page Dongguan template and new 7-page template.

Steps:

- [ ] Add failing tests for no default penalty and赵雨莹 new PDF template.
- [ ] Run focused tests and verify failure.
- [ ] Change penalty extraction default from 20% to 0.
- [ ] Change `_parse_ai_response()` default penalty handling from 20% to 0.
- [ ] Extend `_extract_standard_contract_data()` with new-template regexes for `培训服务费合计`, `综合服务费`, `理论培训费`, second/third practical training fee, and 10% penalty.
- [ ] Run focused tests and existing contract tests.

Verification:

```bash
python3 -m pytest tests/test_contract_pipeline.py::test_parse_ai_response_does_not_invent_default_penalty tests/test_contract_extraction.py::test_new_dongguan_pdf_template_extracts_without_llm tests/test_contract_pipeline.py::test_actual_paid_defaults_to_total_fee_not_model_platform_payment -q
python3 -m pytest tests/test_contract_extraction.py tests/test_contract_pipeline.py -q
```

---

### Task 4: Three-System Authority Cleanup

**Files:**
- Modify: `core/query_engine.py`
- Test: `tests/test_driving_query_performance.py`

**Interfaces:**
- Third-system training hours populate `training_hours` only.
- Third-system training hours do not infer or overwrite primary `exam_stage`.

Steps:

- [ ] Add failing test proving third-system hours do not infer exam stage when internal stage is blank.
- [ ] Run focused test and verify failure.
- [ ] Remove the inference block in `QueryEngine._merge_results()`.
- [ ] Run focused query tests.

Verification:

```bash
python3 -m pytest tests/test_driving_query_performance.py::test_query_engine_does_not_infer_exam_stage_from_third_system_hours -q
python3 -m pytest tests/test_driving_query_performance.py -q
```

---

### Task 5: Integrated Regression

**Files:**
- No new feature files.

Steps:

- [ ] Run all focused suites touched by this plan.
- [ ] Run Python compilation on modified Python modules.
- [ ] Search for removed anti-patterns: default 20 penalty, refund amount statistics in dashboard API, third-system stage inference.
- [ ] Record remaining gaps for future work.

Verification:

```bash
python3 -m pytest tests/test_case_workbench.py tests/test_contract_extraction.py tests/test_contract_pipeline.py tests/test_driving_query_performance.py -q
python3 -m py_compile app.py database.py services/org_unit_service.py services/contract_service.py services/reply_service.py core/query_engine.py
rg -n "0\\.20|refund_sum|用培训最多的阶段推断|fee_plan_status\\).*confirmed" app.py database.py services core static/js/composables
```
