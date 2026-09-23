"""上传件合同分析跳过 LLM + 提取结果复用（2026-09-23 提效）测试。

背景（测试学员丙工单 a734ac56 实测）：
- 上传件任务里 legacy `analyze_contract_from_file` 仍会调 LLM（OpenRouter 免费池），
  实测 59s 超时零产出（占全程 70s 的 84%），且其费用字段随后被上传管线权威覆盖 → 纯浪费；
- legacy 与上传管线各跑一次 `extract_contract_text_from_file`（百度 OCR 2 图 ×2 遍）。

修复：
1. `_run_contract_analysis` 对上传件传 `skip_llm=True` → legacy 只做 OCR/模板匹配/本地规则；
2. 同一文件只提取一次，legacy 与上传管线复用共享 extraction；
3. `analyze_contract` 超时 60s → 30s（仅影响仍走 LLM 的兜底路径）。

覆盖：
1. 上传件 `/api/contract/analyze` 全程不得调用 LLM（cs.analyze_contract 打桩即炸）；
2. 提取只跑一次，且共享 extraction 传给上传管线；
3. `analyze_contract_from_file(skip_llm=True)`：LLM 不被调、本地兜底回填费用、无 error；
4. `skip_llm=False`（电子合同兜底路径）行为不变：LLM 仍被调用；
5. `analyze_upload_contract_file(extraction=...)`：接受共享提取结果，不再重复提取；
6. `analyze_contract` 的 LLM 请求 timeout=30。
"""
import json

import pytest

import database
import app as app_module
import services.contract_service as cs
import services.upload_pipeline as upload_pipeline_module
from conftest import _autologin_admin  # noqa: F401


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
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        try:
            _autologin_admin(c)
        except Exception:
            pass
        yield c, archive_root


FAKE_EXTRACTION = {
    "text": "测试合同正文，不命中本地标准规则。",
    "source": "baidu_ocr",
    "can_confirm_fee_plan": True,
}


def _stub_shared_extraction(monkeypatch, counter):
    """在 upload_pipeline 模块属性上打桩共享提取（app 层调用期解析，应只调它一次）。"""
    def fake_extract(filepath, image_paths=None):
        counter["extract"] += 1
        return dict(FAKE_EXTRACTION)
    monkeypatch.setattr(upload_pipeline_module, "extract_contract_text_from_file", fake_extract)


