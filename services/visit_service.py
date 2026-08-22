"""投诉登记表生成服务（数据源统一为 communications + final_outcome）"""
import os
from datetime import datetime
from docx import Document
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from config import load_config
from services.visit_llm import summarize_visit


def _latest_summary(communications: list) -> str:
    """取最近一条沟通记录的处理摘要，作为回访情况说明的底稿。"""
    if not communications:
        return ""
    for rec in reversed(communications):
        s = (rec.get("summary") or "").strip()
        if s:
            return s
    return ""


def _build_handling_text(final_outcome: str, refund: float, negotiation_outcome: str = "", withdraw_status: str = "") -> str:
    """按最终投诉结果（六类）生成投诉处理结论文案。"""
    fee_part = f"合同总额未列示，应退费用{refund:.0f}元。" if refund else ""
    if final_outcome == "投诉撤销":
        base = "经与学员沟通，学员已撤销投诉，案件终结。"
    elif final_outcome == "同意合同扣费":
        base = "双方就合同扣费达成一致意见。"
    elif final_outcome == "不同意合同扣费但协商一致":
        base = "虽对合同扣费存在异议，但双方协商达成其他一致方案。"
    elif final_outcome == "不同意合同扣费且协商失败":
        base = "经多次沟通协商未果，相关费用按合同约定处理。"
    elif final_outcome == "无法联系":
        base = "经多次联系学员未果，按相关规定处理。"
    elif final_outcome == "继续培训/转校":
        base = "学员选择继续培训或转校，按相关流程办理。"
    else:
        base = "经核实，该学员投诉事宜已按合同约定处理。"
    if refund and final_outcome not in ("投诉撤销", "继续培训/转校", "无法联系"):
        base += fee_part
    return base


def generate_registration_form(
    ticket_data: dict,
    communications: list = None,
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
) -> dict:
    """
    生成学员投诉登记表（DRD 附件 2）。
    回访情况说明来自沟通记录（communications）+ 最终投诉结果（final_outcome），
    由 LLM 润色（无 LLM 时回退为最近一条沟通摘要）。
    返回 {"success": True, "filepath": "..."} 或 {"success": False, "error": "..."}
    """
    try:
        communications = communications or []
        doc = Document()
        style = doc.styles["Normal"]
        style.font.name = "宋体"
        style.font.size = Pt(12)
        style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

        # 标题
        title = doc.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = title.add_run("学员投诉登记表")
        run.font.name = "宋体"
        run.font.size = Pt(16)
        run.bold = True
        run.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

        doc.add_paragraph()

        # 生成回访情况说明（LLM 润色最近一条沟通摘要 + 最终结果）
        student_name = ticket_data.get("student_name", "")
        latest_summary = _latest_summary(communications)
        visit_result = summarize_visit(
            student_name=student_name,
            final_outcome=final_outcome,
            negotiation_outcome=negotiation_outcome,
            withdraw_status=withdraw_status,
            latest_summary=latest_summary,
            total_fee=total_fee,
            refund=refund,
            deductions=deductions or [],
            special_warnings=special_warnings or [],
            exam_stage=exam_stage or ticket_data.get("exam_stage", ""),
        )
        visit_summary = visit_result.get("summary", latest_summary or "经核实，该学员投诉事宜已按合同约定处理。")

        # 构建投诉处理文案（规则，基于最终结果，并前置规范结果标签便于归档核对）
        base_text = _build_handling_text(final_outcome, refund, negotiation_outcome, withdraw_status)
        handling_text = (f"最终投诉结果：{final_outcome}。" + base_text) if final_outcome else base_text

        # 基本信息表
        table = doc.add_table(rows=7, cols=4, style="Table Grid")

        fields = [
            ("编号", ticket_data.get("ticket_no", "") or ticket_data.get("id", "")[:8],
             "日期", datetime.now().strftime("%Y-%m-%d")),
            ("学员姓名", ticket_data.get("student_name", ""),
             "学员电话", ticket_data.get("phone", "")),
            ("学习进度", ticket_data.get("exam_stage", ""),
             "投诉对象", ticket_data.get("school_name", "")),
            ("受理人", "管理员",
             "投诉渠道", ticket_data.get("source_channel", "")),
            ("投诉内容", ticket_data.get("complaint_content", "") or ticket_data.get("complaint_demands", ""),
             "", ""),
            ("投诉处理", handling_text,
             "", ""),
            ("处理人签名", "___________",
             "处理日期", datetime.now().strftime("%Y-%m-%d")),
        ]

        for i, (k1, v1, k2, v2) in enumerate(fields):
            row = table.rows[i]
            row.cells[0].text = k1
            row.cells[1].text = str(v1)
            row.cells[2].text = k2
            row.cells[3].text = str(v2)

        doc.add_paragraph()
        doc.add_paragraph("上级领导意见：")
        doc.add_paragraph("___________")
        record_text = f"回访记录：{visit_summary}" if visit_summary else "回访记录："
        doc.add_paragraph(record_text)

        # 保存
        if not output_dir:
            cfg = load_config()
            output_dir = cfg["paths"]["reply_dir"]
        os.makedirs(output_dir, exist_ok=True)

        name = ticket_data.get("student_name", "未知")
        today = datetime.now().strftime("%Y%m%d")
        filename = f"{today}{name}投诉登记表.docx"
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
