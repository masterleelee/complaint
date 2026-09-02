"""合同分析缓存 + 改档重算测试（工单 08-retier-cache）。

覆盖：
  * compute_cache_key 稳定性 / 分桶（tier / progress[stage,total_fee,exam_counts,license_type,
    training_hours] / 文件指纹[mtime,size,realpath] / image_paths）。
  * get_cached / store / invalidate / cache_stats。
  * compute_cache_key 与 services.upload_pipeline._compute_cache_key 同语义（跨函数等价性）。
  * retier_and_recompute：miss → 调 upload_pipeline → store；同 key 再来 → hit → 0 次调用。
"""

from __future__ import annotations

import copy
import os
import tempfile

import pytest

import services.contract_cache as cc
from services import contract_cache
from services.contract_cache import (
    cache_stats,
    compute_cache_key,
    get_cached,
    invalidate,
    retier_and_recompute,
    store,
)


# ── fixture：每个用例前清缓存、清统计、清索引 ─────────────────────────


@pytest.fixture(autouse=True)
def _clean_cache():
    """每个用例前后清进程内缓存；保证测试顺序无关。"""
    contract_cache.reset_for_tests()
    yield
    contract_cache.reset_for_tests()


# ── fixture：建一个本地临时 PDF 文件用于文件指纹相关用例 ───────────────


@pytest.fixture
def pdf_file():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fp:
        fp.write(b"%PDF-1.4 fake content for cache-key test\n")
        path = fp.name
    try:
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _base_ticket(**overrides):
    base = {
        "id": "ticket-08",
        "exam_stage": "已受理",
        "total_fee": 6000,
        "exam_counts": {"subject1": 1},
        "license_type": "C1",
        "query_result": {"driving_hours": {"subject2": 10}},
    }
    base.update(overrides)
    return base


# ── 1. compute_cache_key 稳定性 ────────────────────────────────────────


def test_compute_cache_key_stable_with_same_inputs(pdf_file):
    ticket = _base_ticket()
    a = compute_cache_key(pdf_file, "2023_branch_store", ticket)
    b = compute_cache_key(pdf_file, "2023_branch_store", ticket)
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_compute_cache_key_changes_with_tier(pdf_file):
    ticket = _base_ticket()
    branch_store = compute_cache_key(pdf_file, "2023_branch_store", ticket)
    branch_school = compute_cache_key(pdf_file, "2023_branch_school", ticket)
    other_year = compute_cache_key(pdf_file, "2021_2022", ticket)
    assert branch_store != branch_school
    assert branch_store != other_year
    assert branch_school != other_year


def test_compute_cache_key_changes_with_progress_exam_stage(pdf_file):
    accepted = compute_cache_key(pdf_file, "2023_branch_store", _base_ticket(exam_stage="已受理"))
    practical = compute_cache_key(pdf_file, "2023_branch_store", _base_ticket(exam_stage="实操中"))
    assert accepted != practical


def test_compute_cache_key_changes_with_progress_total_fee(pdf_file):
    a = compute_cache_key(pdf_file, "2023_branch_store", _base_ticket(total_fee=6000))
    b = compute_cache_key(pdf_file, "2023_branch_store", _base_ticket(total_fee=5000))
    assert a != b


def test_compute_cache_key_changes_with_progress_exam_counts(pdf_file):
    a = compute_cache_key(pdf_file, "2023_branch_store", _base_ticket(exam_counts={}))
    b = compute_cache_key(
        pdf_file, "2023_branch_store", _base_ticket(exam_counts={"subject1": 1}),
    )
    c = compute_cache_key(
        pdf_file, "2023_branch_store", _base_ticket(exam_counts={"subject1": 2}),
    )
    assert a != b and b != c and a != c


def test_compute_cache_key_changes_with_progress_license_type(pdf_file):
    c1 = compute_cache_key(pdf_file, "2023_branch_store", _base_ticket(license_type="C1"))
    c2 = compute_cache_key(pdf_file, "2023_branch_store", _base_ticket(license_type="C2"))
    assert c1 != c2


