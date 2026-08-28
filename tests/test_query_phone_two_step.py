"""回测 query_all_systems_by_phone 三步降级流程。

场景：
A. 蔡振华 + 13800000000（手机号绑别人，2a 姓名不一致 → 2b 兜底）
B. 蔡振华 + 13800000000（手机号未录入，2a 异常 → 2b 兜底）
C. 蔡振华 + 13800000000（姓名 + 手机号完全一致，2a 直接命中）
D. 仅手机号 13800000000（无姓名，2b 唯一候选）
E. 仅手机号 13800000000（2b 多候选 → 列表返回）

策略：mock query_engine 内的 InternalCrawler，避免触达真实三系统。
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _install_fake_internal(monkeypatch, *, lookup_id=None, lookup_students=None,
                            lookup_raises=None, id_query_returns=None,
                            students_lookup_raises=None):
    """在 query_engine._crawlers 中塞一个伪 InternalCrawler。

    lookup_raises: 让 lookup_id_card_by_phone 抛异常（模拟 2a 异常/多证号歧义）
    students_lookup_raises: 让 lookup_students_by_phone 抛异常（模拟 2b 异常）
    """
    import app as app_module
    from core.auth_manager import SystemType

    class FakeCrawler:
        def __init__(self):
            self._lookup_id = lookup_id
            self._lookup_students = lookup_students or []
            self._lookup_raises = lookup_raises
            self._students_lookup_raises = students_lookup_raises
            self._id_query = id_query_returns or {}

        def lookup_id_card_by_phone(self, phone, _retry=0):
            if self._lookup_raises:
                raise self._lookup_raises
            return self._lookup_id or ""

        def lookup_students_by_phone(self, phone, _retry=0):
            if self._students_lookup_raises:
                raise self._students_lookup_raises
            return self._lookup_students

    fake = FakeCrawler()
    # app.py 里 query_engine 是从 core.query_engine 单例取的
    qe = app_module.query_engine
    qe._crawlers[SystemType.INTERNAL] = fake

    # 屏蔽 _query_by_id_cards_with_fallback 内部对 query_all_systems_sync 的依赖
    def fake_query_all_systems_sync(id_card, timeout=60.0):
        return qe._id_query_results.get(id_card, {
            "name": "", "id_card": id_card, "phone": "",
            "license_type": "", "registration_date": "",
            "school_name": "", "school_short": "",
            "student_status": "", "exam_stage": "",
            "exam_counts": {}, "training_hours": {},
            "training_details": [], "fees": [], "timeline_display": [],
            "sources": {"internal": "not_found", "third": "not_found", "driving": "not_found"},
        })
    qe._id_query_results = id_query_returns or {}
    monkeypatch.setattr(app_module, "query_all_systems_sync", fake_query_all_systems_sync)


def test_scenario_a_phone_bound_to_other_name(monkeypatch):
    """A: 蔡振华+13800000000，手机号绑张三 → 2a 命中证号 110...，但姓名不一致 → 仍返回且打 name_mismatch。"""
    from crawlers.internal import PhoneLookupAmbiguityError
    _install_fake_internal(
        monkeypatch,
        lookup_id="110101199003070011",
        id_query_returns={
            "110101199003070011": {
                "name": "张三",  # 系统返回张三，与期望「蔡振华」不一致
                "id_card": "110101199003070011",
                "phone": "13800000000",
                "school_name": "总校", "school_short": "总校",
                "registration_date": "2024-01-01", "exam_stage": "报名",
                "exam_counts": {}, "training_hours": {},
                "training_details": [], "fees": [], "timeline_display": [],
                "sources": {"internal": "success", "third": "not_found", "driving": "not_found"},
            }
        },
    )
    from app import query_all_systems_by_phone
    result = query_all_systems_by_phone("13800000000", expected_name="蔡振华")
    assert result["name"] == "张三", f"应返回张三, got {result.get('name')}"
    assert result.get("name_mismatch") is True, "应打 name_mismatch 标记"
    assert "蔡振华" in result.get("name_mismatch_reason", ""), "原因文案应包含期望姓名"
    print("  [PASS] A: 蔡振华+手机号绑别人 → 返回张三 + name_mismatch=True")


def test_scenario_b_phone_not_in_system(monkeypatch):
    """B: 手机号在系统内不存在 → 2a 抛/返回空 → 2b 也空 → 返回 not_found。"""
    _install_fake_internal(
        monkeypatch,
        lookup_id="",           # 2a 拿不到
        lookup_students=[],     # 2b 也空
    )
    from app import query_all_systems_by_phone
    result = query_all_systems_by_phone("13800000000", expected_name="蔡振华")
    assert result.get("name") == "", "未命中应空姓名"
    assert result["sources"]["internal"] == "not_found"
    assert "手机号在内部系统未查到学员" in result.get("error", "")
    print("  [PASS] B: 手机号不存在 → not_found, 兜底由前端走姓名模糊")


def test_scenario_c_full_match(monkeypatch):
    """C: 蔡振华+手机号完全一致 → 2a 直接命中，无 name_mismatch。"""
    _install_fake_internal(
        monkeypatch,
        lookup_id="110101199003070011",
        id_query_returns={
            "110101199003070011": {
                "name": "蔡振华",
                "id_card": "110101199003070011",
                "phone": "13800000000",
                "school_name": "总校", "school_short": "总校",
                "registration_date": "2024-01-01", "exam_stage": "报名",
                "exam_counts": {}, "training_hours": {},
                "training_details": [], "fees": [], "timeline_display": [],
                "sources": {"internal": "success", "third": "not_found", "driving": "not_found"},
            }
        },
    )
    from app import query_all_systems_by_phone
    result = query_all_systems_by_phone("13800000000", expected_name="蔡振华")
    assert result["name"] == "蔡振华"
    assert not result.get("name_mismatch"), "姓名一致时不应打标记"
    print("  [PASS] C: 姓名+手机号完全一致 → name_mismatch=False")


def test_scenario_d_phone_only_single_candidate(monkeypatch):
    """D: 仅手机号、无姓名 → 2a 跳过 → 2b 唯一候选 → 返回该学员，不打 name_mismatch。"""
    _install_fake_internal(
        monkeypatch,
        lookup_id="",  # 2a 不会跑（无姓名）
        lookup_students=[
            {"id_card": "110101199003070011", "name": "蔡振华", "phone": "13800000000"},
        ],
        id_query_returns={
            "110101199003070011": {
                "name": "蔡振华",
                "id_card": "110101199003070011",
                "phone": "13800000000",
                "school_name": "总校", "school_short": "总校",
                "registration_date": "2024-01-01", "exam_stage": "报名",
                "exam_counts": {}, "training_hours": {},
                "training_details": [], "fees": [], "timeline_display": [],
                "sources": {"internal": "success", "third": "not_found", "driving": "not_found"},
            }
        },
    )
    from app import query_all_systems_by_phone
    result = query_all_systems_by_phone("13800000000", expected_name="")
    assert result["name"] == "蔡振华"
    assert not result.get("name_mismatch")
    print("  [PASS] D: 仅手机号唯一候选 → 返回该学员")


def test_scenario_e_phone_only_multiple_candidates(monkeypatch):
    """E: 仅手机号 2b 多候选 → 返回 candidates 列表。"""
    _install_fake_internal(
        monkeypatch,
        lookup_id="",
        lookup_students=[
            {"id_card": "110101199003070011", "name": "张三", "phone": "13800000000"},
            {"id_card": "110101199003070011", "name": "李四", "phone": "13800000000"},
        ],
    )
    from app import query_all_systems_by_phone
    result = query_all_systems_by_phone("13800000000", expected_name="蔡振华")
    assert "candidates" in result, "应返回 candidates 列表"
    assert len(result["candidates"]) == 2
    assert result["sources"]["internal"] == "ambiguous"
    print("  [PASS] E: 仅手机号多候选 → 返回 candidates 列表（2 条）")


def test_scenario_f_step2a_ambiguous_then_step2b_finds_one(monkeypatch):
    """F: 步骤 2a 多证号歧义 → 步骤 2b 唯一候选 → 返回该学员。"""
    from crawlers.internal import PhoneLookupAmbiguityError
    _install_fake_internal(
        monkeypatch,
        lookup_raises=PhoneLookupAmbiguityError("手机号匹配多个学员"),
        students_lookup_raises=None,  # 2b 正常
        lookup_students=[
            {"id_card": "110101199003070011", "name": "蔡振华", "phone": "13800000000"},
        ],
        id_query_returns={
            "110101199003070011": {
                "name": "蔡振华",
                "id_card": "110101199003070011",
                "phone": "13800000000",
                "school_name": "总校", "school_short": "总校",
                "registration_date": "2024-01-01", "exam_stage": "报名",
                "exam_counts": {}, "training_hours": {},
                "training_details": [], "fees": [], "timeline_display": [],
                "sources": {"internal": "success", "third": "not_found", "driving": "not_found"},
            }
        },
    )
    from app import query_all_systems_by_phone
    result = query_all_systems_by_phone("13800000000", expected_name="蔡振华")
    assert result["name"] == "蔡振华", f"2b 兜底应返回唯一候选, got {result.get('name')}"
    print("  [PASS] F: 步骤 2a 歧义 → 步骤 2b 唯一候选兜底成功")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
