"""投诉登记表生成服务（数据源统一为工单处理情况 + 已确认费用核算）"""
import os
import re
from datetime import datetime
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_ROW_HEIGHT_RULE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from config import load_config

LABEL_FILL = "F2F2F2"
NOTE_GRAY = RGBColor(0x59, 0x59, 0x59)
SIGN_BLANK = "____________"

# 投诉类型 → 通用诉求文案（学员未填诉求、AI 摘要缺失时的兜底）
GENERIC_DEMANDS = {
    "A": "要求按规定退还剩余培训费用",
    "B": "要求改进教学与服务质量，妥善安排后续培训",
    "C": "要求协调尽快安排考试",
    "D": "要求按合同约定履行并明确扣费依据",
    "E": "要求核实情况并妥善处理合理诉求",
}

# 投诉类型 → 投诉内容兜底（无材料原文且 AI 整理失败时使用，只写通用事实不编造细节）
CONTENT_FALLBACKS = {
    "A": "学员报名后申请退出培训，对剩余培训费用的核算与退还标准存在异议，遂通过投诉渠道反映诉求。",
    "B": "学员反映在训期间的教学服务体验未达预期，就训练安排与服务质量问题提出投诉。",
    "C": "学员反映考试预约等待时间较长、排考进度不符合预期，要求协调安排考试。",
    "D": "学员对培训合同的履行条款及费用扣收依据存在争议，要求校方逐项说明并按约履行。",
    "E": "学员通过投诉渠道反映在培期间遇到的问题，要求校方核实处理。",
}

# 投诉类型 → 处理经过兜底（④处理情况缺失或 AI 归纳失败时使用）
HANDLING_FALLBACKS = {
    "A": "受理投诉后已联系学员核实情况，并向其解释合同约定的扣费项目、标准与计算依据，双方共同核对了费用明细。",
    "B": "受理投诉后已向相关教学人员及报名点核实教学服务情况，督促改进服务方式，并与学员沟通后续培训安排。",
    "C": "受理投诉后已核实学员训练学时与约考条件，协调训练调度部门跟进后续考试安排，并向学员说明排考流程。",
    "D": "受理投诉后已组织学员共同核对合同条款与各项扣费明细，逐项说明收费依据，并就争议事项进行协商。",
    "E": "受理投诉后已开展调查核实，将处理进展及时反馈学员，并按规定向主管部门报告有关情况。",
}

# 身份证号形态（15 位旧版 / 18 位新版）；用于剔除被误填进文本字段的身份证号
_IDCARD_RE = re.compile(r'^\d{15}$|^\d{17}[\dxX]$')


def _looks_like_idcard(value) -> bool:
    """字符串是否形似身份证号（曾被错误写入投诉内容/诉求等文本字段）。"""
    v = str(value or "").strip()
    return bool(_IDCARD_RE.match(v))


def _fmt_date_cn(value: str) -> str:
    """YYYY-MM-DD → YYYY年M月D日；无法解析时原样返回。"""
    raw = str(value or "").strip()
    try:
        d = datetime.strptime(raw[:10], "%Y-%m-%d")
        return f"{d.year}年{d.month}月{d.day}日"
    except ValueError:
        return raw


def _stage_display(value: str) -> str:
    """学习进度显示：括号补充说明换到第二行（如「科目一
（待考试）」）。"""
    return re.sub(r"（", "\n（", str(value or "").strip(), count=1)


def _ticket_no(ticket_data: dict) -> str:
    """编号：优先用 ticket_no，否则用 投诉日期-工单ID前6位。"""
    no = str(ticket_data.get("ticket_no") or "").strip()
    if no:
        return no
    tid = str(ticket_data.get("id") or "").strip()
    date_raw = str(ticket_data.get("complaint_date") or "").strip() or datetime.now().strftime("%Y-%m-%d")
    compact = re.sub(r"\D", "", date_raw)[:8] or datetime.now().strftime("%Y%m%d")
    return f"{compact}-{tid[:6].upper()}" if tid else compact