def test_compute_cache_key_changes_with_progress_training_hours(pdf_file):
    """driving_hours 变化要走 query_result.driving_hours；这是进度哈希的一部分。"""
    a = compute_cache_key(
        pdf_file, "2023_branch_store",
        _base_ticket(query_result={"driving_hours": {"subject2": 0}}),
    )
    b = compute_cache_key(
        pdf_file, "2023_branch_store",
        _base_ticket(query_result={"driving_hours": {"subject2": 10}}),
    )
    assert a != b


def test_compute_cache_key_changes_with_file_mtime(pdf_file):
    """同一路径，mtime 变化 → 缓存键变化（产线读到的文件替换/重传场景）。"""
    ticket = _base_ticket()
    before = compute_cache_key(pdf_file, "2023_branch_store", ticket)

    # 改 mtime（不重写内容也行，关键是 mtime_ns 变）
    st = os.stat(pdf_file)
    os.utime(pdf_file, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))

    after = compute_cache_key(pdf_file, "2023_branch_store", ticket)
    assert before != after


def test_compute_cache_key_changes_with_image_paths(pdf_file):
    ticket = _base_ticket()
    only_main = compute_cache_key(pdf_file, "2023_branch_store", ticket, image_paths=[])
    with_extra = compute_cache_key(
        pdf_file, "2023_branch_store", ticket, image_paths=[pdf_file],
    )
    assert only_main != with_extra


# ── 2. compute_cache_key 与 upload_pipeline 私有版等价 ─────────────────


def test_compute_cache_key_matches_upload_pipeline(pdf_file):
    """跨模块同输入 → 同 cache_key。这是 08 消费 04 缓存键的契约守护。"""
    from services.upload_pipeline import _compute_cache_key as upstream_ck

    ticket = _base_ticket()
    for tier in ("2023_branch_store", "2023_branch_school", "2021_2022", "2019_service"):
        mine = compute_cache_key(pdf_file, tier, ticket)
        theirs = upstream_ck(pdf_file, tier, ticket)
        assert mine == theirs, f"tier={tier} mine={mine} theirs={theirs}"

    # 改 progress：仍应等价
    ticket2 = _base_ticket(exam_stage="实操中", exam_counts={"subject1": 2, "subject2": 1})
    assert compute_cache_key(pdf_file, "2023_branch_store", ticket2) == upstream_ck(
        pdf_file, "2023_branch_store", ticket2
    )


# ── 3. get_cached / store 命中 + 未命中 + 覆盖 ────────────────────────


def test_get_cached_miss_returns_none_and_increments_misses():
    assert get_cached("nonexistent-key") is None
    assert cache_stats()["misses"] == 1
    assert cache_stats()["hits"] == 0


def test_store_then_get_returns_cached_marker_and_shallow_copy():
    key = "k1"
    snap = {"tier_id": "2023_branch_store", "cache_key": "", "items": [{"a": 1}]}
    store(key, snap)

    out = get_cached(key)
    assert out is not None
    assert out["cached"] is True
    assert out["cache_key"] == key
    assert out["tier_id"] == "2023_branch_store"
    # 浅拷贝：外层是不同 dict
    assert out is not snap
    # mutate 外层不影响缓存
    out["tier_id"] = "mutated"
    again = get_cached(key)
    assert again["tier_id"] == "2023_branch_store"
    assert cache_stats()["hits"] == 2


def test_store_overwrites_same_key():
    key = "k_overwrite"
    store(key, {"tier_id": "tier_v1", "cache_key": ""})
    store(key, {"tier_id": "tier_v2", "cache_key": ""})
    out = get_cached(key)
    assert out["tier_id"] == "tier_v2"


def test_store_registers_ticket_index_when_ticket_id_given():
    key = "k_t1"
    store(key, {"tier_id": "x", "cache_key": ""}, ticket_id="ticket-A")
    out = get_cached(key)
    assert out["tier_id"] == "x"


