"""合同分析服务：文本提取、合同结构化、退费草案计算。"""
import os
import json
import requests
from datetime import datetime, timedelta
import re
from typing import Optional
from config import load_config, normalize_llm_api_url
from services.image_compressor import compress_for_vision_api
from services.file_parser import (
    extract_text as _file_parser_extract_text,
)
from services.contract_template_service import get_template_service
from utils.logger import system_logger

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff")


def _llm_config(section: str) -> dict:
    """按用途读取模型配置，缺项回退到 legacy llm。"""
    cfg = load_config()
    legacy = cfg.get("llm", {}) if isinstance(cfg.get("llm"), dict) else {}
    specific = cfg.get(section, {}) if isinstance(cfg.get(section), dict) else {}
    merged = dict(legacy)
    for key, value in specific.items():
        if value not in ("", None):
            merged[key] = value
    merged["api_url"] = normalize_llm_api_url(merged.get("api_url", ""))
    return merged


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _compact_spaced_digits(text: str) -> str:
    """东莞驾培 PDF 常把数字拆成 '3 2 8 0'，分析前合并为 '3280'，并先把 '3880 . 00' 桥接为 '3880.00'。"""
    text = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", text or "")
    return re.sub(r"(?<=\d)\s+(?=\d)", "", text)


def _build_contract_text_preview(text: str) -> str:
    normalized = _normalize_text(text)
    if len(normalized) <= 500:
        return normalized

    snippets = [normalized[:260]]
    for pattern in ("第三条 培训收费约定", "培训收费约定", "培训费用合计", "退学退费"):
        idx = normalized.find(pattern)
        if idx >= 0:
            start = max(0, idx - 40)
            snippets.append(normalized[start:start + 420])
            break
    return " ... ".join(snippets)[:700]


def _extract_pdf_text(filepath: str) -> str:
    """直接读取文字型 PDF 的文本层。"""
    import pdfplumber

    with pdfplumber.open(filepath) as pdf:
        pages_text = []
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                pages_text.append(t)
    return _compact_spaced_digits("\n".join(pages_text))


def _extract_contract_text_local(image_paths: list[str]) -> str:
    """本地兜底 OCR（macOS 原生 Vision，file_parser 链）：压缩后逐页识别再拼接。"""
    ocr_paths = compress_for_vision_api(
        image_paths,
        max_size=(1600, 2200),
        jpeg_quality=88,
        target_max_mb=1.5,
    )

    texts = []
    for idx, path in enumerate(ocr_paths, 1):
        page_text = _file_parser_extract_text(path)
        if page_text.strip():
            texts.append(f"--- 第{idx}页 ---\n{page_text}")
    return "\n\n".join(texts)


def _baidu_ocr_enabled() -> bool:
    cfg = load_config().get("baidu_ocr") or {}
    return bool(cfg.get("enabled", False))


def extract_contract_text_baidu(image_paths: list[str]) -> str:
    """百度云 OCR 主路文本：accurate_basic 各页拼接（spec: contract-ocr-baidu v3）。"""
    from services.baidu_ocr import extract_pages_via_baidu
    pages = extract_pages_via_baidu(image_paths)
    return pages.get("printed") or ""


def _extract_text_via_ocr_chain(ocr_inputs: list[str]) -> tuple[str, str, dict]:
    """合同图片 OCR 降级链（spec v3 §4/§6）：百度主路 → macOS Vision 本地兜底。

    返回 `(text, source, extras)`：
    - source：`baidu_ocr`（高置信）或 `local_ocr`（本地 Vision，沿用低置信口径）；
    - extras：`{"baidu_rescue": {"fees": ...}}` —— 省调用策略下，仅当 accurate_basic
      的 total_fee 缺失/被闸门拦下时才追加 handwriting 二次提取（只跑 handwriting，
      不重跑 accurate_basic），金额字段由 upload_pipeline 只填空位合并。
    """
    extras: dict = {}
    if _baidu_ocr_enabled():
        try:
            from services.baidu_ocr import extract_pages_via_baidu
            system_logger.info("[OCR] 百度云 OCR 识别 %d 个合同图片...", len(ocr_inputs))
            pages = extract_pages_via_baidu(ocr_inputs)
            text = pages.get("printed") or ""
            if len(text.strip()) >= 20:
                fees = extract_contract_fees(text)
                if not fees.get("total_fee"):
                    system_logger.info(
                        "[OCR] accurate_basic 缺 total_fee（疑似手写件），追加 handwriting 二次尝试")
                    try:
                        hw_pages = extract_pages_via_baidu(ocr_inputs, need_handwriting=True)
                        hw_text = hw_pages.get("handwriting") or ""
                        if hw_text.strip():
                            hw_fees = extract_contract_fees(hw_text)
                            if any(hw_fees.get(k) for k in ("total_fee", "theory_fee")):
                                extras["baidu_rescue"] = {"fees": hw_fees}
                    except Exception as exc:
                        system_logger.warning("[OCR] handwriting 补提失败（保持 pending 人工）：%s", exc)
                return text, "baidu_ocr", extras
            system_logger.warning("[OCR] 百度云 OCR 无有效文本，降级本地 Vision")
        except Exception as exc:
            system_logger.warning("[OCR] 百度云 OCR 失败，降级本地 Vision: %s", exc)

    system_logger.info("[OCR] 本地 macOS Vision 识别 %d 个合同图片...", len(ocr_inputs))
    text = _extract_contract_text_local(ocr_inputs)
    if len(text.strip()) >= 20:
        return text, "local_ocr", extras
    return "", "", extras


def _pdf_to_images(filepath: str) -> list[str]:
    """扫描 PDF 转图片后交给 OCR；缺少 PyMuPDF 时返回空列表。"""
    try:
        import fitz
    except ImportError:
        return []

    temp_images = []
    pdf_doc = fitz.open(filepath)
    try:
        for page_num in range(len(pdf_doc)):
            page = pdf_doc[page_num]
            pix = page.get_pixmap(dpi=200)
            img_path = filepath.replace(".pdf", f"_p{page_num}.png")
            pix.save(img_path)
            temp_images.append(img_path)
    finally:
        pdf_doc.close()
    return temp_images


def _detect_unclear_fields(contract_text: str) -> tuple[list[str], list[str]]:
    """识别是否缺少正式确认费用方案所需的关键字段。"""
    normalized = _normalize_text(contract_text)
    unclear_fields = []
    blockers = []

    has_amount = bool(re.search(r"(培训服务费|培训费|合同金额|总额|已交|实收|费用).{0,20}?\d{3,}(?:\.\d+)?\s*元", normalized))
    if not has_amount:
        unclear_fields.append("合同金额")

    has_refund_clause = bool(re.search(r"(退学退费|退费|解除合同|第九条)", normalized))
    if not has_refund_clause:
        unclear_fields.append("退费条款")

    if re.search(r"(\[模糊\]|【模糊】|模糊|看不清|无法识别)", normalized):
        if "合同金额" not in unclear_fields and re.search(r"(金额|费用|元).{0,10}(模糊|看不清|无法识别)", normalized):
            unclear_fields.append("合同金额")
        if "手写修改" not in unclear_fields and re.search(r"(手写|修改|补充).{0,10}(模糊|看不清|无法识别)", normalized):
            unclear_fields.append("手写修改")

    if unclear_fields:
        blockers.append("合同关键字段识别不完整，不能确认正式费用方案，请重新上传清晰合同或人工补录。")

    return unclear_fields, blockers


def _build_extraction_result(text: str, source: str) -> dict:
    unclear_fields, blockers = _detect_unclear_fields(text)
    can_confirm = not blockers
    return {
        "text": text,
        "source": source,
        "extraction_source": source,
        "contract_text_preview": _build_contract_text_preview(text),
        "unclear_fields": unclear_fields,
        "blockers": blockers,
        "can_confirm_fee_plan": can_confirm,
        "fee_plan_status": "draft" if can_confirm else "needs_review",
    }


