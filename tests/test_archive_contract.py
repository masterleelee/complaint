"""归档路径统一 + 合同入位回归测试。

覆盖：
1. contract_target_name 命名规范：{姓名}_合同_{原名}{扩展名}
2. 已合规命名的合同保持原名（防重复拼接前缀）
3. archive_contract：案件夹外源复制入位 / 案件夹内旧命名原地改名 / 去重 / 忽略不存在文件
4. archive_case + archive_contract 端到端：登记表+回复函+合同三件套齐备
5. 相对路径 archive_root 锚定项目根（不随 CWD 漂移）
"""
import os
import tempfile
from pathlib import Path

import pytest

from services import archive_service
from services.archive_service import (
    archive_case,
    archive_contract,
    build_archive_dir,
    contract_target_name,
)

_TMP = Path(tempfile.mkdtemp(prefix="archive-contract-tests-"))


@pytest.fixture()
def custom_root(tmp_path, monkeypatch):
    """把归档根指向临时目录，绕开真实案件归档。"""
    root = tmp_path / "归档根"
    monkeypatch.setattr(archive_service, "load_config", lambda: {"archive_root": str(root)})
    return root


def _ticket(**over):
    t = {
        "student_name": "陈志明",
        "id_card": "110101199003070011",
        "school_short": "青C",
        "organization_unit_type": "分校",
        "organization_unit_name": "塘厦林村分校",
        "complaint_date": "2026-08-27",
    }
    t.update(over)
    return t


# ── 1) 命名规范 ──
def test_contract_target_name_basic():
    # 非法字符（+ 等）按 _safe_segment 规则清洗为下划线
    assert contract_target_name("陈志明", "20260828+陈志明+441900+合同.pdf") == \
        "陈志明_合同_20260828_陈志明_441900_合同.pdf"


def test_contract_target_name_keeps_standard_name():
    assert contract_target_name("陈志明", "陈志明_合同_扫描件.pdf") == "陈志明_合同_扫描件.pdf"


def test_contract_target_name_sanitizes_illegal_chars():
    name = contract_target_name("陈志明", 'a*b<c>?.pdf')
    assert "/" not in name and "*" not in name and "?" not in name
    assert name.startswith("陈志明_合同_") and name.endswith(".pdf")


def test_contract_target_name_fallback_when_no_name():
    assert contract_target_name("", "原件.pdf") == "未命名学员_合同_原件.pdf"


# ── 2) archive_contract 行为 ──
def test_archive_contract_copies_external_source(custom_root):
    src_dir = _TMP / "src1"
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "20260828+陈志明+441900+合同.pdf"
    src.write_bytes(b"%PDF-1.4 fake")

    ticket = _ticket()
    case_dir, _, _ = build_archive_dir(ticket)
    res = archive_contract(ticket, [str(src)], case_dir=case_dir)

    assert res["errors"] == []
    assert len(res["copied"]) == 1
    target = Path(res["copied"][0])
    assert target.parent == Path(case_dir)
    assert target.name == "陈志明_合同_20260828_陈志明_441900_合同.pdf"
    assert target.read_bytes() == b"%PDF-1.4 fake"
    assert res["mapping"][str(src)] == str(target)


def test_archive_contract_renames_inplace_for_legacy_naming(custom_root):
    """案件夹内旧命名文件（如爬虫旧格式）→ 原地改名，不产生副本。"""
    ticket = _ticket()
    case_dir, _, _ = build_archive_dir(ticket)
    os.makedirs(case_dir, exist_ok=True)
    legacy = Path(case_dir) / "20260101+陈志明+441900+合同.pdf"
    legacy.write_bytes(b"%PDF-old")

    res = archive_contract(ticket, [str(legacy)], case_dir=case_dir)
    assert res["errors"] == []
    assert not legacy.exists(), "旧命名文件应被改名移除"
    assert Path(res["copied"][0]).name == "陈志明_合同_20260101_陈志明_441900_合同.pdf"


def test_archive_contract_dedup_and_ignore_missing(custom_root):
    src = _TMP / "src2"
    src.mkdir(parents=True, exist_ok=True)
    f = src / "陈志明_合同_a.pdf"
    f.write_bytes(b"%PDF-a")

    res = archive_contract(_ticket(), [str(f), str(f), str(src / "不存在.pdf")], case_dir=None)
    assert len(res["copied"]) == 1
    assert res["errors"] == []


def test_archive_contract_empty_sources(custom_root):
    assert archive_contract(_ticket(), []) == {"copied": [], "errors": [], "mapping": {}}


# ── 3) 三件套端到端 ──
def test_full_archive_two_pieces_plus_contract(custom_root, tmp_path):
    """archive_case（登记表+回复函）+ archive_contract（合同）齐备归档。"""
    reg = tmp_path / "投诉登记表.docx"; reg.write_bytes(b"reg")
    reply = tmp_path / "投诉回复函.docx"; reply.write_bytes(b"reply")
    contract = tmp_path / "old-dir"; contract.mkdir()
    cfile = contract / "20260828+陈志明+441900+合同.pdf"; cfile.write_bytes(b"%PDF")

    ticket = _ticket(handling_notes="已处理", branch_cooperation="配合", fee_plan_status="confirmed")
    files = {"register_form": str(reg), "reply": str(reply)}
    result = archive_case(ticket, files)
    assert result["success"] is True

    cres = archive_contract(ticket, [str(cfile)], case_dir=result["dir"])
    assert cres["errors"] == []
    case_dir = Path(result["dir"])
    names = {p.name for p in case_dir.iterdir()}
    assert names == {"投诉登记表.docx", "投诉回复函.docx", "陈志明_合同_20260828_陈志明_441900_合同.pdf"}


# ── 4) 相对路径锚定项目根 ──
def test_relative_archive_root_anchored_to_project_dir():
    """历史配置中的相对路径「案件归档」必须锚定项目根，不随 CWD 漂移。"""
    case_dir, _, _ = build_archive_dir(_ticket(), root="案件归档")
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(archive_service.__file__)))
    assert case_dir.startswith(os.path.join(project_root, "案件归档") + os.sep)
