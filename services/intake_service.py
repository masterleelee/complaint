"""智能投诉工单受理服务 - 多格式文件解析 + LLM 关键信息提取"""
import json
import os
import re
import requests
import urllib3
from config import load_config
from services.file_parser import extract_text

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 工单文件上传目录
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")


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

    return _llm_extract(cleaned)


def _llm_extract(raw_text: str) -> dict:
    """调用大模型从投诉文本中提取结构化字段"""
    config = load_config()
    llm = config.get("llm", {})

    api_url = llm.get("api_url", "").strip()
    api_key = llm.get("api_key", "").strip()
    model = llm.get("model", "").strip()

    if not api_url or not api_key or not model:
        return {"error": "大模型API未配置"}

    # 截断过长文本
    max_chars = 8000
    if len(raw_text) > max_chars:
        raw_text = raw_text[:max_chars] + "\n\n...(以下内容已截断)"

    prompt = f"""你是一名驾校投诉工单信息提取专员。请从以下投诉工单文本中提取关键信息。

【投诉工单文本】
{raw_text}

请提取以下字段（如无对应信息则填空字符串）：
1. id_card: 证件号。支持以下格式：
   - 大陆18位身份证（如 110101199003070011）
   - 外国人居留许可证（如 F1249468(8)，1字母+8位数字+括号校验码，共10位）
   - 港澳台证件（如 M12345678）
   注意：原文中可能被空格分隔，提取时去掉空格。括号、字母等特殊字符请保留。
2. student_name: 学员姓名
3. phone: 联系电话/手机号
4. complaint_content: 投诉内容（简要概括投诉人反映的问题）
5. complaint_demands: 投诉诉求（投诉人希望得到的处理结果）
6. ticket_no: 工单编号（如有）
7. source_channel: 来源渠道（12345/交通局/信访/其他）
8. forwarding_dept: 转办单位
9. caller_number: 来电号码
10. complaint_date: 投诉日期（如"2026-04-15"格式）

严格要求：
- id_card 支持所有合法证件号格式（见上方说明）
- 如果文本中有"身份证号码"、"身份证号"、"证件号码"、"居留许可证号"等关键词后面跟着号码，优先提取
- 如果号码包含括号等特殊字符，请原样保留
- 输出前检查 id_card 是否为有效证件号（至少7位），不是则填空字符串

严格按以下JSON格式输出（只输出JSON，不要其他文字）：
{{
  "id_card": "",
  "student_name": "",
  "phone": "",
  "complaint_content": "",
  "complaint_demands": "",
  "ticket_no": "",
  "source_channel": "",
  "forwarding_dept": "",
  "caller_number": "",
  "complaint_date": ""
}}"""

    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": llm.get("max_tokens", 4096),
            "temperature": 0.1,
        }

        resp = requests.post(api_url, headers=headers, json=payload, timeout=120, verify=False)
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            return {"error": f"API错误: {data['error']}"}

        content = data["choices"][0]["message"]["content"]

        # 提取JSON
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        content = content.strip()
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1:
            content = content[start:end + 1]

        result = json.loads(content.strip())

        # 后处理：清理证件号中的空格、横线等分隔符
        raw_id = result.get("id_card", "")
        # 去掉空格、横线、点号等分隔符，但保留字母、数字和括号
        cleaned_id = re.sub(r"[\s\-\.\_]", "", raw_id)
        
        # 验证证件号格式（支持多种格式）
        if len(cleaned_id) >= 7:
            result["id_card"] = cleaned_id.upper()
        else:
            result["id_card"] = ""

        # 清理电话号码中的空格和横线
        for field in ("phone", "caller_number"):
            val = result.get(field, "")
            if val:
                result[field] = re.sub(r"[^\d]", "", val)

        return result

    except requests.exceptions.RequestException as e:
        return {"error": f"API请求失败: {e}"}
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        return {"error": f"响应解析失败: {e}"}


def ensure_upload_dir():
    """确保上传目录存在"""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    return UPLOAD_DIR
