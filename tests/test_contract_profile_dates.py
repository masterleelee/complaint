# -*- coding: utf-8 -*-
"""回归：合同关键信息 - 签订日期提取 + 合同期限跨行合并与到期日推导。

背景工单：刘金连 DGJP202605030258（id=3c50c8ef-97bf-498e-99e4-c03f68267682）。
原文特征：
1) 签署栏为「日期：2026年05月03日 日期：2026年05月03日」（无"签订"前缀）；
2) 合同期限句被 PDF 换行截断为「…有效期為 3 年，自签订之日起至 2029 年\n5 月 3 日止。」
   旧正则 [^\n]{4,40} 只取到「…至 2029 年」，丢失月日。
"""
from app import _extract_signing_date, _extract_contract_term, _build_contract_profile


# ── 真实工单结构（节选） ──────────────────────────────────────────────
REAL_CLAUSES = [
    {"no": "", "title": "", "body": (
        "合同编码：DGJP202605030258\n"
        "东莞市机动车驾驶员培训服务合同\n"
        "学 驾 人 ：刘金连\n"
        "驾培机构： 东莞市快捷汽车驾驶员培训有限公司"
    )},
    {"no": "一", "title": "合同有效期", "body": (
        "本培训服务合同有效期为 3 年，自签订之日起至 2029 年\n5 月 3 日止。"
    )},
    {"no": "十二", "title": "合同生效", "body": (
        "甲方应仔细阅读合同内容。\n"
        "本合同通过“东莞驾培”平台签署电子合同，自甲方签字、乙方盖章起生效，\n"
        "合同双方自愿接受合同约束。\n"
        "甲方（签名）： 乙方（盖章）：\n"
        "日期：2026年05月03日 日期：2026年05月03日"
    )},
]


def _full_text(clauses):
    preamble = next((c.get("body", "") for c in clauses if not c.get("no")), "")
    return preamble + "\n" + "\n".join(
        (c.get("title", "") + "\n" + c.get("body", "")) for c in clauses
    )


def test_signing_date_from_signature_block():
    """签署栏「日期：2026年05月03日」应被提取，不再显示 —。"""
    assert _extract_signing_date(_full_text(REAL_CLAUSES)) == "2026年05月03日"


def test_term_cross_line_merged_and_padded():
    """期限句跨行合并，中文日期零填充：…至2029年05月03日（不再截断在 2029 年）。"""
    term = _extract_contract_term(_full_text(REAL_CLAUSES), "2026年05月03日")
    assert term == "本培训服务合同有效期为3年，自签订之日起至2029年05月03日"


def test_term_year_only_derives_exact_expiry():
    """原文只写到「至 2029 年」时，由签订日期 2026年05月03日 + 3 年推导到期日。"""
    text = "本培训服务合同有效期为 3 年，自签订之日起至 2029 年。"
    term = _extract_contract_term(text, "2026年05月03日")
    assert "至2029年05月03日" in term


def test_term_feb29_edge_keeps_original():
    """签订 2028年02月29日 + 3 年不存在（2031 非闰年），应保留原文不崩溃。"""
    text = "本培训服务合同有效期为 3 年，自签订之日起至 2031 年。"
    term = _extract_contract_term(text, "2028年02月29日")
    assert "2031" in term  # 未推导成功时原文仍在


def test_signing_date_fallback_to_registration_date():
    """原文无任何日期时，回退用工单报名日期（东莞驾培电子合同同日签署）。"""
    clauses = [
        {"no": "", "title": "", "body": "合同编码：X\n东莞市机动车驾驶员培训服务合同"},
        {"no": "一", "title": "合同有效期", "body": "本培训服务合同有效期为 3 年，自签订之日起至 2029 年 5 月 3 日止。"},
    ]
    profile = _build_contract_profile({"registration_date": "2026-05-03"}, clauses)
    fields = {f["label"]: f["value"] for f in profile["contract"]}
    assert fields["签订日期"] == "2026年05月03日"
    assert fields["合同期限"] == "本培训服务合同有效期为3年，自签订之日起至2029年05月03日"


def test_real_ticket_profile_end_to_end():
    """端到端：真实工单条款 + 工单字段 → 关键信息完整（回归 DGJP202605030258）。"""
    ticket = {"contract_code": "DGJP202605030258", "student_name": "刘金连", "registration_date": "2026-05-03"}
    profile = _build_contract_profile(ticket, REAL_CLAUSES)
    fields = {f["label"]: f["value"] for f in profile["contract"]}
    assert fields["签订日期"] == "2026年05月03日"
    assert fields["合同期限"] == "本培训服务合同有效期为3年，自签订之日起至2029年05月03日"
    assert fields["合同编号"] == "DGJP202605030258"
