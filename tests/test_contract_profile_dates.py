# -*- coding: utf-8 -*-
"""回归：合同关键信息 - 签订日期提取 + 合同期限跨行合并与到期日推导。

背景工单：刘金连 DGJP202605030258（id=3c50c8ef-97bf-498e-99e4-c03f68267682）。
原文特征：
1) 签署栏为「日期：2026年05月03日 日期：2026年05月03日」（无"签订"前缀）；
2) 合同期限句被 PDF 换行截断为「…有效期為 3 年，自签订之日起至 2029 年\n5 月 3 日止。」
   旧正则 [^\n]{4,40} 只取到「…至 2029 年」，丢失月日。
"""
from app import (
    _extract_signing_date,
    _extract_contract_term,
    _extract_school_name,
    _build_contract_profile,
    _cn_to_int,
    _collapse_spaced_cjk,
)


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
    assert term == "本培训服务合同有效期为3年，自签订之日起至2029年05月03日止"


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
    assert fields["合同期限"] == "本培训服务合同有效期为3年，自签订之日起至2029年05月03日止"


def test_real_ticket_profile_end_to_end():
    """端到端：真实工单条款 + 工单字段 → 关键信息完整（回归 DGJP202605030258）。"""
    ticket = {"contract_code": "DGJP202605030258", "student_name": "刘金连", "registration_date": "2026-05-03"}
    profile = _build_contract_profile(ticket, REAL_CLAUSES)
    fields = {f["label"]: f["value"] for f in profile["contract"]}
    assert fields["签订日期"] == "2026年05月03日"
    assert fields["合同期限"] == "本培训服务合同有效期为3年，自签订之日起至2029年05月03日止"
    assert fields["合同编号"] == "DGJP202605030258"


# ── 真实工单 DGJP202508100084（李永良）回归：甲方=培训机构、乙方=学员新模板 ──
# Bug 2a/2b：旧代码下乙方（驾培机构）显示 —，合同期限只显示到「…起计算」没有到期日。
LIYONGLIANG_PREAMBLE = (
    "合同编号: DGJP202508100084\n"
    "东莞市机动车驾驶员培训服务合同\n"
    "甲方（机动车驾驶员培训机构）\n"
    "名称： 东 莞 市 快 捷 汽 车 驾 驶 员 培 训 有 限 公 司\n"
    "统一社会信用代码： 914419006633471602\n"
    "地址： 广 东 省 东 莞 市 东 坑 镇\n"
    "联系电话： 13800000000\n"
    "乙方（学员）\n"
    "姓名： 李 永 良\n"
    "性别： 男\n"
    "身份证号码： 110101199003070011\n"
)

LIYONGLIANG_CLAUSES = [
    {"no": "", "title": "", "body": LIYONGLIANG_PREAMBLE},
    {"no": "一", "title": "合同有效期", "body": "本合同有效期为三年，自合同签订之日起计算。"},
    {"no": "二", "title": "学驾车型与培训内容", "body": "乙方选择培训的准驾车型：√□小型汽车手动挡C1"},
    {"no": "三", "title": "培训收费约定", "body": "乙方向甲方支付培训费用合计人民币 3980 元（以下均为人民币），其中通过“东莞驾培”平台支付金额为 1500 元。培训费用包含以下项目：1.综合服务费 1100 元；2.理论培训费 880 元。"},
    {"no": "十二", "title": "合同生效", "body": "日期：2025年08月10日 日期：2025年08月10日"},
]


def test_school_name_old_template_unspaced():
    """旧版「驾培机构：xxx」格式不受空格塌缩影响。"""
    full = "学 驾 人 ：刘金连\n驾培机构： 东莞市快捷汽车驾驶员培训有限公司"
    assert _extract_school_name(full) == "东莞市快捷汽车驾驶员培训有限公司"


def test_school_name_new_template_with_spaced_cjk():
    """新版「甲方（机动车驾驶员培训机构）\\n名称： 东 莞 市 … 公 司」要塌缩 CJK 空格。"""
    assert _extract_school_name(LIYONGLIANG_PREAMBLE) == "东莞市快捷汽车驾驶员培训有限公司"


def test_collapse_spaced_cjk_helper():
    """工具函数：'东 莞 市（ 快 捷 ）' → '东莞市（快捷）'。"""
    assert _collapse_spaced_cjk("东 莞 市") == "东莞市"
    assert _collapse_spaced_cjk("（ 机 构 ）") == "（机构）"
    # 不影响 CJK 与 ASCII/数字之间的空格（如「统一社会信用代码： 9144…」）
    assert _collapse_spaced_cjk("代码： 9144") == "代码： 9144"


def test_cn_to_int_chinese_numbers():
    """中文数字 1–30 + 阿拉伯数字 都能解析。"""
    assert (_cn_to_int("一"), _cn_to_int("三"), _cn_to_int("十"),
            _cn_to_int("十二"), _cn_to_int("二十"), _cn_to_int("三十"), _cn_to_int("5")) == (1, 3, 10, 12, 20, 30, 5)
    assert _cn_to_int("") == 0
    assert _cn_to_int("xyz") == 0


def test_term_chinese_year_no_end_date():
    """原文「本合同有效期为三年，自合同签订之日起计算。」无明确截止日 → 由签订日期 + 3 年推导。"""
    term = _extract_contract_term("本合同有效期为三年，自合同签订之日起计算。", "2025年08月10日")
    assert term == "本合同有效期为三年，自合同签订之日起至2028年08月10日止"


def test_term_arabic_year_no_end_date():
    """阿拉伯数字「本合同有效期为 3 年，自合同签订之日起计算。」同样要推导。"""
    term = _extract_contract_term("本合同有效期为 3 年，自合同签订之日起计算。", "2025年08月10日")
    assert term == "本合同有效期为3年，自合同签订之日起至2028年08月10日止"


def test_term_no_end_date_no_signing_keeps_original():
    """没有签订日期时不要硬加截止日，保留原文。"""
    term = _extract_contract_term("本合同有效期为三年，自合同签订之日起计算。", "")
    assert term == "本合同有效期为三年，自合同签订之日起计算"


def test_liyongliang_ticket_profile_end_to_end():
    """端到端：真实工单 DGJP202508100084（李永良）→ 乙方 / 合同期限 / 签订日期都正确。"""
    ticket = {
        "contract_code": "DGJP202508100084",
        "student_name": "李永良",
        "registration_date": "2025-08-10",
        "license_type": "C1",
    }
    profile = _build_contract_profile(ticket, LIYONGLIANG_CLAUSES)
    fields = {f["label"]: f["value"] for f in profile["contract"]}
    assert fields["合同编号"] == "DGJP202508100084"
    assert fields["甲方（学驾人）"] == "李永良"
    assert fields["乙方（驾培机构）"] == "东莞市快捷汽车驾驶员培训有限公司"
    assert fields["培训车型"] == "C1"
    assert fields["合同期限"] == "本合同有效期为三年，自合同签订之日起至2028年08月10日止"
    assert fields["签订日期"] == "2025年08月10日"
