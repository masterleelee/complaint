"""回复函模板管理服务 - 基于 .docx 模板变量替换"""
import os
import re
import json
import uuid
from datetime import datetime
from docx import Document
from docx.shared import Pt
from docx.oxml.ns import qn

from config import load_config
from database import save_template, list_templates, get_default_template, delete_template as db_delete


# 支持的所有模板变量
SUPPORTED_VARIABLES = {
    "name": "学员姓名",
    "id_card": "身份证号",
    "school_short": "驾校代号",
    "school_name": "驾校全称",
    "registration_date": "报名日期",
    "license_type": "车型",
    "exam_stage": "当前考试阶段",
    "total_fee": "培训服务费总额",
    "actual_paid": "学员实际已交金额",
    "total_deduction": "总扣费",
    "refund": "应退金额",
    "contract_code": "合同编号",
    "reply_date": "回复日期（年月）",
    "training_hours": "培训时长描述",
    "special_warnings": "特殊退费情况说明",
}


def generate_reply_from_template(
    template_path: str,
    variables: dict,
    output_dir: str = "",
) -> dict:
    """
    使用 .docx 模板生成回复函。

    模板中使用 {{variable_name}} 占位符，将被替换为实际值。
    扣费明细通过 {{deductions}} 占位符插入表格。

    Args:
        template_path: .docx 模板文件路径
        variables: 变量字典
        output_dir: 输出目录

    Returns:
        {"success": True, "filepath": "...", "filename": "..."}
    """
    if not os.path.exists(template_path):
        return {"success": False, "error": f"模板文件不存在: {template_path}"}

    try:
        doc = Document(template_path)
    except Exception as e:
        return {"success": False, "error": f"模板文件打开失败: {e}"}

    # 替换段落中的变量
    for para in doc.paragraphs:
        _replace_variables_in_paragraph(para, variables)

    # 替换表格中的变量
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    _replace_variables_in_paragraph(para, variables)

    # 扣费明细表格替换 — 查找包含 {{deductions}} 的表格
    for table in doc.tables:
        for row_idx, row in enumerate(table.rows):
            for cell in row.cells:
                text = cell.text.strip()
                if "{{deductions}}" in text:
                    deductions = variables.get("deductions", [])
                    # 清除占位符行
                    table._tbl.remove(row._tr)
                    # 插入扣费行
                    for i, d in enumerate(deductions, 1):
                        item_name = d.get("item", "")
                        amount = d.get("amount", 0)
                        reason = d.get("reason", "")
                        formula = d.get("formula", "")
                        if formula and ("封顶" in formula or "调整" in formula):
                            reason = f"{reason}；计算：{formula}"
                        _add_deduction_row(table, row_idx + i - 1, i, item_name, amount, reason)
                    break

    # 保存
    if not output_dir:
        cfg = load_config()
        output_dir = cfg["paths"]["reply_dir"]
    os.makedirs(output_dir, exist_ok=True)

    name = variables.get("name", "")
    id_card = variables.get("id_card", "")
    school_short = variables.get("school_short", "")
    today_str = datetime.now().strftime("%Y%m%d")
    filename = f"{today_str}{name}{id_card}投诉回复函{school_short}.docx"
    filepath = os.path.join(output_dir, filename)

    if os.path.exists(filepath):
        base, ext = os.path.splitext(filepath)
        n = 1
        while os.path.exists(f"{base}({n}){ext}"):
            n += 1
        filepath = f"{base}({n}){ext}"

    doc.save(filepath)
    return {"success": True, "filepath": filepath, "filename": os.path.basename(filepath)}


def _replace_variables_in_paragraph(para, variables: dict):
    """替换段落中的所有 {{variable}} 占位符"""
    full_text = para.text
    if "{{" not in full_text:
        return

    # 处理扣费明细占位符（整行替换为表格）
    if "{{deductions}}" in full_text:
        # 由表格处理逻辑负责，这里只把行内文本清空
        pass
        return

    new_text = full_text
    for key, value in variables.items():
        placeholder = "{{" + key + "}}"
        str_value = str(value) if value is not None else ""
        new_text = new_text.replace(placeholder, str_value)

    if new_text != full_text:
        # 清除原段落所有 run
        for run in para.runs:
            run.text = ""
        # 写入新文本
        if para.runs:
            para.runs[0].text = new_text
        else:
            para.add_run(new_text)


def _add_deduction_row(table, index: int, num: int, item: str, amount: float, reason: str):
    """向表格插入扣费明细行"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    # index 是不含 tblPr/tblGrid 的行序位，换算成 lxml 实际插入位置
    pos = 0
    tr_seen = 0
    for child in table._tbl.iterchildren():
        if tr_seen >= index:
            break
        if child.tag == qn("w:tr"):
            tr_seen += 1
        pos += 1

    # 在指定位置插入新行
    row = OxmlElement("w:tr")
    table._tbl.insert(pos, row)

    # 序号
    _add_cell_to_row(row, str(num))
    # 扣费项目
    _add_cell_to_row(row, item)
    # 金额
    _add_cell_to_row(row, f"{amount:.0f}元")
    # 依据
    _add_cell_to_row(row, reason)


def _add_cell_to_row(row, text: str):
    """向行中添加单元格"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import nsdecls

    cell = OxmlElement("w:tc")
    row.append(cell)

    p = OxmlElement("w:p")
    cell.append(p)

    r = OxmlElement("w:r")
    p.append(r)

    t = OxmlElement("w:t")
    t.text = text
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    r.append(t)


def get_available_templates() -> list[dict]:
    """获取所有可用模板"""
    return list_templates()


