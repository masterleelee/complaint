# v1 P0 Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or TDD task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the live complaint workflow obey the PRD's financial, identity, and evidence invariants, rather than only satisfying isolated unit tests.

**Architecture:** Retain Flask, Vue, and SQLite. Route live fee-plan creation through the existing pure `core.refund_engine.calculate_fee_plan`; store a versioned immutable confirmed snapshot; treat contract extraction as facts only. Reject ambiguous identities and incomplete query results from cache. Keep uploaded originals immutable and use derived files for compression/OCR.

**Tech Stack:** Python, Flask, SQLite, pytest, Vue 3.

## Global Constraints

- Do not add payment execution, finance approval, roles, or permissions.
- AI and the browser may supply candidate facts and human overrides; only the backend rule engine computes official amounts.
- A confirmed plan may only change through an explicit reopen action with reason.
- Do not revert unrelated dirty worktree changes.
- Every behavior change follows RED → GREEN → focused regression.

---

## Execution Record

- 2026-07-11: Task 1 completed: ambiguous phone lookup no longer chooses a record, driving lookup requires exact identity, and incomplete query results are not cached.
- 2026-07-11: Task 2 completed for original preservation, canonical path checks, and TLS verification. Derived images are used for OCR/vision while originals remain unchanged.
- 2026-07-12: Task 3 completed: deterministic PDF, AI-extracted image/PDF candidates, and manual contract entry all form a canonical contract set. Confirmation recalculates through `core.refund_engine` and ignores client totals/deductions.
- 2026-07-12: Task 4 completed: a confirmed snapshot is stored, external outputs read it, generic updates/re-analysis cannot overwrite it, and explicit reopen requires a reason while marking any reply as outdated.
- 2026-07-12: Task 5 completed for authority cleanup and smoke QA: the browser no longer sends deductions, and local desktop/mobile rendering plus navigation were verified. Historical plan retirement remains documentation housekeeping, not a functional blocker.

---

## File Structure

- `core/refund_engine.py`: pure fee-plan computation (existing authoritative calculator).
- `services/contract_service.py`: contract facts adapter; no official amount computation or insecure TLS bypass.
- `core/case_workflow.py`: fee-plan reconstruction, confirmation, snapshot and reopen policy.
- `app.py`: thin HTTP adapters; no client-side arithmetic accepted as official.
- `core/query_engine.py`, `crawlers/internal.py`, `crawlers/driving.py`: exact identity and complete-cache behavior.
- `services/file_service.py`: original/derived-file and canonical path helpers.
- `static/js/composables/useWorkflow.js`: submit facts/overrides and render backend output only.
- `tests/test_*.py`: behavior tests and regressions.

### Task 1: Exact identity and complete-result cache

**Files:** `crawlers/internal.py`, `crawlers/driving.py`, `core/query_engine.py`, query test files.

- [ ] Add RED tests: phone lookup with multiple identity candidates is ambiguous; driving lookup with no exact ID is `not_found`; a merged result containing timeout/error is not cached.
- [ ] Implement: never select index zero as a fallback; return an explicit ambiguous/not-found result; cache only when all required systems have terminal non-error states.
- [ ] Run focused query suites and full regression.

### Task 2: Preserve original contracts and restore TLS verification

**Files:** `services/file_service.py`, `app.py`, `services/contract_service.py`, `services/visit_llm.py`, tests.

- [ ] Add RED tests: an uploaded image remains byte-identical after preparing a derived compressed/OCR file; LLM calls do not pass `verify=False`; a sibling-prefix path is rejected.
- [ ] Implement canonical `realpath/commonpath` validation and derived-file creation; use normal certificate verification.
- [ ] Run file, contract and LLM regression suites.

### Task 3: Route live analysis and confirmation through the rule engine

**Files:** `services/contract_service.py`, `app.py`, `core/case_workflow.py`, tests.

- [ ] Add RED API tests proving confirmation ignores client supplied totals and only accepts a structured contract set plus progress facts/allowed manual overrides.
- [ ] Adapt extracted contract facts into the canonical multi-contract input expected by `calculate_fee_plan`.
- [ ] Make contract analysis return a rule-engine draft; remove the old official-calculation path from live APIs.
- [ ] Run golden contract and case-workbench regressions.

### Task 4: Immutable confirmed snapshot and explicit reopen

**Files:** `core/case_workflow.py`, `database.py`, `app.py`, tests.

- [ ] Add RED tests: generic ticket update and re-analysis cannot overwrite a confirmed plan; explicit reopen requires reason, preserves prior snapshot, and invalidates generated monetary replies.
- [ ] Persist a full confirmed snapshot and use it for reply, Feishu and completion outputs.
- [ ] Run case-workbench regressions.

### Task 5: Frontend authority cleanup and acceptance evidence

**Files:** `static/js/composables/useWorkflow.js`, `templates/index.html`, browser/E2E tests or acceptance docs, `docs/acceptance/v1-requirements-matrix.md`.

- [ ] Add RED static/behavior tests proving the browser does not compute or submit official deductions and supports backend validation errors visibly.
- [ ] Replace browser arithmetic with backend draft/recalculate calls while retaining human override fields and evidence.
- [ ] Run desktop/mobile core-flow browser verification, full pytest, Python compile and JS syntax checks.
- [ ] Update the acceptance matrix and retire completed/conflicting historical plans.
