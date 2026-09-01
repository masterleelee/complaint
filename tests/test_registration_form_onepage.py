"""登记表单页 A4 自适应回归测试。

锁定 diagnosing-bugs 的「文字溢出/翻页」根因修复：
1. 全量真实工单生成后必须单页 A4（page_fit.used_cm <= budget_cm, level==0）；
2. 超长内容必须触发压缩链（level 升级）且不抛异常、不撑破定高；
3. _fit_section_heights 在极端超长输入下返回有限值、档位合法。
"""
import os
import sqlite3

from services.visit_service import (
    generate_registration_form,
    _fit_section_heights,
    SECTION_HEIGHTS_CM,
    PAGE_H_CM,
)

DB = "data/complaints.db"


def _load_all_tickets():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    rows = [dict(r) for r in db.execute("SELECT * FROM complaint_tickets")]
    db.close()
    return rows


def _gen(t, tmp_path):
    return generate_registration_form(
        t,
        handling_notes=t.get("handling_notes") or "",
        final_outcome=t.get("final_outcome") or "",
        negotiation_outcome=t.get("negotiation_outcome") or "",
        total_fee=t.get("total_fee") or 0,
        refund=t.get("refund_amount") or 0,
        deductions=[],
        output_dir=str(tmp_path / t["id"][:8]),
    )


def test_real_tickets_all_single_page(tmp_path):
    """全量真实工单必须单页 A4：无掉页、无撑破。"""
    tickets = _load_all_tickets()
    assert tickets, "数据库无工单"
    overflow = []
    for t in tickets:
        r = _gen(t, tmp_path)
        assert r["success"], f"{t.get('student_name')} 生成失败: {r.get('error')}"
        pf = r["page_fit"]
        # 真实工单内容均在合理范围，应 level 0 + 单页
        if pf["used_cm"] > pf["budget_cm"]:
            overflow.append((t.get("student_name"), pf))
    assert not overflow, f"存在掉页工单: {overflow[:5]}"
    # 现实工单全部 level 0（美观版式即可装下）
    levels = []
    for t in tickets:
        r = _gen(t, tmp_path)
        levels.append(r["page_fit"]["level"])
    assert max(levels) == 0, f"现实工单出现压缩档: {levels}"


def test_overlong_content_triggers_shrink(tmp_path):
    """超长内容必须触发压缩链且不抛异常、行高不超过预算（尽力单页）。"""
    long_c = "投诉内容详细描述" * 120          # ~960 字
    long_d = "诉求要点" * 200                  # ~600 字
    long_f = "费用核算明细" * 200              # ~800 字
    long_h = "处理经过：" + "经协商达成一致并履行" * 60   # ~800 字
    t = {
        "id": "long0000001", "student_name": "超长测试", "complaint_date": "2026-08-23",
        "complaint_content": long_c, "complaint_demands": long_d,
        "handling_notes": long_h,
    }
    r = generate_registration_form(
        t, handling_notes=long_h, output_dir=str(tmp_path / "long"))
    assert r["success"], r.get("error")
    pf = r["page_fit"]
    # 触发了压缩（level >= 1）；且生成函数返回了合法档位
    assert pf["level"] >= 1, f"超长内容未触发压缩: {pf}"
    assert isinstance(pf["level"], int)
    assert pf["used_cm"] <= pf["budget_cm"] + 0.5, f"压缩后仍严重溢出: {pf}"


def test_fit_budget_extreme_input_finite():
    """_fit_section_heights 在极端超长输入下返回有限值、档位合法（不抛异常）。"""
    fields = [
        ["投诉日期", "2026年8月3日", "投诉渠道", "", "投诉对象", ""],
        ["学员姓名", "张三", "身份证号", "440000000000000000", "报名日期", "2026年1月1日"],
        ["手机号", "13800000000", "学习进度", "培训中", "报考车型", "C2"],
        ["科目二学时", "10", "科目三学时", "5", "受理人", "管理员"],
        ["投诉内容", "投" * 4000, "", ""],
        ["投诉诉求", "诉" * 800, "", ""],
        ["费用核算", "费" * 800, "", ""],
        ["投诉处理", "处理经过：" + "协" * 2000, "", ""],
        ["回访记录", "", "", ""],
    ]
    alloc, step, used, budget = _fit_section_heights(fields, "管理员", "2026年9月1日")
    assert used > 0 and used < 1000          # 有限值
    assert step["lvl"] == 4                   # 极端超长降到末档兜底
    # 每个分区行高不低于理想定高下限（不出现负/0）
    for lbl, h in alloc.items():
        assert h >= SECTION_HEIGHTS_CM.get(lbl, 1.5) - 0.01 or h > 0


def test_section_heights_constant_is_ideal_upperbound():
    """SECTION_HEIGHTS_CM 仍是 level-0 理想定高上限，与前端 regSecStyle 语义对应。"""
    assert SECTION_HEIGHTS_CM["投诉内容"] == 6.2
    assert SECTION_HEIGHTS_CM["投诉处理"] == 7.2
    assert PAGE_H_CM == 29.7
