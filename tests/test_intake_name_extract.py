"""测试 parse_complaint_text 能从「姓名,本人...」开头格式中提取姓名。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_extract_name_standard_lead():
    """标准开头：蔡振华,本人在该驾校..."""
    from services.intake_service import parse_complaint_text
    text = (
        "蔡振华,本人在该驾校缴纳学费3680元，报名仅一年时间，"
        "只完成体检和学籍建档，未参与任何学车培训。"
        "电话13800000000，广东省东莞市桥头新城路332号之一"
    )
    r = parse_complaint_text(text)
    assert r.get("phone") == "13800000000", f"手机号应被提取, got {r.get('phone')}"
    assert r.get("student_name") == "蔡振华", f"姓名应被提取, got {r.get('student_name')}"
    print("  [PASS] 标准开头：姓名+手机号都被正则提取")


def test_extract_name_full_zhongwen_comma():
    """全角逗号：王五，..."""
    from services.intake_service import parse_complaint_text
    text = "王五，投诉驾校乱收费，电话13800000000"
    r = parse_complaint_text(text)
    assert r.get("phone") == "13800000000"
    assert r.get("student_name") == "王五"
    print("  [PASS] 全角逗号开头：姓名+手机号都被正则提取")


def test_extract_name_with_blacklist_word():
    """黑名单词开头不应被误判为姓名"""
    from services.intake_service import parse_complaint_text
    text = "投诉人反映在东莞被骗，电话13800000000"
    r = parse_complaint_text(text)
    assert r.get("phone") == "13800000000"
    # "投诉人" 在黑名单 → 不应被当作姓名
    assert r.get("student_name") == "" or r.get("student_name") != "投诉人"
    print("  [PASS] 黑名单词开头：不会被误判为姓名")


def test_extract_name_four_chars():
    """4字姓名（如复姓/少数民族）"""
    from services.intake_service import parse_complaint_text
    text = "欧阳娜娜，电话13800000000"
    r = parse_complaint_text(text)
    assert r.get("phone") == "13800000000"
    assert r.get("student_name") == "欧阳娜娜"
    print("  [PASS] 4字姓名：被正则提取")


def test_extract_name_too_far_in_text():
    """姓名不出现在前 80 字符、且文本已有手机号时：正则应不预提（AI 路径不在本测试范围）"""
    from services.intake_service import parse_complaint_text
    # 构造一个明显不是开头的姓名 + 有手机号（走正则快路径，不调 AI）
    text = "投诉内容：" + ("某某驾校乱收费。" * 30) + "电话13800000000"
    r = parse_complaint_text(text)
    assert r.get("phone") == "13800000000"
    # 姓名不在开头 80 字符内，正则不应预提
    assert r.get("student_name") == "", f"姓名不应被预提, got {r.get('student_name')}"
    print("  [PASS] 姓名不在开头：不被预提（避免误吞正文）")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
