"""投诉登记表生成服务（数据源统一为工单处理情况 + 已确认费用核算）"""
import math
import os
import re
import unicodedata
from datetime import datetime
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_ROW_HEIGHT_RULE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from config import load_config
from services.archive_service import build_archive_dir

LABEL_FILL = "F2F2F2"
NOTE_GRAY = RGBColor(0x59, 0x59, 0x59)
SIGN_BLANK = "____________"

# ── 分区版式常量（docx 与前端预览 app.js regSecStyle 必须同步）─────────────
# 分区定高（cm）。投诉内容/投诉处理按库内最长真实文本留足，避免文字撑破定高；
# 回访记录/投诉诉求/费用核算压缩，腾出的空间补给长文本区。
# ⚠️ 改这里必须同步改 static/js/app.js 的 regSecStyle（cm → px，96dpi：1cm≈37.8px）。
SECTION_HEIGHTS_CM = {
    "投诉内容": 6.2,
    "投诉诉求": 2.2,
    "费用核算": 1.4,
    "投诉处理": 7.2,
    "回访记录": 2.4,
}
_SEC_TEXT_W_CM = 16.8   # 分区单元格可用文本宽度（A4 17.2cm − 单元格左右内边距 0.38cm）
_SEC_INDENT_CM = 0.74   # 正文首行缩进 2 字符（21pt，level-0 默认字号 10.5pt 下）
_SEC_MARGIN_CM = 0.50   # 单元格上下内边距 + 段尾余量 + 版式估算安全余量
# 字体行高系数：Word/WPS 实际行高 = 字号 × 行距 × 字体度量系数（宋体 ≈1.30），
# 不是 字号 × 行距。漏掉该系数会把内容高度低估 ~25%，导致空段占位多塞、行被撑破翻页
# （赵鹏超工单：处理行 12 个空段实占 ~5.8cm，备注被顶到第二页）。
_FONT_LINE_FACTOR = 1.30
_EMPTY_LINE_CM = 0.50   # 空段占位高度（10.5pt 宋体单行 ≈ 10.5×1.0×1.30 = 13.65pt = 0.48cm，取 0.50 保守）
_PT_TO_CM = 0.0352778   # 1pt → cm

