"""archive_service 单元测试：三闸门缺失报告、目录命名规则、复制成功、重复归档幂等"""
import os
from pathlib import Path

import pytest

import services.archive_service as archive_service
from services.archive_service import archive_case, archive_gate_errors, build_archive_dir


def _ticket(**overrides):
    base = {
        "complaint_date": "2026-08-22",
        "student_name": "张三",
        "id_card": "110101199003070011",
        "school_short": "南城",
        "organization_unit_type": "分校",
        "organization_unit_name": "南城分校",
        "handling_notes": "已协调网点退费",
        "branch_cooperation": "配合",
        "fee_plan_status": "confirmed",
    }
    base.update(overrides)
    return base


class TestArchiveGateErrors:
    def test_handling_notes_missing_reports(self):
        assert archive_gate_errors(_ticket(handling_notes="   ")) == ["处理情况未填写"]

    def test_branch_cooperation_missing_reports(self):
        assert archive_gate_errors(_ticket(branch_cooperation="")) == ["配合度未评定"]

    def test_fee_plan_not_confirmed_reports(self):
        assert archive_gate_errors(_ticket(fee_plan_status="draft")) == ["费用明细未确认"]

    def test_all_missing_reports_three(self):
        assert len(archive_gate_errors({})) == 3

    def test_all_pass_returns_empty(self):
        assert archive_gate_errors(_ticket()) == []


class TestBuildArchiveDir:
    def test_naming_rule_exact(self, tmp_path):
        root = str(tmp_path / "归档根")
        case_dir, register, reply = build_archive_dir(_ticket(), root=root)
        expected = tmp_path / "归档根" / "分校" / "南城-南城分校" / "2026-08-22_张三_110101199003070011_南城"
        assert case_dir == str(expected)
        assert register == str(expected / "投诉登记表.docx")
        assert reply == str(expected / "投诉回复函.docx")

    def test_unknown_org_falls_back_to_weigushu(self, tmp_path):
        case_dir, _, _ = build_archive_dir(
            _ticket(organization_unit_type="", organization_unit_name=""), root=str(tmp_path))
        parts = Path(case_dir).parts
        assert "未归属" in parts
        assert "南城-未归属" in parts


class TestArchiveCase:
    @staticmethod
    def _sources(tmp_path):
        reg = tmp_path / "src_投诉登记表.docx"
        rep = tmp_path / "src_投诉回复函.docx"
        reg.write_text("register-content", encoding="utf-8")
        rep.write_text("reply-content", encoding="utf-8")
        return str(reg), str(rep)

    @staticmethod
    def _patch_default_root(monkeypatch, tmp_path):
        monkeypatch.setattr(
            archive_service, "load_config",
            lambda: {"archive_root": str(tmp_path / "案件归档")},
        )

    def test_gate_failure_returns_errors(self, tmp_path):
        result = archive_case(
            _ticket(handling_notes=""),
            {"register_form": str(tmp_path / "不存在.docx"), "reply": None},
        )
        assert result == {"success": False, "errors": ["处理情况未填写"]}

    def test_no_existing_file_fails(self):
        result = archive_case(_ticket(), {"register_form": None, "reply": None})
        assert result["success"] is False
        assert result["errors"]

    def test_success_copies_both_files(self, tmp_path, monkeypatch):
        self._patch_default_root(monkeypatch, tmp_path)
        reg, rep = self._sources(tmp_path)
        result = archive_case(_ticket(), {"register_form": reg, "reply": rep})
        expected_dir = (
            tmp_path / "案件归档" / "分校" / "南城-南城分校"
            / "2026-08-22_张三_110101199003070011_南城"
        )
        assert result == {
            "success": True,
            "dir": str(expected_dir),
            "files": [
                str(expected_dir / "投诉登记表.docx"),
                str(expected_dir / "投诉回复函.docx"),
            ],
            "opened": False,
        }
        for dst in result["files"]:
            assert os.path.isfile(dst)
        register_dst = next(f for f in result["files"] if "登记表" in f)
        assert Path(register_dst).read_text(encoding="utf-8") == "register-content"

    def test_rearchive_idempotent_overwrites(self, tmp_path, monkeypatch):
        self._patch_default_root(monkeypatch, tmp_path)
        reg, rep = self._sources(tmp_path)
        files = {"register_form": reg, "reply": rep}
        first = archive_case(_ticket(), files)
        Path(reg).write_text("register-v2", encoding="utf-8")
        second = archive_case(_ticket(), files)
        assert first["success"] is True and second["success"] is True
        assert second["dir"] == first["dir"]
        assert sorted(second["files"]) == sorted(first["files"])
        dst = next(f for f in second["files"] if "登记表" in f)
        assert Path(dst).read_text(encoding="utf-8") == "register-v2"

    def test_open_folder_darwin(self, tmp_path, monkeypatch):
        self._patch_default_root(monkeypatch, tmp_path)
        reg, _ = self._sources(tmp_path)
        calls = []
        monkeypatch.setattr(archive_service.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
        result = archive_case(_ticket(), {"register_form": reg, "reply": None}, open_folder=True)
        assert result["opened"] is True
        assert calls == [["open", result["dir"]]]


class TestIsPlainFileName:
    """归档文件名合法性判据（download / preview 共用同一口径）。"""

    @pytest.mark.parametrize("name", [
        "投诉登记表.docx", "张三_合同_2021级.pdf", "a.txt", "证据 页1.PNG",
    ])
    def test_plain_names_accepted(self, name):
        assert archive_service.is_plain_file_name(name) is True

    @pytest.mark.parametrize("name", [
        "", "   ", ".", "..",
        "../secret.txt", "..\\secret.txt", "a/b.txt",
        "/etc/passwd", "/etc/passwd.pdf", "C:\\Windows\\x.txt",
        "./x.txt",
    ])
    def test_path_like_names_rejected(self, name):
        assert archive_service.is_plain_file_name(name) is False


class TestSafeMemberPath:
    """案件夹内安全定位：越界/穿越/软链绕路一律 None。"""

    def test_normal_member_resolved(self, tmp_path):
        case = tmp_path / "案件夹"
        case.mkdir()
        (case / "a.pdf").write_bytes(b"%PDF")
        got = archive_service.safe_member_path(str(case), "a.pdf")
        assert got == str(case / "a.pdf")

    @pytest.mark.parametrize("name", ["../x.pdf", "a/b.pdf", "/etc/passwd", "", ".."])
    def test_illegal_names_return_none(self, tmp_path, name):
        case = tmp_path / "案件夹"
        case.mkdir()
        assert archive_service.safe_member_path(str(case), name) is None

    def test_symlink_escape_returns_none(self, tmp_path):
        case = tmp_path / "案件夹"
        case.mkdir()
        outside = tmp_path / "outside.pdf"
        outside.write_bytes(b"%PDF-outside")
        try:
            os.symlink(str(outside), str(case / "link.pdf"))
        except (OSError, NotImplementedError):
            pytest.skip("当前环境不支持创建符号链接")
        assert archive_service.safe_member_path(str(case), "link.pdf") is None