def _forbid_llm(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("不应调用 LLM（cs.analyze_contract）")
    monkeypatch.setattr(cs, "analyze_contract", forbidden)


def _stub_fees(monkeypatch, total_fee=3580):
    monkeypatch.setattr(cs, "extract_contract_fees", lambda text: {"total_fee": total_fee})


def _stub_upload_analysis(monkeypatch, recorder, filepath):
    """打桩上传管线入口：记录收到的 extraction，返回最小可用 upload_analysis。"""
    def fake_upload(**kw):
        recorder["extraction"] = kw.get("extraction")
        filepath_ = kw.get("filepath", "")
        return {
            "tier_id": "", "tier_result": {}, "cache_key": "",
            "text_source": "baidu_ocr",
            "deductions_result": {
                "items": [], "total_deduction": 0, "refund": 3580,
                "refund_pending": False, "warnings": [],
                "total_fee": 3580, "paid_amount": 3580, "tail_due": 0, "net_refund": 0,
            },
            "contract_set": {"contracts": [{
                "kind": "服务合同", "tier": "", "file": filepath, "filename": "up.pdf",
                "sha256": "aa11", "text": "测试", "text_source": "baidu_ocr",
                "text_confidence": "high",
            }]},
        }
    monkeypatch.setattr(app_module, "analyze_upload_contract_file", fake_upload)


def _make_upload_ticket(fa):
    tid = database.save_ticket({
        "student_name": "李四",
        "id_card": "110101199003070011",
        "complaint_date": "2026-09-01",
        "complaint_type": "A",
        "school_short": "沙田",
        "organization_unit_type": "分店",
        "organization_unit_name": "沙田分店",
        "registration_date": "2023-05-01",
        "license_type": "C1",
        "exam_stage": "已受理",
        "handling_notes": "已沟通",
        "branch_cooperation": "好",
        "fee_plan_status": "draft",
        "total_fee": 0,
        "query_result": {},
    })
    database.update_ticket(tid, {"contract_set": json.dumps({"contracts": [
        {"kind": "", "tier": "", "file": fa, "filename": "up.pdf", "sha256": "aa11",
         "text": "", "text_source": "", "text_confidence": ""},
    ]}, ensure_ascii=False)})
    return tid


def _analyze(c, archive_root, fa, tid):
    resp = c.post("/api/contract/analyze", json={
        "filepath": fa, "ticket_id": tid, "exam_stage": "已受理",
        "training_hours": {}, "total_fee": 0, "id_card": "110101199003070011",
    })
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


# ── 1+2. 端到端：上传件不调 LLM、提取只跑一次、共享 extraction 下传 ──

def test_upload_analyze_never_calls_llm_and_reuses_extraction(client, monkeypatch):
    c, archive_root = client
    counter = {"extract": 0}
    _stub_shared_extraction(monkeypatch, counter)
    _forbid_llm(monkeypatch)
    _stub_fees(monkeypatch, 3580)
    recorder = {}
    fa = str(archive_root / "up.pdf")
    (archive_root / "up.pdf").write_bytes(b"%PDF-1.4 fake")
    _stub_upload_analysis(monkeypatch, recorder, fa)
    tid = _make_upload_ticket(fa)

    result = _analyze(c, archive_root, fa, tid)

    assert counter["extract"] == 1, "同一文件只应提取一次（原为 legacy+上传管线各一次）"
    assert recorder["extraction"] is not None, "共享 extraction 应下传给上传管线"
    assert result["total_fee"] == 3580
    assert not result.get("error")


# ── 3. 服务层单测：skip_llm 跳过 LLM、本地兜底回填 ──

def test_analyze_contract_from_file_skip_llm(monkeypatch):
    monkeypatch.setattr(
        cs, "extract_contract_text_from_file",
        lambda filepath, image_paths=None: dict(FAKE_EXTRACTION),
    )
    _forbid_llm(monkeypatch)
    _stub_fees(monkeypatch, 3580)

    result = cs.analyze_contract_from_file("/tmp/never.pdf", skip_llm=True)

    assert not result.get("error")
    assert result["analysis_source"] == "skip_llm_local"
    assert result["total_fee"] == 3580


def test_analyze_contract_from_file_without_skip_llm_still_calls_llm(monkeypatch):
    """电子合同兜底路径不受影响：skip_llm 缺省时 LLM 照常调用。"""
    monkeypatch.setattr(
        cs, "extract_contract_text_from_file",
        lambda filepath, image_paths=None: dict(FAKE_EXTRACTION),
    )
    llm_calls = []

    def fake_llm(*args, **kwargs):
        llm_calls.append(1)
        return {"summary": "AI 摘要", "total_fee": 3000}
    monkeypatch.setattr(cs, "analyze_contract", fake_llm)

    result = cs.analyze_contract_from_file("/tmp/never.pdf")

    assert llm_calls, "skip_llm 缺省（False）时仍应调用 LLM"
    assert result["summary"] == "AI 摘要"


# ── 4. 上传管线接受共享 extraction，不再自行提取 ──

def test_upload_pipeline_accepts_shared_extraction(monkeypatch):
    def forbidden_extract(*args, **kwargs):
        raise AssertionError("传入共享 extraction 后不应再次提取")
    monkeypatch.setattr(upload_pipeline_module, "extract_contract_text_from_file", forbidden_extract)

    captured = {}
    def fake_pipeline(extraction, ticket, **kwargs):
        captured["extraction"] = extraction
        return {"tier_id": "", "deductions_result": None}
    monkeypatch.setattr(upload_pipeline_module, "_run_pipeline", fake_pipeline)

    shared = dict(FAKE_EXTRACTION)
    upload_pipeline_module.analyze_upload_contract_file(
        "/tmp/never.pdf", {"id": "t1"}, image_paths=[], extraction=shared,
    )
    assert captured["extraction"] is shared


# ── 5. LLM 超时 60s → 30s ──

def test_analyze_contract_timeout_is_30s(monkeypatch):
    captured = {}

    # 密闭化：不依赖未入库的 data/config.json（worktree/CI 无该文件时会提前报「未配置」）
    monkeypatch.setattr(cs, "_llm_config", lambda section: {
        "api_url": "https://example.invalid/v1/chat/completions",
        "api_key": "test-key", "model": "test-model", "max_tokens": 10,
    })

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["timeout"] = timeout
        return FakeResp()
    monkeypatch.setattr(cs.requests, "post", fake_post)

    result = cs.analyze_contract("合同文本")
    assert not result.get("error")
    assert captured["timeout"] == 30, "LLM 超时应为 30 秒（原 60 秒，免费池失败时白等太久）"