def _build_outcome_text(final_outcome: str) -> str:
    """按最终投诉结果（六类）确定性拼接处理结论文案（不含金额，金额见费用核算栏）。"""
    if final_outcome == "投诉撤销":
        return "经与学员沟通，学员已撤销投诉，案件终结。"
    if final_outcome == "同意合同扣费":
        return "双方就合同扣费达成一致意见。"
    if final_outcome == "不同意合同扣费但协商一致":
        return "虽对合同扣费存在异议，但双方协商达成其他一致方案。"
    if final_outcome == "不同意合同扣费且协商失败":
        return "经多次沟通协商未果，相关费用按合同约定处理。"
    if final_outcome == "无法联系":
        return "经多次联系学员未果，按相关规定处理。"
    if final_outcome == "继续培训/转校":
        return "学员选择继续培训或转校，按相关流程办理。"
    if final_outcome:
        return f"经核实，该学员投诉事宜已按「{final_outcome}」处理完毕。"
    return "经核实，该学员投诉事宜已按合同约定处理。"


def _build_fee_text(total_fee: float, refund: float, deductions: list) -> str:
    """费用核算栏文案：合同总额 + 扣费合计（含明细）+ 核定应退；仅费用确认后展示。"""
    parts = []
    if total_fee > 0:
        parts.append(f"合同总额{total_fee:.0f}元")
    if deductions:
        total_ded = sum(float(d.get("amount", 0) or 0) for d in deductions)
        detail = "、".join(
            f"{d.get('item', '')}{float(d.get('amount', 0) or 0):.0f}元" for d in deductions
        )
        parts.append(f"扣费合计{total_ded:.0f}元（{detail}）")
    if not parts:
        return ""
    parts.append(f"核定应退{refund:.0f}元")
    return "；".join(parts) + "。"


def build_registration_form_data(
    ticket_data: dict,
    handling_notes: str = "",
    final_outcome: str = "",
    negotiation_outcome: str = "",
    total_fee: float = 0,
    refund: float = 0,
    deductions: list = None,
    ai_sections: dict = None,
) -> dict:
    """构建登记表内容数据（docx 生成与页面预览共用的唯一数据源）。

    ai_sections：AI 整理的三段 {complaint_content, complaint_demands, handling_summary}，
    由调用方（预览/归档前）统一生成传入；缺失时按 原文 → 摘要 → 类型话术 逐级兜底。
    fields 前 4 行为六元组 (标签,值,标签,值,标签,值)，其余为四元组 (标签,值,"","")，
    四元组表示该行值独占整行。
    """
    ai_sections = ai_sections or {}
    complaint_type = str(ticket_data.get("complaint_type") or "").strip()

    # 科目二/三学时（来自第三系统审核有效学时，未产生则为空）
    hours = ticket_data.get("training_hours") if isinstance(ticket_data.get("training_hours"), dict) else {}
    hours_sub2 = str(hours.get("科目二") or "").strip()
    hours_sub3 = str(hours.get("科目三") or "").strip()

    # 投诉内容/诉求兜底链：AI 整理 → 材料原文 → 摘要 → 类型话术
    summary = str(ticket_data.get("complaint_summary") or "").strip()

    # 投诉诉求：已存诉求优先；被误填为身份证号（或与本工单 id_card 相同）时视为空
    raw_demands = str(ticket_data.get("complaint_demands") or "").strip()
    if _looks_like_idcard(raw_demands) or raw_demands == str(ticket_data.get("id_card") or "").strip():
        raw_demands = ""
    demand_text = raw_demands \
        or str(ai_sections.get("complaint_demands") or "").strip() or summary \
        or GENERIC_DEMANDS.get(complaint_type, "")

    # 投诉内容：AI 整理 → 材料原文 → 摘要 → 诉求 → 类型话术
    # 被误填为身份证号（或与本工单 id_card 相同）时视为空，避免身份证号出现在投诉内容中
    raw_content = str(ticket_data.get("complaint_content") or "").strip()
    if _looks_like_idcard(raw_content) or raw_content == str(ticket_data.get("id_card") or "").strip():
        raw_content = ""
    content_text = str(ai_sections.get("complaint_content") or "").strip() \
        or raw_content or summary or demand_text \
        or CONTENT_FALLBACKS.get(complaint_type, "")

    # 投诉处理：AI 归纳 → 处理情况原文 → 类型话术，作为「处理经过」行
    proc_lines = []
    # 处理经过：AI 归纳 → ④处理情况原文 → 类型话术
    notes = str(ai_sections.get("handling_summary") or "").strip() \
        or str(handling_notes or "").strip() \
        or HANDLING_FALLBACKS.get(complaint_type, "")
    if notes:
        proc_lines.append(f"处理经过：{notes}")
    opinion = str(negotiation_outcome or "").strip()
    if opinion:
        proc_lines.append(f"学员意见：{opinion}")
    conclusion = _build_outcome_text(final_outcome)
    if final_outcome:
        conclusion = f"最终投诉结果：{final_outcome}。" + conclusion
    proc_lines.append(f"处理结论：{conclusion}")

    if (ticket_data.get("fee_plan_status") or "") != "confirmed":
        # 未确认但已有草稿数据 → 如实展示并标注待确认；完全无数据才用占位文案
        fee_text = _build_fee_text(total_fee, refund, deductions or [])
        fee_text = f"{fee_text[:-1]}（费用核算待确认）。" if fee_text else "费用待核算确认。"
    else:
        fee_text = _build_fee_text(total_fee, refund, deductions or []) or "费用待核算确认。"

    handler = str(ticket_data.get("handler_name") or "").strip() or "管理员"
    today_cn = _fmt_date_cn(datetime.now().strftime("%Y-%m-%d"))

    # 投诉对象：三系统校名优先；人工建案（三系统无信息）工单无校名，回落到机构字典单位名
    school_display = str(ticket_data.get("school_name") or "").strip() \
        or str(ticket_data.get("organization_unit_name") or "").strip()

    fields = [
        ("投诉日期", _fmt_date_cn(ticket_data.get("complaint_date")),
         "投诉渠道", str(ticket_data.get("source_channel") or ""),
         "投诉对象", school_display),
        ("学员姓名", str(ticket_data.get("student_name") or ""),
         "身份证号", str(ticket_data.get("id_card") or "").strip(),
         "报名日期", _fmt_date_cn(ticket_data.get("registration_date"))),
        ("手机号", str(ticket_data.get("phone") or ""),
         "学习进度", _stage_display(ticket_data.get("exam_stage")),
         "报考车型", str(ticket_data.get("license_type") or "")),
        ("科目二学时", hours_sub2, "科目三学时", hours_sub3, "受理人", handler),
        ("投诉内容", content_text, "", ""),
        ("投诉诉求", demand_text, "", ""),
        ("费用核算", fee_text, "", ""),
        ("投诉处理", "\n".join(proc_lines), "", ""),
        ("回访记录", "", "", ""),
    ]
    return {
        "title": "学员投诉登记表",
        "no_line": f"编号：{_ticket_no(ticket_data)} 号",
        "fields": fields,
        "visit_summary": notes or "经核实，该学员投诉事宜已按合同约定处理。",
        "handler": handler,
        "today_cn": today_cn,
    }