# ── 单页 A4 自适应预算（内容超量时按压缩链逐级收紧，保证 1 页）──────────────
# 设计：生成前先按「与 _section_cell 一致的渲染口径」预算每个分区高度，
# 若 Σ分区高 + 固定占位 ≤ 页面可用高则保持 level-0 美观版式；否则逐级收紧
# （页边距 → 行距 → 字号 → 信息行高 → 标题字号），直到装下为止。
PAGE_W_CM = 21.0
_CELL_LR_PAD_CM = 0.38        # 分区单元格左右内边距合计（216 twips）
PAGE_H_CM = 29.7
_SEC_BUDGET_SAFE_CM = 0.40   # 安全余量：避免 Word 取整导致临界翻页
# 压缩链（lvl 升序 = 越紧）。mlr/top/bot=页边距；ls_head/ls_body=分区行距；
# font=分区正文/签名字号；info=信息区行高；title=标题字号。
_FIT_STEPS = [
    {"lvl": 0, "mlr": 1.9, "m_top": 1.2, "m_bot": 1.0, "ls_head": 1.25, "ls_body": 1.30, "font": 10.5, "info": 0.85, "title": 16},
    {"lvl": 1, "mlr": 0.9, "m_top": 0.9, "m_bot": 0.7, "ls_head": 1.25, "ls_body": 1.30, "font": 10.5, "info": 0.85, "title": 16},
    {"lvl": 2, "mlr": 0.9, "m_top": 0.9, "m_bot": 0.7, "ls_head": 1.12, "ls_body": 1.15, "font": 10.5, "info": 0.85, "title": 16},
    {"lvl": 3, "mlr": 0.9, "m_top": 0.9, "m_bot": 0.7, "ls_head": 1.12, "ls_body": 1.15, "font": 9.5,  "info": 0.72, "title": 14},
    {"lvl": 4, "mlr": 0.7, "m_top": 0.7, "m_bot": 0.5, "ls_head": 1.12, "ls_body": 1.15, "font": 9.0,  "info": 0.72, "title": 14},
]

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
    # 未定最终投诉结果时留空，由处理人在纸面/预览上人工填写，不再自动兜底话术
    return ""


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
    overrides: dict = None,
) -> dict:
    """构建登记表内容数据（docx 生成与页面预览共用的唯一数据源）。

    ai_sections：AI 整理的三段 {complaint_content, complaint_demands, handling_summary}，
    由调用方（预览/归档前）统一生成传入；缺失时按 原文 → 摘要 → 类型话术 逐级兜底。
    fields 前 4 行为六元组 (标签,值,标签,值,标签,值)，其余为四元组 (标签,值,"","")，
    四元组表示该行值独占整行。

    overrides：预览纸面上用户编辑后的覆盖值（所见即所得）。两种键形：
      - "标签" → 整体覆盖该标签的值（信息区单值字段、单行分区）
      - "标签:行号" → 覆盖分区值中的某一行（如 "投诉处理:1"）
      - "__title__" / "__no_line__" → 覆盖标题 / 编号行
    """
    ai_sections = ai_sections or {}
    complaint_type = str(ticket_data.get("complaint_type") or "").strip()

    # 科目二/三学时（来自第三系统审核有效学时，未产生则为空 → 按业务口径填 0）
    hours = ticket_data.get("training_hours") if isinstance(ticket_data.get("training_hours"), dict) else {}
    hours_sub2 = str(hours.get("科目二") or "").strip() or "0"
    hours_sub3 = str(hours.get("科目三") or "").strip() or "0"

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
    # 处理经过：AI 归纳 → ④处理情况原文 → 类型话术；文本内换行拍平，整段连排不分行
    notes = str(ai_sections.get("handling_summary") or "").strip() \
        or str(handling_notes or "").strip() \
        or HANDLING_FALLBACKS.get(complaint_type, "")
    notes = re.sub(r"\s*\n+\s*", "", notes)
    if notes:
        proc_lines.append(f"处理经过：{notes}")
    opinion = re.sub(r"\s*\n+\s*", "", str(negotiation_outcome or "").strip())
    if opinion:
        proc_lines.append(f"学员意见：{opinion}")
    conclusion = _build_outcome_text(final_outcome)
    if final_outcome:
        conclusion = f"最终投诉结果：{final_outcome}。" + conclusion
    if conclusion:
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
    # 纸面编辑覆盖（所见即所得）：按 标签 / 标签:行号 回填用户在预览纸面上的修改
    fields = _apply_overrides(fields, overrides)

    data = {
        "title": "学员投诉登记表",
        "no_line": f"编号：{_ticket_no(ticket_data)} 号",
        "fields": fields,
        "visit_summary": notes,
        "handler": handler,
        "today_cn": today_cn,
    }
    if overrides:
        t = str(overrides.get("__title__") or "").strip()
        if t:
            data["title"] = t
        n = str(overrides.get("__no_line__") or "").strip()
        if n:
            data["no_line"] = n
    return data


def _apply_overrides(fields: list, overrides: dict) -> list:
    """把预览纸面上的编辑覆盖到 fields。

    覆盖规则：
      - "标签" → 整体替换该标签的值（信息区标签在各行内唯一定位；分区为整值替换）
      - "标签:行号" → 替换分区值按 \\n 拆行后的指定行（行号越界时自动补空行）
      - "__title__" / "__no_line__" 不属于 fields，由调用方按需读取
    空值覆盖（用户清空单元格）同样生效；非法键形忽略。
    """
    if not overrides:
        return fields
    plain: dict = {}
    lines: dict = {}
    for k, v in (overrides or {}).items():
        k = str(k).strip()
        if k in ("__title__", "__no_line__"):
            plain[k] = str(v)
            continue
        lab, sep, idx = k.rpartition(":")
        if sep and idx.isdigit():
            lines.setdefault(lab, {})[int(idx)] = str(v)
        else:
            plain[lab or k] = str(v)

    out = []
    for row in fields:
        if len(row) == 6:
            # 六元组信息行：三对 (标签,值) 逐一检查覆盖
            k1, v1, k2, v2, k3, v3 = row
            v1 = plain.get(k1, v1)
            v2 = plain.get(k2, v2)
            v3 = plain.get(k3, v3)
            out.append((k1, v1, k2, v2, k3, v3))
            continue
        label, value = row[0], row[1]
        if label in lines:
            parts = str(value or "").split("\n")
            for idx, v in lines[label].items():
                while len(parts) <= idx:
                    parts.append("")
                parts[idx] = v
            out.append((label, "\n".join(parts), row[2], row[3]))
        elif label in plain:
            out.append((label, plain[label], row[2], row[3]))
        else:
            out.append(row)
    return out


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


