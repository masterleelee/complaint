"""回复函扣费表回归测试：表头必须在数据行之前，封顶行须带计算链路。"""
from docx import Document

from services.template_service import generate_reply_from_template

TEMPLATE = "回复模板/default_template.docx"

BASE_VARS = {
    "name": "测试", "id_card": "T123", "school_short": "测", "school_name": "测试驾校",
    "registration_date": "2025-01-01", "license_type": "C1", "exam_stage": "科目二",
    "total_fee": "3700", "actual_paid": "3700", "total_deduction": "3166",
    "refund": "534", "contract_code": "DGJPTEST", "training_hours": "科目二培训12时0分",
    "reply_date": "2026", "visit_summary": "", "complaint_summary": "",
    "final_outcome": "", "communication_summary": "", "special_warnings": "",
}

DEDUCTIONS = [
    {"item": "综合服务费", "amount": 1100,
     "clause": "合同第三条及第七条", "trigger": "", "formula": "",
     "reason": "合同第三条及第七条"},
    {"item": "科目三实操培训费", "amount": 60,
     "clause": "合同退费实操培训费条款", "trigger": "科目三实操1538分钟",
     "formula": "1538分钟 ÷ 60 × 150元/小时 = 3845元；按本项合同上限最高800元，调整为800元",
     "reason": "合同退费实操培训费条款；科目三实操1538分钟"},
]


def test_deduction_table_header_first_and_cap_formula(tmp_path):
    result = generate_reply_from_template(
        template_path=TEMPLATE,
        variables={**BASE_VARS, "deductions": DEDUCTIONS},
        output_dir=str(tmp_path),
    )
    assert result["success"]
    doc = Document(result["filepath"])
    table = doc.tables[0]
    rows = ["".join(c.text for c in row.cells) for row in table.rows]

    assert "序号" in rows[0], f"表头必须是第一行，实际第一行: {rows[0][:40]}"
    assert "{{deductions}}" not in "".join(rows), "占位符行应被移除"
    data_rows = rows[1:]
    assert len(data_rows) == 2

    normal, capped = rows[1], rows[2]
    assert "综合服务费" in normal and "计算：" not in normal
    assert "计算：" in capped and "3845" in capped and "调整为" in capped


def test_empty_warnings_leave_no_placeholder(tmp_path):
    result = generate_reply_from_template(
        template_path=TEMPLATE,
        variables={**BASE_VARS, "deductions": DEDUCTIONS},
        output_dir=str(tmp_path),
    )
    doc = Document(result["filepath"])
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "{{" not in full_text
