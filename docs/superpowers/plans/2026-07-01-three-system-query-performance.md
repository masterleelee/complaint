# Three-System Query Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce avoidable internal-system latency, preserve three-system data accuracy, and let users see completed system results without waiting for the slowest external system.

**Architecture:** Keep the existing three-way parallel query engine and synchronous crawler implementations. First add phase-level timing and a repeatable benchmark, then fix stale-session handling without adding extra requests to every healthy query. Only after actual latency is stable, add a polled query job that exposes partial results while the slowest system continues in the background.

**Tech Stack:** Python 3, Flask, `requests.Session`, `ThreadPoolExecutor`, pytest, Vue 3

---

## Measured Baseline

Measured on 2026-07-01 with one authorized test identity:

| Path | Result |
|---|---:|
| Existing in-memory result cache | `0.006s` |
| Three-system cold query wall time | `10.551s` |
| Same cold query, internal component | `3.611s` |
| Same cold query, third-system component | `4.273s` |
| Same cold query, driving component | `10.549s` |
| Healthy internal query, 3 runs | `1.738–3.286s`, median `2.253s` |
| Healthy third-system query, 3 runs | `1.219–2.272s`, median `1.249s` |
| Healthy driving query, 3 runs | `4.300–5.723s`, median `4.390s` |
| Reproduced stale internal session | `6.420s` |
| User-observed internal duration retained in query result | `12.538s` |

The cold wall time and slowest-system time differ by only `0.002s`, so the current parallel scheduler is not adding meaningful completion latency.

The stale internal-session reproduction performed:

1. Failed student-list request: `1.501s`.
2. Captcha login: `1.635s`.
3. Repeated student-list request: `1.833s`.
4. Timeline and fee enrichment: up to `1.436s`.

The local authentication cache currently treats all three systems as valid for `7200s`. Recent logs show the internal system requiring a new login much earlier, so a locally “valid” session can already be invalid on the server.

## Non-Goals

- Do not remove timeline, examination-count, fee, training-hour, or contract-related data.
- Do not lower accuracy by treating cached data as final current data.
- Do not replace `requests` with an async HTTP library without benchmark evidence.
- Do not increase retry counts.
- Do not query the three systems sequentially.

### Task 1: Establish a deterministic query benchmark

**Files:**
- Create: `scripts/benchmark_three_system_query.py`
- Create: `tests/test_query_performance_instrumentation.py`
- Modify: `core/query_engine.py`

- [ ] **Step 1: Write a failing timing-schema test**

The test must require each `QueryResult` to expose phase timings without personal data:

```python
def test_query_result_exposes_phase_timings():
    result = QueryResult(
        system="internal",
        status=QueryStatus.SUCCESS,
        phase_durations_ms={"auth_wait": 10, "student_list": 20},
    )
    assert result.phase_durations_ms["student_list"] == 20
```

- [ ] **Step 2: Run the test and verify RED**

```bash
pytest -q tests/test_query_performance_instrumentation.py::test_query_result_exposes_phase_timings
```

Expected: `QueryResult` rejects or lacks `phase_durations_ms`.

- [ ] **Step 3: Add phase timing fields**

Add `phase_durations_ms: Dict[str, int] = field(default_factory=dict)` to `QueryResult` and `system_phase_durations_ms` to `MergedStudentInfo`. Record only endpoint labels and durations, never identifiers, response bodies, cookies, usernames, or passwords.

- [ ] **Step 4: Add a read-only benchmark command**

The command must accept the test identity through `TEST_ID_CARD`, bypass only the five-minute result cache, preserve authentication caches, and report JSON lines for:

- wall time;
- each system total;
- login wait;
- main lookup;
- internal timeline;
- internal fee lookup;
- retry count;
- cache hit.

It must not create complaint tickets or write benchmark records.

- [ ] **Step 5: Run a baseline matrix**

```bash
TEST_ID_CARD='***' python3 scripts/benchmark_three_system_query.py \
  --runs 10 \
  --scenarios healthy,stale-session,repeat
```

Expected output: p50, p95, maximum, retry count, and raw JSON lines for each scenario.