def extract_contract_text_from_file(filepath: str, image_paths: list[str] = None) -> dict:
    """按文件类型选择最低成本的合同文本提取方式。"""
    image_paths = image_paths or []
    if not filepath or not os.path.exists(filepath):
        return {"error": "合同文件不存在"}

    contract_text = ""
    source = ""
    ocr_extras: dict = {}  # OCR 链产物（baidu_rescue）；非图片路径（docx/pdf 文本层）保持空
    lower_path = filepath.lower()

    if lower_path.endswith(".pdf"):
        try:
            contract_text = _extract_pdf_text(filepath)
            if contract_text and len(contract_text.strip()) > 200:
                source = "pdf_text"
                system_logger.info("[PDF] pdfplumber 提取成功: %d 字符", len(contract_text))
            else:
                system_logger.warning("[PDF] pdfplumber 提取文本不足(%d字)，尝试本地 OCR", len(contract_text))
                contract_text = ""
        except Exception as e:
            system_logger.warning("[PDF] pdfplumber 提取失败: %s，尝试本地 OCR", e)
            contract_text = ""

    if not contract_text and lower_path.endswith(".docx"):
        try:
            contract_text = _file_parser_extract_text(filepath)
            if contract_text and len(contract_text.strip()) >= 20:
                source = "docx_text"
                system_logger.info("[DOCX] 提取成功: %d 字符", len(contract_text))
            else:
                contract_text = ""
        except Exception as e:
            system_logger.warning("[DOCX] 提取失败: %s", e)
            contract_text = ""

    ocr_inputs: list[str] = []
    if not contract_text:
        ocr_inputs = [
            p for p in image_paths
            if p and os.path.exists(p) and p.lower().endswith(IMAGE_EXTS)
        ]
        if not ocr_inputs and lower_path.endswith(IMAGE_EXTS):
            ocr_inputs = [filepath]
        if not ocr_inputs and lower_path.endswith(".pdf"):
            try:
                ocr_inputs = _pdf_to_images(filepath)
            except Exception as e:
                system_logger.warning("[PDF→IMG] 转换失败: %s", e)
        if ocr_inputs:
            contract_text, source, ocr_extras = _extract_text_via_ocr_chain(ocr_inputs)
            if contract_text:
                system_logger.info("[OCR] 识别成功（source=%s）：%d 字符", source, len(contract_text))
            else:
                system_logger.error("[OCR/VISION] 所有识别方式均失败")

    if not contract_text or len(contract_text.strip()) < 20:
        return {"error": "无法从合同文件中提取可分析文本"}

    # 尝试模板匹配（仅对 OCR 提取的文本；baidu_ocr 置信高，与 vision_text 同级放行）
    template_match = None
    extracted_fields = {}
    if source in ("baidu_ocr", "vision_text", "local_ocr"):
        try:
            template_service = get_template_service()
            matches = template_service.match_template(contract_text)
            if matches and matches[0].is_confident:
                template_match = matches[0]
                extracted_fields = template_service.extract_fields(
                    template_match.template_id, 
                    contract_text
                )
                system_logger.info(f"[模板匹配] 成功匹配 {template_match.template_name}, 提取 {len(extracted_fields)} 个字段")
        except Exception as e:
            system_logger.warning("[模板匹配] 匹配失败：%s", e)
    
    result = _build_extraction_result(contract_text, source or "unknown")

    # 注入模板匹配结果
    if template_match:
        result["template_match"] = {
            "template_id": template_match.template_id,
            "template_name": template_match.template_name,
            "provider": template_match.provider,
            "confidence": template_match.confidence,
        }
        result["extracted_fields"] = {k: {"value": v.value, "confidence": v.confidence} for k, v in extracted_fields.items()}

    # 百度 handwriting 二次补提的金额字段（省调用策略触发时才有），
    # 由 upload_pipeline._merge_rescued_fees 只填空位合并（spec v3 §4）
    if ocr_extras.get("baidu_rescue"):
        result["baidu_rescue"] = ocr_extras["baidu_rescue"]

    # 付款计划字段（spec v3 §4 欠款口径）：首付取文本，欠款 = 总额 − 首付 本地推算
    try:
        fees_probe = extract_contract_fees(contract_text)
        result["payment_plan"] = _extract_payment_plan(
            contract_text, total_fee=float(fees_probe.get("total_fee") or 0),
        )
    except Exception as exc:
        system_logger.warning("[PAYMENT-PLAN] 付款计划推算失败：%s", exc)

    return result


def build_contract_clauses(filepath: str) -> dict:
    """仅用 pdfplumber 提取文本层并按"第X条"切块，预览路径不触发 LLM/Vision/OCR。"""
    if not filepath or not os.path.exists(filepath):
        return {"clauses": [], "error": "合同文件不存在"}
    try:
        text = _extract_pdf_text(filepath)
    except Exception as e:
        system_logger.warning("[合同条款] pdfplumber 提取失败: %s", e)
        text = ""

    if not text or len(text.strip()) < 20:
        return {"clauses": [], "error": "no_text_layer"}

    matches = list(re.finditer(r"(?m)^\s*第([一二三四五六七八九十百零\d]+)条", text))
    clauses = []
    preamble = text[:matches[0].start()].strip() if matches else text.strip()
    if preamble:
        clauses.append({"no": "", "title": "", "body": preamble})
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        segment = text[start:end]
        if "\n" in segment:
            title, body = segment.split("\n", 1)
            title, body = title.strip(), body.strip()
        else:
            title, body = "", segment.strip()
        clauses.append({"no": m.group(1), "title": title, "body": body})

    return {"clauses": clauses, "error": ""}


def _blank_analysis_result(message: str = "") -> dict:
    result = _parse_ai_response("{}")
    result["error"] = message or "合同识别结果不完整，需人工核对登记"
    if message:
        result["summary"] = message
        result["summary_lines"] = [message]
    return result


def _format_llm_error(resp: requests.Response) -> str:
    """把 LLM API 的 HTTP 错误转换成处理人能执行的中文提示。"""
    status = getattr(resp, "status_code", "")
    raw_body = getattr(resp, "text", "") or ""
    error_code = ""
    error_type = ""
    message = raw_body.strip()

    try:
        body = resp.json()
    except ValueError:
        body = {}

    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict):
            error_code = str(err.get("code", "") or "")
            error_type = str(err.get("type", "") or "")
            message = str(err.get("message", "") or message)

    normalized = f"{error_code} {error_type} {message}".lower()
    if "insufficient_quota" in normalized or "free tier" in normalized:
        return (
            "大模型免费额度已耗尽。请到阿里云百炼/模型服务控制台关闭“仅使用免费额度”模式，"
            "开通付费调用或切换到仍有额度的模型/API Key 后重试。"
        )

    if status in (401, 403):
        return f"大模型接口鉴权或权限失败（HTTP {status}）：{message or '请检查 API Key、工作空间地址和模型权限'}"

    return f"大模型接口请求失败（HTTP {status}）：{message or raw_body[:300]}"


def _extract_penalty_rate(text: str) -> float:
    """从合同文本中提取违约金比例"""
    if not text:
        return 0

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

    return 0


def _money(pattern: str, text: str) -> float:
    match = re.search(pattern, text, re.S)
    if not match:
        return 0
    try:
        return float(str(match.group(1)).replace(",", ""))
    except (TypeError, ValueError):
        return 0


def _first_money(text: str, patterns: list[str]) -> float:
    for pattern in patterns:
        value = _money(pattern, text)
        if value > 0:
            return value
    return 0


