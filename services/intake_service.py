"""投诉工单受理服务 - 多格式文件解析 + 查询线索提取"""
import json
import os
import re
import requests
from services.contract_service import _format_llm_error, _llm_config
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

    return parse_complaint_text(raw_text)


EXTRACT_PROMPT = """你是驾校投诉受理助手。从投诉文本中提取信息，只输出 JSON，不要输出任何其他文字：
{"student_name": "学员姓名，未提及则空串", "id_card": "证件号，文本未出现则空串", "phone": "手机号，文本未出现则空串"}
规则：
1. 严禁编造文本中不存在的信息；证件号、手机号文本未出现必须留空串

投诉文本：
"""

SUMMARY_PROMPT = """你是驾校投诉受理助手。阅读投诉文本，输出 JSON，不要输出任何其他文字：
{"complaint_summary": "不超过120字的客观摘要：投诉人、涉事驾校或训练场、车型、缴费情况、核心诉求", "complaint_demands": "学员核心诉求，一句话，不超过40字"}
规则：只陈述事实与诉求，不加评价；文本未体现的诉求不要编造。

投诉文本：
"""

# 登记表三段整理：投诉内容 / 投诉诉求 / 投诉处理（预览与归档共用一次调用）
POLISH_PROMPT = """你是驾校投诉登记表撰写助手。根据材料整理登记表的三个栏目，只输出 JSON，不要输出任何其他文字：
{"complaint_content": "投诉内容", "complaint_demands": "投诉诉求", "handling_summary": "处理经过"}
规则：
1. complaint_content：客观陈述投诉事实（何时报名、缴了多少费、发生了什么、向谁投诉），不超过120字；只能使用材料中出现的信息，材料为空或缺细节时按投诉类型的常见情形概括，不得编造具体金额、日期、人名。
2. complaint_demands：学员核心诉求一句话，不超过40字；材料未体现时按投诉类型写通用诉求。
3. handling_summary：把校方处理情况归纳为规范书面表述（联系核实、解释依据、协商结果），不超过100字；不新增处理动作；处理情况为空时输出空串。
4. 全部使用陈述句，不加评价性用语。

投诉类型：%s
学员与培训事实：%s
投诉材料原文：%s
校方处理情况：%s

"""


