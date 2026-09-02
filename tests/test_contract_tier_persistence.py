"""档位落库（P0-2）测试：档位识别结果写回 complaint_tickets 的四列。

背景：档位识别（identify_tier）此前只在内存 result.tier_id / tier_result 中，
从未落库 —— 改档重算、历史审计、跨会话核对都拿不到「这份合同被定成哪档」的凭据。

覆盖：
1. 列迁移：init_db 后 complaint_tickets 含 contract_tier_id / contract_kind /
   contract_tier_display / contract_tier_confidence 四列。
2. 白名单落库：save_ticket 能持久化上述四列（roundtrip）。
3. 回写提取：从 tier_result 提取 display_name/confidence、从 TIERS_BY_ID 取 kind 的映射正确。
"""
import pytest
from conftest import _autologin_admin  # noqa: F401

import database
from services.contract_tiers import TIERS_BY_ID


@pytest.fixture()
def fresh_db(tmp_path):
    database.DB_PATH = tmp_path / "test.db"
    database._local.clear()
    database.init_db()
    yield database
    database._local.clear()


def _ticket_columns(conn):
    cur = conn.execute("PRAGMA table_info(complaint_tickets)")
    return {row[1] for row in cur.fetchall()}


def test_migration_adds_tier_columns(fresh_db):
    """init_db 迁移后，complaint_tickets 应含四个档位列。"""
    with database.get_db() as conn:
        cols = _ticket_columns(conn)
    for col in ("contract_tier_id", "contract_kind",
                "contract_tier_display", "contract_tier_confidence"):
        assert col in cols, f"缺少列 {col}"


def test_save_ticket_persists_tier_columns(fresh_db):
    """save_ticket 白名单放行四列，写入后读回一致（生产路径 update_ticket→save_ticket）。"""
    record_id = database.save_ticket({
        "student_name": "王俊林",
        "contract_tier_id": "2019_dongcheng",
        "contract_kind": "单一培训",
        "contract_tier_display": "东城自制",
        "contract_tier_confidence": "high",
    })
    row = database.get_ticket(record_id)
    assert row["contract_tier_id"] == "2019_dongcheng"
    assert row["contract_kind"] == "单一培训"
    assert row["contract_tier_display"] == "东城自制"
    assert row["contract_tier_confidence"] == "high"


def test_update_ticket_overwrites_tier_columns(fresh_db):
    """update_ticket 可覆盖档位列（改档重算不残留旧值）。"""
    record_id = database.save_ticket({
        "student_name": "王俊林",
        "contract_tier_id": "2019_dongcheng",
        "contract_kind": "单一培训",
    })
    database.update_ticket(record_id, {
        "contract_tier_id": "2023_branch_store",
        "contract_kind": "单一培训",
        "contract_tier_display": "2023·分店",
        "contract_tier_confidence": "medium",
    })
    row = database.get_ticket(record_id)
    assert row["contract_tier_id"] == "2023_branch_store"
    assert row["contract_tier_display"] == "2023·分店"
    assert row["contract_tier_confidence"] == "medium"


@pytest.mark.parametrize(
    "tier_id, expected_display, expected_kind, expected_conf",
    [
        ("2019_service", "2019·服务", "服务", "high"),
        ("2019_pay_agent", "2019·代缴", "代缴", "high"),
        ("2019_training", "2019·培训", "培训", "high"),
        ("2021_2022", "2021-2022", "单一培训", "high"),
        ("2023_branch_school", "2023·分校", "单一培训", "high"),
        ("2023_branch_store", "2023·分店", "单一培训", "high"),
        ("2019_dongcheng", "东城自制", "单一培训", "high"),
    ],
)
def test_tier_result_mapping(tier_id, expected_display, expected_kind, expected_conf):
    """回写提取的映射与档位表一致（display/confidence 取自 tier_result，kind 取自档位表）。"""
    tier = TIERS_BY_ID[tier_id]
    # 模拟 identify_tier 返回结构
    tier_result = {
        "tier_id": tier_id,
        "display_name": tier["display_name"],
        "confidence": expected_conf,
    }
    tier_id_out = str(tier_result.get("tier_id") or "")
    tier_display = str(tier_result.get("display_name") or "")
    tier_conf = str(tier_result.get("confidence") or "")
    tier_kind = ""
    if tier_id_out:
        tier_kind = str((TIERS_BY_ID.get(tier_id_out) or {}).get("kind") or "")

    assert tier_id_out == tier_id
    assert tier_display == expected_display
    assert tier_conf == expected_conf
    assert tier_kind == expected_kind


def test_empty_tier_result_maps_to_blank(fresh_db):
    """识别失败（空 tier）时四列落空值，不残留旧档位。"""
    record_id = database.save_ticket({
        "student_name": "无名",
        "contract_tier_id": "",
        "contract_kind": "",
        "contract_tier_display": "",
        "contract_tier_confidence": "",
    })
    row = database.get_ticket(record_id)
    assert row["contract_tier_id"] == ""
    assert row["contract_kind"] == ""
    assert row["contract_tier_display"] == ""
    assert row["contract_tier_confidence"] == ""