def _extract_standard_contract_data(text: str) -> dict:
    """从东莞驾培标准合同文本层直接提取费用结构，成功则无需调用大模型。"""
    normalized = _compact_spaced_digits(_normalize_text(text))
    contract_code_match = re.search(r"合同(?:编号|编码)[:：]?\s*([A-Z0-9]+)", normalized)
    total_fee = _first_money(normalized, [
        r"培训费用合计人民币\s*([\d.]+)\s*元",
        r"培训服务费合计\s*([\d.]+)\s*元",
    ])
    service_fee = _first_money(normalized, [
        r"综合服务费\s*([\d.]+)\s*元",
        r"综合服务费(?:（[^）]*）)?\s*([\d.]+)\s*元",
    ])
    theory_fee = _first_money(normalized, [
        r"理论培训费\s*([\d.]+)\s*元",
        r"理论培训费(?:（[^）]*）)?\s*([\d.]+)\s*元",
    ])
    subject2_fee = _first_money(normalized, [
        r"科目二实际操作培训费人民币\s*([\d.]+)\s*元",
        r"第二部分基础和场地驾驶培训费\s*([\d.]+)\s*元",
    ])
    subject3_fee = _first_money(normalized, [
        r"科目三实际操作培训费人民币\s*([\d.]+)\s*元",
        r"第三部分道路驾驶培训费\s*([\d.]+)\s*元",
    ])
    subject2_unit = _first_money(normalized, [
        r"科目二实际操作培训费人民币\s*[\d.]+\s*元（学时单价为\s*([\d.]+)\s*元/学时",
        r"第二部分基础和场地驾驶培训费\s*[\d.]+\s*元，退学退费时折算\s*学时单价\s*([\d.]+)\s*元/学时",
    ])
    subject3_unit = _first_money(normalized, [
        r"科目三实际操作培训费人民币\s*[\d.]+\s*元（学时单价为\s*([\d.]+)\s*元/学时",
        r"第三部分道路驾驶培训费\s*[\d.]+\s*元(?:（[^）]*）)?，\s*退学退费时折算\s*学时单价\s*([\d.]+)\s*元/学时",
    ])

    if total_fee <= 0 or service_fee <= 0 or theory_fee <= 0:
        return {}

    return {
        "contract_code": contract_code_match.group(1) if contract_code_match else "",
        "signing_date": "",
        "total_fee": total_fee,
        "actual_paid": total_fee,
        "penalty_rate": _extract_penalty_rate(normalized),
        "includes_exam_fee": False,
        "includes_makeup_fee": False,
        "exam_fee_table": {},
        "makeup_fee_table": {},
        "training_fees": {
            "subject2": {"unit_price": subject2_unit, "cap": subject2_fee},
            "subject3": {"unit_price": subject3_unit, "cap": subject3_fee},
        },
        "clauses_summary": [
            {"number": "第三条", "summary": "培训费用及学时单价"},
            {"number": "第七条", "summary": "退学退费扣费规则"},
        ],
        "handwritten_annotations": [],
        "deduction_items": [
            {"item": "综合服务费", "amount": service_fee, "basis": "合同第三条及第七条：已在平台备案注册的综合服务费按100%扣除"},
            {"item": "理论培训费", "amount": theory_fee, "basis": "合同第三条及第七条：已发计时IC卡的理论培训费按全额计算"},
        ],
        "special_terms": ["本合同不含体检、考前适应性训练、考试及补考费用"],
    }


# ── 上传合同费用提取 + 一致性校验（旧模板兼容 + OCR 噪声容错）──────────

_OCR_SEP = r"[\s_—_－\-–·]"


def _clean_ocr_noise(text: str) -> str:
    """OCR 噪声清洗：下划线/破折号/中点等分隔符 → 空格；金额尾随点去除（'490.元'→'490元'）。"""
    t = re.sub(_OCR_SEP + r"+", " ", text or "")
    t = re.sub(r"(?<=\d)\.(?=\s*元)", "", t)
    return t


# 付款计划金额与标签间允许的噪声（手写标记/下划线/全角空格，与闸门 _NOISE 同口径）
_PLAN_NOISE = r"[^\d元]{0,8}?"


def _extract_payment_plan(text: str, total_fee: float = 0.0) -> dict:
    """付款计划字段（spec: contract-ocr-baidu v3 §4 欠款口径）。

    实缴 = 付款计划「首付」；欠款（尾款）= total_fee − 首付 **本地推算**（0 额外 OCR 调用）。
    accurate_basic 常把小字「尾款1580」拆错成「尾580」→ 尾款 OCR 值一律不直接采信：
    与推算值不一致时以推算值为准并写 warning 提示人工核对（省调用下 handwriting 不触发）。
    """
    cleaned = _clean_ocr_noise(text or "")
    down = _first_money(cleaned, [
        r"首(?:次)?付(?:款)?(?:人民币)?" + _PLAN_NOISE + r"(\d+(?:\.\d+)?)\s*元",
        r"首(?:次)?付(?:款)?(?:人民币)?" + _PLAN_NOISE + r"(\d+(?:\.\d+)?)",
    ])
    balance_ocr = _first_money(cleaned, [
        r"(?:尾款?|欠款|余款)" + _PLAN_NOISE + r"(\d+(?:\.\d+)?)\s*元",
        r"(?:尾款?|欠款|余款)" + _PLAN_NOISE + r"(\d+(?:\.\d+)?)",
    ])

    warnings: list[str] = []
    balance = 0.0
    balance_source = ""
    if down > 0 and total_fee > 0:
        balance = round(total_fee - down, 2)
        balance_source = "derived"
        if balance < 0:
            warnings.append(
                f"合同付款计划首付 {down:g} 元大于培训费总额 {total_fee:g} 元，"
                "无法推算欠款，请人工核对"
            )
            balance = 0.0
            balance_source = ""
        elif balance_ocr > 0 and abs(balance_ocr - balance) > 0.01:
            warnings.append(
                f"合同付款计划「尾款」OCR 识别为 {balance_ocr:g} 元，与推算值"
                f"（总额 {total_fee:g} − 首付 {down:g} = {balance:g}）不一致，"
                "已按推算值处理，请人工核对"
            )
    elif balance_ocr > 0:
        # 无法推算（总额/首付缺失）时回退 OCR 值
        balance = balance_ocr
        balance_source = "ocr"

    return {
        "down_payment": down if down > 0 else None,
        "balance": balance if balance > 0 else None,
        "balance_source": balance_source,
        "warnings": warnings,
    }