def _text_em(text: str) -> float:
    """文本折算为全角字符数（全角/中日韩 = 1.0，半角 = 0.5），用于宋体排版宽度估算。"""
    return sum(1.0 if unicodedata.east_asian_width(c) in ("W", "F") else 0.5
               for c in str(text or ""))


def _rendered_lines(text: str, font_pt: float = 10.5, indent_cm: float = 0.0,
                    text_w_cm: float = None) -> int:
    """估算一段宋体文本在分区单元格内折行后的渲染行数。"""
    if not str(text or "").strip():
        return 1
    text_w = text_w_cm if text_w_cm is not None else _SEC_TEXT_W_CM
    char_cm = font_pt * _PT_TO_CM
    cap_first = max(1.0, (text_w - indent_cm) / char_cm)   # 首行受缩进影响
    cap_rest = max(1.0, text_w / char_cm)
    n = _text_em(text)
    if n <= cap_first:
        return 1
    return 1 + math.ceil((n - cap_first) / cap_rest)


def _para_height_cm(text: str, font_pt: float = 10.5, line_spacing: float = 1.25,
                    indent_cm: float = 0.0, space_after_pt: float = 0.0,
                    text_w_cm: float = None) -> float:
    """段落渲染高度（cm）：行数 × 字号 × 行距 × 字体行高系数 + 段后距。

    必须乘 _FONT_LINE_FACTOR（宋体 ≈1.30）：Word/WPS 的「多倍行距」作用于字体
    度量行高而非字面字号，漏乘会系统性低估 ~25%（赵鹏超工单翻页根因之一）。
    """
    lines = _rendered_lines(text, font_pt, indent_cm=indent_cm, text_w_cm=text_w_cm)
    return (lines * font_pt * line_spacing * _FONT_LINE_FACTOR + space_after_pt) * _PT_TO_CM


def _spacer_count(row_h_cm: float, used_cm: float) -> int:
    """固定行高内剩余空间可容纳的空段数，用于把签名行顶到单元格底部。

    旧实现按「逻辑行数」估算占用：长文本换行后实际占用远大于估算值，会在本已
    撑破定高的行里再塞入空段（张玉富 363 字处理经过 → 9 个空段，行高 9.25cm
    / 定高 7.0cm）。现改为按内容实际渲染高度计算，长文本时空段自动归零。
    """
    return int(max(0.0, row_h_cm - _SEC_MARGIN_CM - used_cm) / _EMPTY_LINE_CM)


def _sec_text_w_cm(mlr_cm: float) -> float:
    """分区单元格可用文本宽（cm）：A4 文本宽 − 单元格左右内边距。随页边距变化。"""
    return PAGE_W_CM - 2 * mlr_cm - _CELL_LR_PAD_CM


def _sec_indent_cm(font_pt: float) -> float:
    """正文首行缩进 2 字符（cm），随字号等比。"""
    return font_pt * 2 * _PT_TO_CM


def _section_need_cm(label: str, value: str, sign_line: str, step: dict) -> float:
    """分区单元格真实渲染高度（cm），口径必须与 _section_cell 完全一致。

    口径：投诉内容/投诉诉求/费用核算 → 整体单段 [value]（内部 \\n 不拆段）；
         投诉处理 → value.split('\\n') 拆多段；回访记录 → 无正文段，仅签名行。
    否则会与真实 docx 渲染高度漂移（梁思念曾因此被高估 4.5cm）。
    """
    text_w = _sec_text_w_cm(step["mlr"])
    indent = _sec_indent_cm(step["font"])
    header = label + "："
    body_lines = [ln for ln in (str(value or "").split("\n") if label == "投诉处理"
                                else [str(value or "")]) if ln.strip()]
    first = body_lines[0] if body_lines else ""
    used = _para_height_cm(header + first, font_pt=step["font"], line_spacing=step["ls_head"],
                           indent_cm=indent, space_after_pt=4, text_w_cm=text_w)
    for ln in body_lines[1:]:
        used += _para_height_cm(ln, font_pt=step["font"], line_spacing=step["ls_body"],
                                indent_cm=indent, space_after_pt=2, text_w_cm=text_w)
    if sign_line:
        used += _para_height_cm(sign_line, font_pt=step["font"], line_spacing=step["ls_body"],
                                indent_cm=0.0, text_w_cm=text_w)
    return used + _SEC_MARGIN_CM


