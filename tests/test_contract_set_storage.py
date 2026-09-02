"""合同集合分条落库测试（工单 03-contract-set-storage，ADR-0002）。

覆盖：旧单份结构兼容、分条规范化、正文随分析落库（来源+置信度）、
上传多份分条累积（HTTP 层）、归档/requery 读取方回归护栏。
"""

import io
import shutil
import tempfile
from pathlib import Path

import pytest
from conftest import _autologin_admin  # noqa: F401

_TMP_DIR = Path(tempfile.mkdtemp(prefix="contract-set-tests-"))

import database  # noqa: E402

database.DB_PATH = _TMP_DIR / "test.db"
database._local.clear()

import app as app_module  # noqa: E402
import config as config_module  # noqa: E402  # noqa: F401
import services.archive_service as archive_service_module  # noqa: E402
from services.contract_service import (  # noqa: E402
    analyze_contract_from_file,
    attach_contract_text,
    contract_set_from_ai_data,
    merge_analysis_into_contract_set,
    normalize_contract_set,
    text_confidence_of,
)

PROJECT = Path(__file__).parent.parent
TEMPLATE_PDF = PROJECT / "1合同种类" / "2019年" / "1服务合同.pdf"

# img2pdf 拒绝小于 3 PDF 单位的页面（1×1 像素夹具会触发
# ValueError: Page size must be between 3 and 14400）→ 用 PIL 生成真实尺寸 PNG
def _png_bytes(size, color):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


_PNG_A = _png_bytes((64, 64), (255, 0, 0))
_PNG_B = _png_bytes((64, 64), (0, 255, 0))


@pytest.fixture()
def fresh_db(tmp_path):
    """每用例独立 DB 文件：save_ticket 默认「同日同人合并」，共享库会让
    后续用例的 _make_ticket() 复用上一用例工单、合同分条跨用例累积。"""
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
        "student_name": "王小明",
        "id_card": "110101199003070011",
        "complaint_date": "2026-08-31",
        "complaint_type": "A",
        "school_short": "沙田",
        "organization_unit_type": "分校",
        "organization_unit_name": "沙田分校",
        "registration_date": "2019-05-01",
        "license_type": "C1",
        "exam_stage": "科目一",
    }
    data.update(overrides)
    tid = database.save_ticket(data)
    return tid


# ── 服务层：normalize_contract_set ───────────────────────────────────

LEGACY_SET = {
    "contracts": [
        {
            "contract_id": "extracted-contract",
            "title": "东莞驾培电子合同",
            "total_fee": 5000,
            "evidence": {"file": "/case/张三_合同_a.pdf", "source": "local_rules"},
            "rules": [{"type": "fixed", "item": "服务费", "amount": 600}],
        }
    ]
}


def test_legacy_structure_normalizes_to_single_entry():
    norm = normalize_contract_set(LEGACY_SET)
    assert len(norm["contracts"]) == 1
    entry = norm["contracts"][0]
    assert entry["file"] == "/case/张三_合同_a.pdf"
    assert entry["kind"] == ""
    assert entry["tier"] == ""
    assert entry["text"] == ""
    assert entry["text_source"] == ""
    assert entry["text_confidence"] == ""
    # 业务字段原样保留
    assert entry["contract_id"] == "extracted-contract"
    assert entry["total_fee"] == 5000
    assert entry["rules"] == LEGACY_SET["contracts"][0]["rules"]


def test_new_structure_passthrough():
    new_set = {
        "contracts": [
            {"kind": "服务", "tier": "2019_service", "file": "/a.pdf", "text": "内容A", "text_source": "pdf_text", "text_confidence": "high"},
            {"kind": "培训", "tier": "", "file": "/b.pdf", "text": "内容B", "text_source": "vision_text", "text_confidence": "medium"},
        ]
    }
    norm = normalize_contract_set(new_set)
    assert len(norm["contracts"]) == 2
    assert norm["contracts"][0]["kind"] == "服务"
    assert norm["contracts"][1]["text"] == "内容B"


@pytest.mark.parametrize("bad", [None, "abc", 123, {}, {"contracts": None}, {"contracts": "x"}, {"contracts": [None, 1, "s"]}])
def test_malformed_inputs_never_raise(bad):
    norm = normalize_contract_set(bad)
    assert isinstance(norm, dict) and isinstance(norm.get("contracts"), list)
    assert all(isinstance(item, dict) for item in norm["contracts"])


def test_normalize_is_idempotent_with_canonical_keys():
    """读取方护栏（ADR-0003）：contract_set 的全部读取方都经 normalize_contract_set，
    护栏断言其幂等且每条产出六个规范字段——不再引用零引用的 refund_engine 契约。"""
    norm = normalize_contract_set(LEGACY_SET)
    again = normalize_contract_set(norm)
    assert again == norm
    for entry in norm["contracts"]:
        for key in ("kind", "tier", "file", "text", "text_source", "text_confidence"):
            assert key in entry