def extract_contract_fees(contract_text: str) -> dict:
    """从上传合同文本提取费用结构 + 一致性校验告警（旧模板兼容 + OCR 噪声容错）。

    这是「上传合同」路径的扣费明细数据源（区别于 `_extract_standard_contract_data`
    面向东莞驾培电子合同）：按合同正文逐项提取，供扣费引擎以合同为准计算。

    Returns:
        {
          "total_fee": float,              # 培训费总额（普通培训合计）
          "theory_fee": float,             # 理论培训费
          "practical_unit_price": float,   # 实操单价（元/学时，采信合同正文）
          "exam_fees": {subjectN: float},  # 考试费分项
          "material_fee": float,           # 工本费
          "service_fee": float,            # 协助报考服务费合计
          "service_breakdown": {label: float},  # 服务费拆分明细
          "penalty_rate": float,           # 违约金比例（小数，0.1=10%）
          "warnings": [str],               # 「合计≠拆分」等一致性告警
        }
    提取失败字段为 0/空；warnings 供前端提示经办人人工核对合同错漏。
    """
    cleaned = _clean_ocr_noise(contract_text)
    warnings: list[str] = []

    # 视觉/LLM 会把手写金额标成「【手写】3580」「[手写] 3580」或在全角空格后给出数字，
    # 旧正则要求「合计人民币」与金额之间只有空白 → 手写金额一律漏抽（ISS-VC-01 P0-4）。
    # 这里容忍两者之间最多 8 个非数字字符（标记、下划线、全角空格）。
    _NOISE = r"[^\d]{0,8}?"

    total_fee = _first_money(cleaned, [
        r"培训费用(?:总额)?合计(?:人民币)?" + _NOISE + r"(\d+(?:\.\d+)?)\s*元",
        r"培训服务费合计" + _NOISE + r"(\d+(?:\.\d+)?)\s*元",
    ])

    # ── 手写总额可疑性闸门（ISS-VC-01，2026-09-14 实测补） ──────────────
    # 实测：免费视觉模型把本合同手写的「3580」读成「¥6800-2500」，并把
    # 「欠尾款1580」读成「欠4800」，读出来的组内自洽（2000+4800=6800），
    # 因此首付+欠款的一致性校验抓不住这类幻觉。
    # 策略：只要「合计人民币 … 元」这一段里出现两组及以上数字，或带减号/斜杠
    # 等修改痕迹，就拒绝自动采信，交人工录入 —— 宁可让经办人填一次，
    # 也不能让一个错误金额静默流进退费明细。
    _seg = re.search(r"培训费用(?:总额)?合计(?:人民币)?(.{0,80}?)元", cleaned, re.S)
    if _seg:
        _seg_text = _seg.group(1)
        _seg_nums = re.findall(r"\d+(?:\.\d+)?", _seg_text)
        if len(_seg_nums) >= 2 or re.search(r"[－\-—–—/／~～]", _seg_text):
            warnings.append(
                "合同「培训费用总额」处手写含修改痕迹或多组数字（识别为 "
                + " / ".join(_seg_nums)
                + "），无法确定唯一金额，请人工核对合同并录入培训费总额"
            )
            total_fee = 0.0

    theory_fee = _first_money(cleaned, [
        r"理论培训费(?:及相关手续费)?(?:人民币)?" + _NOISE + r"(\d+(?:\.\d+)?)\s*元",
        r"理论培训费(?:（[^）]*）)?" + _NOISE + r"(\d+(?:\.\d+)?)\s*元",
    ])

    practical_unit_price = _first_money(cleaned, [
        r"按人民(?:币)?\s*(\d+(?:\.\d+)?)\s*元/学时",
        r"科目二实际操作培训费人民币\s*[\d.]+\s*元（学时单价为\s*(\d+(?:\.\d+)?)\s*元/学时",
        r"第二部分基础和场地驾驶培训费\s*[\d.]+\s*元，退学退费时折算\s*学时单价\s*(\d+(?:\.\d+)?)\s*元/学时",
    ])

    exam_fees: dict[str, float] = {}
    for label, key in (("科目一", "subject1"), ("科目二", "subject2"), ("科目三", "subject3")):
        v = _first_money(cleaned, [
            rf"{label}考试费\s*(\d+(?:\.\d+)?)\s*元",
            rf"{label}\s*(\d+(?:\.\d+)?)\s*元",
        ])
        if v > 0:
            exam_fees[key] = v
    material_fee = _first_money(cleaned, [r"工本费\s*(\d+(?:\.\d+)?)\s*元"])

    service_fee = _first_money(cleaned, [r"协助报考服务费(?:合计)?\s*(\d+(?:\.\d+)?)\s*元"])
    service_breakdown: dict[str, float] = {}
    for label, key in (
        ("报名服务和学员卡费", "enroll_card"),
        ("科目一服务费", "subject1_service"),
    ):
        v = _first_money(cleaned, [
            rf"{label}\s*(\d+(?:\.\d+)?)\s*元",
            rf"{label}\s*(\d+(?:\.\d+)?)",
        ])
        if v > 0:
            service_breakdown[key] = v
    # 科目二三服务费（OCR 常漏「务」字：'科目二，三服费'）
    v23 = _first_money(cleaned, [r"科目二[，,、]?三服?务?费\s*(\d+(?:\.\d+)?)"])
    if v23 > 0:
        service_breakdown["subject23_service"] = v23

    penalty_rate = _extract_penalty_rate(cleaned)

    # ── 一致性校验：合计 vs 拆分 ──────────────────────────────
    if service_fee > 0 and service_breakdown:
        bd_sum = round(sum(service_breakdown.values()), 2)
        if abs(bd_sum - service_fee) > 0.01:
            parts = "+".join(f"{v:.0f}" for v in service_breakdown.values())
            warnings.append(
                f"协助报考服务费合计 {service_fee:.0f} 元，与拆分明细 {parts}={bd_sum:.0f} 元不一致，请人工核对"
            )

    # 代交费 = 各科目考试费 + 工本费；总金额 = 培训费总额 + 代交费 + 服务费（退费基数）
    agency_fee = round(sum(exam_fees.values()) + material_fee, 2)
    total_amount = round(total_fee + agency_fee + service_fee, 2)

    return {
        "total_fee": total_fee,
        "theory_fee": theory_fee,
        "practical_unit_price": practical_unit_price,
        "exam_fees": exam_fees,
        "material_fee": material_fee,
        "agency_fee": agency_fee,
        "service_fee": service_fee,
        "service_breakdown": service_breakdown,
        "penalty_rate": penalty_rate,
        "total_amount": total_amount,
        "warnings": warnings,
    }


# ── 合同集合分条结构（工单 03-contract-set-storage，ADR-0002） ─────────

# 提取来源 → 正文置信度：文本层直读无损失；百度 OCR 次之；本地 OCR 最弱。
TEXT_SOURCE_CONFIDENCE = {
    "pdf_text": "high",
    "baidu_ocr": "high",
    "vision_text": "medium",
    "local_ocr": "low",
}


def text_confidence_of(source: str) -> str:
    return TEXT_SOURCE_CONFIDENCE.get(str(source or ""), "low")


def normalize_contract_set(raw) -> dict:
    """合同集合规范化：旧单份/畸形结构兼容为分条，绝不抛错。

    新结构每条含 kind/tier/file/text/text_source/text_confidence；
    旧结构条目（contract_id/title/total_fee/evidence/rules）按单条兼容读取，
    file 从 evidence.file 兜底补齐。非 dict 条目剔除。
    """
    contracts = []
    if isinstance(raw, dict) and isinstance(raw.get("contracts"), list):
        for item in raw["contracts"]:
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            evidence = entry.get("evidence") if isinstance(entry.get("evidence"), dict) else {}
            entry.setdefault("kind", "")
            entry.setdefault("tier", "")
            entry.setdefault("file", str(evidence.get("file") or ""))
            entry.setdefault("text", "")
            entry.setdefault("text_source", "")
            entry.setdefault("text_confidence", "")
            contracts.append(entry)
    return {"contracts": contracts}


def attach_contract_text(contract_set_raw, extraction: dict, filepath: str) -> dict:
    """把提取正文按文件引用写回合同集合条目（ADR-0002：正文随结果落库）。

    - file 与 filepath 匹配的条目 ← text/text_source/text_confidence；
    - 唯一条目兜底（旧分析结果 file 未登记场景）；
    - 集合为空 → 生成单条。
    """
    normalized = normalize_contract_set(contract_set_raw)
    contracts = normalized["contracts"]
    text = str(extraction.get("text") or "")
    if not text:
        return normalized
    source = str(extraction.get("source") or "")

    target = None
    for entry in contracts:
        if (entry.get("file") or "") == filepath:
            target = entry
            break
    if target is None and len(contracts) == 1:
        target = contracts[0]
    if target is None:
        target = {"kind": "", "tier": "", "file": filepath, "text": "", "text_source": "", "text_confidence": ""}
        contracts.append(target)
    target["text"] = text
    target["text_source"] = source
    target["text_confidence"] = text_confidence_of(source)
    return {"contracts": contracts}


def merge_analysis_into_contract_set(existing_raw, analysis_raw, filepath: str) -> dict:
    """把单份分析结果合并进工单合同集合（上传分条累积后的回填，工单 03/04）。

    - file 与 filepath 匹配的条目 ← 分析字段（rules/total_fee/title/contract_id/evidence/kind/tier/text*）；
    - 无匹配 → 追加为一条；集合为空或唯一条目未登记文件时直接采用；
    - 文件登记字段（file/analysis_path/sha256/filename）保留不动。
    """
    existing = normalize_contract_set(existing_raw)
    analysis = normalize_contract_set(analysis_raw)
    contracts = existing["contracts"]
    source_entry = dict(analysis["contracts"][0]) if analysis["contracts"] else None
    if source_entry is None:
        return {"contracts": contracts}

    target = None
    for entry in contracts:
        if (entry.get("file") or "") == filepath:
            target = entry
            break
    if target is None:
        if not contracts:
            return {"contracts": [source_entry]}
        if len(contracts) == 1 and not (contracts[0].get("file") or ""):
            target = contracts[0]
        else:
            contracts.append(source_entry)
            return {"contracts": contracts}

    # 档位/种类：分析条目暂空时保留条目已登记值（改档重算不丢用户选择）
    for key in ("kind", "tier"):
        if not source_entry.get(key) and target.get(key):
            source_entry[key] = target[key]
    preserved = {k: target[k] for k in ("file", "analysis_path", "sha256", "filename") if k in target}
    target.clear()
    target.update(source_entry)
    target.update(preserved)
    return {"contracts": contracts}


