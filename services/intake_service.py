"""投诉工单受理服务 - 多格式文件解析 + 查询线索提取"""
import os
import re
from services.file_parser import extract_text

# 工单文件上传目录
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")

CORE_FIELDS = ("id_card", "phone")


def parse_complaint_file(filepath: str) -> dict:
    """
    从投诉工单文件中提取关键信息。
    返回结构化字段字典。
    """
    raw_text = extract_text(filepath)
    if not raw_text or len(raw_text.strip()) < 10:
        return {"error": "无法从文件中提取有效文本内容，请确认文件格式正确且非空"}

    # 清洗：将连续空白压缩为单个空格，消除表格/Excel中大量填充空格的影响
    cleaned = re.sub(r"[ \t]+", " ", raw_text)
    # 压缩连续空行为最多2个换行
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    return _rule_extract(cleaned)


def _rule_extract(text: str) -> dict:
    """用确定性规则提取查询线索，优先保证身份证和手机号准确。"""
    return {
        "id_card": _extract_id_card(text),
        "phone": _extract_phone(text),
    }


def _extract_id_card(text: str) -> str:
    # 先提大陆身份证候选；允许 OCR/表格中夹杂空格、横线、点号。
    for match in re.finditer(r"(?<!\d)(\d[\d\s\-._]{15,24}[\dXx])(?![A-Za-z0-9])", text):
        candidate = re.sub(r"[\s\-._]", "", match.group(1)).upper()
        if _is_valid_mainland_id(candidate):
            return candidate

    # 其他证件只在关键词附近提取，避免把工单号误当证件号。
    keyword_pattern = (
        r"(?:身份证号(?:码)?|证件号(?:码)?|居留许可证号?|港澳台证件号?)"
        r"[:：\s]*([A-Za-z0-9()（）\-\s]{7,30})"
    )
    match = re.search(keyword_pattern, text)
    if match:
        candidate = re.sub(r"[\s\-._]", "", match.group(1)).replace("（", "(").replace("）", ")").upper()
        if len(candidate) >= 7:
            return candidate
    return ""


def _is_valid_mainland_id(id_card: str) -> bool:
    if not re.fullmatch(r"\d{17}[\dX]", id_card):
        return False
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    check_codes = "10X98765432"
    total = sum(int(id_card[i]) * weights[i] for i in range(17))
    return id_card[-1] == check_codes[total % 11]


def _extract_phone(text: str) -> str:
    for match in re.finditer(r"(?<!\d)(1[3-9]\d[\s\-]?\d{4}[\s\-]?\d{4})(?!\d)", text):
        return re.sub(r"\D", "", match.group(1))
    return ""


def ensure_upload_dir():
    """确保上传目录存在"""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    return UPLOAD_DIR
