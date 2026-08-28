"""AI 投诉文本提取测试。

覆盖：
1. _parse_llm_json：```json 围栏 / 裸 JSON / 杂质文本兜底 / 非法输出
2. parse_complaint_text 合并逻辑：正则优先、AI 补姓名与摘要、AI 幻觉证件号被本地校验拦截
3. AI 失败降级：返回正则结果 + ai_error，不阻塞
4. /api/intake/parse JSON 入参路由
5. complaint_summary 落库（save_ticket 白名单 + _persist_query_result 映射）
"""
import pytest
from conftest import _autologin_admin  # noqa: F401

import database
from services import intake_service
from services.intake_service import _parse_llm_json, parse_complaint_text

VALID_ID = "110101199003070011"
VALID_PHONE = "13800000000"

CASE_TEXT = (
    "（刘乐怡）我投诉东莞市快捷机动车驾驶员培训有限公司（主山训练场），学员刘乐怡，C2，缴费2000元。"
    "我没有上过任何课程、学时全部为0，没有约考科目一。"
    "我只愿意承担400元档案建档费，要求退回剩余1600元。我手上有聊天记录、退费审批表作为证据。"
)


@pytest.fixture()
def fresh_db(tmp_path):
    database.DB_PATH = tmp_path / "test.db"
    database._local.clear()
    database.init_db()
    yield database
    database._local.clear()


@pytest.fixture()
def client(fresh_db):
    from app import app as flask_app

    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        try: _autologin_admin(c)
        except Exception: pass
        yield c


# ── 1. _parse_llm_json ──

def test_parse_llm_json_fenced():
    assert _parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_llm_json_plain():
    assert _parse_llm_json('{"a": "b"}') == {"a": "b"}


def test_parse_llm_json_with_noise():
    assert _parse_llm_json('好的，结果如下：{"a": 1} 以上。') == {"a": 1}


def test_parse_llm_json_invalid_returns_empty():
    assert _parse_llm_json("无法解析") == {}
    assert _parse_llm_json("") == {}
    assert _parse_llm_json('[1,2,3]') == {}


# ── 2. 合并逻辑 ──

def test_regex_hit_skips_ai_and_returns_fast(monkeypatch):
    """正则命中证号/手机号 → 跳过 AI 调用（毫秒级快路径），姓名/摘要留空待查询阶段补。"""
    calls = []
    monkeypatch.setattr(
        intake_service, "ai_extract_complaint",
        lambda text: calls.append(text) or {},
    )
    text = f"学员刘乐怡，身份证号{VALID_ID}，手机号{VALID_PHONE}，要求退费。"
    out = parse_complaint_text(text)
    assert out["id_card"] == VALID_ID
    assert out["phone"] == VALID_PHONE
    assert out["student_name"] == ""
    assert out["complaint_summary"] == ""
    assert calls == []  # AI 未被调用


def test_merge_ai_fills_missing_id_only_if_valid(monkeypatch):
    """文本无证号/手机号时，AI 提供的值必须通过本地校验才采纳。"""
    monkeypatch.setattr(
        intake_service, "ai_extract_complaint",
        lambda text: {
            "student_name": "刘乐怡",
            "id_card": VALID_ID,
            "phone": VALID_PHONE,
        },
    )
    out = parse_complaint_text(CASE_TEXT)
    assert out["id_card"] == VALID_ID
    assert out["phone"] == VALID_PHONE
    assert out["student_name"] == "刘乐怡"


def test_merge_rejects_hallucinated_id(monkeypatch):
    monkeypatch.setattr(
        intake_service, "ai_extract_complaint",
        lambda text: {"student_name": "刘乐怡", "id_card": "123456789012345678",
                      "phone": "12345"},
    )
    out = parse_complaint_text(CASE_TEXT)
    assert out["id_card"] == ""
    assert out["phone"] == ""


def test_ai_failure_degrades_to_regex(monkeypatch):
    """无证号/手机号文本且 AI 失败 → 返回空结果 + ai_error，不阻塞受理。"""
    monkeypatch.setattr(
        intake_service, "ai_extract_complaint",
        lambda text: {"error": "大模型接口请求失败：超时"},
    )
    out = parse_complaint_text(CASE_TEXT)
    assert out["id_card"] == ""
    assert out["phone"] == ""
    assert out["ai_error"] == "大模型接口请求失败：超时"


