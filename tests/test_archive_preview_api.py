"""归档文件内联预览 API 测试（ISS-AP-03）+ 错误分级 code 断言（ISS-AP-01）。

覆盖 GET /api/tickets/<id>/archive-files/preview?name=：
1. 白名单内类型内联返回：PDF / PNG / TXT（Content-Type 正确 + Content-Disposition: inline）
2. 白名单外类型 415：docx / 无扩展名 / 未知扩展名 —— 不得被 mimetypes 猜测兜底放行
3. 穿越与非法文件名 400（不得读到案件夹之外）
4. 文件不存在 404；工单不存在 404
5. 夹子缺失 → 404 + code=dir_missing；根不可用 → 400 + code=root_unavailable
"""
import os
import tempfile
from pathlib import Path

import pytest
from conftest import _autologin_admin  # noqa: F401

_TMP_DIR = Path(tempfile.mkdtemp(prefix="archive-preview-tests-"))

import database  # noqa: E402

database.DB_PATH = _TMP_DIR / "test.db"
database._local.clear()

import app as app_module  # noqa: E402
import services.archive_service as archive_service_module  # noqa: E402


@pytest.fixture()
def fresh_db():
    database._local.clear()
    database.init_db()
    yield database
    database._local.clear()


@pytest.fixture()
def client(fresh_db):
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        try: _autologin_admin(c)
        except Exception: pass
        yield c


def _make_ticket(**overrides):
    data = {
        "student_name": "钱七",
        "id_card": "110101199003070011",
        "complaint_date": "2026-09-02",
        "complaint_type": "A",
        "school_short": "南城",
        "organization_unit_type": "分校",
        "organization_unit_name": "南城分校",
        "registration_date": "2026-01-01",
        "license_type": "C1",
        "exam_stage": "科目二",
        "total_fee": 3700,
        "actual_paid": 3700,
        "fee_plan_status": "confirmed",
    }
    data.update(overrides)
    tid = database.save_ticket(data)
    return database.get_ticket(tid)


def _patch_root(monkeypatch, root):
    """把归档根指向指定路径（resolve_case_dir → build_archive_dir 走服务层 load_config）。"""
    monkeypatch.setattr(archive_service_module, "load_config",
                        lambda: {"archive_root": str(root)})


def _make_case_dir(tmp_path, ticket):
    case_dir = Path(app_module.build_archive_dir(ticket, root=str(tmp_path))[0])
    case_dir.mkdir(parents=True, exist_ok=True)
    return case_dir


