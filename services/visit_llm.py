"""回访AI归纳服务 - LLM生成回访情况说明（基于沟通记录 + 最终结果）"""
import json
import requests
from config import load_config


OUTCOME_LABELS = {
    "投诉撤销": "学员已撤销投诉",
    "同意合同扣费": "同意合同扣费",
    "不同意合同扣费但协商一致": "对扣费有异议但协商达成一致",
    "不同意合同扣费且协商失败": "对扣费有异议且协商失败",
    "无法联系": "无法联系",
    "继续培训/转校": "继续培训或转校",
}


def summarize_visit(
    student_name: str,
    final_outcome: str = "",
    latest_summary: str = "",
    negotiation_outcome: str = "",
    withdraw_status: str = "",
    total_fee: float = 0,
    refund: float = 0,
    deductions: list = None,
    special_warnings: list = None,
    exam_stage: str = "",
) -> dict:
    """调用LLM，基于最近一条沟通记录摘要 + 最终投诉结果，润色成回访情况说明。"""
    config = load_config()
    llm = config.get("llm", {})
    api_url = llm.get("api_url", "").strip()
    api_key = llm.get("api_key", "").strip()
    model = llm.get("model", "").strip()

    if not api_url or not api_key or not model:
        return {"error": "大模型API未配置", "summary": latest_summary or "回访已完成。"}

    outcome_label = OUTCOME_LABELS.get(final_outcome, final_outcome or "其他情况")

    deduction_desc = ""
    if deductions:
        for d in deductions:
            deduction_desc += f"- {d.get('item', '未知')}: {d.get('amount', 0)}元 ({d.get('reason', '')})\n"

    warnings_desc = ""
    if special_warnings:
        for w in special_warnings:
            warnings_desc += f"- {w.get('message', '')}\n"

    prompt = (
        "你是一名驾校投诉处理专员。请根据以下信息，撰写一段正式、客观的**回访情况说明**（约100-200字），\n"
        "用于投诉登记表归档。要求：语言正式、客观、简洁，不虚构未提供的信息。\n"
        "\n"
        "【学员信息】\n"
        f"姓名：{student_name}\n"
        f"学习进度：{exam_stage}\n"
        f"最终投诉结果：{outcome_label}\n"
        f"协商结果：{negotiation_outcome or '无'}\n"
        f"撤诉状态：{withdraw_status or '未撤诉'}\n"
        f"\n"
        "【最近一次沟通记录摘要】\n"
        f"{latest_summary or '无'}\n"
        f"\n"
        f"【费用情况】\n"
        f"合同总额：{total_fee}元\n"
        f"应退金额：{refund}元\n"
        f"扣费明细：\n{deduction_desc or '无'}\n"
        f"\n"
        f"【特殊情况】\n{warnings_desc or '无'}\n"
        "\n"
        "请直接输出回访情况说明正文，不要带任何前缀或标记：\n"
    )

    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1024,
            "temperature": 0.3,
        }

        resp = requests.post(api_url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        if data.get("choices"):
            summary = data["choices"][0]["message"]["content"].strip()
            return {"success": True, "summary": summary}

        return {"error": "API返回空结果", "summary": latest_summary or "回访已完成。"}

    except Exception as e:
        return {"error": f"AI归纳失败: {str(e)}", "summary": latest_summary or "回访已完成。"}