def set_default_template(template_id: str) -> bool:
    """设置默认模板（目标不存在时拒绝，避免产生幽灵默认行）"""
    from database import get_db
    with get_db() as conn:
        exists = conn.execute("SELECT id FROM reply_templates WHERE id=?", (template_id,)).fetchone()
    if not exists:
        return False
    return bool(save_template({"id": template_id, "is_default": 1}))


def upload_template(filepath: str, name: str, description: str = "") -> dict:
    """上传新模板"""
    template_dir = _get_template_dir()
    os.makedirs(template_dir, exist_ok=True)

    ext = os.path.splitext(filepath)[1]
    new_filename = f"{uuid.uuid4().hex}{ext}"
    dest_path = os.path.join(template_dir, new_filename)

    import shutil
    shutil.copy2(filepath, dest_path)

    template_id = save_template({
        "name": name,
        "description": description,
        "template_path": dest_path,
        "variables": json.dumps(list(SUPPORTED_VARIABLES.keys()), ensure_ascii=False),
    })

    return {"success": True, "id": template_id}


def _get_template_dir() -> str:
    """获取模板存储目录"""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(base, "回复模板")
    os.makedirs(d, exist_ok=True)
    return d


def create_default_template() -> str:
    """
    创建默认 .docx 模板并存入数据库。
    返回模板文件路径。
    判重：已存在任一默认模板或同名「默认模板」记录时不再插入新行，
    仅在文件缺失时按既有记录的路径重建文件并复用。
    """
    template_dir = _get_template_dir()
    filepath = os.path.join(template_dir, "default_template.docx")

    default_tmpl = get_default_template()
    existing = default_tmpl or next(
        (t for t in list_templates() if t.get("name") == "默认模板"), None
    )
    if existing:
        existing_path = existing.get("template_path", "") or filepath
        if not os.path.exists(existing_path):
            _write_default_reply_doc(existing_path)
        return existing_path

    _write_default_reply_doc(filepath)

    # 注册到数据库
    save_template({
        "name": "默认模板",
        "description": "默认回复函模板，含 {{name}}, {{id_card}} 等变量",
        "template_path": filepath,
        "variables": json.dumps(list(SUPPORTED_VARIABLES.keys()), ensure_ascii=False),
        "is_default": 1,
    })

    return filepath


def _write_default_reply_doc(filepath: str):
    """生成默认回复函 .docx 文件并写入 filepath。"""
    doc = Document()

    # 设置默认字体
    style = doc.styles["Normal"]
    font = style.font
    font.name = "仿宋_GB2312"
    font.size = Pt(14)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "仿宋_GB2312")

    # 标题
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("关于{{name}}投诉的回复")
    run.font.name = "仿宋_GB2312"
    run.font.size = Pt(18)
    run.bold = True
    run.element.rPr.rFonts.set(qn("w:eastAsia"), "仿宋_GB2312")
    doc.add_paragraph()

    _add_t_para(doc, "东莞市交通运输局：")
    _add_t_para(doc, f"经我驾校调查核实，投诉人{{{{name}}}}（身份证号：{{{{id_card}}}}），于{{{{registration_date}}}}在{{{{school_name}}}}网点报名{{{{license_type}}}}驾照培训。现收到学员投诉，要求退费。")
    _add_t_para(doc, f"据了解，学员签订合同培训服务费总额为{{{{total_fee}}}}元，实际已交费用{{{{actual_paid}}}}元，目前进度处于：{{{{exam_stage}}}}阶段，{{{{training_hours}}}}。")
    _add_t_para(doc, f"按照《东莞市机动车驾驶员培训服务合同》（合同编码：{{{{contract_code}}}}）退学退费相关约定及已确认的扣费明细，核算扣费如下：")

    # 扣费明细表格
    table = doc.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    hdr[0].text = "序号"
    hdr[1].text = "扣费项目"
    hdr[2].text = "金额"
    hdr[3].text = "依据"
    # 占位行
    row = table.add_row().cells
    row[0].text = "{{deductions}}"

    doc.add_paragraph()
    _add_t_para(doc, f"总扣费：{{{{total_deduction}}}}元")
    doc.add_paragraph()
    _add_t_para(doc, f"学员实际已交费用{{{{actual_paid}}}}元，应退回：{{{{refund}}}}元。")
    _add_t_para(doc, "{{special_warnings}}")
    _add_t_para(doc, "以上扣费严格依据双方签订的《东莞市机动车驾驶员培训服务合同》、已确认的扣费明细及已核实的学员培训、考试进度计算，我驾校愿意按案件最终处理结果继续办理。")

    for _ in range(3):
        doc.add_paragraph()

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = p.add_run("驾校名称：东莞市快捷汽车驾驶员培训有限公司（公章）")
    run.font.name = "仿宋_GB2312"
    run.font.size = Pt(14)
    run.element.rPr.rFonts.set(qn("w:eastAsia"), "仿宋_GB2312")

    doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = p.add_run("{{reply_date}}年  月  日")
    run.font.name = "仿宋_GB2312"
    run.font.size = Pt(14)
    run.element.rPr.rFonts.set(qn("w:eastAsia"), "仿宋_GB2312")

    doc.save(filepath)


def _add_t_para(doc, text: str):
    """添加左对齐段落（首行缩进2字符）"""
    from docx.shared import Pt as PtSize
    p = doc.add_paragraph()
    p.paragraph_format.first_line_indent = PtSize(28)
    run = p.add_run(text)
    run.font.name = "仿宋_GB2312"
    run.font.size = PtSize(14)
    run.element.rPr.rFonts.set(qn("w:eastAsia"), "仿宋_GB2312")
