import re

import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt

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


def test_hours_empty_filled_with_zero(tmp_path):
    # 无学时（第三系统无记录）→ 按业务口径填 0，表内不留空白
    data = build_registration_form_data(BASE_TICKET)
    row = [r for r in data["fields"] if r[0] == "科目二学时"][0]
    assert row[1] == "0"
    assert row[3] == "0"

    result = generate_registration_form(BASE_TICKET, output_dir=str(tmp_path))
    text = _doc_text(result["filepath"])
    assert "科目二学时0" in text.replace("\n", "")
    assert "科目三学时0" in text.replace("\n", "")

    # 已有学时按原值展示，不被 0 覆盖
    with_hours = dict(BASE_TICKET, training_hours={"科目二": "12", "科目三": "3"})
    row2 = [r for r in build_registration_form_data(with_hours)["fields"] if r[0] == "科目二学时"][0]
    assert row2[1] == "12" and row2[3] == "3"


def test_handling_notes_newlines_flattened(tmp_path):
    # 处理经过整段连排：文本内换行拍平，不再每条一行
    notes = "1）已与学员进行电话联系，告知其扣费依据与扣费明细，但学员不同意扣费；\n2）双方经多次协商无果；\n3）学员表示将寻求主管部门协助处理。"
    result = generate_registration_form(BASE_TICKET, handling_notes=notes, output_dir=str(tmp_path))
    text = _doc_text(result["filepath"])
    assert "1）已与学员进行电话联系，告知其扣费依据与扣费明细，但学员不同意扣费；2）双方经多次协商无果；3）学员表示将寻求主管部门协助处理。" in text
    # 处理经过单元格内不再出现换行（_doc_text 用 \n 拼接各单元格，故此处用「；2）」连排判定）
    cell_lines = [c.text for c in [r.cells[0] for r in Document(result["filepath"]).tables[0].rows][4:]]
    proc_cell = [c for c in cell_lines if c.startswith("投诉处理：")][0]
    assert "；\n2）" not in proc_cell


def test_overrides_applied_to_fields(tmp_path):
    # 纸面编辑（所见即所得）：标签整体覆盖 + 分区按行覆盖
    data = build_registration_form_data(
        BASE_TICKET,
        overrides={"手机号": "13800000000", "投诉内容": "覆盖后的投诉内容", "__title__": "学员投诉登记表（改）"},
    )
    assert data["title"] == "学员投诉登记表（改）"
    assert [r for r in data["fields"] if r[0] == "投诉内容"][0][1] == "覆盖后的投诉内容"

    # 信息区：按标签定位三对中的具体位置（手机号所在行）
    phone_row = [r for r in data["fields"] if len(r) == 6 and "手机号" in (r[0], r[2], r[4])][0]
    pos = 0 if phone_row[0] == "手机号" else (2 if phone_row[2] == "手机号" else 4)
    assert phone_row[pos + 1] == "13800000000"

    # 分区按行覆盖：指定行存在时替换该行，其余行不动
    base = build_registration_form_data(BASE_TICKET, handling_notes="原始处理经过")
    proc_before = [r for r in base["fields"] if r[0] == "投诉处理"][0][1]
    after = build_registration_form_data(
        BASE_TICKET, handling_notes="原始处理经过",
        overrides={"投诉处理:1": "学员意见：已当面沟通"},
    )
    proc_after = [r for r in after["fields"] if r[0] == "投诉处理"][0][1]
    assert proc_after.split("\n")[0] == "处理经过：原始处理经过"   # 第 0 行未动
    assert proc_after.split("\n")[1] == "学员意见：已当面沟通"     # 第 1 行被覆盖
    # 未定最终投诉结果时不再自动生成处理结论行，覆盖后仅 处理经过+学员意见 两行
    assert proc_after == "处理经过：原始处理经过\n学员意见：已当面沟通"

    # 行号越界时自动补行，不篡改已有行
    padded = build_registration_form_data(
        BASE_TICKET, handling_notes="原始处理经过",
        overrides={"投诉处理:3": "补充事项：已同步财务"},
    )["fields"]
    padded_lines = [r for r in padded if r[0] == "投诉处理"][0][1].split("\n")
    assert padded_lines[0] == "处理经过：原始处理经过"
    assert padded_lines[3] == "补充事项：已同步财务"
    assert len(padded_lines) == 4

    # 覆盖值进入 docx
    result = generate_registration_form(
        BASE_TICKET, handling_notes="原始处理经过",
        overrides={"投诉处理:1": "学员意见：已当面沟通"}, output_dir=str(tmp_path))
    assert "学员意见：已当面沟通" in _doc_text(result["filepath"])


