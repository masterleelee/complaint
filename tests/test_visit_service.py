import re

import pytest
from docx import Document
from docx.shared import Cm

from services.visit_service import generate_registration_form, build_registration_form_data, _stage_display


BASE_TICKET = {
    "id": "abc123456789",
    "student_name": "测试学员乙",
    "complaint_date": "2026-08-23",
}


def _doc_text(path):
    doc = Document(path)
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return "\n".join(parts)


def test_handling_notes_in_processing_section_not_visit(tmp_path):
    result = generate_registration_form(
        BASE_TICKET,
        handling_notes="  已电话联系学员，双方达成和解，学员确认无异议。 ",
        output_dir=str(tmp_path),
    )
    assert result["success"]
    text = _doc_text(result["filepath"])
    assert "处理经过：已电话联系学员，双方达成和解，学员确认无异议。" in text
    assert "回访记录：已电话" not in text


def test_negotiation_outcome_rendered(tmp_path):
    result = generate_registration_form(
        BASE_TICKET,
        handling_notes="已沟通",
        negotiation_outcome="学员同意按合同扣除已产生费用",
        final_outcome="同意合同扣费",
        output_dir=str(tmp_path),
    )
    text = _doc_text(result["filepath"])
    assert "学员意见：学员同意按合同扣除已产生费用" in text
    assert "处理结论：最终投诉结果：同意合同扣费。" in text


def test_fee_confirmed_shows_total_and_deduction_sum(tmp_path):
    ticket = dict(BASE_TICKET, fee_plan_status="confirmed")
    result = generate_registration_form(
        ticket,
        final_outcome="同意合同扣费",
        total_fee=3800,
        refund=2800,
        deductions=[{"item": "科目一培训费", "amount": 500}, {"item": "违约金", "amount": 500.4}],
        output_dir=str(tmp_path),
    )
    assert result["success"]
    text = _doc_text(result["filepath"])
    assert "合同总额3800元" in text
    assert "扣费合计1000元（科目一培训费500元、违约金500元）" in text
    assert "核定应退2800元" in text


def test_fee_draft_shows_annotated_amounts(tmp_path):
    # 未确认但有草稿数据 → 如实展示金额并标注待确认（预览阶段所见）
    ticket = dict(BASE_TICKET)
    result = generate_registration_form(
        ticket,
        total_fee=3800,
        refund=2800,
        deductions=[{"item": "科目一培训费", "amount": 500}],
        output_dir=str(tmp_path),
    )
    assert result["success"]
    text = _doc_text(result["filepath"])
    assert "合同总额3800元" in text
    assert "核定应退2800元" in text
    assert "（费用核算待确认）。" in text

    # 完全无数据 → 占位文案
    result_empty = generate_registration_form(dict(BASE_TICKET), output_dir=str(tmp_path))
    assert "费用待核算确认。" in _doc_text(result_empty["filepath"])


def test_ai_sections_override_content_demands_handling(tmp_path):
    ticket = dict(BASE_TICKET, complaint_type="A",
                  complaint_content="原始材料长文本",
                  handling_notes="手填处理情况")
    ai = {
        "complaint_content": "学员于2026年7月报名C1课程，缴费后申请退费，对扣费标准有异议。",
        "complaint_demands": "要求按合同约定退还剩余培训费用",
        "handling_summary": "已联系学员核实情况，解释合同扣费依据并共同核对费用明细。",
    }
    result = generate_registration_form(ticket, ai_sections=ai, output_dir=str(tmp_path))
    text = _doc_text(result["filepath"])
    assert "投诉内容：学员于2026年7月报名C1课程" in text
    assert "投诉诉求：要求按合同约定退还剩余培训费用" in text
    assert "处理经过：已联系学员核实情况" in text
    assert "原始材料长文本" not in text

    # 已存诉求优先于 AI 整理（受理时提取落库的为准）
    result2 = generate_registration_form(
        dict(ticket, complaint_demands="原始诉求"), ai_sections=ai, output_dir=str(tmp_path))
    assert "投诉诉求：原始诉求" in _doc_text(result2["filepath"])