# ── 服务层：正文来源置信度 ───────────────────────────────────────────

@pytest.mark.parametrize(
    "source, expected",
    [
        ("pdf_text", "high"),
        ("vision_text", "medium"),
        ("local_ocr", "low"),
        ("unknown", "low"),
        ("", "low"),
    ],
)
def test_text_confidence_mapping(source, expected):
    assert text_confidence_of(source) == expected


# ── 服务层：attach_contract_text（正文随分析落库） ────────────────────

def test_attach_text_to_matching_entry():
    cs = {
        "contracts": [
            {"kind": "服务", "file": "/a.pdf", "text": "", "text_source": "", "text_confidence": ""},
            {"kind": "培训", "file": "/b.pdf", "text": "", "text_source": "", "text_confidence": ""},
        ]
    }
    merged = attach_contract_text(cs, {"text": "服务合同全文", "source": "pdf_text"}, "/a.pdf")
    assert merged["contracts"][0]["text"] == "服务合同全文"
    assert merged["contracts"][0]["text_source"] == "pdf_text"
    assert merged["contracts"][0]["text_confidence"] == "high"
    assert merged["contracts"][1]["text"] == ""


def test_attach_text_creates_entry_when_set_empty():
    merged = attach_contract_text({}, {"text": "全文", "source": "vision_text"}, "/c.jpg")
    assert len(merged["contracts"]) == 1
    entry = merged["contracts"][0]
    assert entry["file"] == "/c.jpg"
    assert entry["text"] == "全文"
    assert entry["text_source"] == "vision_text"
    assert entry["text_confidence"] == "medium"
    assert entry["kind"] == "" and entry["tier"] == ""


def test_attach_text_unique_entry_fallback():
    """旧分析结果条目 file 未填（normalize 兜底失败场景）→ 唯一条目兜底落正文。"""
    cs = {"contracts": [{"contract_id": "x", "title": "t", "rules": []}]}
    merged = attach_contract_text(cs, {"text": "全文", "source": "local_ocr"}, "/d.pdf")
    assert merged["contracts"][0]["text"] == "全文"
    assert merged["contracts"][0]["text_confidence"] == "low"


def test_ai_data_attach_keeps_rules_and_text():
    """AI 分析结果 attach 正文并 normalize 后，规则与正文并存（读取方护栏同上：normalize 幂等）。"""
    raw = contract_set_from_ai_data(
        {"total_fee": 5000, "deduction_items": [{"item": "服务费", "amount": 600}]}, "/fake.pdf"
    )
    merged = attach_contract_text(raw, {"text": "全文", "source": "pdf_text"}, "/fake.pdf")
    norm = normalize_contract_set(merged)
    assert normalize_contract_set(norm) == norm
    entry = norm["contracts"][0]
    assert entry["text"] == "全文"
    assert entry["rules"]  # 规则保留


# ── 服务层：merge_analysis_into_contract_set（工单集合聚合） ──────────

def test_merge_updates_matching_entry_and_keeps_others():
    existing = {
        "contracts": [
            {"kind": "服务", "file": "/a.pdf", "text": "", "text_source": "", "sha256": "aaa"},
            {"kind": "培训", "file": "/b.pdf", "text": "", "text_source": "", "sha256": "bbb"},
        ]
    }
    analysis = contract_set_from_ai_data({"total_fee": 4000}, "/a.pdf")
    merged = merge_analysis_into_contract_set(existing, analysis, "/a.pdf")
    assert len(merged["contracts"]) == 2
    first = merged["contracts"][0]
    assert first["rules"] == analysis["contracts"][0]["rules"]
    assert first["total_fee"] == 4000
    assert first["sha256"] == "aaa"  # 文件登记字段不丢
    assert merged["contracts"][1]["file"] == "/b.pdf"  # 另一份不受影响


def test_merge_appends_when_no_match():
    existing = {"contracts": [{"kind": "服务", "file": "/a.pdf"}]}
    analysis = contract_set_from_ai_data({"total_fee": 4000}, "/z.pdf")
    merged = merge_analysis_into_contract_set(existing, analysis, "/z.pdf")
    assert len(merged["contracts"]) == 2
    assert merged["contracts"][1]["file"] == "/z.pdf"


# ── 服务层：analyze_contract_from_file 正文落库（真实模板 PDF） ───────