### Task 2: Fix stale internal sessions without penalizing healthy queries

**Files:**
- Modify: `core/auth_manager.py`
- Modify: `crawlers/internal.py`
- Modify: `app.py`
- Test: `tests/test_query_performance_instrumentation.py`

- [ ] **Step 1: Write stale-session and network-error tests**

Required behaviors:

```python
def test_internal_auth_expiry_relogs_once_and_retries_once(monkeypatch):
    crawler = InternalCrawler()
    calls = []
    relogins = []

    class ExpiredResponse:
        text = '<form action="userController/login.action"></form>'

        def json(self):
            raise ValueError("login page is not JSON")

    class ValidResponse:
        text = '{"rows":[]}'

        def json(self):
            return {"rows": []}

    responses = iter([ExpiredResponse(), ValidResponse()])
    monkeypatch.setattr(
        crawler,
        "post",
        lambda *args, **kwargs: calls.append(args[0]) or next(responses),
    )
    monkeypatch.setattr(crawler, "logout", lambda: None)
    monkeypatch.setattr(
        crawler,
        "ensure_login",
        lambda: relogins.append("login") or True,
    )

    assert crawler.query_student("TEST-ID") is None
    assert len(calls) == 2
    assert relogins == ["login"]


def test_internal_network_timeout_does_not_force_login(monkeypatch):
    crawler = InternalCrawler()
    monkeypatch.setattr(
        crawler,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.Timeout()),
    )
    monkeypatch.setattr(
        crawler,
        "logout",
        lambda: (_ for _ in ()).throw(AssertionError("must not relogin")),
    )

    with pytest.raises(requests.Timeout):
        crawler.query_student("TEST-ID")
```

- [ ] **Step 2: Verify both tests fail**

```bash
pytest -q tests/test_query_performance_instrumentation.py \
  -k 'auth_expiry or network_timeout'
```

Expected: the current broad `except Exception` path incorrectly treats unrelated failures as authentication expiry.

- [ ] **Step 3: Classify authentication expiry explicitly**

Introduce one internal-only response validator:

```python
def _is_auth_expired_response(response) -> bool:
    text = response.text.lstrip()
    return "userController/login.action" in text or not text.startswith("{")
```

The exact predicate must be calibrated against captured successful JSON and expired-session HTML. Only confirmed authentication expiry may execute `logout()`, one login, and one retry.

- [ ] **Step 4: Move session refresh off the user request path**

Add a lightweight background session-maintenance loop for the internal system only:

- record `last_verified_at` after a successful authenticated response;
- probe or refresh before the measured server idle timeout;
- use the existing login lock so only one refresh can run;
- never launch multiple captcha logins concurrently;
- stop cleanly with the app process.

The refresh interval must be chosen from Task 1 measurements. Do not copy the current global `7200s` cache TTL.

- [ ] **Step 5: Preserve healthy-query request count**

Add a test proving a healthy internal query still uses:

- one student-list request;
- one timeline request;
- one fee request;
- zero login requests.

- [ ] **Step 6: Re-run the stale-session benchmark**

Acceptance:

- no user-triggered internal query performs an avoidable failed request when background refresh is healthy;
- stale-session p95 improves by at least `40%` against the Task 1 baseline;
- healthy-session p50 does not regress by more than `10%`;
- returned internal data exactly matches the pre-change result, excluding timing metadata.

### Task 3: Keep internal enrichment parallel and bounded

**Files:**
- Modify: `crawlers/internal.py`
- Test: `tests/test_query_performance_instrumentation.py`

- [ ] **Step 1: Write a parallel-enrichment regression test**

Use controlled delayed timeline and fee functions and assert total enrichment duration is near the slower function, not their sum.

```python
assert elapsed < 0.16  # two 0.10s calls must overlap
```

- [ ] **Step 2: Verify the current behavior and lock it down**

If the test already passes, keep the implementation and retain the regression test. Do not refactor working concurrency.

- [ ] **Step 3: Remove sequential timeout accumulation**