def parse_complaint_text(text: str) -> dict:
    """从粘贴的投诉文本提取查询线索：正则优先保证证件号/手机号准确并即时返回；
    仅当正则未命中时才调用 AI 快速补姓名（含证件号/手机号兜底）。
    投诉摘要与三系统查询无关联，延后到查询建案阶段与爬虫并行生成，不阻塞受理。"""
    cleaned = re.sub(r"[ \t]+", " ", text or "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if len(cleaned) < 10:
        return {"error": "无法从投诉内容中提取有效文本，请确认内容完整"}

    result = {
        "id_card": _extract_id_card(cleaned),
        "phone": _extract_phone(cleaned),
        "student_name": "",
        "complaint_summary": "",
    }

    # 正则已命中证件号/手机号 → 足以精确查询三系统，跳过 AI 调用（毫秒级返回）
    if result["id_card"] or result["phone"]:
        return result

    ai = ai_extract_complaint(cleaned)
    if ai.get("error"):
        # AI 失败不阻塞受理：返回正则结果 + 失败原因，前端降级为手动
        result["ai_error"] = ai["error"]
        return result

    result["student_name"] = str(ai.get("student_name") or "").strip()
    # AI 兜底证件号/手机号必须通过本地校验才采纳，防止幻觉
    if not result["id_card"]:
        cand = re.sub(r"[\s\-._]", "", str(ai.get("id_card") or "")).upper()
        if _is_valid_mainland_id(cand):
            result["id_card"] = cand
    if not result["phone"]:
        cand = re.sub(r"\D", "", str(ai.get("phone") or ""))
        if re.fullmatch(r"1[3-9]\d{9}", cand):
            result["phone"] = cand
    return result


def _llm_chat(prompt: str, max_tokens: int) -> dict:
    """调用 LLM 并解析 JSON 返回。失败返回 {"error": ...}，不抛异常。"""
    llm = _llm_config("llm_intake")
    if not llm.get("api_url") or not llm.get("api_key") or not llm.get("model"):
        return {"error": "未配置大模型接口，请在系统设置中填写后重试"}
    try:
        resp = requests.post(
            llm["api_url"],
            headers={
                "Authorization": f"Bearer {llm['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": llm["model"],
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": int(max_tokens or 1024),
                "temperature": 0.05,
                # qwen3 系列默认开启思考模式，思考 token 拖慢响应；线索提取/摘要无需思考
                "enable_thinking": False,
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    except requests.HTTPError:
        return {"error": _format_llm_error(resp)}
    except requests.RequestException as e:
        return {"error": f"大模型接口请求失败：{e}"}
    except (KeyError, IndexError, ValueError):
        return {"error": "大模型返回格式异常"}
    data = _parse_llm_json(content)
    if not data:
        return {"error": "AI 未返回有效的 JSON 结果"}
    return data


def ai_extract_complaint(text: str) -> dict:
    """调用 LLM 提取姓名/证件号/手机号（不含摘要）。失败返回 {"error": ...}，不抛异常。"""
    return _llm_chat(EXTRACT_PROMPT + text, 150)


def ai_summarize_complaint(text: str) -> dict:
    """调用 LLM 生成投诉摘要与诉求（查询建案阶段与爬虫并行执行）。失败返回 {"error": ...}，不抛异常。"""
    return _llm_chat(SUMMARY_PROMPT + text, 320)


TYPE_LABELS = {"A": "退费纠纷", "B": "教学服务", "C": "考试安排", "D": "合同争议", "E": "其他"}


def ai_polish_registration(ticket: dict) -> dict:
    """登记表三段整理：结合投诉类型、培训事实、材料原文与处理情况，一次调用生成
    {complaint_content, complaint_demands, handling_summary}。失败返回 {}，不抛异常。"""
    facts = (
        f"报名日期 {ticket.get('registration_date') or '未知'}，"
        f"车型 {ticket.get('license_type') or '未知'}，"
        f"当前进度 {ticket.get('exam_stage') or ticket.get('student_status') or '未知'}"
    )
    hours = ticket.get("training_hours") if isinstance(ticket.get("training_hours"), dict) else {}
    if hours:
        detail = "、".join(f"{k}学时{v}" for k, v in hours.items() if str(v or "").strip())
        if detail:
            facts += f"，{detail}"
    type_label = TYPE_LABELS.get(str(ticket.get("complaint_type") or "").strip(), "其他")
    prompt = POLISH_PROMPT % (
        type_label,
        facts,
        str(ticket.get("complaint_content") or "").strip() or "（无，学员未提交书面材料）",
        str(ticket.get("handling_notes") or "").strip() or "（无）",
    )
    data = _llm_chat(prompt, 700)
    if data.get("error"):
        return {}
    return {
        "complaint_content": str(data.get("complaint_content") or "").strip(),
        "complaint_demands": str(data.get("complaint_demands") or "").strip(),
        "handling_summary": str(data.get("handling_summary") or "").strip(),
    }


def _parse_llm_json(content: str) -> dict:
    """剥离 ```json 围栏后解析；失败再用 {...} 正则兜底（与 contract_service 同口径）。"""
    json_str = (content or "").strip()
    if "```json" in json_str:
        json_str = json_str.split("```json")[1].split("```")[0].strip()
    elif "```" in json_str:
        json_str = json_str.split("```")[1].split("```")[0].strip()
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        brace = re.search(r"\{.*\}", json_str, re.DOTALL)
        if not brace:
            return {}
        try:
            data = json.loads(brace.group(0))
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


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
