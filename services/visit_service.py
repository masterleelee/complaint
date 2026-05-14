"""回访管理 + 投诉登记表生成服务"""
import os
from datetime import datetime
from docx import Document
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from config import load_config
from database import update_ticket


def save_visit(ticket_id: str, visit_status: str, visit_remark: str) -> bool:
    """保存回访状态到数据库"""
    return update_ticket(ticket_id, {
        "visit_status": visit_status,
        "visit_remark": visit_remark,
        "visit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


def get_docs_for_visit_status(visit_status: str) -> list[str]:
    """根据回访状态返回应生成的文档类型列表"""
    rules = {
        "a": ["registration_form", "reply_letter"],  # 拒绝协商
        "b": ["registration_form"],                   # 无法联系
        "c": ["registration_form"],                   # 同意协商
        "d": [],                                      # 其他情况（用户自由勾选）
    }
    return rules.get(visit_status, [])


def generate_registration_form(ticket_data: dict, output_dir: str = "") -> dict:
    """
    生成学员投诉登记表（DRD 附件 2）。
    返回 {"success": True, "filepath": "..."} 或 {"success": False, "error": "..."}
    """
    try:
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
            ("投诉处理", "经核实，该学员投诉事宜已按合同约定处理。",
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
        doc.add_paragraph("回访记录：")

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
