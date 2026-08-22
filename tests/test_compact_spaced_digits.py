from services.contract_service import _compact_spaced_digits, _extract_standard_contract_data


def test_luo_jun_contract_regression():
    raw = (
        "第三条 培训收费约定 （一）乙方向甲方支付培训费用合计人民币 3880 . 00 元（以下均为人民币），"
        " 其中通过“东莞驾培”平台支付金额为 1500 . 00 元。培训费用包含以下项目："
        " 1.综合服务费 1100 . 00 元（包含档案资料费、乙方计时IC卡费、办公费等相关服务费用）；"
        " 2.理论培训费 780 . 00 元（包含道路安全法律相关知识和安全文明驾驶常识内容）；"
    )
    data = _extract_standard_contract_data(raw)
    assert isinstance(data, dict)
    assert data["total_fee"] == 3880.0
    deductions = {item["item"]: item["amount"] for item in data["deduction_items"]}
    assert deductions["综合服务费"] == 1100.0
    assert deductions["理论培训费"] == 780.0


def test_merges_individually_spaced_digits():
    assert _compact_spaced_digits("3 2 8 0") == "3280"


def test_preserves_chinese_date_structure():
    result = _compact_spaced_digits("2026 年 8 月 21 日")
    assert "2026 年" in result
    assert "8 月" in result
    assert "21 日" in result


def test_preserves_phone_with_dash():
    assert _compact_spaced_digits("电话 0769 - 82838383") == "电话 0769 - 82838383"