def test_idcard_column_widened(tmp_path):
    # 标签 1400 twips (2.47cm) + 值 1825 twips (3.22cm) + 身份证号 1870 twips (3.30cm)
    # 字号统一：标签 10pt 加粗 / 值 9pt（小五） 视觉整齐
    # 身份证号 18 位 9pt 数字 ≈ 2.85cm + padding 0.38cm = 3.23cm < 3.30cm 单行可放
    result = generate_registration_form(
        dict(BASE_TICKET, id_card="110101199003070011"), output_dir=str(tmp_path))
    table = Document(result["filepath"]).tables[0]
    # 标签列 0/2/4 = 1400 twips
    for idx in (0, 2, 4):
        assert float(table.columns[idx].width) > float(Cm(2.3)), f"col{idx} 太窄"
        assert float(table.columns[idx].width) < float(Cm(2.6)), f"col{idx} 太宽"
    # v1/v3 = 1825 twips
    for idx in (1, 5):
        assert float(table.columns[idx].width) > float(Cm(3.0)), f"col{idx} 太窄"
        assert float(table.columns[idx].width) < float(Cm(3.4)), f"col{idx} 太宽"
    # v2 = 1870 twips（身份证号列）
    assert float(table.columns[3].width) > float(Cm(3.2))
    assert float(table.columns[3].width) < float(Cm(3.4))
    # 总宽不变（9752 twips = 17.2cm）
    assert abs(sum(float(c.width) for c in table.columns) - float(Cm(17.2))) < float(Cm(0.15))


def test_info_rows_single_line(tmp_path):
    # 前 4 行所有单元格必须单行不换行 + 字号统一为标签 10pt / 值 9pt
    ticket = dict(BASE_TICKET,
                  id_card="110101199003070011",
                  school_name="东部工业园招生点",
                  phone="13800000000",
                  source_channel="12345 热线")
    result = generate_registration_form(ticket, output_dir=str(tmp_path))
    table = Document(result["filepath"]).tables[0]

    def _single_para(cell, expected_text):
        assert len(cell.paragraphs) == 1, f"单元格被换行: {[p.text for p in cell.paragraphs]}"
        assert cell.paragraphs[0].text == expected_text, \
            f"单元格内容={cell.paragraphs[0].text!r} 期望={expected_text!r}"

    # 第 0 行：投诉日期 / 投诉渠道 / 投诉对象
    row0 = table.rows[0]
    _single_para(row0.cells[0], "投诉日期")
    _single_para(row0.cells[2], "投诉渠道")
    _single_para(row0.cells[4], "投诉对象")
    assert row0.cells[5].paragraphs[0].text == "东部工业园招生点"

    # 第 1 行：学员姓名 / 身份证号 / 报名日期
    row1 = table.rows[1]
    _single_para(row1.cells[0], "学员姓名")
    _single_para(row1.cells[2], "身份证号")
    _single_para(row1.cells[4], "报名日期")
    assert row1.cells[3].paragraphs[0].text == "110101199003070011"

    # 第 2 行：手机号 / 学习进度 / 报考车型
    row2 = table.rows[2]
    _single_para(row2.cells[0], "手机号")
    _single_para(row2.cells[2], "学习进度")
    _single_para(row2.cells[4], "报考车型")

    # 第 3 行：科目二学时 / 科目三学时 / 受理人 ← 关键回归
    row3 = table.rows[3]
    _single_para(row3.cells[0], "科目二学时")
    _single_para(row3.cells[2], "科目三学时")
    _single_para(row3.cells[4], "受理人")

    # 字号统一：标签列 10pt 加粗；值列 9pt（含身份证号）
    label_size = pt_of(row3.cells[0])
    assert label_size == 10, f"标签字号={label_size}pt 应为 10pt"
    v_size = pt_of(row3.cells[1])
    assert v_size == 9, f"值字号={v_size}pt 应为 9pt"
    v3_size = pt_of(row1.cells[3])
    assert v3_size == 9, f"身份证号字号={v3_size}pt 应为 9pt（小五，不再 8pt）"


def pt_of(cell):
    """读取单元格第一个 run 的字号（pt），用于断言字号统一。"""
    r = cell.paragraphs[0].runs[0]
    return r.font.size.pt if r.font.size else None


def test_section_paragraph_first_line_indent(tmp_path):
    # 分区段落首行缩进 2 字符（10.5pt × 2 = 21pt）；签名行右对齐不缩进
    result = generate_registration_form(BASE_TICKET, output_dir=str(tmp_path))
    table = Document(result["filepath"]).tables[0]
    sec_row = [r for r in table.rows if r.cells[0].text.startswith("投诉内容：")][0]
    cell = sec_row.cells[0]
    assert cell.paragraphs[0].paragraph_format.first_line_indent == Pt(21)

    handle_row = [r for r in table.rows if r.cells[0].text.startswith("投诉处理：")][0]
    sign_p = [p for p in handle_row.cells[0].paragraphs if p.text.startswith("处理人签名")][0]
    assert sign_p.alignment == WD_ALIGN_PARAGRAPH.RIGHT
    assert sign_p.paragraph_format.first_line_indent == Pt(0)