def test_analysis_result_carries_text(monkeypatch):
    import services.contract_service as svc

    def fake_analyze(*args, **kwargs):
        return {
            "summary": "测试摘要",
            "raw_analysis": "{}",
            "deductions": [],
            "total_deduction": 0,
            "refund": 0,
            "actual_paid": 0,
            "total_fee": 0,
        }

    monkeypatch.setattr(svc, "analyze_contract", fake_analyze)
    result = analyze_contract_from_file(filepath=str(TEMPLATE_PDF))
    # 2019 服务合同空白手填字段多，可能走「识别不完整」路径（error=提示而非异常），
    # 但无论走哪条路径，正文都必须随结果落库（ADR-0002 硬前提）。
    cs = normalize_contract_set(result.get("contract_set"))
    assert cs["contracts"], "分析结果必须带 contract_set"
    assert cs["contracts"][0]["text"], "正文必须随结果落库（ADR-0002 硬前提）"
    assert cs["contracts"][0]["text_source"] == "pdf_text"
    assert cs["contracts"][0]["text_confidence"] == "high"


# ── HTTP 层：上传 → 分条读回 ─────────────────────────────────────────

def _upload(c, tid, files):
    data = {
        "ticket_id": tid,
        "id_card": "110101199003070011",
        "name": "王小明",
        "school_short": "沙田",
        "file": [(io.BytesIO(content), Path(filename).name) for filename, content in files],
    }
    resp = c.post("/api/contract/upload", data=data, content_type="multipart/form-data")
    return resp


def test_upload_two_pdfs_creates_two_entries(client, fresh_db, tmp_path):
    tid = _make_ticket()
    pdf_a = TEMPLATE_PDF.read_bytes()
    pdf_b = pdf_a + b"\n%%distinct-tail"
    resp = _upload(c := client, tid, [("a.pdf", pdf_a), ("b.pdf", pdf_b)])
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["success"], body
    contracts = body["data"]["contract_set"]["contracts"]
    assert len(contracts) == 2, contracts
    uploaded = {entry["file"] for entry in contracts}
    assert len(uploaded) == 2
    assert all(Path(p).name.endswith(".pdf") for p in uploaded)
    # 读回工单一致
    ticket = database.get_ticket(tid)
    assert len(normalize_contract_set(ticket["contract_set"])["contracts"]) == 2


def test_upload_pdf_plus_images_creates_two_entries(client, fresh_db, tmp_path):
    tid = _make_ticket()
    pdf_a = TEMPLATE_PDF.read_bytes()
    resp = _upload(c := client, tid, [("c.pdf", pdf_a), ("p1.png", _PNG_A), ("p2.png", _PNG_B)])
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"], body
    contracts = body["data"]["contract_set"]["contracts"]
    assert len(contracts) == 2, contracts
    # 图片组合并 PDF 后作为一个条目，file 指向合并版
    merged_pdf = body["data"]["manifest"]["merged_pdf_path"]
    assert merged_pdf
    files = {entry["file"] for entry in contracts}
    assert merged_pdf in files
    # 单份 PDF 是另一个条目
    pdf_entries = [e for e in contracts if e["file"].endswith(".pdf") and e["file"] != merged_pdf]
    assert len(pdf_entries) == 1


def test_reupload_same_content_no_duplicate_entry(client, fresh_db, tmp_path):
    tid = _make_ticket()
    pdf_a = TEMPLATE_PDF.read_bytes()
    resp1 = _upload(client, tid, [("a.pdf", pdf_a)])
    assert resp1.status_code == 200 and resp1.get_json()["success"]
    resp2 = _upload(client, tid, [("a2.pdf", pdf_a)])  # 同内容不同名
    assert resp2.status_code == 200
    ticket = database.get_ticket(tid)
    contracts = normalize_contract_set(ticket["contract_set"])["contracts"]
    assert len(contracts) == 1, contracts


def test_legacy_ticket_contract_set_reads_as_single_entry(client, fresh_db):
    tid = _make_ticket(contract_set=LEGACY_SET)
    ticket = database.get_ticket(tid)
    norm = normalize_contract_set(ticket["contract_set"])
    assert len(norm["contracts"]) == 1
    assert norm["contracts"][0]["contract_id"] == "extracted-contract"
    assert normalize_contract_set(norm) == norm  # 读取方护栏：normalize 幂等


def test_archive_paths_reader_unaffected_by_new_structure(client, fresh_db):
    """归档读取方（_ticket_contract_paths / manifest）对新分条结构不报错。"""
    tid = _make_ticket(contract_set=LEGACY_SET)
    pdf_a = TEMPLATE_PDF.read_bytes()
    resp = _upload(client, tid, [("a.pdf", pdf_a)])
    assert resp.status_code == 200
    ticket = database.get_ticket(tid)
    paths = app_module._ticket_contract_paths(ticket)
    assert paths  # 归档清单读取不报错且能取到合同路径
    # 新旧结构混存的工单：归档读取方稳定 + normalize 幂等（ADR-0003 护栏对象）
    assert app_module._ticket_contract_paths(ticket) == paths
    norm = normalize_contract_set(ticket["contract_set"])
    assert normalize_contract_set(norm) == norm
    assert norm["contracts"]  # 旧条目 + 新上传条目都在
