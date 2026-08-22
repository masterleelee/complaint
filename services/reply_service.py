"""回复函生成服务 - Word 文档（支持模板替换 + 代码生成回退）"""
import os
from datetime import datetime
from docx import Document
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from config import load_config
from database import get_default_template
from services.template_service import generate_reply_from_template
from utils.logger import system_logger


def generate_reply(
    name: str,
    id_card: str,
    school_short: str,
    registration_date: str,
    license_type: str,
    school_name: str,
    exam_stage: str,
    total_fee: float,
    deductions: list[dict],
    total_deduction: float,
    refund: float,
    contract_code: str = "",
    training_hours: dict = None,
    output_dir: str = "",
    template_id: str = "",
    visit_summary: str = "",
    actual_paid: float = 0,
    complaint_summary: str = "",
    final_outcome: str = "",
    communications: list[dict] = None,
    special_warnings: list = None,
) -> dict:
    """
    生成回复函Word文档。

    如果存在默认模板或指定 template_id，优先使用模板替换生成；
    否则回退到代码生成。

    返回 {"success": True, "filepath": "...", "filename": "..."} 或 {"success": False, "error": "..."}
    """
    actual_paid = float(actual_paid or 0)
    if actual_paid <= 0:
        actual_paid = float(total_fee or 0)
    communications = communications or []
    special_warnings = special_warnings or []
    communication_summary = "；".join(
        str(record.get("summary", "")).strip()
        for record in communications
        if str(record.get("summary", "")).strip()
    )
    warning_summary = "；".join(
        str(item.get("message", item)) if isinstance(item, dict) else str(item)
        for item in special_warnings
        if item
    )

    # ── 尝试使用模板 ──
    try:
        tmpl = None
        if template_id:
            # 按 template_id 查找特定模板
            from database import list_templates
            all_tmpls = list_templates()
            for t in all_tmpls:
                if str(t.get("id", "")) == str(template_id):
                    tmpl = t
                    break
        if not tmpl:
            tmpl = get_default_template()

        if tmpl and tmpl.get("template_path") and os.path.exists(tmpl["template_path"]):
            hours_desc = "未进行过实际操作培训（未练过车）"
            if training_hours:
                parts = []
                for subject, hours in training_hours.items():
                    if hours and hours != "0时0分":
                        parts.append(f"{subject}培训{hours}")
                if parts:
                    hours_desc = "、".join(parts)

            return generate_reply_from_template(
                template_path=tmpl["template_path"],
                variables={
                    "name": name,
                    "id_card": id_card,
                    "school_short": school_short,
                    "school_name": school_name,
                    "registration_date": registration_date,
                    "license_type": license_type,
                    "exam_stage": exam_stage,
                    "total_fee": f"{total_fee:.0f}",
                    "actual_paid": f"{actual_paid:.0f}",
                    "total_deduction": f"{total_deduction:.0f}",
                    "refund": f"{refund:.0f}",
                    "contract_code": contract_code,
                    "training_hours": hours_desc,
                    "reply_date": str(datetime.now().year),
                    "deductions": deductions,
                    "visit_summary": visit_summary,
                    "complaint_summary": complaint_summary,
                    "final_outcome": final_outcome,
                    "communication_summary": communication_summary,
                    "special_warnings": warning_summary,
                },
                output_dir=output_dir,
            )
    except Exception as e:
        system_logger.warning("[回复函] 模板生成失败，回退代码生成: %s", e)

    # ── 回退：代码生成 ──
    try:
        doc = Document()

        # 设置默认字体
        style = doc.styles["Normal"]
        font = style.font
        font.name = "仿宋_GB2312"
        font.size = Pt(14)
        style.element.rPr.rFonts.set(qn("w:eastAsia"), "仿宋_GB2312")

        # ── 标题 ──
        title = doc.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = title.add_run(f"关于{name}投诉的回复")
        run.font.name = "仿宋_GB2312"
        run.font.size = Pt(18)
        run.bold = True
        run.element.rPr.rFonts.set(qn("w:eastAsia"), "仿宋_GB2312")

        doc.add_paragraph()  # 空行

        # ── 收件方 ──
        _add_paragraph(doc, "东莞市交通运输局：")

        # ── 第一段：学员基本情况 ──
        _add_paragraph(
            doc,
            f"经我驾校调查核实，投诉人{name}（身份证号：{id_card}），"
            f"于{registration_date}在{school_name}网点报名{license_type}驾照培训。"
            f"现收到学员投诉，投诉事项概述：{complaint_summary or '学员对培训服务提出异议'}。"
        )

        # ── 第二段：培训费及进度 ──
        hours_desc = "未进行过实际操作培训（未练过车）"
        if training_hours:
            parts = []
            for subject, hours in training_hours.items():
                if hours and hours != "0时0分":
                    parts.append(f"{subject}培训{hours}")
            if parts:
                hours_desc = "、".join(parts)

        _add_paragraph(
            doc,
            f"据了解，学员签订合同培训服务费总额为{total_fee:.0f}元，"
            f"实际已交费用{actual_paid:.0f}元，"
            f"目前进度处于：{exam_stage}阶段，{hours_desc}。"
        )

        # ── 第三段：扣费明细 ──
        contract_ref = f"（合同编码：{contract_code}）" if contract_code else ""
        _add_paragraph(
            doc,
            f"按照《东莞市机动车驾驶员培训服务合同》{contract_ref}"
            f"退学退费相关约定及已确认的扣费明细，核算扣费如下："
        )

        # 逐项扣费 - 简化依据，只保留合同条款
        for i, d in enumerate(deductions, 1):
            item_name = d.get("item", "")
            amount = d.get("amount", 0)
            reason = d.get("reason", "")
            
            # 简化依据：只保留"合同第X条"部分，截断长文本
            if reason:
                # 提取合同条款引用（如"合同第七条第一款"）
                import re
                match = re.search(r'合同第[一二三四五六七八九十\d]+条[^：:；]*', reason)
                if match:
                    simplified_reason = match.group(0)
                else:
                    # 如果太长，截断
                    simplified_reason = reason[:30] + "..." if len(reason) > 30 else reason
                formula = d.get("formula", "")
                if formula and ("封顶" in formula or "调整" in formula):
                    simplified_reason = f"{simplified_reason}；计算：{formula}"
                text = f"{i}、{item_name}（{simplified_reason}）：{amount:.0f}元"
            else:
                text = f"{i}、{item_name}：{amount:.0f}元"
            _add_paragraph(doc, text)

        doc.add_paragraph()

        # ── 总扣费（加粗）──
        p = doc.add_paragraph()
        deduction_items = " + ".join([f"{d.get('amount', 0):.0f}" for d in deductions])
        run = p.add_run(f"总扣费：{deduction_items} = {total_deduction:.0f}元")
        run.font.name = "仿宋_GB2312"
        run.font.size = Pt(14)
        run.bold = True
        run.element.rPr.rFonts.set(qn("w:eastAsia"), "仿宋_GB2312")

        doc.add_paragraph()

        # ── 应退金额 ──
        _add_paragraph(
            doc,
            f"学员实际已交费用{actual_paid:.0f}元，应退回："
            f"实际已交费用扣减总扣费后，按不低于0元处理，金额为{refund:.0f}元。"
        )

        if communication_summary:
            _add_paragraph(doc, f"沟通处理情况：{communication_summary}。")
        if final_outcome:
            _add_paragraph(doc, f"本案最终处理结果：{final_outcome}。")
        if warning_summary:
            _add_paragraph(doc, f"合同核验说明：{warning_summary}。")

        _add_paragraph(
            doc,
            "以上扣费严格依据双方签订的《东莞市机动车驾驶员培训服务合同》、"
            "已确认的扣费明细及已核实的学员培训、考试进度计算，"
            "我驾校将依据案件最终处理结果继续办理。"
        )

        # ── 回访情况 ──
        if visit_summary:
            _add_paragraph(doc, f"回访情况：{visit_summary}")

        # 空行
        for _ in range(3):
            doc.add_paragraph()

        # ── 落款 ──
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        run = p.add_run("驾校名称：东莞市快捷汽车驾驶员培训有限公司（公章）")
        _set_font(run)

        doc.add_paragraph()

        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        now = datetime.now()
        run = p.add_run(f"{now.year}年  月  日")
        _set_font(run)

        # ── 保存 ──
        if not output_dir:
            cfg = load_config()
            output_dir = cfg["paths"]["reply_dir"]
        os.makedirs(output_dir, exist_ok=True)

        today_str = datetime.now().strftime("%Y%m%d")
        base_filename = f"{today_str}{name}{id_card}投诉回复函{school_short}"
        filepath = os.path.join(output_dir, base_filename + ".docx")

        # Versioning: if file exists, rename existing as _v1, new as _v2
        if os.path.exists(filepath):
            existing_versions = []
            for f in os.listdir(output_dir):
                if f.startswith(base_filename) and f.endswith(".docx"):
                    existing_versions.append(f)
            existing_versions.sort()

            # Find max version number from existing files
            max_v = 0
            for f in existing_versions:
                import re
                m = re.search(r'_v(\d+)\.docx$', f)
                if m:
                    v = int(m.group(1))
                    if v > max_v:
                        max_v = v
                elif f == base_filename + ".docx":
                    max_v = 1  # bare file exists, will become v1

            if max_v == 0:
                # No versioned files, just rename current
                os.rename(filepath, os.path.join(output_dir, base_filename + "_v1.docx"))
                max_v = 1

            new_version = max_v + 1
            filepath = os.path.join(output_dir, f"{base_filename}_v{new_version}.docx")

        doc.save(filepath)

        # Collect all version files for return
        all_versions = []
        for f in os.listdir(output_dir):
            if f.startswith(base_filename) and f.endswith(".docx"):
                all_versions.append({"filename": f, "filepath": os.path.join(output_dir, f)})
        all_versions.sort(key=lambda x: x["filename"])

        return {
            "success": True,
            "filepath": filepath,
            "filename": os.path.basename(filepath),
            "all_versions": all_versions,
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


def _add_paragraph(doc: Document, text: str) -> None:
    """添加正文段落，统一格式（首行缩进2字符）"""
    p = doc.add_paragraph()
    p.paragraph_format.first_line_indent = Pt(28)
    run = p.add_run(text)
    _set_font(run)


def _set_font(run, size=14, bold=False):
    """设置字体为仿宋_GB2312"""
    run.font.name = "仿宋_GB2312"
    run.font.size = Pt(size)
    run.bold = bold
    run.element.rPr.rFonts.set(qn("w:eastAsia"), "仿宋_GB2312")
