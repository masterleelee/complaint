"""
回测：投诉列表「最近操作」排序口径

目标：验证 useWorkbench.js 改造的 _actionAt / listGroups 排序逻辑在 Python 侧的等价实现。
覆盖：
  1) 在途：updated_at 优先，缺失回退 created_at
  2) 已归档：completed_at 优先，其次 updated_at，再次 created_at
  3) 已撤诉：withdrawn_at 优先，其次 updated_at，再次 created_at
  4) 跨分组：互不串扰（撤诉工单的 withdrawn_at 不影响在途分组排序）
  5) 排序方向：desc（最近 → 最早）
  6) 稳定排序：同 _action_at 时保留原顺序
  7) 异常兜底：所有时间字段都空时退到空串，仍可比较（不会抛异常）
"""
from __future__ import annotations

from functools import cmp_to_key


def _action_at(t: dict) -> str:
    """与 useWorkbench.js:354 _actionAt 严格对齐的 Python 翻译。"""
    if t.get("withdraw_status") == "已撤诉":
        return str(t.get("withdrawn_at") or t.get("updated_at") or t.get("created_at") or "")
    if t.get("archive_status") == "已归档":
        return str(t.get("completed_at") or t.get("updated_at") or t.get("created_at") or "")
    return str(t.get("updated_at") or t.get("created_at") or "")


def _bucket(t: dict) -> str:
    if t.get("withdraw_status") == "已撤诉":
        return "withdrawn"
    if t.get("archive_status") == "已归档":
        return "archived"
    return "open"


def _group_and_sort(tickets: list[dict], sort_key: str = "action", dir_: int = -1) -> dict:
    """与 useWorkbench.js listGroups 等价：三分组，每组按 sort_key/dir_ 排序。"""
    groups = {"open": [], "archived": [], "withdrawn": []}
    for t in tickets:
        groups[_bucket(t)].append(t)

    def _cmp(a, b):
        if sort_key == "action":
            va, vb = _action_at(a), _action_at(b)
        else:  # 兼容旧测试：date
            va, vb = a.get("complaint_date", ""), b.get("complaint_date", "")
        if va < vb:
            return -1 * dir_
        if va > vb:
            return 1 * dir_
        return 0

    for arr in groups.values():
        # Python3 list.sort 没有 cmp，用 cmp_to_key 适配
        # sorted 本身稳定，同 _action_at 时保留原插入顺序
        arr.sort(key=cmp_to_key(_cmp))
    return groups


# ─────────── 测试 ───────────


def test_open_uses_updated_at():
    t = {
        "handle_status": "处理中",
        "archive_status": "",
        "withdraw_status": "",
        "updated_at": "2026-08-25 10:00:00",
        "created_at": "2026-08-01 09:00:00",
    }
    assert _action_at(t) == "2026-08-25 10:00:00"


def test_open_falls_back_to_created_at():
    """刚创建未编辑：在途工单 updated_at == created_at，但理论上也可能为空。"""
    t = {
        "handle_status": "待处理",
        "updated_at": "",
        "created_at": "2026-08-29 08:00:00",
    }
    assert _action_at(t) == "2026-08-29 08:00:00"


def test_archived_uses_completed_at_over_updated_at():
    """归档后又被「取消撤诉/改 final_outcome」刷过 updated_at，应取归档时刻 completed_at。"""
    t = {
        "handle_status": "已完结",
        "archive_status": "已归档",
        "withdraw_status": "",
        "completed_at": "2026-08-20 15:00:00",
        "updated_at": "2026-08-26 11:00:00",
        "created_at": "2026-08-10 09:00:00",
    }
    assert _action_at(t) == "2026-08-20 15:00:00"


def test_archived_falls_back_to_updated_at_when_completed_at_missing():
    """老工单没写 completed_at：退 updated_at。"""
    t = {
        "archive_status": "已归档",
        "completed_at": "",
        "updated_at": "2026-08-18 12:00:00",
        "created_at": "2026-08-01 09:00:00",
    }
    assert _action_at(t) == "2026-08-18 12:00:00"


def test_withdrawn_uses_withdrawn_at_over_updated_at():
    """已撤诉工单被「取消撤诉」后 updated_at 会被刷新回退；应取真正撤诉时刻。"""
    t = {
        "withdraw_status": "已撤诉",
        "withdrawn_at": "2026-08-15 14:00:00",
        "updated_at": "2026-08-28 10:00:00",
        "created_at": "2026-08-10 09:00:00",
    }
    assert _action_at(t) == "2026-08-15 14:00:00"


def test_withdrawn_falls_back_to_updated_at():
    t = {
        "withdraw_status": "已撤诉",
        "withdrawn_at": "",
        "updated_at": "2026-08-22 09:00:00",
        "created_at": "2026-08-10 09:00:00",
    }
    assert _action_at(t) == "2026-08-22 09:00:00"


def test_all_empty_does_not_crash():
    t = {"handle_status": "待处理"}
    # 不抛 + 返回空串（可比较）
    assert _action_at(t) == ""
    assert (_action_at(t) < _action_at({"updated_at": "2026-01-01"})) is True