def contract_set_from_ai_data(data: dict, source_file: str, source: str = "ai_candidate") -> dict:
    """Turn extracted contract facts into rules while excluding AI-calculated exam amounts."""
    total_fee = _as_float(data.get("total_fee", 0))
    rules = []
    for item in data.get("deduction_items", []):
        amount = _as_float(item.get("amount", 0))
        name = str(item.get("item") or "")
        dynamic_keywords = ("违约金", "考试费", "补考费", "工本费", "实操", "科目一", "科目二", "科目三")
        if amount > 0 and not any(keyword in name for keyword in dynamic_keywords):
            rules.append({
                "type": "fixed",
                "item": name or "合同固定费用",
                "amount": amount,
                "clause": str(item.get("basis") or "合同退费条款"),
            })
    # 违约金：统一为固定金额（元）。优先取 deduction_items 里的违约金金额；
    # 若仅有比例（penalty_rate），按合同总额折算成金额。
    # 注意：违约金规则固定排在所有扣费规则之后（展示顺序：违约金最后）。
    penalty_amount = 0.0
    for item in data.get("deduction_items", []):
        if "违约金" in str(item.get("item") or ""):
            penalty_amount = _as_float(item.get("amount", 0))
            break
    penalty_rate = _as_float(data.get("penalty_rate", 0))
    if penalty_rate > 0:
        if penalty_rate <= 1:
            penalty_rate *= 100
        if penalty_amount <= 0 and total_fee > 0:
            penalty_amount = round(total_fee * penalty_rate / 100, 2)

    for subject_key, subject_label in (("subject2", "科目二"), ("subject3", "科目三")):
        fee = (data.get("training_fees") or {}).get(subject_key, {})
        unit_price = _as_float(fee.get("unit_price", 0))
        cap = _as_float(fee.get("cap", 0))
        if unit_price > 0:
            rules.append({
                "type": "training_hour_fee",
                "item": f"{subject_label}实操培训费",
                "subject": subject_label,
                "hourly_rate": unit_price,
                "max_amount": cap if cap > 0 else None,
                "clause": "合同退费实操培训费条款",
            })

    # 考试费 / 补考费：金额来自 AI 提取的合同考试费表（或人工补录），次数由规则引擎从内部系统考试次数计算
    includes_exam = bool(data.get("includes_exam_fee", True))
    includes_makeup = bool(data.get("includes_makeup_fee", True))
    exam_table = data.get("exam_fee_table") or {}
    makeup_table = data.get("makeup_fee_table") or {}
    for subject_key, subject_label in (("subject1", "科目一"), ("subject2", "科目二"), ("subject3", "科目三")):
        exam_fee = _as_float(exam_table.get(subject_key, 0))
        if includes_exam and exam_fee > 0:
            rules.append({
                "type": "exam_fee",
                "item": f"{subject_label}考试费",
                "subject": subject_label,
                "amount": exam_fee,
                "clause": "合同退费考试费条款",
            })
        makeup_fee = _as_float(makeup_table.get(subject_key, 0))
        # 不判断补考次数：本函数产出的是「单次金额」规则模板，次数由下游结合三系统
        # 考试次数计算（refund_engine._calculate_rule: makeup_count = max(attempts - 1, 0)，
        # 并自行处理 count == 0）。与上方考试费规则、以及人工复核版
        # contract_set_from_reviewed_fields 的 `if includes_makeup and makeup_fee > 0` 对称。
        # 历史 bug：此处曾误加未定义变量 `makeup_count > 0`，导致 AI 路径 NameError。
        if includes_makeup and makeup_fee > 0:
            rules.append({
                "type": "makeup_fee",
                "item": f"{subject_label}补考费",
                "subject": subject_label,
                "amount": makeup_fee,
                "clause": "合同退费补考费条款",
            })
    if penalty_amount > 0:
        rules.append({
            "type": "fixed_penalty",
            "item": "违约金",
            "amount": penalty_amount,
            "clause": "合同退费违约金条款",
        })
    return {
        "contracts": [{
            "contract_id": str(data.get("contract_code") or "extracted-contract"),
            "title": "东莞驾培电子合同" if source == "local_rules" else "待人工确认合同",
            "total_fee": total_fee,
            "evidence": {"file": source_file, "source": source},
            "rules": rules,
        }],
    }


def contract_set_from_standard_data(data: dict, source_file: str) -> dict:
    """Convert deterministic PDF facts into the rule engine's canonical input."""
    return contract_set_from_ai_data(data, source_file, source="local_rules")


def contract_set_from_ai_response(content: str, source_file: str) -> dict:
    """Decode the structured candidate response without trusting its calculated totals."""
    json_str = (content or "").strip()
    if "```json" in json_str:
        json_str = json_str.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in json_str:
        json_str = json_str.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", json_str, re.DOTALL)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return contract_set_from_ai_data(data, source_file) if isinstance(data, dict) else {}


def _as_float(value, default: float = 0) -> float:
    try:
        return float(str(value).replace(",", "").replace("元", "").strip())
    except (TypeError, ValueError):
        return default


def _reviewed_value(fields: dict, name: str, default=0):
    value = (fields or {}).get(name, default)
    if isinstance(value, dict):
        value = value.get("value", default)
    return value


def contract_set_from_reviewed_fields(
    fields: dict,
    source_file: str,
    contract_code: str = "",
    evidence: dict | None = None,
) -> dict:
    """Convert human-reviewed contract facts into canonical dynamic refund rules."""
    if not isinstance(fields, dict):
        raise ValueError("contract_fields must be an object")
    total_fee = _as_float(_reviewed_value(fields, "total_fee"))
    refund_clause = str(_reviewed_value(fields, "refund_clause", "") or "").strip()
    if total_fee <= 0:
        raise ValueError("合同总培训费必须大于0")
    if not refund_clause:
        raise ValueError("退费条款不能为空")

    contract_evidence = dict(evidence or {})
    contract_evidence.setdefault("file", source_file)
    contract_evidence.setdefault("source", "human_review")
    rules = []

    for key, item in (
        ("service_fee", "服务费"),
        ("archive_fee", "建档费"),
        ("ic_card_fee", "学员IC卡费"),
        ("theory_fee", "理论培训费"),
    ):
        amount = _as_float(_reviewed_value(fields, key))
        if amount > 0:
            rules.append({
                "type": "fixed",
                "item": item,
                "amount": amount,
                "clause": refund_clause,
            })

    includes_exam = bool(_reviewed_value(fields, "includes_exam_fee", True))
    includes_makeup = bool(_reviewed_value(fields, "includes_makeup_fee", True))
    for subject_key, subject_name in (("subject1", "科目一"), ("subject2", "科目二"), ("subject3", "科目三")):
        exam_fee = _as_float(_reviewed_value(fields, f"{subject_key}_exam_fee"))
        makeup_fee = _as_float(_reviewed_value(fields, f"{subject_key}_makeup_fee"))
        if includes_exam and exam_fee > 0:
            rules.append({
                "type": "exam_fee",
                "item": f"{subject_name}考试费",
                "subject": subject_name,
                "amount": exam_fee,
                "clause": refund_clause,
            })
        if includes_makeup and makeup_fee > 0:
            rules.append({
                "type": "makeup_fee",
                "item": f"{subject_name}补考费",
                "subject": subject_name,
                "amount": makeup_fee,
                "clause": refund_clause,
            })

    for subject_key, subject_name in (("subject2", "科目二"), ("subject3", "科目三")):
        rate = _as_float(_reviewed_value(fields, f"{subject_key}_unit_price"))
        cap = _as_float(_reviewed_value(fields, f"{subject_key}_cap"))
        if rate > 0:
            rule = {
                "type": "training_hour_fee",
                "item": f"{subject_name}实操培训费",
                "subject": subject_name,
                "hourly_rate": rate,
                "clause": refund_clause,
            }
            if cap > 0:
                rule["max_amount"] = cap
            rules.append(rule)

    license_fee = _as_float(_reviewed_value(fields, "license_fee"))
    if license_fee > 0:
        rules.append({
            "type": "stage_fee",
            "item": "驾驶证工本费",
            "stage": 4,
            "amount": license_fee,
            "clause": refund_clause,
        })

    penalty_amount = _as_float(_reviewed_value(fields, "penalty_amount"))
    if penalty_amount <= 0:
        # 兼容旧字段 penalty_rate（百分比）：按合同总额折算成固定金额
        penalty_rate = _as_float(_reviewed_value(fields, "penalty_rate"))
        if 0 < penalty_rate <= 1:
            penalty_rate *= 100
        if penalty_rate > 0 and total_fee > 0:
            penalty_amount = round(total_fee * penalty_rate / 100, 2)
    if penalty_amount > 0:
        rules.append({
            "type": "fixed_penalty",
            "item": "违约金",
            "amount": penalty_amount,
            "clause": refund_clause,
        })

    return {
        "contracts": [{
            "contract_id": str(contract_code or "reviewed-paper-contract"),
            "title": "人工核对纸质培训合同",
            "total_fee": total_fee,
            "evidence": contract_evidence,
            "rules": rules,
        }],
    }


