"""合同分析缓存 + 改档重算（工单 08-retier-cache）。

设计要点：
  * 进程内 dict 缓存（项目单进程足够；无 DB 锁问题；与 DB 落库的 contract_cache 表互不干扰）。
  * cache_key = sha256(文件指纹 + tier_id + progress_hash16)；与
    ``services.upload_pipeline._compute_cache_key`` 同语义。
    本模块复制了 ``_progress_hash`` 与 ``_compute_cache_key`` 的实现，不依赖 upload_pipeline
    的私有成员；跨函数等价性由 ``tests/test_contract_cache.py::test_compute_cache_key_matches_upload_pipeline``
    守护。
  * 改档 → 新 tier_id → 新 key；命中即返回缓存快照（``cached=True``），不再跑管线。
  * 进度变 → ``progress_hash16`` 变 → key 变 → 缓存自动失效，无须手动 invalidate。
  * 二次打开同一合同 = 同 filepath + tier + progress = 同 key = 命中缓存，不重跑 OCR/LLM。

公开 API：
  ``compute_cache_key(filepath, tier_id, ticket, *, image_paths=None) -> str``
  ``get_cached(cache_key) -> dict | None``
  ``store(cache_key, result, *, ticket_id=None) -> None``
  ``invalidate(cache_key=None, *, ticket_id=None) -> int``
  ``cache_stats() -> dict``
  ``retier_and_recompute(filepath, ticket, *, new_tier_id, image_paths=None) -> dict``
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from typing import Any


# 进程内缓存。项目单进程；用 dict 不上 SQLite 是为了避免 DB 锁 + 单测用例间清缓存麻烦。
_CACHE: dict[str, dict] = {}
# ticket_id -> set(cache_key) 索引；invalidate(ticket_id=...) 用。
_TICKET_INDEX: dict[str, set[str]] = {}

_STATS = {"hits": 0, "misses": 0, "stores": 0, "invalidations": 0}


# ── 进度哈希（与 services.upload_pipeline._progress_hash 同语义） ────────────


def _progress_hash(ticket: dict) -> str:
    """进度哈希：阶段 + 培训费 + 已考次数 + 审核学时 + 车型 + 东城自制字段；用于缓存键。"""
    payload: dict[str, Any] = {
        "exam_stage": str(ticket.get("exam_stage") or ""),
        "total_fee": ticket.get("total_fee"),
        "exam_counts": ticket.get("exam_counts") or {},
        "license_type": str(ticket.get("license_type") or ""),
        "service_fee": ticket.get("service_fee"),
        "training_mode": str(ticket.get("training_mode") or ""),
    }
    qr = ticket.get("query_result") or {}
    if isinstance(qr, str):
        try:
            qr = json.loads(qr)
        except (TypeError, ValueError):
            qr = {}
    if isinstance(qr, dict):
        payload["training_hours"] = qr.get("driving_hours") or {}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


# ── 缓存键：文件指纹 + tier_id + 进度哈希 ────────────────────────────────


def compute_cache_key(
    filepath: str,
    tier_id: str,
    ticket: dict,
    *,
    image_paths: list | None = None,
) -> str:
    """sha256(文件指纹 + tier_id + progress_hash16)。

    与 ``services.upload_pipeline._compute_cache_key`` 等价：
      file_facts = [(realpath, size, mtime_ns), ...]，按 path 排序
      json(sort_keys=True, ensure_ascii=False) → sha256.hexdigest()
    任一变化即重算；空 filepath / tier_id 也输出稳定 key。
    """
    file_facts: list[tuple[str, int, int]] = []
    for path in [filepath] + list(image_paths or []):
        if not path:
            continue
        try:
            st = os.stat(path)
            file_facts.append((os.path.realpath(path), st.st_size, st.st_mtime_ns))
        except OSError:
            continue
    raw = json.dumps(
        {
            "files": sorted(file_facts),
            "tier_id": tier_id or "",
            "progress": _progress_hash(ticket),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── 缓存读写 ─────────────────────────────────────────────────────────


def get_cached(cache_key: str) -> dict | None:
    """命中 → 返回 result 浅拷贝 + 新增 ``cached=True`` / ``cache_key`` 字段；未命中 → None。

    返回的字典是浅拷贝，调用方可 mutate 外层键；内嵌 deduction/items 等被原管线共享。
    """
    if not cache_key:
        _STATS["misses"] += 1
        return None
    snap = _CACHE.get(cache_key)
    if snap is None:
        _STATS["misses"] += 1
        return None
    _STATS["hits"] += 1
    out = copy.copy(snap)
    out["cached"] = True
    out["cache_key"] = cache_key
    return out


def store(cache_key: str, result: dict, *, ticket_id: str | None = None) -> None:
    """存 result 浅拷贝；同 key 重复覆盖。

    若提供 ``ticket_id``，会额外登记 ``_TICKET_INDEX[ticket_id] ← {cache_key}``，
    便于 ``invalidate(ticket_id=...)``。ticket_id 为空字符串视作「不登记」。
    """
    if not cache_key or not isinstance(result, dict):
        return
    _CACHE[cache_key] = copy.copy(result)
    _STATS["stores"] += 1
    if ticket_id:
        bucket = _TICKET_INDEX.setdefault(ticket_id, set())
        bucket.add(cache_key)


def invalidate(cache_key: str | None = None, *, ticket_id: str | None = None) -> int:
    """按 cache_key 或 ticket_id 清缓存；返回清理条数。

    - 仅给 cache_key：删该 key 一次；
    - 仅给 ticket_id：清该 ticket 关联的所有 cache_key；
    - 两个都给：先按 cache_key，再按 ticket_id；
    - 都不给：0。
    """
    n = 0
    if cache_key and cache_key in _CACHE:
        for tid, keys in list(_TICKET_INDEX.items()):
            if cache_key in keys:
                keys.discard(cache_key)
                if not keys:
                    _TICKET_INDEX.pop(tid, None)
        _CACHE.pop(cache_key, None)
        _STATS["invalidations"] += 1
        n += 1
    if ticket_id:
        keys = _TICKET_INDEX.pop(ticket_id, None)
        if keys:
            for k in keys:
                if k in _CACHE:
                    _CACHE.pop(k, None)
                    _STATS["invalidations"] += 1
                    n += 1
    return n


def cache_stats() -> dict:
    """调试/UI 用统计：命中/未命中/存/失效 + 当前容量与 key 列表。"""
    return {
        "hits": _STATS["hits"],
        "misses": _STATS["misses"],
        "stores": _STATS["stores"],
        "invalidations": _STATS["invalidations"],
        "size": len(_CACHE),
        "ticket_count": len(_TICKET_INDEX),
        "keys": list(_CACHE.keys()),
    }


def reset_for_tests() -> None:
    """清缓存、清计数、清 ticket 索引；仅供单测 setup 使用。"""
    _CACHE.clear()
    _TICKET_INDEX.clear()
    for k in _STATS:
        _STATS[k] = 0


# ── 改档重算：缓存优先；miss 时调 upload_pipeline 重算并 store ────────────


def retier_and_recompute(
    filepath: str,
    ticket: dict,
    *,
    new_tier_id: str,
    image_paths: list | None = None,
) -> dict:
    """改档重算（V1）：

    1. 用 ``new_tier_id`` 计算新 ``cache_key``；
    2. 命中 → 直接返回缓存快照（``cached=True``，不再跑管线）；
    3. miss → 调用 ``services.upload_pipeline.analyze_upload_contract_file`` 重算
       并以 ``new_tier_id`` 为权威覆写 ``tier_id`` / ``cache_key``，再 ``store`` 进缓存。

    返回值保证含 ``cache_key`` 字段（即使管线异常也透传出来），并带 ``cached`` 标记。

    ticket 由调用方准备（应包含改档后的 organization_unit_type 等线索）。
    本函数不修改 ticket；它只决定「命中还是重算」。
    """
    # 延迟导入避免 upload_pipeline import 阶段循环（理论上不会发生，保持稳健）
    from services.upload_pipeline import analyze_upload_contract_file

    new_key = compute_cache_key(filepath, new_tier_id, ticket, image_paths=image_paths)
    snapshot = get_cached(new_key)
    if snapshot is not None:
        # get_cached 已加 cached / cache_key；这里再次断言 tier_id 与新档一致
        snapshot["tier_id"] = new_tier_id
        snapshot["cache_key"] = new_key
        snapshot["cached"] = True
        return snapshot

    result = analyze_upload_contract_file(
        filepath,
        ticket,
        image_paths=image_paths,
    )
    # 改档语义：以 new_tier_id 为权威；identify_tier 在 ticket 字段下可能给出不同 tier，
    # 这里以前端用户明确选择的 tier 为准（与 spec Implementation Decisions「识别结果持久化在工单上，
    # 用户改档位走重算接口」一致）。
    result["tier_id"] = new_tier_id
    result["cache_key"] = new_key
    result["cached"] = False
    tid = ticket.get("id") if isinstance(ticket, dict) else None
    store(new_key, result, ticket_id=str(tid) if tid else None)
    return result
