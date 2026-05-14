"""合同分析服务 - 纯云端 Vision API 方案"""
import os
import json
import base64
import requests
from datetime import datetime, timedelta
import urllib3
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from config import load_config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _recognize_single_image(args):
    """识别单张图片的辅助函数（用于并发）"""
    idx, img_path, api_url, api_key, model = args
    
    if not os.path.exists(img_path):
        return idx, ""
    
    try:
        with open(img_path, "rb") as f:
            b64_image = base64.b64encode(f.read()).decode('utf-8')
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text", 
                            "text": f"请详细识别这张驾校培训合同的第{idx}页内容。保持原有的段落和表格结构，不要遗漏任何文字。"
                        },
                        {
                            "type": "image_url", 
                            "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}
                        }
                    ]
                }
            ],
            "max_tokens": 2000
        }
        
        resp = requests.post(api_url, headers=headers, json=payload, timeout=60, verify=False)
        result = resp.json()
        
        if "choices" in result and len(result["choices"]) > 0:
            text = result["choices"][0]["message"]["content"]
            print(f"✅ 第{idx}页识别完成 ({len(text)} 字符)")
            return idx, text
        else:
            print(f"⚠️ 第{idx}页识别失败: {result}")
            return idx, ""
    except Exception as e:
        print(f"⚠️ 第{idx}页请求异常: {e}")
        return idx, ""


def extract_contract_text_vision(image_paths: list) -> str:
    """
    使用 Qwen-VL Vision API 并发识别多张合同图片
    返回拼接后的完整合同文本
    """
    cfg = load_config()
    api_url = cfg["llm"]["api_url"]
    api_key = cfg["llm"]["api_key"]
    model = cfg["llm"]["model"]

    if not image_paths:
        return ""
    
    # ── 压缩图片以减少 Token 消耗 ──
    try:
        from services.image_compressor import compress_for_vision_api
        compressed_paths = compress_for_vision_api(image_paths)
    except Exception as e:
        print(f"[COMPRESS WARNING] 压缩失败，使用原图: {e}")
        compressed_paths = image_paths
    
    print(f"[VISION] 并发识别 {len(compressed_paths)} 张图片...")
    
    # ── 并发识别所有图片 ──
    all_texts = [""] * len(compressed_paths)
    
    with ThreadPoolExecutor(max_workers=min(len(compressed_paths), 3)) as executor:
        futures = {
            executor.submit(_recognize_single_image, (i+1, path, api_url, api_key, model)): i 
            for i, path in enumerate(compressed_paths)
        }
        
        for future in as_completed(futures):
            idx, text = future.result()
            if text:
                all_texts[idx-1] = text
    
    all_texts = [t for t in all_texts if t]
    print(f"[VISION] 识别完成: {len(all_texts)}/{len(compressed_paths)} 页成功")
    
    return "\n\n--- 下一页 ---\n\n".join(all_texts)


