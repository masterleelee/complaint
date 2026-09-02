"""档位落库 helper（_persist_tier_columns）测试（P0-2 / P1-1 统一入口）。

背景：档位落库此前只有一个入口（上传后 analyze），改档重算（retier）与按网点重算
（recompute）都不写回 DB，导致 DB 凭据与内存结果不一致。本轮抽出 `_persist_tier_columns`
统一三个入口。

关键语义：
- display_name / kind 一律从 TIERS_BY_ID[tier_id] 权威推导，**不 trust 可能过期的
  tier_result**（改档重算时 tier_result 可能是旧档或 identify_tier 另算结果）。
- confidence 优先取 tier_result；档位存在但无 confidence → "manual"（改档/人工指定）。
- 空 tier → 四列落空值，不残留旧档位。
"""
import tempfile
from pathlib import Path

import pytest
from conftest import _autologin_admin  # noqa: F401

_TMP_DIR = Path(tempfile.mkdtemp(prefix="tier-persist-tests-"))

import database  # noqa: E402

database.DB_PATH = _TMP_DIR / "test.db"
database._local.clear()
database.init_db()

import app as app_module  # noqa: E402

from app import _persist_tier_columns  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_db():
    """每个用例前清空工单表，避免跨用例串扰。"""
    with database.get_db() as conn:
        conn.execute("DELETE FROM complaint_tickets")
    yield
    with database.get_db() as conn:
        conn.execute("DELETE FROM complaint_tickets")


def _seed_ticket(**kwargs):
    return database.save_ticket({"student_name": "测试学员", **kwargs})


def test_analyze_entry_persists_from_tier_result():
    """上传后分析场景：display/kind 从档位表权威推导，confidence 取 tier_result。"""
    tid = _seed_ticket()
    result = {
        "tier_id": "2019_dongcheng",
        "tier_result": {
            "tier_id": "2019_dongcheng",
            "display_name": "东城自制",
            "confidence": "high",
        },
    }
    _persist_tier_columns(tid, result)
    row = database.get_ticket(tid)
    assert row["contract_tier_id"] == "2019_dongcheng"
    assert row["contract_kind"] == "单一培训"  # 档位表 kind，非 tier_result
    assert row["contract_tier_display"] == "东城自制"
    assert row["contract_tier_confidence"] == "high"


def test_retier_stale_tier_result_does_not_corrupt_display():
    """改档重算场景：tier_id 是新档，但 tier_result 是旧档/另算结果 → display 仍以新档为准。"""
    tid = _seed_ticket()
    # new_tier_id=2023_branch_store，但 result 里残留旧档 2019_dongcheng 的 tier_result
    result = {
        "tier_id": "2023_branch_store",
        "tier_result": {
            "tier_id": "2019_dongcheng",
            "display_name": "东城自制",  # 过期值，不应被采信
            "confidence": "medium",
        },
    }
    _persist_tier_columns(tid, result)
    row = database.get_ticket(tid)
    assert row["contract_tier_id"] == "2023_branch_store"
    assert row["contract_kind"] == "单一培训"
    assert row["contract_tier_display"] == "2023·分店"  # 权威档位表，非「东城自制」
    assert row["contract_tier_confidence"] == "medium"  # confidence 仍取 tier_result


def test_retier_without_confidence_marks_manual():
    """改档场景：tier_result 无 confidence → confidence 落 manual。"""
    tid = _seed_ticket()
    result = {"tier_id": "2023_branch_store", "tier_result": {}}
    _persist_tier_columns(tid, result)
    row = database.get_ticket(tid)
    assert row["contract_tier_id"] == "2023_branch_store"
    assert row["contract_tier_display"] == "2023·分店"
    assert row["contract_tier_confidence"] == "manual"


def test_empty_tier_clears_all_four_columns():
    """识别失败/空 tier：四列落空值，不残留旧档位。"""
    tid = _seed_ticket(
        contract_tier_id="2019_dongcheng",
        contract_kind="单一培训",
        contract_tier_display="东城自制",
        contract_tier_confidence="high",
    )
    _persist_tier_columns(tid, {"tier_id": "", "tier_result": {}})
    row = database.get_ticket(tid)
    assert row["contract_tier_id"] == ""
    assert row["contract_kind"] == ""
    assert row["contract_tier_display"] == ""
    assert row["contract_tier_confidence"] == ""


def test_no_ticket_id_is_noop():
    """无 ticket_id 时不做任何写库（幂等、不抛）。"""
    _persist_tier_columns("", {"tier_id": "2019_dongcheng", "tier_result": {}})


@pytest.mark.parametrize(
    "tier_id, expected_display, expected_kind",
    [
        ("2019_service", "2019·服务", "服务"),
        ("2019_pay_agent", "2019·代缴", "代缴"),
        ("2019_training", "2019·培训", "培训"),
        ("2021_2022", "2021-2022", "单一培训"),
        ("2023_branch_school", "2023·分校", "单一培训"),
        ("2023_branch_store", "2023·分店", "单一培训"),
        ("2019_dongcheng", "东城自制", "单一培训"),
    ],
)
def test_all_tiers_map_display_and_kind_from_registry(tier_id, expected_display, expected_kind):
    """七档的 display/kind 都从档位表权威推导（不依赖 tier_result 的 display_name）。"""
    tid = _seed_ticket()
    _persist_tier_columns(tid, {"tier_id": tier_id, "tier_result": {"confidence": "high"}})
    row = database.get_ticket(tid)
    assert row["contract_tier_id"] == tier_id
    assert row["contract_tier_display"] == expected_display
    assert row["contract_kind"] == expected_kind
    assert row["contract_tier_confidence"] == "high"