# ───────────────────────────────────────────────────────────
# 1) 白名单内类型 → 200 + inline
# ───────────────────────────────────────────────────────────
def test_preview_pdf_inline_ok(client, tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    case_dir = _make_case_dir(tmp_path, ticket)
    payload = b"%PDF-1.4\nfake pdf bytes"
    (case_dir / "张三_合同_2021级.pdf").write_bytes(payload)

    resp = client.get(f"/api/tickets/{ticket['id']}/archive-files/preview",
                      query_string={"name": "张三_合同_2021级.pdf"})
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert "inline" in resp.headers.get("Content-Disposition", "")
    assert "attachment" not in resp.headers.get("Content-Disposition", "")
    assert resp.data == payload


def test_preview_png_inline_ok(client, tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    case_dir = _make_case_dir(tmp_path, ticket)
    (case_dir / "证据页1.png").write_bytes(b"\x89PNG\r\n\x1a\n fake")

    resp = client.get(f"/api/tickets/{ticket['id']}/archive-files/preview",
                      query_string={"name": "证据页1.png"})
    assert resp.status_code == 200
    assert resp.mimetype == "image/png"
    assert "inline" in resp.headers.get("Content-Disposition", "")


def test_preview_txt_inline_ok(client, tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    case_dir = _make_case_dir(tmp_path, ticket)
    (case_dir / "撤诉说明.txt").write_text("撤诉原因：已协商解决", encoding="utf-8")

    resp = client.get(f"/api/tickets/{ticket['id']}/archive-files/preview",
                      query_string={"name": "撤诉说明.txt"})
    assert resp.status_code == 200
    assert resp.mimetype == "text/plain"
    # charset 由 Flask 自动追加且**只出现一次**（曾在 mimetype 里重复书写导致
    # `text/plain; charset=utf-8; charset=utf-8`，此断言即为防回归）
    ctype = resp.headers["Content-Type"]
    assert ctype == "text/plain; charset=utf-8"
    assert ctype.count("charset") == 1
    assert resp.data.decode("utf-8") == "撤诉原因：已协商解决"


# ───────────────────────────────────────────────────────────
# 2) 白名单外类型 → 415（不得被 mimetypes 猜测兜底放行）
# ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad_name", [
    "投诉登记表.docx",     # 本期明确不支持 docx
    "投诉回复函.doc",
    "合同.zip",
    "扫描件",              # 无扩展名
    "archive.unknownext",  # 未知扩展名
])
def test_preview_non_whitelisted_415(client, tmp_path, monkeypatch, bad_name):
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    case_dir = _make_case_dir(tmp_path, ticket)
    (case_dir / bad_name).write_bytes(b"payload")

    resp = client.get(f"/api/tickets/{ticket['id']}/archive-files/preview",
                      query_string={"name": bad_name})
    assert resp.status_code == 415, f"{bad_name!r} 应被 415 拒绝"
    assert "不支持在线预览" in resp.get_json()["error"]


def test_preview_docx_415_even_when_dir_missing(client, tmp_path, monkeypatch):
    """校验顺序固化：扩展名白名单先于夹子探测 → 不可预览类型恒 415。"""
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()  # 故意不建夹子
    resp = client.get(f"/api/tickets/{ticket['id']}/archive-files/preview",
                      query_string={"name": "投诉登记表.docx"})
    assert resp.status_code == 415


# ───────────────────────────────────────────────────────────
# 3) 穿越 / 非法文件名 → 400
# ───────────────────────────────────────────────────────────
def test_preview_traversal_rejected(client, tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    _make_case_dir(tmp_path, ticket)
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"%PDF-secret")

    for bad in ["../secret.pdf", "..\\secret.pdf", "a/../../secret.pdf",
                "/etc/passwd", "", "   ", "."]:
        resp = client.get(f"/api/tickets/{ticket['id']}/archive-files/preview",
                          query_string={"name": bad})
        assert resp.status_code in (400, 415), f"name={bad!r} 应被拒绝，实得 {resp.status_code}"
    assert secret.read_bytes() == b"%PDF-secret"


def test_preview_percent_encoded_slash_blocked(client, tmp_path, monkeypatch):
    """..%2F 变体：werkzeug 不解码 query 里的 %2F → 按字面文件名处理 → 400/404，绝不穿越。"""
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    _make_case_dir(tmp_path, ticket)
    (tmp_path / "secret.txt").write_text("top", encoding="utf-8")

    resp = client.get(f"/api/tickets/{ticket['id']}/archive-files/preview",
                      query_string={"name": "..%2Fsecret.txt"})
    assert resp.status_code in (400, 404, 415)
    assert (tmp_path / "secret.txt").read_text(encoding="utf-8") == "top"


def test_preview_missing_file_404(client, tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    _make_case_dir(tmp_path, ticket)
    resp = client.get(f"/api/tickets/{ticket['id']}/archive-files/preview",
                      query_string={"name": "不存在.pdf"})
    assert resp.status_code == 404


def test_preview_absolute_path_400(client, tmp_path, monkeypatch):
    """AC#7 字面判据：绝对路径即便扩展名在白名单内也必须 400，不得被 basename 化放行。"""
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    _make_case_dir(tmp_path, ticket)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-outside")

    for bad in [str(outside), "/etc/passwd.pdf", f"{tmp_path}/x.pdf"]:
        resp = client.get(f"/api/tickets/{ticket['id']}/archive-files/preview",
                          query_string={"name": bad})
        assert resp.status_code == 400, f"name={bad!r} 应 400，实得 {resp.status_code}"


def test_preview_symlink_escape_400(client, tmp_path, monkeypatch):
    """夹内软链指向夹外 → realpath 越界拦截，绝不外泄文件内容。"""
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    case_dir = _make_case_dir(tmp_path, ticket)
    outside = tmp_path / "outside.txt"
    outside.write_text("top-secret", encoding="utf-8")
    link = case_dir / "内联.txt"
    try:
        os.symlink(str(outside), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不支持创建符号链接")

    resp = client.get(f"/api/tickets/{ticket['id']}/archive-files/preview",
                      query_string={"name": "内联.txt"})
    assert resp.status_code == 400
    assert b"top-secret" not in resp.data


def test_preview_ticket_404(client):
    resp = client.get("/api/tickets/no-such-id/archive-files/preview",
                      query_string={"name": "x.pdf"})
    assert resp.status_code == 404


# ───────────────────────────────────────────────────────────
# 4) ISS-AP-01 错误分级：code 透传到 HTTP 层
# ───────────────────────────────────────────────────────────
def test_preview_dir_missing_returns_code(client, tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()  # 不建夹子
    resp = client.get(f"/api/tickets/{ticket['id']}/archive-files/preview",
                      query_string={"name": "x.pdf"})
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["success"] is False
    assert body["code"] == "dir_missing"
    assert "登记表" in body["errors"][0]
    assert body.get("detail")


def test_preview_root_unavailable_returns_code(client, tmp_path, monkeypatch):
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("x", encoding="utf-8")
    _patch_root(monkeypatch, blocker)  # 归档根指向一个文件 → 不可用
    ticket = _make_ticket()

    resp = client.get(f"/api/tickets/{ticket['id']}/archive-files/preview",
                      query_string={"name": "x.pdf"})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["code"] == "root_unavailable"
    assert "归档根目录不可用" in body["errors"][0]
    # 技术细节不得混进展示用文案（errors[0]），只能出现在 detail 里
    assert "Permission denied" not in body["errors"][0]