def _extract_penalty_rate(text: str) -> float:
    """从合同文本中提取违约金比例"""
    if not text:
        return 0.20
    
    patterns = [
        r'违约金.*?([\d]+)\s*%',
        r'([\d]+)\s*%\s*违约金',
        r'百分之([一二三四五六七八九十]+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            num_str = match.group(1)
            chinese_nums = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
                          '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
            if num_str in chinese_nums:
                return chinese_nums[num_str] / 100
            try:
                return int(num_str) / 100
            except:
                pass
    
    if re.search(r'无违约金|不收取违约金|不扣违约金|没有违约金', text):
        return 0
    
    return 0.20


def _parse_ai_response(content: str, exam_counts: dict = None) -> dict:
    """解析 AI 返回的自然语言，提取结构化数据"""
    result = {
        "total_fee": 0,
        "actual_paid": 0,
        "contract_code": "",
        "penalty_rate": 0.20,
        "deductions": [],
        "total_deduction": 0,
        "refund": 0,
        "summary": "",
        "raw_analysis": content,
    }
    
    # 提取合同编号
    code_match = re.search(r'合同编号[：:]\s*([A-Za-z0-9\-]+)', content)
    if code_match:
        result["contract_code"] = code_match.group(1)
    
    # 提取培训费总额
    fee_patterns = [
        r'培训费总额[：:]\s*([\d,]+)',
        r'合同标价[：:]\s*([\d,]+)',
        r'总培训费[：:]\s*([\d,]+)',
    ]
    for pattern in fee_patterns:
        match = re.search(pattern, content)
        if match:
            result["total_fee"] = float(match.group(1).replace(',', ''))
            break
    
    # 提取实际已交金额
    paid_patterns = [
        r'实际已交金额[：:]\s*([\d,]+)',
        r'首付款[：:]\s*([\d,]+)',
        r'已付金额[：:]\s*([\d,]+)',
    ]
    for pattern in paid_patterns:
        match = re.search(pattern, content)
        if match:
            result["actual_paid"] = float(match.group(1).replace(',', ''))
            break
    
    if result["actual_paid"] == 0:
        result["actual_paid"] = result["total_fee"]
    
    # 提取违约金比例
    result["penalty_rate"] = _extract_penalty_rate(content)
    
    # 提取扣费项目
    seen_items = set()
    deduction_patterns = [
        (r'综合服务费[：:]\s*([\d,]+)', '综合服务费'),
        (r'建档费[：:]\s*([\d,]+)', '建档费'),
        (r'IC卡费[：:]\s*([\d,]+)', 'IC卡费'),
        (r'理论培训费[：:]\s*([\d,]+)', '理论培训费'),
    ]
    
    for pattern, item_name in deduction_patterns:
        match = re.search(pattern, content)
        if match and item_name not in seen_items:
            amount = float(match.group(1).replace(',', ''))
            if amount > 0:
                result["deductions"].append({
                    "item": item_name,
                    "amount": amount,
                    "reason": f"合同约定的{item_name}",
                })
                result["total_deduction"] += amount
                seen_items.add(item_name)
    
    # 计算违约金
    if result["penalty_rate"] > 0 and result["total_fee"] > 0:
        penalty_amount = round(result["total_fee"] * result["penalty_rate"], 2)
        result["deductions"].append({
            "item": "违约金",
            "amount": penalty_amount,
            "reason": f"违约金={result['total_fee']}×{result['penalty_rate']*100}%={penalty_amount}元",
        })
        result["total_deduction"] += penalty_amount
    
    # 计算应退金额
    result["refund"] = max(0, result["actual_paid"] - result["total_deduction"])
    
    # 生成 summary
    result["summary"] = (
        f"合同金额{result['total_fee']}元，"
        f"实际已交{result['actual_paid']}元，"
        f"违约金比例{result['penalty_rate']*100}%，"
        f"总扣费{result['total_deduction']}元，"
        f"应退{result['refund']}元"
    )
    
    return result


def analyze_contract(
    contract_text: str,
    exam_stage: str = "",
    training_hours: dict = None,
    total_fee: float = 0,
    exam_counts: dict = None,
) -> dict:
    """调用大模型分析合同文本"""
    config = load_config()
    llm = config.get("llm", {})

    api_url = llm.get("api_url", "").strip()
    api_key = llm.get("api_key", "").strip()
    model = llm.get("model", "").strip()

    if not api_url or not api_key or not model:
        return {"error": "大模型API未配置"}

    hours_desc = ""
    if training_hours:
        for subject, hours in training_hours.items():
            hours_desc += f"{subject}：{hours}学时；"

    exam_counts_desc = ""
    if exam_counts:
        for subj, cnt in exam_counts.items():
            exam_counts_desc += f"{subj}：{cnt}次；"

    prompt = (
        "请分析这份驾校培训合同，提取以下信息：\n"
        "\n"
        "1. 合同编号、签订日期\n"
        "2. 培训费总额（合同标价）\n"
        "3. 学员实际已交金额（看手写'首付款'等标注）\n"
        "4. 退学退费条款（违约金比例）\n"
        "5. 扣费项目及金额（综合服务费、建档费、IC卡费等）\n"
        "\n"
        "【学员情况】\n"
        f"- 当前阶段：{exam_stage or '未知'}\n"
        f"- 培训学时：{hours_desc or '未提供'}\n"
        f"- 考试次数：{exam_counts_desc or '未提供'}\n"
        "\n"
        "请用自然语言描述分析结果。\n"
        "\n"
        "【合同内容】\n"
        f"{contract_text[:8000]}\n"
    )

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

        if not data.get("choices"):
            return {"error": "API返回空结果"}

        content = data["choices"][0]["message"]["content"]
        print(f"[AI分析] {content[:200]}...")

        # 解析 AI 返回的内容
        result = _parse_ai_response(content, exam_counts)

        return result

    except Exception as e:
        return {"error": f"分析失败: {str(e)}"}


def _detect_special_cases(registration_date: str, exam_counts: dict = None) -> list[dict]:
    """检测 4 种特殊退费情况，返回警告列表"""
    warnings = []
    today = datetime.now().date()
    three_years_ago = today - timedelta(days=1095)

    # 1. 合同报名时间 > 3年
    if registration_date:
        try:
            reg_date = datetime.strptime(registration_date[:10], "%Y-%m-%d").date()
            if reg_date < three_years_ago:
                warnings.append({
                    "type": "contract_expired",
                    "message": "该学员合同报名时间已超过3年，此种情况下没有费用退还。",
                    "reply_text": "该学员合同报名时间已超过3年，根据合同约定，此种情况下没有费用退还。",
                })
        except (ValueError, AttributeError):
            pass

    # 2. 技能证时间 > 3年（从考试次数和时间轴推断）
    # 3. 科二不合格 > 5 次
    # 4. 科三不合格 > 5 次
    if exam_counts:
        k2_fails = exam_counts.get("科目二", 0)
        k3_fails = exam_counts.get("科目三", 0)
        if k2_fails >= 5:
            warnings.append({
                "type": "k2_exceed",
                "message": "该学员科目二考试次数已超过5次不合格，此种情况下没有费用退还。",
                "reply_text": "该学员科目二考试次数已超过5次不合格，根据合同约定，此种情况下没有费用退还。",
            })
        if k3_fails >= 5:
            warnings.append({
                "type": "k3_exceed",
                "message": "该学员科目三考试次数已超过5次不合格，此种情况下没有费用退还。",
                "reply_text": "该学员科目三考试次数已超过5次不合格，根据合同约定，此种情况下没有费用退还。",
            })

    return warnings


def analyze_contract_from_file(
    filepath: str,
    exam_stage: str = "",
    training_hours: dict = None,
    total_fee: float = 0,
    image_paths: list = None,
    exam_counts: dict = None,
    registration_date: str = "",
) -> dict:
    """从合同图片文件进行分析"""
    contract_text = ""
    
    if image_paths and len(image_paths) > 0:
        print(f"🔍 识别 {len(image_paths)} 张图片...")
        contract_text = extract_contract_text_vision(image_paths)
    elif filepath and os.path.exists(filepath):
        print(f"🔍 识别单张图片...")
        contract_text = extract_contract_text_vision([filepath])
    
    if not contract_text or len(contract_text) < 50:
        return {"error": "无法从图片中提取合同文本"}

    print(f"✅ 识别完成，{len(contract_text)} 字符")

    result = analyze_contract(
        contract_text=contract_text,
        exam_stage=exam_stage,
        training_hours=training_hours,
        total_fee=total_fee,
        exam_counts=exam_counts,
    )

    # 注入特殊退费检测警告
    result["special_warnings"] = _detect_special_cases(registration_date, exam_counts)
    return result
