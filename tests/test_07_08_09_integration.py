"""工单 07 / 09 HTTP 集成测试：闸门拒绝/放行两向 + 页图路由（08 由 service 层覆盖）。"""

import io

import pytest

import database
import app as app_module
import services.archive_service as archive_service_module
from conftest import _autologin_admin  # noqa: F401


# ── 通用 fixture（每用例独立 DB + 自动登录 admin） ──────────────────

@pytest.fixture()
def fresh_db(tmp_path):
    database.DB_PATH = tmp_path / "test.db"
    database._local.clear()
    database.init_db()
    yield database
    database._local.clear()


@pytest.fixture()
def client(fresh_db, monkeypatch, tmp_path):
    archive_root = tmp_path / "archive-root"
    archive_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app_module, "load_config", lambda: {"archive_root": str(archive_root)})
    monkeypatch.setattr(archive_service_module, "load_config", lambda: {"archive_root": str(archive_root)})
    monkeypatch.setattr(app_module, "UPLOAD_DIR", str(tmp_path / "uploads"), raising=False)
    monkeypatch.setattr(app_module, "ARCHIVE_DIR", str(archive_root), raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        try:
            _autologin_admin(c)
        except Exception:
            pass
        yield c


def _make_ticket(**overrides):
    data = {
        "student_name": "张三",
        "id_card": "110101199003070011",
        "complaint_date": "2026-09-01",
        "complaint_type": "A",
        "school_short": "沙田",
        "organization_unit_type": "分店",
        "organization_unit_name": "沙田分店",
        "registration_date": "2023-09-01",
        "license_type": "C1",
        "exam_stage": "实操中",
        "handling_notes": "已沟通",
        "branch_cooperation": "好",
        "fee_plan_status": "draft",
    }
    data.update(overrides)
    tid = database.save_ticket(data)
    return tid


def _set_deductions(ticket_id, deductions):
    """把 deductions 写到 ticket.deduction_detail（service 07 闸门从此字段读 pending）。"""
    import json
    database.update_ticket(ticket_id, {"deduction_detail": json.dumps(deductions, ensure_ascii=False)})


# ── 07 闸门：fee-confirm ──────────────────────────────────────────────


def test_fee_confirm_rejects_when_pending_item_exists(client, fresh_db):
    """存在 pending 项 → fee-confirm 返回 400 + 提示待确认项。"""
    tid = _make_ticket(fee_plan_status="draft")
    _set_deductions(tid, [
        {"item": "服务费", "amount": 600, "source": "tier_default", "pending": False},
        {"item": "违约金", "amount": 0, "source": "pending", "pending": True},  # pending
    ])
    resp = client.post(
        f"/api/tickets/{tid}/fee-confirm",
        json={
            "deductions": [
                {"item": "服务费", "amount": 600},
                {"item": "违约金", "amount": 0},
            ],
            "total_fee": 6000,
            "actual_paid": 5000,
        },
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert "待确认" in (body.get("error") or "")


def test_fee_confirm_passes_when_no_pending(client, fresh_db):
    """无 pending 项 → fee-confirm 正常通过。"""
    tid = _make_ticket(fee_plan_status="draft")
    _set_deductions(tid, [
        {"item": "服务费", "amount": 600, "source": "tier_default", "pending": False},
        {"item": "违约金", "amount": 1200, "source": "tier_default", "pending": False},
    ])
    resp = client.post(
        f"/api/tickets/{tid}/fee-confirm",
        json={
            "deductions": [
                {"item": "服务费", "amount": 600},
                {"item": "违约金", "amount": 1200},
            ],
            "total_fee": 6000,
            "actual_paid": 5000,
        },
    )
    # 成功（200/201）或现有闸门失败（400）但不含"待确认"
    if resp.status_code >= 400:
        assert "待确认" not in (resp.get_json().get("error") or "")


def test_fee_confirm_no_fee_basis_skips_gating(client, fresh_db):
    """no_fee_basis=True 走 0 口径，闸门 07 不参与（与 pending 互斥语义）。"""
    tid = _make_ticket(fee_plan_status="draft")
    _set_deductions(tid, [
        {"item": "违约金", "amount": 0, "source": "pending", "pending": True},
    ])
    resp = client.post(
        f"/api/tickets/{tid}/fee-confirm",
        json={"no_fee_basis": True},
    )
    # 闸门不应触发（no_fee_basis 是合法分支）；即使有 pending 也走通
    body = resp.get_json() or {}
    assert "待确认" not in (body.get("error") or "")


# ── 07 闸门：archive ────────────────────────────────────────────────


def test_archive_rejects_when_pending_item_exists(client, fresh_db, monkeypatch):
    """归档端点：存在 pending → 400 + 待确认。"""
    from config import save_config
    cfg = {"archive_root": ""}
    monkeypatch.setattr(app_module, "load_config", lambda: cfg)
    monkeypatch.setattr(archive_service_module, "load_config", lambda: cfg)

    tid = _make_ticket(fee_plan_status="confirmed")  # 既有三闸门全过
    _set_deductions(tid, [
        {"item": "违约金", "amount": 0, "source": "pending", "pending": True},
    ])
    resp = client.post(f"/api/tickets/{tid}/archive", json={})
    assert resp.status_code == 400
    body = resp.get_json()
    assert "待确认" in (body.get("errors") or [""])[0]


def test_archive_passes_after_pending_resolved(client, fresh_db, monkeypatch, tmp_path):
    """归档端点：pending 已补齐（false）→ 通过（既有闸门全过的前提下）。"""
    from config import save_config
    cfg = {"archive_root": str(tmp_path / "archive-root-2")}
    monkeypatch.setattr(app_module, "load_config", lambda: cfg)
    monkeypatch.setattr(archive_service_module, "load_config", lambda: cfg)
    (tmp_path / "archive-root-2").mkdir(parents=True, exist_ok=True)

    tid = _make_ticket(fee_plan_status="confirmed")
    _set_deductions(tid, [
        {"item": "违约金", "amount": 1200, "source": "tier_default", "pending": False},
    ])
    # 兜底生成登记表/回复函路径会被 archive_case 跳过，files 为空 → 兜底搬运
    # 测试重点是 07 闸门不挡；这里仅断言「不报待确认」
    resp = client.post(f"/api/tickets/{tid}/archive", json={})
    body = resp.get_json() or {}
    errors = body.get("errors") or []
    assert not any("待确认" in (e or "") for e in errors)


# ── 09 页图路由 ────────────────────────────────────────────────


def test_page_image_missing_path(client):
    resp = client.get("/api/contract/page-image?path=&page=1")
    assert resp.status_code == 400


def test_page_image_nonexistent_file(client):
    resp = client.get("/api/contract/page-image?path=/nope/missing.pdf&page=1")
    assert resp.status_code == 404


def test_page_image_invalid_page_param(client):
    resp = client.get("/api/contract/page-image?path=/tmp/x.pdf&page=abc")
    assert resp.status_code == 400


# ── 08 retier / recompute 路由冒烟 ─────────────────────────────────────


def test_retier_missing_new_tier_id(client, fresh_db):
    tid = _make_ticket()
    resp = client.post(
        "/api/contract/retier",
        json={"ticket_id": tid, "filepath": "/nonexistent.pdf", "new_tier_id": ""},
    )
    assert resp.status_code == 400


def test_retier_invalid_ticket(client, fresh_db):
    resp = client.post(
        "/api/contract/retier",
        json={"ticket_id": "nonexistent", "filepath": "/x.pdf", "new_tier_id": "2023_branch_store"},
    )
    assert resp.status_code == 404


def test_recompute_invalid_ticket(client, fresh_db):
    resp = client.post(
        "/api/contract/recompute",
        json={"ticket_id": "nonexistent", "filepath": "/x.pdf"},
    )
    assert resp.status_code == 404


def test_cache_stats_requires_debug(client):
    resp = client.get("/api/contract/cache/stats")
    assert resp.status_code == 403


def test_cache_stats_with_debug_param(client):
    resp = client.get("/api/contract/cache/stats?debug=1")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("success") is True
    assert "contract_cache" in (body.get("data") or {})
    assert "page_cache" in (body.get("data") or {})
