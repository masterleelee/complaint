r"""网页内置归档文件面板 API 测试（2026-09-01 零安装兜底）。

覆盖 GET /api/tickets/<id>/archive-files（列目录）与
GET /api/tickets/<id>/archive-files/download?name=（单文件下载）：
1. 列目录：200 + 文件清单（name/size/mtime）、空目录 200、夹子缺失 404、工单 404
2. 下载：200 内容一致、目录穿越 400、文件不存在 404、工单 404
"""
import os
import tempfile
from pathlib import Path

import pytest
from conftest import _autologin_admin  # noqa: F401

_TMP_DIR = Path(tempfile.mkdtemp(prefix="archive-files-tests-"))

import database  # noqa: E402

database.DB_PATH = _TMP_DIR / "test.db"
database._local.clear()

import app as app_module  # noqa: E402
import config as config_module  # noqa: E402
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
        "student_name": "赵六",
        "id_card": "110101199003070011",
        "complaint_date": "2026-09-01",
        "complaint_type": "A",
        "school_short": "南城",
        "organization_unit_type": "分校",
        "organization_unit_name": "南城分校",
        "registration_date": "2026-01-01",
        "license_type": "C2",
        "exam_stage": "科目二",
        "total_fee": 3700,
        "actual_paid": 3700,
        "fee_plan_status": "confirmed",
    }
    data.update(overrides)
    tid = database.save_ticket(data)
    return database.get_ticket(tid)


def _patch_root(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "load_config", lambda: {"archive_root": str(tmp_path)})
    monkeypatch.setattr(archive_service_module, "load_config", lambda: {"archive_root": str(tmp_path)})


