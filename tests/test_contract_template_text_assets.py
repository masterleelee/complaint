"""合同模板正文资产测试（合同原文对照弹窗 v4.4 · S0）。

覆盖：
- 7 个档位资产文件都存在且非空（逐档列举，非阈值）
- 每档 template_text() 长度 > 500
- clause() 返回的条款正文首行标题正确（断言标题文字，非「长度>0」）
- refund_rows() 的 golden 逐行断言（2023 两版 + 东城自制无退费表）
- 未知档位 / 未知条款号安静返回空值，不抛异常
"""

from pathlib import Path

import pytest

from services.contract_template_text import (
    clause,
    refund_rows,
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


def _row(rows: list[list[str]], content: str) -> list[str] | None:
    """按退费表「内容」列（第 2 列）取行。"""
    for row in rows:
        if len(row) > 1 and row[1] == content:
            return row
    return None


# ── 资产存在性（逐档列举） ─────────────────────────────────────────────

@pytest.mark.parametrize("tier_id", TIER_IDS)
def test_asset_file_exists_and_nonempty(tier_id):
    path = ASSET_DIR / f"{tier_id}.txt"
    assert path.is_file(), f"资产缺失：{path}"
    assert path.stat().st_size > 0, f"资产为空：{path}"


@pytest.mark.parametrize("tier_id", TIER_IDS)
def test_template_text_longer_than_500(tier_id):
    text = template_text(tier_id)
    assert len(text) > 500, f"{tier_id} 正文过短：{len(text)}"


def test_refund_tsv_presence_matches_known_facts():
    # 2023 两版与 2021-2022 有退费表 → 旁挂文件存在
    assert (ASSET_DIR / "2023_branch_school.refund.tsv").is_file()
    assert (ASSET_DIR / "2023_branch_store.refund.tsv").is_file()
    assert (ASSET_DIR / "2021_2022.refund.tsv").is_file()
    # 东城自制无退费表（退费走第六条纯文字）→ 不得有旁挂文件
    assert not (ASSET_DIR / "2019_dongcheng.refund.tsv").exists()


def test_template_text_accepts_id_str_and_dict_same():
    from services.contract_tiers import TIERS_BY_ID

    assert template_text("2023_branch_school") == template_text(TIERS_BY_ID["2023_branch_school"])


# ── clause() ──────────────────────────────────────────────────────────

def test_clause_2023_school_eight_title():
    body = clause("2023_branch_school", "第八条")
    assert body
    first_line = body.strip().splitlines()[0].strip()
    assert first_line.startswith("第八条")
    assert "退学退费" in first_line


def test_clause_2023_store_eight_title():
    body = clause("2023_branch_store", "第八条")
    assert "退学退费" in body.strip().splitlines()[0]


def test_clause_dongcheng_six_and_eleven_titles():
    six = clause("2019_dongcheng", "第六条")
    assert "退学退费" in six.strip().splitlines()[0]
    eleven = clause("2019_dongcheng", "第十一条")
    assert eleven
    assert "违约责任" in eleven.strip().splitlines()[0]


@pytest.mark.parametrize("no", ["第八条", "八条", "第八", "8", "第 八 条"])
def test_clause_no_forms_equivalent(no):
    assert clause("2023_branch_school", no) == clause("2023_branch_school", "第八条")


def test_clause_unknown_returns_empty():
    assert clause("2023_branch_school", "第九十九条") == ""
    assert clause("__unknown_tier__", "第八条") == ""


def test_clause_body_stops_before_next_clause_title():
    """窗口止于「下一条标题」：条款正文不得吞进后续条款。"""
    body8 = clause("2023_branch_school", "第八条")
    assert "退学退费" in body8
    assert "第九条" not in body8
    assert "合同的变更、终止" not in body8

    body6 = clause("2019_dongcheng", "第六条")
    assert "退学退费" in body6
    assert "第七条" not in body6
    assert "合同有效期限" not in body6


# ── refund_rows() golden ──────────────────────────────────────────────

def test_refund_rows_school_golden():
    rows = refund_rows("2023_branch_school")

    assert _row(rows, "服务费") == ["基础服务（必扣项）", "服务费", "600", ""]
    assert _row(rows, "建档费") == ["", "建档费", "300", ""]
    assert _row(rows, "学员IC卡") == ["", "学员IC卡", "100", ""]

    # 分校版退费表没有场地费行
    assert not any("场地费" in cell for row in rows for cell in row)

    # 代收代缴：工本费 10；三科考试费与补考费备注
    assert _row(rows, "工本费") == ["", "工本费", "10", ""]
    assert _row(rows, "科目一考试费") == ["代收代缴（依实项）", "科目一考试费", "70", "补考费35元/次"]
    assert _row(rows, "科目二考试费") == ["", "科目二考试费", "130", "补考费65元/次"]
    assert _row(rows, "科目三考试费") == ["", "科目三考试费", "280", "补考费140元/次"]

    # 实操培训：科目二实操 C1=120元/学时、C2=150元/学时
    c1 = _row(rows, "科目二实操")
    assert c1 is not None
    assert "C1" in c1 and "120元/学时" in c1
    assert any("150元/学时" in cell for row in rows for cell in row)


def test_refund_rows_store_has_venue_fee_row():
    rows = refund_rows("2023_branch_store")
    assert _row(rows, "场地费") == ["", "场地费", "700", ""]
    # 场地费在基础服务（必扣项）组内、紧随学员IC卡
    names = [row[1] for row in rows if len(row) > 1]
    assert names.index("场地费") == names.index("学员IC卡") + 1


def test_refund_rows_dongcheng_empty():
    assert refund_rows("2019_dongcheng") == []


def test_refund_rows_2021_2022_golden():
    """2021-2022 版退费表逐行等值（对齐 2023 那条的写法，非「长度>0」）。"""
    assert refund_rows("2021_2022") == [
        ["退费时所处阶段", "应退费用的计算标准（以下“－”符号代表减去的意思）"],
        ["乙方在公安部门受理前退学的", "已收费用－违约金"],
        ["乙方在公安部门受理后退学的", "已收费用－理论培训费用及相关手续费－违约金"],
        [
            "乙方在实操阶段退学的",
            "已收费用－理论培训费用及相关手续费－已发生的实际操作培训费－违约金",
        ],
    ]


def test_refund_rows_unknown_tier_empty():
    assert refund_rows("__unknown_tier__") == []


def test_template_text_unknown_tier_empty():
    assert template_text("__unknown_tier__") == ""
    assert template_text(None) == ""
    assert template_text(123) == ""


def test_refund_rows_returns_copy():
    first = refund_rows("2023_branch_school")
    first[0][0] = "MUTATED"
    assert refund_rows("2023_branch_school")[0][0] == "项目"
