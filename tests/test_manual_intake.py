"""三系统无信息学员 · 人工建案（intake_type）测试。

覆盖：
1. manual-create 必填缺失 → 400 且错误逐项点名
2. 证件号校验失败 / 机构不在字典 / 报名时间格式错 / 手机号格式错 → 400
3. 正常创建 → 200：intake_type 落库、机构字典归一化（school_short=代号）
4. 同日同人重复提交 → 并入既有工单（merged_into_existing=True）
5. 空壳拦截 job 附 no_match/manual_intake_eligible（查无放行、超时不放行）
6. 统计 manual_no_record_count 单独列示 + 网点 manual_count
7. 登记表/回复函 投诉对象回落 organization_unit_name
"""
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import _autologin_admin  # noqa: F401

_TMP_DIR = Path(tempfile.mkdtemp(prefix="manual-intake-tests-"))

import database  # noqa: E402

database.DB_PATH = _TMP_DIR / "test.db"
database._local.clear()

import app as app_module  # noqa: E402
from docx import Document  # noqa: E402

VALID_ID = "110101199003070011"
UNIT_ID = "branch-tangxia-lincun"  # 塘厦青溪分校（青C）


@pytest.fixture()
def fresh_db():
    database._local.clear()
    database.init_db()
    yield database
    database._local.clear()


@pytest.fixture()
def client(fresh_db, tmp_path, monkeypatch):
    # 受理建夹打桩：测试不写真实「案件归档」目录
    def _fake_build_dir(ticket, root=None):
        return str(tmp_path), str(tmp_path / "投诉登记表.docx"), str(tmp_path / "投诉回复函.docx")
    monkeypatch.setattr(app_module, "build_archive_dir", _fake_build_dir)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        try: _autologin_admin(c)
        except Exception: pass
        yield c


def _payload(**overrides):
    data = {
        "student_name": "陈志明",
        "id_card": VALID_ID,
        "organization_unit_id": UNIT_ID,
        "registration_date": "2026-07-15",
        "phone": "13800000000",
        "complaint_content": "2026年7月报名缴费3800元，三系统查无信息",
        "complaint_date": "2026-08-27",
    }
    data.update(overrides)
    return data


def _create(client, **overrides):
    return client.post("/api/tickets/manual-create", json=_payload(**overrides))


# ── 1) 必填校验逐项点名 ──
def test_missing_required_fields_pointed_out(client):
    resp = _create(client, student_name="", organization_unit_id="",
                   registration_date="", id_card="")
    assert resp.status_code == 400
    msg = resp.get_json()["error"]
    for word in ("学员姓名", "身份证号", "所属分校/分店", "报名时间"):
        assert word in msg


# ── 2) 非法值校验 ──
def test_invalid_id_card_rejected(client):
    resp = _create(client, id_card="110101199003070011")  # 校验码错误
    assert resp.status_code == 400
    assert "身份证" in resp.get_json()["error"] or "证件" in resp.get_json()["error"]


def test_org_must_in_dictionary(client):
    resp = _create(client, organization_unit_id="no-such-unit")
    assert resp.status_code == 400
    assert "机构字典" in resp.get_json()["error"]


def test_bad_registration_date_format(client):
    resp = _create(client, registration_date="2026/07/15")
    assert resp.status_code == 400


def test_bad_phone_rejected(client):
    resp = _create(client, phone="12345")
    assert resp.status_code == 400


# ── 3) 正常创建 + 机构归一化 ──
def test_create_success_and_normalization(client):
    resp = _create(client)
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["intake_type"] == "三系统无信息"
    ticket = database.get_ticket(body["ticket_id"])
    assert ticket["intake_type"] == "三系统无信息"
    assert ticket["school_short"] == "青C"
    assert ticket["organization_unit_name"] == "塘厦青溪分校"
    assert ticket["organization_unit_type"] == "分校"
    assert ticket["organization_unit_code"] == "青C"
    assert ticket["registration_date"] == "2026-07-15"
    assert ticket["complaint_content"].startswith("2026年7月")


# ── 4) 同日同人去重并入 ──
def test_same_day_dedup_merges(client):
    r1 = _create(client)
    r2 = _create(client)
    assert r1.status_code == 200 and r2.status_code == 200
    b1, b2 = r1.get_json()["data"], r2.get_json()["data"]
    assert b2["merged_into_existing"] is True
    assert b2["ticket_id"] == b1["ticket_id"]
    records, total = database.list_tickets(limit=200)
    assert total == 1 and len(records) == 1


# ── 5) 空壳拦截：no_match 资格判定 ──
def _inject_job(job_id):
    with app_module.QUERY_JOBS_LOCK:
        app_module.QUERY_JOBS[job_id] = {
            "id": job_id, "status": "queued", "result": None,
            "sources": {"internal": "pending", "third": "pending", "driving": "pending"},
            "query_durations_ms": {}, "error": "",
        }