def _non_table_cm(step: dict) -> float:
    """表格外固定占位（cm）：标题 + 编号行 + 底部备注，随标题字号变化。

    行高同样乘字体行高系数（与 _para_height_cm 口径一致）。
    """
    title = step["title"] * 1.15 * _FONT_LINE_FACTOR + 4
    no = 10.5 * 1.15 * _FONT_LINE_FACTOR + 3
    note = 6 + 9 * 1.15 * _FONT_LINE_FACTOR
    return (title + no + note) * _PT_TO_CM


def _fit_section_heights(fields: list, handler: str, today: str):
    """单页 A4 自适应：返回 (alloc{label:行高cm}, step, used_cm, budget_cm)。

    alloc 为各分区实际行高——内容不超定高时用理想定高，超则用 need 下限；
    若 Σ高度超页面预算，逐级收紧 _FIT_STEPS（页边距→行距→字号→信息行高→标题字号）
    直到装下；极端超长则降到末档按 need 兜底，绝不强行撑破翻页。
    """
    signs = {
        "投诉处理": f"处理人签名：{handler}　　日期：{today}",
        "回访记录": f"回访人：{SIGN_BLANK}　　______年____月____日",
    }
    secs = [(label, str(value or ""), signs.get(label, ""))
            for label, value, _, _ in fields[_INFO_ROWS:]]
    for step in _FIT_STEPS:
        needs = {lbl: _section_need_cm(lbl, val, sign, step) for lbl, val, sign in secs}
        alloc = {lbl: max(needs[lbl], SECTION_HEIGHTS_CM.get(lbl, 1.5)) for lbl, _, _ in secs}
        used = step["info"] * _INFO_ROWS + sum(alloc.values()) + _non_table_cm(step)
        budget = PAGE_H_CM - step["m_top"] - step["m_bot"] - _SEC_BUDGET_SAFE_CM
        if used <= budget:
            return alloc, step, used, budget
    # 极端超长：末档按 need 下限兜底（不撑破）
    step = _FIT_STEPS[-1]
    needs = {lbl: _section_need_cm(lbl, val, sign, step) for lbl, val, sign in secs}
    alloc = {lbl: needs[lbl] for lbl, _, _ in secs}
    used = step["info"] * _INFO_ROWS + sum(alloc.values()) + _non_table_cm(step)
    budget = PAGE_H_CM - step["m_top"] - step["m_bot"] - _SEC_BUDGET_SAFE_CM
    return alloc, step, used, budget