# ── 4. invalidate ────────────────────────────────────────────────────


def test_invalidate_by_cache_key():
    store("k_a", {"tier_id": "a", "cache_key": ""}, ticket_id="T1")
    store("k_b", {"tier_id": "b", "cache_key": ""}, ticket_id="T2")
    n = invalidate("k_a")
    assert n == 1
    assert get_cached("k_a") is None
    assert get_cached("k_b") is not None


def test_invalidate_by_ticket_id():
    store("k_x1", {"tier_id": "x1", "cache_key": ""}, ticket_id="TIX")
    store("k_x2", {"tier_id": "x2", "cache_key": ""}, ticket_id="TIX")
    store("k_y", {"tier_id": "y", "cache_key": ""}, ticket_id="TIY")
    n = invalidate(ticket_id="TIX")
    assert n == 2
    assert get_cached("k_x1") is None
    assert get_cached("k_x2") is None
    assert get_cached("k_y") is not None


def test_invalidate_no_args_returns_zero():
    store("k_z", {"tier_id": "z", "cache_key": ""})
    assert invalidate() == 0


# ── 5. cache_stats ───────────────────────────────────────────────────


def test_cache_stats_reflects_counters_and_size():
    store("k1", {"tier_id": "a", "cache_key": ""})
    store("k2", {"tier_id": "b", "cache_key": ""})
    get_cached("k1")          # 命中
    get_cached("k_none")      # 未命中
    invalidate("k2")

    s = cache_stats()
    assert s["size"] == 1  # 只剩 k1
    assert s["hits"] == 1
    assert s["misses"] == 1
    assert s["stores"] == 2
    assert s["invalidations"] == 1
    assert set(s["keys"]) == {"k1"}


# ── 6. retier_and_recompute：miss → 调 1 次；同 key 再来 → hit → 0 次 ──


def test_retier_and_recompute_miss_then_hit(monkeypatch, pdf_file):
    """改档 → 新 tier + 旧 progress → cache miss → 调 upload_pipeline → store
    → 第二次同 tier → cache hit → 0 次调用。

    用 monkeypatch 把 services.upload_pipeline.analyze_upload_contract_file 替换为
    fake，断言调用次数。
    """
    from services import upload_pipeline

    call_count = {"n": 0}

    def fake_analyze_upload_contract_file(filepath, ticket, *, image_paths=None):
        call_count["n"] += 1
        # 返回值携带 spec Implementation Decisions 关心的全部字段
        return {
            "tier_id": "2023_branch_school",
            "tier_result": {"tier_id": "2023_branch_school", "confidence": "high"},
            "deductions_result": {
                "items": [
                    {"item": "服务费", "amount": 600, "category": "必扣"},
                ],
                "total_deduction": 600,
                "refund": 5400,
                "warnings": [],
            },
            "cache_key": "",
            "text_source": "pdf_text",
            "extraction_error": None,
            "contract_set": {"contracts": []},
        }

    # retier_and_recompute 内重新 `from services.upload_pipeline import analyze_upload_contract_file`
    # 会读模块当前属性；monkeypatch setattr 修改模块属性即可。
    monkeypatch.setattr(
        upload_pipeline, "analyze_upload_contract_file", fake_analyze_upload_contract_file,
    )

    ticket = _base_ticket(exam_stage="实操中")

    # 第一次：miss（缓存空）
    r1 = retier_and_recompute(
        pdf_file, ticket, new_tier_id="2023_branch_school", image_paths=None,
    )
    assert r1["cached"] is False
    assert r1["tier_id"] == "2023_branch_school"
    assert r1["cache_key"]
    assert call_count["n"] == 1

    # 关键确认：r1 的 cache_key 与 compute_cache_key（新 tier + 当前 ticket）一致
    expected_key = compute_cache_key(
        pdf_file, "2023_branch_school", ticket, image_paths=None,
    )
    assert r1["cache_key"] == expected_key

    # 第二次：同 tier + 同 ticket → cache key 不变 → 命中 → 不调管线
    r2 = retier_and_recompute(
        pdf_file, ticket, new_tier_id="2023_branch_school", image_paths=None,
    )
    assert r2["cached"] is True
    assert r2["cache_key"] == expected_key
    assert call_count["n"] == 1, "二次访问应命中缓存，不再调用 upload_pipeline"

    # 计数器
    s = cache_stats()
    assert s["misses"] == 1
    assert s["hits"] == 1
    assert s["stores"] == 1