Collect both futures against one shared enrichment deadline instead of applying a new full timeout to each `.result()` call. On deadline:

- preserve whichever detail result completed;
- mark only the missing detail phase as timed out;
- do not discard the base student record.

- [ ] **Step 4: Verify accuracy**

Compare name, phone, branch, status, exam stage, exam counts, timeline, and fees before and after. Every field must match when both detail calls succeed.

### Task 4: Harden third and driving retry behavior

**Files:**
- Modify: `crawlers/third.py`
- Modify: `crawlers/driving.py`
- Test: `tests/test_query_performance_instrumentation.py`

- [ ] **Step 1: Write error-classification tests**

For each crawler:

- login-page or explicit auth code: one relogin and one retry;
- connection timeout: return timeout/error without captcha relogin;
- valid empty result: return `None` without relogin;
- malformed response: return parse error without relogin.

- [ ] **Step 2: Replace broad relogin behavior**

Only authentication failures may clear cookies and relogin. Keep the retry limit at one.

- [ ] **Step 3: Benchmark request counts**

Acceptance for healthy sessions:

- third system: one lookup request;
- driving system: one student-list request;
- no contract check during the main query;
- no additional login or validation request.

Expected actual speed:

- third system has little remaining code-side optimization because it already uses one request;
- driving system has little remaining code-side optimization because its `4–6s` is inside one external endpoint.

### Task 5: Return completed systems progressively

**Files:**
- Modify: `core/query_engine.py`
- Modify: `app.py`
- Modify: `static/js/composables/useComplaint.js`
- Modify: `templates/index.html`
- Test: `tests/test_case_workbench.py`
- Test: `tests/test_query_performance_instrumentation.py`

- [ ] **Step 1: Write query-job API tests**

Define:

```text
POST /api/query/start
GET  /api/query/status/<job_id>
```

The status response must expose each system as `pending`, `running`, `success`, `not_found`, `error`, or `timeout`, plus completed partial data.

- [ ] **Step 2: Verify tests fail before implementation**

```bash
pytest -q tests/test_case_workbench.py -k 'query_job'
```

Expected: both routes return `404`.

- [ ] **Step 3: Implement a bounded in-memory query job**

Follow the existing contract-analysis job pattern:

- bounded job retention;
- one task per system;
- result merge after each system completes;
- ticket persistence only once, when the job reaches a terminal state;
- no duplicate external query when the frontend polls.

- [ ] **Step 4: Render truthful partial progress**

The frontend must:

- display internal data as soon as internal completes;
- display training hours when third completes;
- display driving data when driving completes;
- label the case “仍在查询东莞驾培” until that task finishes;
- prevent fee analysis from becoming confirmable until required query inputs are complete or explicitly marked unavailable.

- [ ] **Step 5: Verify perceived and final latency**

Acceptance:

- first meaningful result p50 is at most `3.5s` under healthy sessions;
- final completion still contains all three systems;
- final data equals the current synchronous result;
- polling adds no external requests;
- one slow system does not hide already completed results.

### Task 6: Final performance and accuracy gate

**Files:**
- Verify only

- [ ] **Step 1: Run unit and integration tests**

```bash
pytest -q tests/test_query_performance_instrumentation.py \
  tests/test_driving_query_performance.py \
  tests/test_case_workbench.py
```

- [ ] **Step 2: Run the full suite**

```bash
pytest -q
```

- [ ] **Step 3: Run before/after benchmark matrix**

Use at least ten uncached runs for each scenario:

- healthy authentication;
- expired internal authentication;
- one slow external system;
- repeated same identity;
- two overlapping queries.

- [ ] **Step 4: Compare business data**

Diff the normalized results before and after while excluding:

- timing metadata;
- job identifiers;
- timestamps;
- ticket identifiers.

No student, examination, training, fee, contract, or branch field may disappear or change.

- [ ] **Step 5: Publish a concise acceptance report**

Report:

- p50, p95, maximum, and retry counts per system;
- time to first meaningful result;
- time to complete three-system result;
- cache-hit behavior;
- accuracy diff result;
- any remaining external-system latency that code cannot remove.