# ───────────────────────────────────────────────────────────
# 1) 列目录
# ───────────────────────────────────────────────────────────
def test_list_files_ok(client, tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    case_dir, _, _ = app_module.build_archive_dir(ticket, root=str(tmp_path))
    case_dir = Path(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "投诉登记表.docx").write_bytes(b"%PK-docx")
    (case_dir / "投诉回复函.docx").write_bytes(b"%PK-docx2")

    resp = client.get(f"/api/tickets/{ticket['id']}/archive-files")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    names = {f["name"] for f in body["data"]["files"]}
    assert names == {"投诉登记表.docx", "投诉回复函.docx"}
    for f in body["data"]["files"]:
        assert f["size"] > 0 and "T" not in f["mtime"]
    assert body["data"]["dir"] == str(case_dir)


def test_list_files_subdir_ignored(client, tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    case_dir = Path(app_module.build_archive_dir(ticket, root=str(tmp_path))[0])
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "a.txt").write_text("x", encoding="utf-8")
    (case_dir / "sub").mkdir()
    (case_dir / "sub" / "b.txt").write_text("y", encoding="utf-8")

    resp = client.get(f"/api/tickets/{ticket['id']}/archive-files")
    names = [f["name"] for f in resp.get_json()["data"]["files"]]
    assert names == ["a.txt"]


def test_list_files_empty_dir_ok(client, tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    Path(app_module.build_archive_dir(ticket, root=str(tmp_path))[0]).mkdir(parents=True)
    resp = client.get(f"/api/tickets/{ticket['id']}/archive-files")
    body = resp.get_json()
    assert resp.status_code == 200 and body["data"]["files"] == []


def test_list_files_dir_missing_404(client, tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    resp = client.get(f"/api/tickets/{ticket['id']}/archive-files")
    assert resp.status_code == 404
    assert resp.get_json()["success"] is False


def test_list_files_ticket_404(client, tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    resp = client.get("/api/tickets/no-such-id/archive-files")
    assert resp.status_code == 404


# ───────────────────────────────────────────────────────────
# 2) 单文件下载
# ───────────────────────────────────────────────────────────
def test_download_ok(client, tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    case_dir = Path(app_module.build_archive_dir(ticket, root=str(tmp_path))[0])
    case_dir.mkdir(parents=True, exist_ok=True)
    payload = "登记表内容-中文测试".encode("utf-8")
    (case_dir / "投诉登记表.docx").write_bytes(payload)

    resp = client.get(
        f"/api/tickets/{ticket['id']}/archive-files/download?name=投诉登记表.docx")
    assert resp.status_code == 200
    assert resp.data == payload
    assert "attachment" in resp.headers.get("Content-Disposition", "")


def test_download_traversal_rejected(client, tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    # 写一个夹子，确保即使 resolve 成功也不能越出去
    Path(app_module.build_archive_dir(ticket, root=str(tmp_path))[0]).mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("top", encoding="utf-8")

    for bad in ["../secret.txt", "..\\secret.txt", "a/../../secret.txt"]:
        resp = client.get(
            f"/api/tickets/{ticket['id']}/archive-files/download",
            query_string={"name": bad})
        assert resp.status_code == 400, f"name={bad!r} 应被拒绝"
    # werkzeug 不解码 query 里的 %2F → 按字面文件名处理 → 404（同样不可能穿越）
    resp = client.get(
        f"/api/tickets/{ticket['id']}/archive-files/download",
        query_string={"name": "..%2Fsecret.txt"})
    assert resp.status_code in (400, 404), "..%2F 变体应被阻断"
    assert secret.read_text(encoding="utf-8") == "top"


def test_download_missing_file_404(client, tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    Path(app_module.build_archive_dir(ticket, root=str(tmp_path))[0]).mkdir(parents=True)
    resp = client.get(
        f"/api/tickets/{ticket['id']}/archive-files/download?name=不存在.txt")
    assert resp.status_code == 404


def test_download_ticket_404(client, tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    resp = client.get(
        "/api/tickets/no-such-id/archive-files/download?name=x.txt")
    assert resp.status_code == 404


# ───────────────────────────────────────────────────────────
# 3) ISS-AP-01 错误分级：list / download 共用同一套 code 语义
#    root_unavailable=400（找管理员） / dir_missing=404（自己去生成文档）
# ───────────────────────────────────────────────────────────
def test_list_files_dir_missing_returns_code(client, tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()  # 故意不建案件夹
    resp = client.get(f"/api/tickets/{ticket['id']}/archive-files")
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["success"] is False
    assert body["code"] == "dir_missing"
    assert "登记表" in body["errors"][0]
    assert body.get("detail")


def test_list_files_root_unavailable_returns_code(client, tmp_path, monkeypatch):
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("x", encoding="utf-8")
    _patch_root(monkeypatch, blocker)  # 归档根指向一个文件 → 不可用
    ticket = _make_ticket()
    resp = client.get(f"/api/tickets/{ticket['id']}/archive-files")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["code"] == "root_unavailable"
    assert "归档根目录不可用" in body["errors"][0]
    # 展示文案不得包含程序员语言（技术细节只在 detail 里）
    assert "Permission denied" not in body["errors"][0]
    assert "无法创建归档根目录" not in body["errors"][0]


def test_download_dir_missing_returns_code(client, tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    resp = client.get(
        f"/api/tickets/{ticket['id']}/archive-files/download?name=x.txt")
    assert resp.status_code == 404
    assert resp.get_json()["code"] == "dir_missing"


def test_download_root_unavailable_returns_code(client, tmp_path, monkeypatch):
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("x", encoding="utf-8")
    _patch_root(monkeypatch, blocker)
    ticket = _make_ticket()
    resp = client.get(
        f"/api/tickets/{ticket['id']}/archive-files/download?name=x.txt")
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "root_unavailable"


def test_download_absolute_path_400(client, tmp_path, monkeypatch):
    """绝对路径必须 400，不得被 basename 化后在案件夹内找同名文件放行。"""
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    case_dir = Path(app_module.build_archive_dir(ticket, root=str(tmp_path))[0])
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "x.txt").write_text("in-case", encoding="utf-8")

    resp = client.get(
        f"/api/tickets/{ticket['id']}/archive-files/download",
        query_string={"name": str(case_dir / "x.txt")})
    assert resp.status_code == 400


def test_download_symlink_escape_400(client, tmp_path, monkeypatch):
    """夹内软链指向夹外 → realpath 越界拦截（补上 download 原先缺失的这道闸）。"""
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    case_dir = Path(app_module.build_archive_dir(ticket, root=str(tmp_path))[0])
    case_dir.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("top-secret", encoding="utf-8")
    try:
        os.symlink(str(outside), str(case_dir / "内联.txt"))
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不支持创建符号链接")

    resp = client.get(
        f"/api/tickets/{ticket['id']}/archive-files/download?name=内联.txt")
    assert resp.status_code == 400
    assert b"top-secret" not in resp.data


# ───────────────────────────────────────────────────────────
# 4) ISS-AP-09 追加键 unc：Windows UNC 路径（「复制路径」的主载荷）
#    dir 是服务器本机的挂载路径（/Volumes/File/...），局域网 Windows 同事拿到打不开；
#    unc（\\192.0.2.199\File\...）才是可分享的。server 必须是 IP——
#    挂载点给出的主机名（kj-server）在部分 Windows 客户端上解析不了。
# ───────────────────────────────────────────────────────────
def test_list_files_returns_unc_when_mapping_configured(client, tmp_path, monkeypatch):
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    case_dir = Path(app_module.build_archive_dir(ticket, root=str(tmp_path))[0])
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "投诉登记表.docx").write_bytes(b"%PK")

    mp = os.path.realpath(str(tmp_path))
    monkeypatch.setattr(archive_service_module, "get_smb_mappings",
                        lambda: [{"server": "192.0.2.199", "share": "File",
                                  "mount_point": mp}])
    resp = client.get(f"/api/tickets/{ticket['id']}/archive-files")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    # dir 保持不变（本机路径仍有用：服务器端排查）
    assert data["dir"] == str(case_dir)
    real_dir = os.path.realpath(str(case_dir))
    assert real_dir.startswith(mp + os.sep)
    expected = "\\\\192.0.2.199\\File\\" + real_dir[len(mp) + 1:].replace("/", "\\")
    assert data["unc"] == expected


def test_list_files_unc_empty_when_no_mapping(client, tmp_path, monkeypatch):
    """无 SMB 映射时 unc 为空串（不能编造），前端回退用 dir。"""
    _patch_root(monkeypatch, tmp_path)
    ticket = _make_ticket()
    Path(app_module.build_archive_dir(ticket, root=str(tmp_path))[0]).mkdir(parents=True)
    monkeypatch.setattr(archive_service_module, "get_smb_mappings", lambda: [])
    resp = client.get(f"/api/tickets/{ticket['id']}/archive-files")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["unc"] == ""
    assert data["dir"]