def _section_cell(cell, header, body_lines, sign_line="", row_h_cm=0.0,
                  font_pt: float = 10.5, ls_head: float = 1.25, ls_body: float = 1.30,
                  indent_cm: float = None, text_w_cm: float = None):
    """全宽分区单元格：加粗小标题与正文首行同段；正文段落首行缩进 2 字符；
    签名行置于右下角（右对齐、不缩进）。

    font_pt/ls_*/text_w_cm 由压缩链档位驱动，必须与 _section_need_cm 预算口径一致；
    text_w_cm 必须随页边距传入——否则渲染用默认宽会算出比预算更多的行 → 高度虚高翻页。
    """
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
    indent = indent_cm if indent_cm is not None else _sec_indent_cm(font_pt)
    body_lines = [str(ln).strip() for ln in body_lines if str(ln or "").strip()]

    head_p = cell.paragraphs[0]
    head_p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    head_p.paragraph_format.line_spacing = ls_head
    head_p.paragraph_format.space_after = Pt(4)
    head_p.paragraph_format.first_line_indent = Pt(round(font_pt * 2))   # 首行缩进 2 字符
    _style_run(head_p.add_run(header), size=font_pt, bold=True)
    first_line = body_lines[0] if body_lines else ""
    if first_line:
        _style_run(head_p.add_run(first_line), size=font_pt)
    used_cm = _para_height_cm(header + first_line, font_pt=font_pt, line_spacing=ls_head,
                              indent_cm=indent, space_after_pt=4, text_w_cm=text_w_cm)

    for line in body_lines[1:]:
        bp = cell.add_paragraph()
        bp.alignment = WD_ALIGN_PARAGRAPH.LEFT
        bp.paragraph_format.line_spacing = ls_body
        bp.paragraph_format.space_after = Pt(2)
        bp.paragraph_format.first_line_indent = Pt(round(font_pt * 2))
        _style_run(bp.add_run(line), size=font_pt)
        used_cm += _para_height_cm(line, font_pt=font_pt, line_spacing=ls_body,
                                   indent_cm=indent, space_after_pt=2, text_w_cm=text_w_cm)

    if sign_line:
        # 签名行自身占位也要计入，否则长文本时空段会把签名顶出单元格
        used_cm += _para_height_cm(sign_line, font_pt=font_pt, line_spacing=ls_body,
                                   indent_cm=0.0, text_w_cm=text_w_cm)
        for _ in range(_spacer_count(row_h_cm, used_cm)):
            sp = cell.add_paragraph()
            sp.paragraph_format.line_spacing = 1.0
            sp.paragraph_format.space_after = Pt(0)
        sp = cell.add_paragraph()
        sp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        sp.paragraph_format.line_spacing = ls_body
        # 显式清零段后距：默认样式带 w:after=200（10pt），不清理会成预算外高度
        sp.paragraph_format.space_after = Pt(0)
        sp.paragraph_format.first_line_indent = Pt(0)    # 签名行右下对齐，不随正文缩进
        _style_run(sp.add_run(sign_line), size=font_pt)


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
    overrides: dict = None,
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
            overrides=overrides,
        )
        handler = data["handler"]
        today_cn = data["today_cn"]

        doc = Document()
        style = doc.styles["Normal"]
        style.font.name = "宋体"
        style.font.size = Pt(10.5)
        style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

        # 单页 A4 自适应：生成前按内容预算分区行高与压缩档（保证 1 页）
        alloc, fit_step, fit_used, fit_budget = _fit_section_heights(
            data["fields"], data["handler"], data["today_cn"])

        section = doc.sections[0]
        section.page_width = Cm(21)
        section.page_height = Cm(29.7)
        section.top_margin = Cm(fit_step["m_top"])
        section.bottom_margin = Cm(fit_step["m_bot"])
        section.left_margin = Cm(fit_step["mlr"])
        section.right_margin = Cm(fit_step["mlr"])
        # 移除模板自带的 docGrid（linePitch=360，18pt 网格）：WPS 会把每个段落行
        # 吸附到 18pt 整数倍，空段占位实占远超估算 → 行被撑破、备注掉页。
        # 删除后按自然行高排版，与上方预算口径一致。
        _sect_pr = section._sectPr
        for _grid in _sect_pr.findall(qn("w:docGrid")):
            _sect_pr.remove(_grid)

        # 标题（加字距）
        title = doc.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title.paragraph_format.space_after = Pt(4)
        run = title.add_run(data["title"])
        _style_run(run, size=fit_step["title"], bold=True)
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

        # A4 文本宽 = 21cm − 1.9cm×2 = 17.2cm ≈ 9752 twips
        # 列宽分配：每个单元格内容都要单行不换行；标签 10pt / 值统一 9pt（小五） 视觉整齐
        # 列内容宽 = 列宽 − 左右内边距 216 twips (10.8pt)，最长值实测：
        #   标签 1400 twips (2.47cm → 内容 59.2pt)：容纳「科目二学时」5 字 10pt = 50pt ✓
        #   v1 1815 twips (3.20cm → 内容 79.95pt)：投诉日期「2026年8月3日」6 em = 54pt / 手机号 11 位 = 49.5pt ✓
        #   v2 1875 twips (3.31cm → 内容 82.95pt)：身份证号 18 位 9pt 数字 = 81pt ✓（余 2pt）
        #   v3 1860 twips (3.28cm → 内容 82.2pt)：投诉对象最长「厚街科技工业园分校」9 字 = 81pt ✓（余 1.2pt）
        #     ↑ 旧值 1825 内容仅 80.4pt，9 字网点名（如阳金林工单）会折成两行把信息行撑高
        #   总宽 3×1400 + 1815 + 1875 + 1860 = 9750 twips（贴满文本宽，仅留 2 twips 舍入余量）
        col_tw = [1400, 1815, 1400, 1875, 1400, 1860]
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
            row.height = Cm(fit_step["info"])
            cells = row.cells
            # 标签列 1400 twips (2.47cm) 容纳 5 字 10pt + padding；值列 1825/1870 twips 容纳 9pt 内容
            for c, label in ((cells[0], k1), (cells[2], k2), (cells[4], k3)):
                _set_cell_shading(c, LABEL_FILL)
                _cell_write(c, [(label, WD_ALIGN_PARAGRAPH.CENTER, 10, True, 1.15, 0)],
                            valign=center_valign)
            # 值列字号统一 9pt（小五），身份证号不再单独缩小——9pt 18 位数字在 1870 twips 列内单行可放
            for c, label, val in ((cells[1], k1, v1), (cells[3], k2, v2), (cells[5], k3, v3)):
                val_blocks = [(ln, WD_ALIGN_PARAGRAPH.CENTER, 9, False, 1.15, 0)
                              for ln in str(val).split("\n")]
                _cell_write(c, val_blocks, valign=center_valign)

        # 第 5 行起：全宽分区（行高来自 _fit_section_heights 自适应结果，与 app.js regSecStyle 同步）
        _sec_tw = _sec_text_w_cm(fit_step["mlr"])
        for i in range(_INFO_ROWS, n_rows):
            label, value, _, _ = data["fields"][i]
            row = table.rows[i]
            h_cm = alloc.get(label, 1.5)
            row.height = Cm(h_cm)
            merged = merge_full(row)
            merged.width = full_w

            if label == "投诉内容":
                _section_cell(merged, "投诉内容：", [value], row_h_cm=h_cm,
                              font_pt=fit_step["font"], ls_head=fit_step["ls_head"],
                              ls_body=fit_step["ls_body"], text_w_cm=_sec_tw)
            elif label == "投诉诉求":
                _section_cell(merged, "投诉诉求：", [value], row_h_cm=h_cm,
                              font_pt=fit_step["font"], ls_head=fit_step["ls_head"],
                              ls_body=fit_step["ls_body"], text_w_cm=_sec_tw)
            elif label == "费用核算":
                _section_cell(merged, "费用核算：", [value], row_h_cm=h_cm,
                              font_pt=fit_step["font"], ls_head=fit_step["ls_head"],
                              ls_body=fit_step["ls_body"], text_w_cm=_sec_tw)
            elif label == "投诉处理":
                sign = f"处理人签名：{handler}　　日期：{today_cn}"
                _section_cell(merged, "投诉处理：", str(value).split("\n"),
                              sign_line=sign, row_h_cm=h_cm,
                              font_pt=fit_step["font"], ls_head=fit_step["ls_head"],
                              ls_body=fit_step["ls_body"], text_w_cm=_sec_tw)
            elif label == "回访记录":
                sign = f"回访人：{SIGN_BLANK}　　______年____月____日"
                _section_cell(merged, "回访记录：", [], sign_line=sign, row_h_cm=h_cm,
                              font_pt=fit_step["font"], ls_head=fit_step["ls_head"],
                              ls_body=fit_step["ls_body"], text_w_cm=_sec_tw)

        # 底部备注
        note = doc.add_paragraph()
        note.paragraph_format.space_before = Pt(6)
        note_run = note.add_run(
            "备注：一般投诉事项应在 3 日内处理完毕并填写本表；重大投诉事项需及时报上级部门处理。"
        )
        _style_run(note_run, size=9, color=NOTE_GRAY)

        if not output_dir:
            cfg = load_config()
            root = cfg.get("archive_root") or "案件归档"
            case_dir, reg_target, _ = build_archive_dir(ticket_data, root=root)
            output_dir = case_dir
            filepath = reg_target
        else:
            filepath = os.path.join(output_dir, "投诉登记表.docx")
        os.makedirs(output_dir, exist_ok=True)
        # 直接覆盖：归档是幂等操作；每案件目录只一份登记表，unique_path 加 (1) 无意义

        doc.save(filepath)
        return {"success": True, "filepath": filepath, "filename": os.path.basename(filepath),
                "page_fit": {"level": fit_step["lvl"], "used_cm": round(fit_used, 2),
                             "budget_cm": round(fit_budget, 2)}}

    except Exception as e:
        return {"success": False, "error": str(e)}