def _deduction_amount(deduction_items: list, keywords: tuple[str, ...]) -> float:
    for item in deduction_items or []:
        name = str(item.get("item", ""))
        if any(keyword in name for keyword in keywords):
            return _as_float(item.get("amount", 0))
    return 0


def _contract_field(value, source: str = "contract", evidence: str = "") -> dict:
    return {
        "value": value,
        "source": source,
        "evidence": evidence,
    }


def _build_contract_fields(data: dict, result: dict) -> dict:
    """给前端字段确认页使用的规范化合同字段。"""
    deduction_items = data.get("deduction_items", []) if isinstance(data, dict) else []
    training_fees = data.get("training_fees", {}) if isinstance(data, dict) else {}
    subject2 = training_fees.get("subject2", {}) if isinstance(training_fees, dict) else {}
    subject3 = training_fees.get("subject3", {}) if isinstance(training_fees, dict) else {}
    exam_fee_table = data.get("exam_fee_table", {}) if isinstance(data, dict) else {}
    makeup_fee_table = data.get("makeup_fee_table", {}) if isinstance(data, dict) else {}
    clauses = data.get("clauses_summary", data.get("clauses", [])) if isinstance(data, dict) else []
    special_terms = data.get("special_terms", []) if isinstance(data, dict) else []
    refund_clause = ""
    evidence_snippets = []

    for clause in clauses or []:
        if isinstance(clause, dict):
            text = f"{clause.get('number', '')} {clause.get('summary', '')}".strip()
        else:
            text = str(clause)
        if text:
            evidence_snippets.append(text)
        if not refund_clause and re.search(r"(退费|退学|解除|违约)", text):
            refund_clause = text

    for term in special_terms or []:
        term_text = str(term)
        if term_text:
            evidence_snippets.append(term_text)

    return {
        "status": "draft",
        "total_fee": _contract_field(result.get("total_fee", 0), "contract", "合同总培训费"),
        "service_fee": _contract_field(_deduction_amount(deduction_items, ("综合服务费", "服务费")), "contract", "固定扣费项目"),
        "archive_fee": _contract_field(_deduction_amount(deduction_items, ("建档费", "档案费")), "contract", "固定扣费项目"),
        "ic_card_fee": _contract_field(_deduction_amount(deduction_items, ("IC卡费", "学员IC卡")), "contract", "固定扣费项目"),
        "theory_fee": _contract_field(_deduction_amount(deduction_items, ("理论培训费", "理论费")), "contract", "固定扣费项目"),
        "subject2_unit_price": _contract_field(_as_float(subject2.get("unit_price", 0)), "contract", "科目二学时单价"),
        "subject2_cap": _contract_field(_as_float(subject2.get("cap", 0)), "contract", "科目二扣费上限"),
        "subject3_unit_price": _contract_field(_as_float(subject3.get("unit_price", 0)), "contract", "科目三学时单价"),
        "subject3_cap": _contract_field(_as_float(subject3.get("cap", 0)), "contract", "科目三扣费上限"),
        "penalty_amount": _contract_field(
            _deduction_amount(result.get("deductions", []), ("违约金",))
            or _deduction_amount(deduction_items, ("违约金",)),
            "contract", "违约金金额（元）"),
        "includes_exam_fee": _contract_field(bool(result.get("includes_exam_fee", True)), "contract", "考试费是否包含在合同总培训费内"),
        "includes_makeup_fee": _contract_field(bool(result.get("includes_makeup_fee", True)), "contract", "补考费是否包含在合同总培训费内"),
        "subject1_exam_fee": _contract_field(_as_float(exam_fee_table.get("subject1", 0)), "contract", "科目一考试费"),
        "subject2_exam_fee": _contract_field(_as_float(exam_fee_table.get("subject2", 0)), "contract", "科目二考试费"),
        "subject3_exam_fee": _contract_field(_as_float(exam_fee_table.get("subject3", 0)), "contract", "科目三考试费"),
        "license_fee": _contract_field(_as_float(exam_fee_table.get("license", 0)), "contract", "工本费"),
        "subject1_makeup_fee": _contract_field(_as_float(makeup_fee_table.get("subject1", 0)), "contract", "科目一补考费"),
        "subject2_makeup_fee": _contract_field(_as_float(makeup_fee_table.get("subject2", 0)), "contract", "科目二补考费"),
        "subject3_makeup_fee": _contract_field(_as_float(makeup_fee_table.get("subject3", 0)), "contract", "科目三补考费"),
        "refund_clause": _contract_field(refund_clause, "contract_clause", "退费/退学/违约相关条款"),
        "uncertain_fields": [],
        "blockers": [],
        "evidence_snippets": evidence_snippets[:6],
    }


def _attach_extraction_review(result: dict, extraction: dict) -> dict:
    fields = result.get("contract_fields") or _build_contract_fields({}, result)
    fields["status"] = extraction.get("fee_plan_status", "draft")
    fields["uncertain_fields"] = extraction.get("unclear_fields", [])
    fields["blockers"] = extraction.get("blockers", [])
    fields["extraction_source"] = extraction.get("extraction_source") or extraction.get("source", "")
    result["contract_fields"] = fields
    return result


def _build_llm_contract_context(contract_text: str) -> str:
    """只发送费用和退费相关条款，减少模型延迟和超时概率。"""
    normalized = _normalize_text(contract_text)
    snippets = []
    for pattern in ("培训收费约定", "培训费用合计", "退学退费相关约定", "违约金", "第七条", "第九条"):
        idx = normalized.find(pattern)
        if idx >= 0:
            start = max(0, idx - 260)
            end = min(len(normalized), idx + 1400)
            snippet = normalized[start:end]
            if snippet not in snippets:
                snippets.append(snippet)
    if not snippets:
        return normalized[:5000]
    return "\n\n--- 相关条款 ---\n\n".join(snippets)[:6000]


def _parse_hours(hours_str: str) -> float:
    """将 '12时36分' 格式转为十进制小时数"""
    if not hours_str:
        return 0
    hours = 0
    m = re.search(r'(\d+(?:\.\d+)?)\s*时', hours_str)
    if m:
        hours += float(m.group(1))
    m = re.search(r'(\d+)\s*分', hours_str)
    if m:
        hours += int(m.group(1)) / 60
    return round(hours, 2)


def _bool_from_contract(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "y", "是", "含", "包含", "包括"):
        return True
    if text in ("false", "0", "no", "n", "否", "不含", "不包含", "另收"):
        return False
    return default


def _exam_count(exam_counts: dict, subject: str) -> int:
    if not exam_counts:
        return 0
    aliases = {
        "subject1": ("科目一", "科一", "subject1", "k1"),
        "subject2": ("科目二", "科二", "subject2", "k2"),
        "subject3": ("科目三", "科三", "subject3", "k3"),
    }.get(subject, ())
    for key in aliases:
        if key in exam_counts:
            try:
                return max(0, int(float(exam_counts.get(key) or 0)))
            except (TypeError, ValueError):
                return 0
    return 0


