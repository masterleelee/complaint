"""回复函 Word 生成服务（v2）— 致东莞市交通运输局的正式文书

按任务规格纯代码生成（不依赖模板占位符替换）：
- 标题居中加粗三号(16pt)；正文宋体12pt、首行缩进2字符(Pt(28))
- 正文含「据了解」进度/培训时长段与合同编码+第九条退学退费条款引用
- 扣费编号清单动态读取明细行（金额<=0 跳过），编号行首行缩进
- 总扣费行加粗；落款右对齐 = 撰写当天日期
"""
import datetime
import os

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

SCHOOL_FULL_NAME = "东莞市快捷汽车驾驶员培训有限公司"
_FONT_CN = "宋体"


def generate_reply_docx(ticket: dict, deductions: list[dict], output_path: str) -> dict:
    """生成投诉回复函 docx。

    ticket 来自 complaint_tickets 表行；字段缺失时容错为空串/0，不报错。
    deductions=[{item, amt|amount, basis|reason}]，金额<=0 的行跳过。
    返回 {"success": True, "path": output_path} 或 {"success": False, "error": "..."}。
    """
    try:
        ticket = ticket or {}
        name = str(ticket.get("student_name") or ticket.get("name") or "").strip()
        id_card = str(ticket.get("id_card") or "").strip()
        complaint_date = str(ticket.get("complaint_date") or "").strip()[:10]
        registration_date = str(ticket.get("registration_date") or "").strip()[:10] or complaint_date
        # 校名：三系统校名优先；人工建案（三系统无信息）工单回落到机构字典单位名
        school_name = str(ticket.get("school_name") or "").strip() \
            or str(ticket.get("organization_unit_name") or "").strip()
        unit_type = str(ticket.get("organization_unit_type") or "").strip()
        if unit_type and school_name.endswith(unit_type):
            unit_type = ""
        license_type = str(ticket.get("license_type") or "").strip()
        exam_stage = str(ticket.get("exam_stage") or "").strip() or "未知"
        actual_paid = _to_float(ticket.get("actual_paid"))
        contract_code = str(ticket.get("contract_code") or "").strip()

        # 培训时长描述（参考函格式：「科目三培训27时15分、科目二培训17时2分」）
        th = ticket.get("training_hours") if isinstance(ticket.get("training_hours"), dict) else {}
        hours_parts = []
        for label, alt_key in (("科目三", "subject3"), ("科目二", "subject2")):
            v = str(th.get(label) or th.get(alt_key) or "").strip()
            if v and "".join(ch for ch in v if ch.isdigit()) != "0":
                hours_parts.append(f"{label}培训{v}")
        hours_desc = f"，{'、'.join(hours_parts)}" if hours_parts else ""

        rows = []
        for d in deductions or []:
            row = d or {}
            amt = _to_float(row.get("amt", row.get("amount")))
            if amt <= 0:
                continue
            rows.append((str(row.get("item") or ""), amt,
                         str(row.get("basis") or row.get("reason") or "").strip()))
        total_deduction = round(sum(r[1] for r in rows), 2)
        refund = max(0, round(actual_paid - total_deduction, 2))

        doc = Document()
        style = doc.styles["Normal"]
        style.font.name = _FONT_CN
        style.font.size = Pt(12)
        style.element.rPr.rFonts.set(qn("w:eastAsia"), _FONT_CN)

        # ── 标题：居中 加粗 三号16pt ──
        title = doc.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _set_run(title.add_run(f"关于{name}投诉的回复"), size=16, bold=True)

        doc.add_paragraph()

        # ── 收件方：顶格无缩进 ──
        _add_body(doc, "东莞市交通运输局：", indent=False)

        # ── 正文 ──
        _add_body(doc, (
            f"经我驾校调查核实，投诉人{name}（身份证号：{id_card}），"
            f"于{registration_date}在{school_name}{unit_type}网点报名{license_type}驾照培训。"
            f"我司于{complaint_date}收到学员投诉，现回复如下。"
        ))
        _add_body(doc, (
            f"据了解，学员报名共交培训服务费{_fmt(actual_paid)}元，"
            f"目前进度处于：{exam_stage}阶段{hours_desc}。"
        ))
        code_part = f"（合同编码：{contract_code}）" if contract_code else ""
        _add_body(doc, (
            f"按照《东莞市机动车驾驶员培训服务合同》{code_part}第九条退学退费相关约定："
            "（一）合同有效期内，甲方因个人原因中途提出退学的应向乙方提交书面申请，"
            "按项目扣除费用，剩余款项由乙方退回。扣费如下："
        ))

        # ── 扣费编号清单：首行缩进 ──
        for i, (item, amt, basis) in enumerate(rows, 1):
            basis_part = f"（{basis}）" if basis else ""
            _add_body(doc, f"{i}、{item}{basis_part}：{_fmt(amt)}元")

        # ── 总扣费行：加粗 首行缩进 ──
        p = doc.add_paragraph()
        p.paragraph_format.first_line_indent = Pt(28)
        _set_run(p.add_run(
            f"总扣费：{_fmt(total_deduction)}元。学员实际已交费用{_fmt(actual_paid)}元，"
            f"应退回：{_fmt(refund)}元。"
        ), bold=True)

        # ── 结尾承诺段 ──
        _add_body(doc, (
            "以上扣费严格依据双方签订的《东莞市机动车驾驶员培训服务合同》、"
            "已确认的扣费明细及已核实的学员培训、考试进度计算，"
            "我驾校愿意按案件最终处理结果继续办理，并积极配合贵局相关工作。"
        ))

        for _ in range(3):
            doc.add_paragraph()

        # ── 右对齐落款两行 ──
        sign = doc.add_paragraph()
        sign.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        _set_run(sign.add_run(f"驾校名称：{SCHOOL_FULL_NAME}（公章）"))

        today = datetime.date.today()
        date_p = doc.add_paragraph()
        date_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        _set_run(date_p.add_run(
            f"{today.year}年{today.month}月{today.day}日"
        ))

        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        doc.save(output_path)
        return {"success": True, "path": output_path}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _add_body(doc: Document, text: str, indent: bool = True) -> None:
    """正文段落：首行缩进2字符（Pt(28)，可关闭用于顶格/编号行）"""
    p = doc.add_paragraph()
    if indent:
        p.paragraph_format.first_line_indent = Pt(28)
    _set_run(p.add_run(text))


def _set_run(run, size=12, bold=False) -> None:
    run.font.name = _FONT_CN
    run.font.size = Pt(size)
    run.bold = bold
    run.element.rPr.rFonts.set(qn("w:eastAsia"), _FONT_CN)


def _to_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _fmt(value: float) -> str:
    """金额显示：整数不带小数，非整数最多保留2位"""
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")