# ── docx 版式辅助 ──────────────────────────────────────────────

_INFO_ROWS = 4          # 六列信息区行数（含身份证号/学时行）


def _style_run(run, *, size=10.5, bold=False, color=None):
    run.font.name = "宋体"
    run.font.size = Pt(size)
    run.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    if color is not None:
        run.font.color.rgb = color


def _set_cell_shading(cell, fill: str):
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), fill)
    cell._tc.get_or_add_tcPr().append(shd)


def _cell_write(cell, blocks, *, valign=WD_CELL_VERTICAL_ALIGNMENT.TOP):
    """清空单元格后按 blocks 写入段落。

    block 结构：(text, align, size, bold, line_spacing, space_after_pt)
    """
    cell.vertical_alignment = valign
    first = True
    for text, align, size, bold, spacing, space_after in blocks:
        p = cell.paragraphs[0] if first else cell.add_paragraph()
        first = False
        p.alignment = align
        p.paragraph_format.line_spacing = spacing
        p.paragraph_format.space_after = Pt(space_after)
        if text:
            run = p.add_run(text)
            _style_run(run, size=size, bold=bold)


def _table_borders(table):
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "12")
        el.set(qn("w:color"), "000000")
        borders.append(el)
    for edge in ("insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:color"), "000000")
        borders.append(el)
    tbl_pr.append(borders)

    margins = OxmlElement("w:tblCellMar")
    for edge, w in (("top", 57), ("left", 108), ("bottom", 57), ("right", 108)):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:w"), str(w))
        el.set(qn("w:type"), "dxa")
        margins.append(el)
    tbl_pr.append(margins)

    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tbl_pr.append(layout)