def _parse_ai_response(content: str, exam_counts: dict = None, training_hours: dict = None) -> dict:
    """解析 AI 返回的 JSON，提取结构化数据"""
    result = {
        "total_fee": 0,
        "actual_paid": 0,
        "contract_code": "",
        "penalty_rate": 0,
        "deductions": [],
        "total_deduction": 0,
        "refund": 0,
        "summary": "",
        "clauses": [],
        "handwritten_annotations": [],
        "special_terms": [],
        "includes_exam_fee": True,
        "includes_makeup_fee": True,
        "exam_fee_table": {},
        "makeup_fee_table": {},
        "raw_analysis": content,
    }

    # Try to extract JSON from the response (handle possible markdown wrapping)
    json_str = content.strip()
    if "```json" in json_str:
        json_str = json_str.split("```json")[1].split("```")[0].strip()
    elif "```" in json_str:
        json_str = json_str.split("```")[1].split("```")[0].strip()

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        # Fallback: try to find JSON object with braces
        brace_match = re.search(r'\{.*\}', json_str, re.DOTALL)
        if brace_match:
            try:
                data = json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}

    if not data:
        return result

    result["contract_code"] = data.get("contract_code", "")
    result["total_fee"] = float(data.get("total_fee", 0) or 0)
    result["actual_paid"] = result["total_fee"]

    penalty = data.get("penalty_rate", 0)
    penalty = float(penalty) if penalty else 0
    if penalty > 1:
        penalty /= 100  # LLM 返回整数 10 而非小数 0.10
    result["penalty_rate"] = penalty

    result["clauses"] = data.get("clauses_summary", data.get("clauses", []))
    result["handwritten_annotations"] = data.get("handwritten_annotations", [])
    result["special_terms"] = data.get("special_terms", [])
    result["includes_exam_fee"] = _bool_from_contract(data.get("includes_exam_fee"), True)
    result["includes_makeup_fee"] = _bool_from_contract(data.get("includes_makeup_fee"), True)
    result["exam_fee_table"] = data.get("exam_fee_table", {}) if isinstance(data.get("exam_fee_table", {}), dict) else {}
    result["makeup_fee_table"] = data.get("makeup_fee_table", {}) if isinstance(data.get("makeup_fee_table", {}), dict) else {}

    # Extract deductions from JSON or compute
    deduction_items = data.get("deduction_items", [])
    if deduction_items:
        for item in deduction_items:
            amt = float(item.get("amount", 0) or 0)
            if amt > 0:
                # 这些项目必须由规则引擎基于合同字段和三系统进度计算，不能直接采用模型金额。
                name = item.get("item", "")
                skip_keywords = [
                    "违约金", "考试费", "补考费", "工本费",
                    "科目一", "科目二", "科目三", "实操", "场地驾驶", "道路驾驶",
                ]
                if any(k in name for k in skip_keywords):
                    continue
                result["deductions"].append({
                    "item": name,
                    "amount": amt,
                    "reason": item.get("basis", ""),
                })
                result["total_deduction"] += amt

    # 从违约金比例计算违约金（如果尚未包含在 deduction_items 中）。
    # 违约金固定排在所有扣费项之后（展示顺序：违约金最后）。
    penalty_row = None
    penalty_rate = result["penalty_rate"]
    if penalty_rate > 0 and result["total_fee"] > 0:
        has_penalty = any("违约金" in d.get("item", "") for d in result["deductions"])
        if not has_penalty:
            penalty_amount = round(result["total_fee"] * penalty_rate, 2)
            penalty_row = {
                "item": "违约金",
                "amount": penalty_amount,
                "penalty_rate": penalty_rate,
                "reason": f"违约金={result['total_fee']}×{penalty_rate*100}%={penalty_amount}元",
            }

    if result["includes_exam_fee"] and result["exam_fee_table"] and exam_counts:
        for subject_key, subject_label in [("subject1", "科目一"), ("subject2", "科目二"), ("subject3", "科目三")]:
            count = _exam_count(exam_counts, subject_key)
            exam_fee = _as_float(result["exam_fee_table"].get(subject_key, 0))
            if count > 0 and exam_fee > 0:
                result["deductions"].append({
                    "item": f"{subject_label}考试费",
                    "amount": exam_fee,
                    "exam_count": count,
                    "reason": f"三系统显示{subject_label}考试{count}次，合同考试费{exam_fee}元",
                })
                result["total_deduction"] += exam_fee

            makeup_fee = _as_float(result["makeup_fee_table"].get(subject_key, 0))
            makeup_count = max(0, count - 1)
            if result["includes_makeup_fee"] and makeup_count > 0 and makeup_fee > 0:
                amount = round(makeup_count * makeup_fee, 2)
                result["deductions"].append({
                    "item": f"{subject_label}补考费",
                    "amount": amount,
                    "exam_count": count,
                    "makeup_count": makeup_count,
                    "reason": f"三系统显示{subject_label}补考{makeup_count}次，合同补考费{makeup_fee}元/次",
                })
                result["total_deduction"] += amount

    # ── 实操培训费：用第三系统实际学时 × 合同单价 ──
    training_fees = data.get("training_fees", {})
    if training_hours and training_fees:
        for subject_key, subject_label in [("subject2", "科目二"), ("subject3", "科目三")]:
            fee_info = training_fees.get(subject_key, {})
            unit_price = float(fee_info.get("unit_price", 0) or 0)
            cap = float(fee_info.get("cap", 0) or 0)
            if unit_price <= 0:
                continue
            # 找对应的培训学时
            hours_str = ""
            if subject_label in training_hours:
                hours_str = training_hours[subject_label]
            elif "二" in str(training_hours) and subject_key == "subject2":
                hours_str = training_hours.get(list(training_hours.keys())[0], "")
            hours_val = _parse_hours(hours_str)
            if hours_val <= 0:
                continue
            calculated = round(hours_val * unit_price, 2)
            raw_calc = calculated
            is_capped = cap > 0 and raw_calc > cap
            if is_capped:
                calculated = cap
            result["deductions"].append({
                "item": f"{subject_label}实操费",
                "amount": calculated,
                "duration": hours_str,
                "unit_price": f"{unit_price}元/学时",
                "max_amount": cap if cap > 0 else 0,
                "raw_amount": raw_calc if is_capped else 0,
                "reason": f"总时长{hours_str}×{unit_price}元/学时{'，已超合同科目上限'+str(cap)+'元' if is_capped else ''}",
            })
            result["total_deduction"] += calculated

    # 违约金固定排在最后
    if penalty_row:
        result["deductions"].append(penalty_row)
        result["total_deduction"] += penalty_row["amount"]

    result["refund"] = max(0, result["actual_paid"] - result["total_deduction"])
    result["summary"] = (
        f"合同金额{result['total_fee']}元，"
        f"实际已交{result['actual_paid']}元，"
        f"违约金比例{result['penalty_rate']*100}%，"
        f"总扣费{result['total_deduction']}元，"
        f"应退{result['refund']}元"
    )
    result["contract_fields"] = _build_contract_fields(data, result)

    return result


def apply_authoritative_total_fee(result: dict, contract_fee: float) -> dict:
    """东莞驾培 contract_fee 是权威合同金额：覆盖 AI/规则解析结果并重算扣费与应退。

    AI/规则结果仅作校验对比。违约金按权威金额重算，总扣费封顶、应退金额与摘要同步更新。
    """
    if result.get("error") or not contract_fee or contract_fee <= 0:
        return result

    result["total_fee"] = contract_fee
    result["actual_paid"] = contract_fee

    penalty_rate = result.get("penalty_rate", 0) or 0
    for deduction in result.get("deductions", []):
        if "违约金" in deduction.get("item", ""):
            amount = round(contract_fee * penalty_rate, 2)
            deduction["amount"] = amount
            deduction["reason"] = f"违约金={contract_fee}×{penalty_rate*100}%={amount}元"

    result["total_deduction"] = round(
        sum(float(d.get("amount", 0) or 0) for d in result.get("deductions", [])), 2
    )
    result["refund"] = max(0, round(result["actual_paid"] - result["total_deduction"], 2))
    result["summary"] = (
        f"合同金额{result['total_fee']}元，"
        f"实际已交{result['actual_paid']}元，"
        f"违约金比例{penalty_rate*100}%，"
        f"总扣费{result['total_deduction']}元，"
        f"应退{result['refund']}元"
    )

    total_field = result.get("contract_fields", {}).get("total_fee")
    if isinstance(total_field, dict):
        total_field["value"] = contract_fee

    return result