def test_idcard_misfilled_content_treated_as_empty(tmp_path):
    # 投诉内容被误填为身份证号（或与 id_card 相同）时，不应出现在登记表，应走兜底链
    ticket = dict(BASE_TICKET, complaint_type="A",
                  id_card="110101199003070011",
                  complaint_content="110101199003070011",
                  complaint_demands="110101199003070011")
    data = build_registration_form_data(ticket, ai_sections={})
    content_rows = [r for r in data["fields"] if r[0] == "投诉内容"]
    demand_rows = [r for r in data["fields"] if r[0] == "投诉诉求"]
    assert content_rows, "缺少投诉内容行"
    assert "110101199003070011" not in content_rows[0][1], "身份证号不应出现在投诉内容"
    assert "110101199003070011" not in demand_rows[0][1], "身份证号不应出现在投诉诉求"
    # 走类型兜底话术
    assert "退" in content_rows[0][1] or "异议" in content_rows[0][1]


def test_handling_fallback_by_type_when_no_notes(tmp_path):
    # ④处理情况缺失且无 AI 归纳 → 按投诉类型规范话术兜底
    ticket = dict(BASE_TICKET, complaint_type="A")
    result = generate_registration_form(ticket, output_dir=str(tmp_path))
    text = _doc_text(result["filepath"])
    assert "处理经过：受理投诉后已联系学员核实情况" in text

    # AI 归纳优先于手填原文
    ai = {"handling_summary": "AI归纳版处理经过。"}
    result2 = generate_registration_form(
        dict(BASE_TICKET, complaint_type="A"), handling_notes="手填原文",
        ai_sections=ai, output_dir=str(tmp_path))
    text2 = _doc_text(result2["filepath"])
    assert "处理经过：AI归纳版处理经过。" in text2
    assert "手填原文" not in text2


def test_content_and_demands_fallback_chain(tmp_path):
    # AI 摘要兜底投诉内容与诉求
    ticket = dict(BASE_TICKET, complaint_summary="学员刘乐怡要求退费")
    result = generate_registration_form(ticket, output_dir=str(tmp_path))
    text = _doc_text(result["filepath"])
    assert "投诉内容：学员刘乐怡要求退费" in text
    assert "投诉诉求：学员刘乐怡要求退费" in text

    # 投诉类型 A（退费纠纷）通用诉求兜底
    ticket2 = dict(BASE_TICKET, complaint_type="A")
    result2 = generate_registration_form(ticket2, output_dir=str(tmp_path))
    text2 = _doc_text(result2["filepath"])
    assert "投诉诉求：要求按规定退还剩余培训费用" in text2


def test_id_card_not_masked(tmp_path):
    ticket = dict(BASE_TICKET, id_card="110101199003070011")
    result = generate_registration_form(ticket, output_dir=str(tmp_path))
    text = _doc_text(result["filepath"])
    assert "110101199003070011" in text
    assert "*" not in text


def test_page_margins_and_layout(tmp_path):
    result = generate_registration_form(BASE_TICKET, output_dir=str(tmp_path))
    doc = Document(result["filepath"])
    sec = doc.sections[0]
    assert sec.page_width == pytest.approx(Cm(21), abs=1000)
    assert sec.page_height == pytest.approx(Cm(29.7), abs=1000)
    assert sec.top_margin == pytest.approx(Cm(1.2), abs=1000)
    assert sec.bottom_margin == pytest.approx(Cm(1.0), abs=1000)
    assert sec.left_margin == pytest.approx(Cm(1.9), abs=1000)
    assert sec.right_margin == pytest.approx(Cm(1.9), abs=1000)
    title_run = doc.paragraphs[0].runs[0]
    assert title_run.font.size.pt == 16
    assert title_run.bold is True
    # 主表存在且为 6 列结构
    assert len(doc.tables) == 1
    assert len(doc.tables[0].columns) == 6


def test_filename_simplified_to_固定名(tmp_path):
    result = generate_registration_form(BASE_TICKET, output_dir=str(tmp_path))
    assert result["filename"] == "投诉登记表.docx"


def test_fallback_filename_固定名(tmp_path):
    result = generate_registration_form({"id": "abc123def456"}, output_dir=str(tmp_path))
    assert result["filename"] == "投诉登记表.docx"
    text = _doc_text(result["filepath"])
    assert "ABC123" in text


def test_stage_display_breaks_parenthetical_to_new_line():
    assert _stage_display("科目一（待考试）") == "科目一\n（待考试）"
    assert _stage_display("科目二") == "科目二"
    assert _stage_display("") == ""