def _spacer_count(row_h_cm: float, used_lines: int) -> int:
    """估算固定行高内可容纳的行槽数，返回签名前需插入的空段数。"""
    avail = int((row_h_cm - 0.35) / 0.5)
    return max(0, avail - used_lines)


def _section_cell(cell, header, body_lines, sign_line="", row_h_cm=0.0):
    """全宽分区单元格：加粗小标题与正文首行同段；签名行置于右下角。"""
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
    body_lines = [str(ln).strip() for ln in body_lines if str(ln or "").strip()]

    head_p = cell.paragraphs[0]
    head_p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    head_p.paragraph_format.line_spacing = 1.25
    head_p.paragraph_format.space_after = Pt(4)
    _style_run(head_p.add_run(header), size=10.5, bold=True)
    if body_lines:
        _style_run(head_p.add_run(body_lines[0]), size=10.5)

    for line in body_lines[1:]:
        bp = cell.add_paragraph()
        bp.alignment = WD_ALIGN_PARAGRAPH.LEFT
        bp.paragraph_format.line_spacing = 1.3
        bp.paragraph_format.space_after = Pt(2)
        _style_run(bp.add_run(line), size=10.5)

    if sign_line:
        used = 1 + len(body_lines) + 1
        for _ in range(_spacer_count(row_h_cm, used)):
            sp = cell.add_paragraph()
            sp.paragraph_format.line_spacing = 1.0
            sp.paragraph_format.space_after = Pt(0)
        sp = cell.add_paragraph()
        sp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        sp.paragraph_format.line_spacing = 1.3
        _style_run(sp.add_run(sign_line), size=10.5)