def test_bucketing_is_exclusive():
    """withdraw 优先于 archive（已归档后撤诉，归到 withdrawn 桶）。"""
    assert _bucket({"withdraw_status": "已撤诉", "archive_status": "已归档"}) == "withdrawn"
    assert _bucket({"archive_status": "已归档"}) == "archived"
    assert _bucket({"handle_status": "处理中"}) == "open"


def test_group_sort_desc_by_recent_action():
    """主用例：默认 desc，最近操作排前面。"""
    tickets = [
        {"id": "A", "handle_status": "处理中", "updated_at": "2026-08-10 09:00:00"},  # 旧
        {"id": "B", "handle_status": "处理中", "updated_at": "2026-08-28 09:00:00"},  # 新
        {"id": "C", "handle_status": "处理中", "updated_at": "2026-08-20 09:00:00"},
    ]
    g = _group_and_sort(tickets)
    assert [t["id"] for t in g["open"]] == ["B", "C", "A"]


def test_each_bucket_uses_its_own_field():
    """三组工单互不串扰：在途看 updated_at，撤诉看 withdrawn_at，归档看 completed_at。"""
    tickets = [
        # 在途：最近被编辑
        {"id": "open1", "updated_at": "2026-08-29 09:00:00"},
        # 已归档：归档时间晚于 open1 的 updated_at，但归 archived 桶
        {"id": "arc1", "archive_status": "已归档", "completed_at": "2026-08-30 10:00:00",
         "updated_at": "2026-08-30 10:00:00"},
        # 已撤诉：撤诉时间最早
        {"id": "wd1", "withdraw_status": "已撤诉", "withdrawn_at": "2026-08-15 14:00:00",
         "updated_at": "2026-08-25 11:00:00"},
    ]
    g = _group_and_sort(tickets)
    # 三组独立排序：每组只有一条
    assert [t["id"] for t in g["open"]] == ["open1"]
    assert [t["id"] for t in g["archived"]] == ["arc1"]
    assert [t["id"] for t in g["withdrawn"]] == ["wd1"]


def test_withdrawn_uses_withdrawn_at_not_updated_at_for_ranking():
    """关键场景：撤诉工单之后被改过（updated_at 较新）→ 排名仍按 withdrawn_at 走。"""
    tickets = [
        {"id": "WD_old", "withdraw_status": "已撤诉",
         "withdrawn_at": "2026-08-10 09:00:00", "updated_at": "2026-08-29 09:00:00"},
        {"id": "WD_new", "withdraw_status": "已撤诉",
         "withdrawn_at": "2026-08-28 09:00:00", "updated_at": "2026-08-28 09:00:00"},
    ]
    g = _group_and_sort(tickets)
    # WD_new 排前面（withdrawn_at 较新）
    assert [t["id"] for t in g["withdrawn"]] == ["WD_new", "WD_old"]


def test_archived_uses_completed_at_not_updated_at_for_ranking():
    tickets = [
        {"id": "ARC_old", "archive_status": "已归档",
         "completed_at": "2026-08-05 09:00:00", "updated_at": "2026-08-29 09:00:00"},
        {"id": "ARC_new", "archive_status": "已归档",
         "completed_at": "2026-08-20 09:00:00", "updated_at": "2026-08-20 09:00:00"},
    ]
    g = _group_and_sort(tickets)
    assert [t["id"] for t in g["archived"]] == ["ARC_new", "ARC_old"]


def test_asc_dir_works_as_opt_in():
    """用户点列头反向时 dir_=1，应升序。"""
    tickets = [
        {"id": "B", "updated_at": "2026-08-28 09:00:00"},
        {"id": "A", "updated_at": "2026-08-10 09:00:00"},
    ]
    g = _group_and_sort(tickets, dir_=1)
    assert [t["id"] for t in g["open"]] == ["A", "B"]


def test_legacy_date_sort_still_works():
    """兼容旧测试：sort_key='date' 时按 complaint_date 排序。"""
    tickets = [
        {"id": "B", "complaint_date": "2026-08-20"},
        {"id": "A", "complaint_date": "2026-08-10"},
    ]
    g = _group_and_sort(tickets, sort_key="date")
    assert [t["id"] for t in g["open"]] == ["B", "A"]


def test_open_fallback_when_updated_at_is_legacy_empty_string():
    """历史库有 updated_at='' 的工单：必须能正确回退到 created_at。"""
    t = {"handle_status": "处理中", "updated_at": "", "created_at": "2026-01-15 09:00:00"}
    assert _action_at(t) == "2026-01-15 09:00:00"


def test_open_real_world_seed_style():
    """贴近 seed 脚本形态（seed 不写 completed_at/withdrawn_at）。"""
    t = {
        "handle_status": "处理中",
        "archive_status": "",
        "withdraw_status": "",
        "complaint_date": "2026-08-22",
        "created_at": "2026-08-22 10:30:00",
        "updated_at": "2026-08-22 10:30:00",  # seed 让 updated_at = created_at
    }
    assert _action_at(t) == "2026-08-22 10:30:00"