def analyze_contract(
    contract_text: str,
    exam_stage: str = "",
    training_hours: dict = None,
    total_fee: float = 0,
    exam_counts: dict = None,
) -> dict:
    """调用大模型分析合同文本"""
    llm = _llm_config("llm_contract_text")

    api_url = llm.get("api_url", "").strip()
    api_key = llm.get("api_key", "").strip()
    model = llm.get("model", "").strip()

    if not api_url or not api_key or not model:
        return {
            "error": "大模型 API 未配置：请到「系统设置 → AI大模型配置」填写 API地址、API Key 和 模型名称后重试。"
        }

    hours_desc = ""
    if training_hours:
        for subject, hours in training_hours.items():
            hours_desc += f"{subject}：{hours}学时；"

    exam_counts_desc = ""
    if exam_counts:
        for subj, cnt in exam_counts.items():
            exam_counts_desc += f"{subj}：{cnt}次；"

    contract_context = _build_llm_contract_context(contract_text)

    prompt = (
        "你是一名驾校合同审查专家。请分析以下驾校培训合同，\n"
        "输出 **纯 JSON**（不要包含任何其他文字）：\n"
        "\n"
        "```json\n"
        "{\n"
        '  "contract_code": "合同编号",\n'
        '  "signing_date": "签订日期",\n'
        '  "total_fee": 培训费总金额（数字）,\n'
        '  "actual_paid": 学员实际已交金额（数字，与总金额相同则填总金额）,\n'
        '  "penalty_rate": 违约金比例（小数，如10%填0.1）,\n'
        '  "includes_exam_fee": 培训费是否含考试费（true/false，无法确认默认true）,\n'
        '  "includes_makeup_fee": 培训费是否含补考费（true/false，无法确认默认true）,\n'
        '  "exam_fee_table": {"subject1": 科目一考试费, "subject2": 科目二考试费, "subject3": 科目三考试费, "license": 工本费},\n'
        '  "makeup_fee_table": {"subject1": 科目一补考费, "subject2": 科目二补考费, "subject3": 科目三补考费},\n'
        '  "training_fees": {\n'
        '    "subject2": {"unit_price": "科目二单价(数字)", "cap": "科目二上限(数字)"},\n'
        '    "subject3": {"unit_price": "科目三单价(数字)", "cap": "科目三上限(数字)"}\n'
        "  },\n"
        '  "clauses_summary": [\n'
        '    {"number": "第X条", "summary": "本条核心内容的简短概括（20字以内）"}\n'
        "  ],\n"
        '  "handwritten_annotations": [\n'
        '    {"location": "出现位置", "content": "手写内容原文"}\n'
        "  ],\n"
        '  "deduction_items": [\n'
        '    {"item": "扣费项目名称", "amount": 金额, "basis": "依据条款"}\n'
        "  ],\n"
        '  "special_terms": ["特殊/补充条款说明"]\n'
        "}\n"
        "```\n"
        "\n"
        "**要求：**\n"
        "1. 提取合同中的**关键费用信息**：培训费总额、基础扣费项目、考试费表、补考费表、违约金比例、各科目学时单价和上限\n"
        "2. **特别注意**识别合同中的**手写内容**（金额修改、日期、备注、补充条款等），放在 handwritten_annotations\n"
        "3. deduction_items 只列出合同中明确的固定扣费项目（如服务费、建档费、学员IC卡等），**不要包含考试费、补考费、科目二/科目三实操费、违约金**（由系统根据进度和规则计算）\n"
        "4. 如果看不清的文字，用 '[模糊]' 标注\n"
        "5. 金额数字保留原始格式（含小数点）\n"
        "\n"
        "【学员情况】\n"
        f"- 当前阶段：{exam_stage or '未知'}\n"
        f"- 培训学时：{hours_desc or '未提供'}\n"
        f"- 考试次数：{exam_counts_desc or '未提供'}\n"
        "\n"
        "【合同内容】\n"
        f"{contract_context}\n"
    )

    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": min(int(llm.get("max_tokens", 2048) or 2048), 2048),
            "temperature": 0.05,
        }

        resp = requests.post(api_url, headers=headers, json=payload, timeout=60)
        try:
            resp.raise_for_status()
        except requests.HTTPError:
            return {"error": _format_llm_error(resp)}
        data = resp.json()

        if "error" in data:
            return {"error": f"API错误: {data['error']}"}

        if not data.get("choices"):
            return {"error": "API返回空结果"}

        content = data["choices"][0]["message"]["content"]
        system_logger.info("[AI分析] %s...", content[:200])

        # 解析 AI 返回的内容（含实操培训费计算）
        result = _parse_ai_response(content, exam_counts, training_hours)

        return result

    except requests.exceptions.ReadTimeout:
        return {"error": "分析失败: 大模型响应超过60秒。已优先尝试本地规则解析；请稍后重试或切换更快模型。"}
    except Exception as e:
        return {"error": f"分析失败: {str(e)}"}


def _detect_special_cases(registration_date: str, exam_counts: dict = None, skill_cert_date: str = "") -> list[dict]:
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

    # 2. 技能证时间 > 3年
    if skill_cert_date:
        try:
            sc_date = datetime.strptime(skill_cert_date[:10], "%Y-%m-%d").date()
            if sc_date < three_years_ago:
                warnings.append({
                    "type": "skill_cert_expired",
                    "message": "该学员技能证（科目一通过日期）已超过3年，此种情况下没有费用退还。",
                    "reply_text": "该学员技能证（科目一通过日期）已超过3年，根据合同约定，此种情况下没有费用退还。",
                })
        except (ValueError, AttributeError):
            pass

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
    skill_cert_date: str = "",
) -> dict:
    """从合同文件进行分析：先提取文本，再让文本模型结构化。"""
    extraction = extract_contract_text_from_file(filepath, image_paths=image_paths)
    if extraction.get("error"):
        return extraction

    contract_text = extraction["text"]
    system_logger.info("✅ 识别完成，%d 字符，来源: %s", len(contract_text), extraction.get("source"))

    if not extraction.get("can_confirm_fee_plan", True):
        result = _blank_analysis_result("合同关键字段识别不完整，请补充清晰合同或人工补录后再确认费用方案。")
        result.update({k: v for k, v in extraction.items() if k != "text"})
        _attach_extraction_review(result, extraction)
        result["special_warnings"] = _detect_special_cases(registration_date, exam_counts, skill_cert_date)
        # 正文随结果落库（ADR-0002 硬前提）——识别不完整也要留正文供预览/人工核对
        result["contract_set"] = attach_contract_text(result.get("contract_set") or {}, extraction, filepath)
        return result

    standard_data = _extract_standard_contract_data(contract_text)
    if standard_data:
        result = _parse_ai_response(json.dumps(standard_data, ensure_ascii=False), exam_counts, training_hours)
        result["contract_set"] = contract_set_from_standard_data(standard_data, filepath)
        result["analysis_source"] = "local_rules"
        result["summary_lines"] = [line.strip() for line in result["summary"].split("\n") if line.strip()]
        result["special_warnings"] = _detect_special_cases(registration_date, exam_counts, skill_cert_date)
        result.update({k: v for k, v in extraction.items() if k != "text"})
        _attach_extraction_review(result, extraction)
        result["contract_set"] = attach_contract_text(result["contract_set"], extraction, filepath)
        return result

    result = analyze_contract(
        contract_text=contract_text,
        exam_stage=exam_stage,
        training_hours=training_hours,
        total_fee=total_fee,
        exam_counts=exam_counts,
    )
    if not result.get("error"):
        result["contract_set"] = contract_set_from_ai_response(
            result.get("raw_analysis", ""),
            filepath,
        )
    else:
        # BUG-02：LLM 失败（典型 OpenRouter 免费池 429）时本地 OCR 已提取的费用
        # 不应被整体丢弃——用 contract_text 本地兜底回填费用字段，失败不影响原 error 返回。
        try:
            fees = extract_contract_fees(contract_text)
            if not result.get("total_fee"):
                result["total_fee"] = fees.get("total_fee")
            payment_plan = extraction.get("payment_plan") or {}
            if "down_payment" not in result and payment_plan.get("down_payment") is not None:
                result["down_payment"] = payment_plan["down_payment"]
            if "balance" not in result and payment_plan.get("balance") is not None:
                result["balance"] = payment_plan["balance"]
            if "balance_source" not in result and payment_plan.get("balance_source"):
                result["balance_source"] = payment_plan["balance_source"]
            result.setdefault("warnings", []).append(
                "大模型分析失败，费用字段为本地兜底提取值，请人工核对后确认"
            )
        except Exception as exc:
            system_logger.warning("[FALLBACK-FEE] 本地兜底回填费用失败：%s", exc)

    # Build multi-line summary
    summary_lines = []
    if result.get("summary"):
        summary_lines = [line.strip() for line in result["summary"].split("\n") if line.strip()]
    result["summary_lines"] = summary_lines

    # 注入特殊退费检测警告
    result["special_warnings"] = _detect_special_cases(registration_date, exam_counts, skill_cert_date)
    result.update({k: v for k, v in extraction.items() if k != "text"})
    _attach_extraction_review(result, extraction)
    result["contract_set"] = attach_contract_text(result.get("contract_set") or {}, extraction, filepath)
    return result