def test_retier_and_recompute_progress_change_triggers_recompute(monkeypatch, pdf_file):
    """进度哈希变化 → 下次分析应重新计算而不命中旧缓存。"""
    from services import upload_pipeline

    call_count = {"n": 0}

    def fake_analyze_upload_contract_file(filepath, ticket, *, image_paths=None):
        call_count["n"] += 1
        return {
            "tier_id": "2023_branch_store",
            "tier_result": {"tier_id": "2023_branch_store", "confidence": "high"},
            "deductions_result": None,
            "cache_key": "",
            "text_source": "pdf_text",
            "extraction_error": None,
            "contract_set": {"contracts": []},
        }

    monkeypatch.setattr(
        upload_pipeline, "analyze_upload_contract_file", fake_analyze_upload_contract_file,
    )

    t1 = _base_ticket(exam_stage="已受理")
    r1 = retier_and_recompute(pdf_file, t1, new_tier_id="2023_branch_store")
    assert r1["cached"] is False
    assert call_count["n"] == 1

    # 进度推进：科二 10 学时 → 学时也算 progress，key 必须变
    t2 = _base_ticket(
        exam_stage="实操中",
        exam_counts={"subject1": 1, "subject2": 0},
        query_result={"driving_hours": {"subject2": 24}},
    )
    assert compute_cache_key(pdf_file, "2023_branch_store", t1) != compute_cache_key(
        pdf_file, "2023_branch_store", t2
    )

    r2 = retier_and_recompute(pdf_file, t2, new_tier_id="2023_branch_store")
    assert r2["cached"] is False, "进度变化 → key 变 → 不应命中"
    assert call_count["n"] == 2


def test_retier_and_recompute_tier_change_yields_new_cache_key(monkeypatch, pdf_file):
    """改档 → 新 tier → 新 cache_key（验收第 1 条「2023·分店改 2023·分校 → 缓存键变化」）。"""
    from services import upload_pipeline

    call_count = {"n": 0}

    def fake_analyze_upload_contract_file(filepath, ticket, *, image_paths=None):
        call_count["n"] += 1
        return {
            "tier_id": "2023_branch_school",
            "tier_result": {},
            "deductions_result": None,
            "cache_key": "",
            "text_source": "pdf_text",
            "extraction_error": None,
            "contract_set": {"contracts": []},
        }

    monkeypatch.setattr(
        upload_pipeline, "analyze_upload_contract_file", fake_analyze_upload_contract_file,
    )

    ticket = _base_ticket(organization_unit_type="分店")

    # 第一次：分店（new_tier_id=分店）
    branch_store_key = compute_cache_key(pdf_file, "2023_branch_store", ticket)
    r_store = retier_and_recompute(pdf_file, ticket, new_tier_id="2023_branch_store")
    assert r_store["cache_key"] == branch_store_key
    assert call_count["n"] == 1

    # 改档 → 分校（cache_key 必须不同）
    branch_school_key = compute_cache_key(pdf_file, "2023_branch_school", ticket)
    assert branch_store_key != branch_school_key

    r_school = retier_and_recompute(pdf_file, ticket, new_tier_id="2023_branch_school")
    assert r_school["cache_key"] == branch_school_key
    assert r_school["tier_id"] == "2023_branch_school"
    assert call_count["n"] == 2

    # 缓存里现在有两份不同 key 的快照
    s = cache_stats()
    assert s["size"] == 2