def test_query_status_surfaces_no_match_flags(client):
    with app_module.QUERY_JOBS_LOCK:
        app_module.QUERY_JOBS["job-surface"] = {
            "id": "job-surface", "status": "failed",
            "error": "未匹配到学员档案", "no_match": True,
            "manual_intake_eligible": True,
            "sources": {"internal": "not_found", "third": "not_found", "driving": "not_found"},
        }
    resp = client.get("/api/query/status/job-surface")
    body = resp.get_json()
    assert body["no_match"] is True
    assert body["manual_intake_eligible"] is True


def test_worker_marks_no_match_eligible(client, fresh_db, monkeypatch):
    async def fake_query_all(id_card, timeout=60, on_update=None):
        return SimpleNamespace(
            name="", id_card="",
            sources={"internal": "not_found", "third": "not_found", "driving": "not_found"},
        )

    monkeypatch.setattr(app_module.query_engine, "query_all", fake_query_all)
    _inject_job("job-ok")
    app_module._query_job_worker("job-ok", {"id_card": VALID_ID})
    job = client.get("/api/query/status/job-ok").get_json()
    assert job["status"] == "failed"
    assert job["no_match"] is True
    assert job["manual_intake_eligible"] is True


def test_worker_no_match_when_engine_backfills_id_card(client, fresh_db, monkeypatch):
    """真实引擎会把查询用证件号回填进结果，仅姓名判空才算查无，不得落库空工单。"""
    async def fake_query_all(id_card, timeout=60, on_update=None):
        return SimpleNamespace(
            name="", id_card=id_card,
            sources={"internal": "not_found", "third": "not_found", "driving": "not_found"},
        )

    monkeypatch.setattr(app_module.query_engine, "query_all", fake_query_all)
    monkeypatch.setattr(database, "save_ticket",
                        lambda *a, **k: pytest.fail("查无记录不应创建工单"))
    _inject_job("job-backfill")
    app_module._query_job_worker("job-backfill", {"id_card": VALID_ID})
    job = client.get("/api/query/status/job-backfill").get_json()
    assert job["status"] == "failed"
    assert job["no_match"] is True
    assert job["manual_intake_eligible"] is True


def test_worker_blocks_manual_when_timeout(client, fresh_db, monkeypatch):
    async def fake_query_all(id_card, timeout=60, on_update=None):
        return SimpleNamespace(
            name="", id_card="",
            sources={"internal": "not_found", "third": "timeout", "driving": "not_found"},
        )

    monkeypatch.setattr(app_module.query_engine, "query_all", fake_query_all)
    _inject_job("job-timeout")
    app_module._query_job_worker("job-timeout", {"id_card": VALID_ID})
    job = client.get("/api/query/status/job-timeout").get_json()
    assert job["no_match"] is True
    assert job["manual_intake_eligible"] is False


# ── 6) 统计口径：全额计入 + 单独列示 ──
def test_statistics_counts_manual(fresh_db, client):
    _create(client)
    _create(client, id_card="F1249468(8)", student_name="外籍学员", complaint_date="2026-08-27")
    stats = database.get_ticket_statistics()
    assert stats["manual_no_record_count"] == 2
    assert stats["total"] == 2  # 全额计入总量
    school = [s for s in stats["by_school"] if s["code"] == "青C"]
    assert school and school[0]["manual_count"] == 2


# ── 7) 文档字段回落 ──
def test_registration_form_school_fallback():
    from services.visit_service import build_registration_form_data
    data = build_registration_form_data({
        "organization_unit_name": "塘厦青溪分校",
        "organization_unit_type": "分校",
        "complaint_type": "A",
    })
    assert data["fields"][0][5] == "塘厦青溪分校"


def test_reply_docx_school_fallback(tmp_path):
    from services.reply_docx import generate_reply_docx
    ticket = {
        "student_name": "陈志明", "id_card": VALID_ID,
        "complaint_date": "2026-08-27", "registration_date": "2026-07-15",
        "school_name": "", "organization_unit_name": "塘厦青溪分校",
        "organization_unit_type": "分校", "license_type": "C1",
        "actual_paid": 3800,
    }
    out = tmp_path / "reply.docx"
    res = generate_reply_docx(ticket, [], str(out))
    assert res["success"] is True
    text = "\n".join(p.text for p in Document(str(out)).paragraphs)
    assert "塘厦青溪" in text


# ── 8) 旧库迁移：intake_type 列存在 ──
def test_intake_type_column_exists(fresh_db):
    with database.get_db() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(complaint_tickets)").fetchall()]
    assert "intake_type" in cols