def test_regex_hit_not_blocked_by_ai_failure(monkeypatch):
    """正则已命中时即使 AI 故障也照常快返回（AI 根本不会被调用）。"""
    monkeypatch.setattr(
        intake_service, "ai_extract_complaint",
        lambda text: {"error": "大模型接口请求失败：超时"},
    )
    text = f"身份证号{VALID_ID}，手机号{VALID_PHONE}，要求退费。"
    out = parse_complaint_text(text)
    assert out["id_card"] == VALID_ID
    assert out["phone"] == VALID_PHONE
    assert "ai_error" not in out


def test_short_text_returns_error():
    assert "error" in parse_complaint_text("太短")


# ── 3. 路由 ──

def test_parse_route_accepts_json_text(client, monkeypatch):
    monkeypatch.setattr(
        intake_service, "ai_extract_complaint",
        lambda text: {"student_name": "刘乐怡", "id_card": "", "phone": ""},
    )
    resp = client.post("/api/intake/parse", json={"text": CASE_TEXT})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["data"]["student_name"] == "刘乐怡"


def test_summarize_parses_llm_output(monkeypatch):
    """ai_summarize_complaint 解析 LLM JSON 输出；失败透传 error。"""
    monkeypatch.setattr(
        intake_service, "_llm_chat",
        lambda prompt, max_tokens: {"complaint_summary": "学员刘乐怡要求退费"},
    )
    out = intake_service.ai_summarize_complaint(CASE_TEXT)
    assert out["complaint_summary"] == "学员刘乐怡要求退费"

    monkeypatch.setattr(
        intake_service, "_llm_chat",
        lambda prompt, max_tokens: {"error": "大模型接口请求失败：超时"},
    )
    out = intake_service.ai_summarize_complaint(CASE_TEXT)
    assert "error" in out


def test_parse_route_rejects_short_text(client):
    resp = client.post("/api/intake/parse", json={"text": "太短"})
    assert resp.status_code == 400


# ── 4. 摘要落库 ──

def test_save_ticket_summary_roundtrip(fresh_db):
    tid = database.save_ticket({"student_name": "张三", "complaint_summary": "测试摘要"})
    assert database.get_ticket(tid)["complaint_summary"] == "测试摘要"


def test_persist_query_result_maps_summary(fresh_db):
    import app as app_module

    data = {
        "complaint_desc": "投诉原文",
        "complaint_summary": "投诉摘要",
        "source_channel": "12345",
        "complaint_type": "A",
        "complaint_date": "2026-08-25",
    }
    result = {"name": "刘乐怡", "id_card": VALID_ID, "phone": VALID_PHONE}
    out = app_module._persist_query_result(data, result, VALID_ID, VALID_PHONE)
    ticket = database.get_ticket(out["ticket_id"])
    assert ticket["complaint_summary"] == "投诉摘要"
    assert ticket["complaint_content"] == "投诉原文"


# ── 5. 查询阶段并行摘要落库 ──

def test_query_job_generates_summary_in_parallel(client, fresh_db, monkeypatch):
    """查询 worker 与爬虫并行生成摘要，建案时随单存档。"""
    import time
    import app as app_module
    from types import SimpleNamespace

    def fake_summary(text):
        time.sleep(0.3)  # 模拟 LLM 耗时，验证与爬虫并行而非串行阻塞
        return {"complaint_summary": "并行生成的投诉摘要"}

    monkeypatch.setattr(app_module, "ai_summarize_complaint", fake_summary)

    merged = SimpleNamespace(
        name="刘乐怡", id_card=VALID_ID, phone=VALID_PHONE,
        license_type="", school_name="", school_short="",
        registration_date="", exam_stage="", student_status="",
        training_hours={},
        sources={"internal": "success", "third": "not_found", "driving": "not_found"},
        query_durations_ms={}, error="",
    )
    async def fake_query_all(id_card, timeout=60, on_update=None):
        return merged

    monkeypatch.setattr(app_module.query_engine, "query_all", fake_query_all)

    resp = client.post("/api/query/start", json={
        "id_card": VALID_ID, "phone": VALID_PHONE,
        "complaint_desc": "投诉原文内容",
        "source_channel": "12345", "complaint_type": "A",
        "complaint_date": "2026-08-25",
    })
    assert resp.status_code == 200
    job_id = resp.get_json()["job_id"]
    st = {}
    for _ in range(40):
        st = client.get(f"/api/query/status/{job_id}").get_json()
        if st.get("status") in ("done", "failed"):
            break
        time.sleep(0.1)
    assert st.get("status") == "done", st
    ticket = database.get_ticket(st["result"]["ticket_id"])
    assert ticket["complaint_summary"] == "并行生成的投诉摘要"
    assert ticket["complaint_content"] == "投诉原文内容"
