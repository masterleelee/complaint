"""合同原文对照弹窗 · 上传件走「档位模板条款」（v4.4 · S4 后端）。

覆盖两处（契约 `.scratch/contract-preview-v44-landing/s4-contract.md` §1.1 / §1.2）：

1. `services.contract_template_text.template_clauses`：把档位模板正文切成条款块
   `[{no, title, body}]`，与 `contract_service.build_contract_clauses` 同切法，
   但**不产出**前导（preamble）块；未知档位安静返回 `[]`。
2. `app._build_contract_comparison`：上传件改走 `source="template"`（读档位模板正文
   + 退费表结构化行），**不再**用 pdfplumber 去读纸质照片，也不污染 `contract_text`
   PDF 条款缓存；电子合同链路行为不变（`source="pdf"`）。

各档位数字均**逐档列举**（非「长度>0」），切片丢条会在 `test_clause_count_matches_asset`
上失败。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from services.contract_template_text import (
    refund_rows,
    template_clauses,
    template_text,
)

ASSET_DIR = Path(__file__).resolve().parent.parent / "data" / "contract_template_text"

# 权威档位清单（与 scripts/extract_contract_templates.py 的 TIER_SOURCES 一致）
TIER_IDS = [
    "2019_service",
    "2019_pay_agent",
    "2019_training",
    "2021_2022",
    "2023_branch_school",
    "2023_branch_store",
    "2019_dongcheng",
]

# 独立于实现的「标题行」口径：行首（容忍缩进/全角空格）的「第X条」。
_TITLE_LINE_RE = re.compile(
    r"(?m)^[ \t\u3000]*第[一二三四五六七八九十百零〇]+[ \t\u3000]*条"
)


def _forbid(name: str):
    """造一个「被调用即断言失败」的替身，用于证明某条路径未被走到。"""

    def _boom(*args, **kwargs):
        raise AssertionError(f"上传件模板分支不得调用 {name}")

    return _boom


def _upload_ticket(tier_key: str = "tier_id", tier_id: str = "2023_branch_school") -> dict:
    """造一个「上传件形态」的 ticket：contract_set 每份带 sha256 标记。"""
    return {
        "id": "T-UPLOAD",
        "contract_set": {
            "contracts": [
                {"file": "/tmp/photo-1.jpg", "sha256": "a" * 64, "text": "（OCR 原文）"},
            ]
        },
        tier_key: tier_id,
        "deductions_result": {"tier_result": {"display_name": "2023·分校"}},
        # 纸质照片路径：模板分支绝不该去读它
        "contract_path": "/tmp/photo-1.jpg",
        "contract_code": "HT-001",
    }


# ── template_clauses() ────────────────────────────────────────────────

def test_template_clauses_school_four_and_eight():
    clauses = template_clauses("2023_branch_school")
    by_no = {c["no"]: c for c in clauses}

    assert by_no["四"]["title"] == "费用及支付"
    assert by_no["八"]["title"] == "退学退费"

    # 条款号是纯中文数字（不带「第」「条」），且**无** preamble 块（no 为空）
    assert set(by_no) <= {"一", "二", "三", "四", "五", "六", "七", "八", "九",
                          "十", "十一", "十二", "十三", "十四"}
    assert all(c["no"] for c in clauses), "不得产出 no 为空的前导块"
    # body 非空（切出的是正文，不是空壳）
    assert by_no["八"]["body"]


def test_template_clauses_dongcheng_fee_four_refund_six():
    """东城自制的退费在第六条（不是第八条）——照抄 plan §4.1。"""
    clauses = template_clauses("2019_dongcheng")
    by_no = {c["no"]: c for c in clauses}

    assert by_no["四"]["title"].startswith("费用及支付")
    assert by_no["六"]["title"] == "退学退费"
    # 第八条必须是别的东西（甲方的权利和义务）——防呆：退费不在第八条
    assert by_no["八"]["title"] != "退学退费"


def test_template_clauses_unknown_tier_returns_empty():
    assert template_clauses("nope") == []
    assert template_clauses(None) == []
    assert template_clauses("") == []
    assert template_clauses(123) == []


@pytest.mark.parametrize("tier_id", TIER_IDS)
def test_clause_count_matches_asset(tier_id):
    """7 档逐档校验：切片条数 == 正文里「第X条」标题行数（防切片丢条）。"""
    raw = (ASSET_DIR / f"{tier_id}.txt").read_text(encoding="utf-8")
    expected = len(_TITLE_LINE_RE.findall(raw))
    assert expected >= 10, f"{tier_id} 资产异常：仅 {expected} 条标题行"
    assert len(template_clauses(tier_id)) == expected


# ── refund_rows()：仅 3 档非空 ────────────────────────────────────────

@pytest.mark.parametrize(
    "tier_id,expected_rows",
    [
        ("2021_2022", 4),
        ("2023_branch_school", 14),
        ("2023_branch_store", 15),
    ],
)
def test_refund_rows_three_tiers_have_structured_rows(tier_id, expected_rows):
    assert len(refund_rows(tier_id)) == expected_rows


@pytest.mark.parametrize(
    "tier_id",
    ["2019_service", "2019_pay_agent", "2019_training", "2019_dongcheng"],
)
def test_refund_rows_other_tiers_empty(tier_id):
    assert refund_rows(tier_id) == []


# ── _build_contract_comparison() 分支 ─────────────────────────────────

def test_upload_ticket_uses_template_source(monkeypatch):
    import app

    monkeypatch.setattr(app, "build_contract_clauses", _forbid("build_contract_clauses"))
    monkeypatch.setattr(app, "update_ticket", _forbid("update_ticket（污染 contract_text 缓存）"))

    out = app._build_contract_comparison(_upload_ticket(), "T-UPLOAD")

    assert out["source"] == "template"
    assert out["tier_id"] == "2023_branch_school"
    assert out["tier_display_name"] == "2023·分校"
    assert out["clauses"], "上传件必须切出档位模板条款"
    assert len(out["clauses"]) == 14
    assert out["text_available"] is True
    assert len(out["refund_rows"]) == 14
    # 其余既有字段一个都不许少
    for key in ("ticket_id", "contract_code", "platform_available", "platform",
                "items", "platform_extra", "deductions", "clauses",
                "text_available", "text_error", "profile"):
        assert key in out, f"既有字段被删：{key}"


def test_upload_ticket_reads_contract_tier_id_column(monkeypatch):
    """生产真实形态：工单表档位列是 contract_tier_id（无内存态 tier_id）。"""
    import app

    monkeypatch.setattr(app, "build_contract_clauses", _forbid("build_contract_clauses"))
    monkeypatch.setattr(app, "update_ticket", _forbid("update_ticket"))

    ticket = _upload_ticket(tier_key="contract_tier_id")
    ticket.pop("deductions_result")
    ticket["contract_tier_display"] = "2023·分校"

    out = app._build_contract_comparison(ticket, "T-UPLOAD")

    assert out["source"] == "template"
    assert out["tier_id"] == "2023_branch_school"
    assert out["tier_display_name"] == "2023·分校"


def test_electronic_ticket_keeps_pdf_source(monkeypatch):
    """非上传件（电子合同）行为不变：source=="pdf"，不读模板。"""
    import app

    monkeypatch.setattr(app, "update_ticket", _forbid("update_ticket（已有缓存时不该写库）"))

    ticket = {
        "id": "T-PDF",
        "contract_text": json.dumps(
            {"clauses": [{"no": "一", "title": "订立合同的条件", "body": "…"}], "error": ""},
            ensure_ascii=False,
        ),
    }
    out = app._build_contract_comparison(ticket, "T-PDF")

    assert out["source"] == "pdf"
    assert out["tier_id"] == ""
    assert out["tier_display_name"] == ""
    assert out["refund_rows"] == []
    assert out["text_available"] is True