def generate_registration_form(
    ticket_data: dict,
    handling_notes: str = "",
    final_outcome: str = "",
    negotiation_outcome: str = "",
    withdraw_status: str = "",
    branch_cooperation: str = "",
    output_dir: str = "",
    total_fee: float = 0,
    refund: float = 0,
    deductions: list = None,
    special_warnings: list = None,
    exam_stage: str = "",
    ai_sections: dict = None,
) -> dict:
    """
    生成学员投诉登记表（DRD 附件 2），单页 A4 表格式版面。
    「投诉内容」「投诉诉求」优先用 AI 整理结果（ai_sections），缺失时回退
    材料原文/AI 摘要/类型话术；「投诉处理」由 AI 归纳或沟通环节（handling_notes）
    + 学员意见（negotiation_outcome）+ 处理结论组成；
    「费用核算」含扣费合计与明细，未确认时标注；「回访记录」留空待回访后手填。
    返回 {"success": True, "filepath": ..., "filename": ...} 或 {"success": False, "error": ...}
    """
    try:
        data = build_registration_form_data(
            ticket_data,
            handling_notes=handling_notes,
            final_outcome=final_outcome,
            negotiation_outcome=negotiation_outcome,
            total_fee=total_fee,
            refund=refund,
            deductions=deductions,
            ai_sections=ai_sections,
        )
        handler = data["handler"]
        today_cn = data["today_cn"]

        doc = Document()
        style = doc.styles["Normal"]
        style.font.name = "宋体"
        style.font.size = Pt(10.5)
        style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

        section = doc.sections[0]
        section.page_width = Cm(21)
        section.page_height = Cm(29.7)
        section.top_margin = Cm(1.2)
        section.bottom_margin = Cm(1.0)
        section.left_margin = Cm(1.9)
        section.right_margin = Cm(1.9)

        # 标题（加字距）
        title = doc.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title.paragraph_format.space_after = Pt(4)
        run = title.add_run(data["title"])
        _style_run(run, size=16, bold=True)
        char_spacing = OxmlElement("w:spacing")
        char_spacing.set(qn("w:val"), "40")
        run._element.get_or_add_rPr().append(char_spacing)

        # 编号行（表格右上）
        no_p = doc.add_paragraph()
        no_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        no_p.paragraph_format.space_after = Pt(3)
        _style_run(no_p.add_run(data["no_line"]), size=10.5)

        n_rows = len(data["fields"])
        table = doc.add_table(rows=n_rows, cols=6)
        table.autofit = False
        _table_borders(table)

        # A4 文本宽 = 21cm − 1.9cm×2 = 17.2cm ≈ 9752 twips，六列：标签 1300 / 值约 1950
        # （标签需容纳「科目二学时」5 字，值列保证 18 位身份证号 9pt 单行）
        col_tw = [1300, 1951, 1300, 1951, 1300, 1950]
        for i, col in enumerate(table.columns):
            col.width = Cm(col_tw[i] / 567.0)
        for row in table.rows:
            row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST
            for idx, cell in enumerate(row.cells):
                cell.width = Cm(col_tw[idx] / 567.0)

        full_w = Cm(sum(col_tw) / 567.0)

        def merge_full(row):
            return row.cells[0].merge(row.cells[-1])

        center_valign = WD_CELL_VERTICAL_ALIGNMENT.CENTER

        # 信息区四行（每行三对 标签/值）
        for i in range(_INFO_ROWS):
            k1, v1, k2, v2, k3, v3 = data["fields"][i]
            row = table.rows[i]
            row.height = Cm(0.85)
            cells = row.cells
            for c, label in ((cells[0], k1), (cells[2], k2), (cells[4], k3)):
                _set_cell_shading(c, LABEL_FILL)
                _cell_write(c, [(label, WD_ALIGN_PARAGRAPH.CENTER, 10.5, True, 1.15, 0)],
                            valign=center_valign)
            # 18 位身份证号在值列宽内需缩小字号才能单行显示
            for c, label, val in ((cells[1], k1, v1), (cells[3], k2, v2), (cells[5], k3, v3)):
                vsize = 9 if label == "身份证号" else 10.5
                val_blocks = [(ln, WD_ALIGN_PARAGRAPH.CENTER, vsize, False, 1.15, 0)
                              for ln in str(val).split("\n")]
                _cell_write(c, val_blocks, valign=center_valign)

        # 第 5 行起：全宽分区
        section_heights_cm = {"投诉内容": 4.6, "投诉诉求": 2.4, "费用核算": 1.6,
                              "投诉处理": 7.0, "回访记录": 2.8}
        for i in range(_INFO_ROWS, n_rows):
            label, value, _, _ = data["fields"][i]
            row = table.rows[i]
            h_cm = section_heights_cm.get(label, 1.5)
            row.height = Cm(h_cm)
            merged = merge_full(row)
            merged.width = full_w

            if label == "投诉内容":
                _section_cell(merged, "投诉内容：", [value])
            elif label == "投诉诉求":
                _section_cell(merged, "投诉诉求：", [value])
            elif label == "费用核算":
                _section_cell(merged, "费用核算：", [value])
            elif label == "投诉处理":
                sign = f"处理人签名：{handler}　　日期：{today_cn}"
                _section_cell(merged, "投诉处理：", str(value).split("\n"),
                              sign_line=sign, row_h_cm=h_cm)
            elif label == "回访记录":
                sign = f"回访人：{SIGN_BLANK}　　______年____月____日"
                _section_cell(merged, "回访记录：", [], sign_line=sign, row_h_cm=h_cm)

        # 底部备注
        note = doc.add_paragraph()
        note.paragraph_format.space_before = Pt(6)
        note_run = note.add_run(
            "备注：一般投诉事项应在 3 日内处理完毕并填写本表；重大投诉事项需及时报上级部门处理。"
        )
        _style_run(note_run, size=9, color=NOTE_GRAY)

        if not output_dir:
            cfg = load_config()
            output_dir = cfg["paths"]["reply_dir"]
        os.makedirs(output_dir, exist_ok=True)

        name = re.sub(r'[\\/:*?"<>|\s]', "_", str(ticket_data.get("student_name") or "").strip()) or "未知"
        date_str = str(ticket_data.get("complaint_date") or "").strip() or datetime.now().strftime("%Y%m%d")
        filename = f"{date_str}_{name}_投诉登记表.docx"
        filepath = os.path.join(output_dir, filename)
        if os.path.exists(filepath):
            base, ext = os.path.splitext(filepath)
            n = 1
            while os.path.exists(f"{base}({n}){ext}"):
                n += 1
            filepath = f"{base}({n}){ext}"

        doc.save(filepath)
        return {"success": True, "filepath": filepath, "filename": os.path.basename(filepath)}

    except Exception as e:
        return {"success": False, "error": str(e)}
