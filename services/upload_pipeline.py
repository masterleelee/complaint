"""上传件分析管线（工单 04-analysis-pipeline，ADR-0003 + spec Implementation Decisions）。

流程（每份上传文件）：
  1. 提取文本：复用 `services.contract_service.extract_contract_text_from_file` 现有降级链
     （文本层 PDF → 视觉 LLM → 本地 OCR；ADR-0002 硬前提：正文随结果落库）
  2. 档位识别：`services.contract_tiers.identify_tier`（年份 + 网点类型 → 候选；特征句打分；
     文本与权威数据冲突输出告警证据）
  3. 扣费引擎（仅档位可定时）：`services.deduction_engine.calculate_deductions` 按档位 +
     阶段 + 进度 + 手写金额算有序明细（pending 项不计入合计，refund_pending=True）
  4. 缓存键产出：文件指纹 + 档位 + 进度哈希（任一变化即重算；ticket 04 阶段产出与消费留待 08）
  5. 合同集合分条 + 正文落库：`attach_contract_text`

失败语义：提取/识别任意环节失败以 `extraction_error` / 空 `tier_id` / 空 `cache_key` /
`deductions_result=None` 返回，**不抛未捕获异常**——上层 UI 可识别并提示。

下载链路：本模块不改 `services.contract_service.analyze_contract_from_file` 的输出结构；
`is_upload_ticket()` 判定 ticket 的合同集合由上传路径填充（`sha256` 标记）后，上层
（`app.py _run_contract_analysis`）才把本模块的输出合并进 result。
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from services.contract_service import (
    attach_contract_text,
    extract_contract_fees,
    extract_contract_text_from_file,
)
from services.contract_tiers import TIERS_BY_ID, identify_tier
from services.deduction_engine import calculate_deductions
from services.anchor_resolver import (
    detect_text_conflicts,
    resolve_anchors_for_items,
)


# ── ticket 判定：upload 来源的 contract_set 每条带 sha256（app.py:2707 写入） ───

def is_upload_ticket(contract_set_raw: Any) -> bool:
    """判定 ticket 的合同集合是否由上传路径填充（每份含 sha256 标记）。"""
    if not contract_set_raw:
        return False
    if isinstance(contract_set_raw, str):
        try:
            contract_set_raw = json.loads(contract_set_raw)
        except (TypeError, ValueError):
            return False
    contracts = contract_set_raw.get("contracts") if isinstance(contract_set_raw, dict) else None
    if not isinstance(contracts, list) or not contracts:
        return False
    return any(isinstance(c, dict) and c.get("sha256") for c in contracts)


# ── 内部：进度/总额解析 ──────────────────────────────────────────────

def _as_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _resolve_progress(ticket: dict) -> dict:
    """从 ticket 提取扣费引擎需要的 progress 字段。"""
    qr = ticket.get("query_result") or {}
    if isinstance(qr, str):
        try:
            qr = json.loads(qr)
        except (TypeError, ValueError):
            qr = {}
    training_hours = (qr.get("driving_hours") if isinstance(qr, dict) else None) or {}
    if not isinstance(training_hours, dict):
        training_hours = {}
    exam_counts = ticket.get("exam_counts") or {}
    if not isinstance(exam_counts, dict):
        exam_counts = {}
    license_type = ticket.get("license_type") or "C1"
    return {
        "exam_counts": exam_counts,
        "training_hours": training_hours,
        "license_type": license_type,
    }


def _progress_hash(ticket: dict) -> str:
    """进度哈希：阶段 + 培训费 + 已考次数 + 审核学时 + 车型 + 东城自制字段；用于缓存键。"""
    payload = {
        "exam_stage": str(ticket.get("exam_stage") or ""),
        "total_fee": ticket.get("total_fee"),
        "exam_counts": ticket.get("exam_counts") or {},
        "license_type": str(ticket.get("license_type") or ""),
        "service_fee": ticket.get("service_fee"),
        "training_mode": str(ticket.get("training_mode") or ""),
    }
    qr = ticket.get("query_result") or {}
    if isinstance(qr, str):
        try:
            qr = json.loads(qr)
        except (TypeError, ValueError):
            qr = {}
    if isinstance(qr, dict):
        payload["training_hours"] = qr.get("driving_hours") or {}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


# ── 缓存键：文件指纹 + 档位 + 进度哈希 ────────────────────────────────

def _compute_cache_key(
    filepath: str,
    tier_id: str,
    ticket: dict,
    *,
    image_paths: list | None = None,
) -> str:
    file_facts: list[tuple[str, int, int]] = []
    for path in ([filepath] + list(image_paths or [])):
        if not path:
            continue
        try:
            st = os.stat(path)
            file_facts.append((os.path.realpath(path), st.st_size, st.st_mtime_ns))
        except OSError:
            continue
    raw = json.dumps(
        {
            "files": sorted(file_facts),
            "tier_id": tier_id or "",
            "progress": _progress_hash(ticket),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── 金额口径：合同金额 / 已支付 / 尾款冲抵（用户拍板 2026-09-14） ────────

# 本地 OCR 对印刷体尚可，但手写数字错识率高（实测把扫描件手写「3580」读成「3.0」）。
# 这些来源抽出的金额不直接进扣费引擎，改标 pending 由经办人补录。
LOW_QUALITY_TEXT_SOURCES = frozenset({"local_ocr"})

# 机动车驾驶培训合同不可能低于起步必扣额（2023 分校/分店档服务费600+建档费300+
# 学员IC卡100=1000）。低于该线的金额必是误识，不是真实合同额。
_MIN_PLAUSIBLE_TOTAL_FEE = 1000.0


def _attach_payment_context(
    deductions_result: dict,
    total_fee: float,
    paid_amount: float,
    tail_due: float,
    *,
    low_quality_amount: bool = False,
) -> dict:
    """把合同金额、已支付、应付尾款与实退金额一并返回面板。

    口径（用户拍板，2026-09-14，见 `.scratch/contract-vision-failure-20260914.md`）：
    1. 退费基数 = 已支付金额（不是合同额）；
    2. 违约金基数 = 合同金额 × 20%（合同原文「全部培训费用的 20%」）；
    3. 应退金额先冲抵尚未支付的尾款，剩余部分才是实退。

    引擎侧 `refund` 保持「应退核算值」语义不变，新增 `net_refund` 表示冲抵后实退；
    pending 状态（上游缺失）时 net_refund 为 None，由面板提示人工补录。
    """
    dr = dict(deductions_result or {})
    if total_fee is None:
        # 合同额确实未知：显示 None（面板提示「待录入」），不能显示 0 元 —— 0 元
        # 会让经办人以为「合同免费」，是更危险的误导（ISS-VC-01 实跑暴露）。
        dr["total_fee"] = None
    else:
        total = float(total_fee or 0)
        dr["total_fee"] = round(total, 2)
    total = float(total_fee or 0)
    paid = float(paid_amount or 0)
    tail = float(tail_due or 0)
    dr["paid_amount"] = round(paid, 2)
    dr["tail_due"] = round(tail, 2)

    if total <= 0 and low_quality_amount:
        dr["total_fee"] = None
        dr["refund_pending"] = True
        dr["warnings"] = list(dr.get("warnings") or []) + [
            "本地 OCR 未能可靠识别合同培训费用总额，请在「合同金额」处人工录入后重试；"
            "已支付金额与违约金基数需以人工金额为准。"
        ]

    if dr.get("refund_pending"):
        dr["net_refund"] = None
        return dr

    refund = float(dr.get("refund") or 0)
    net = max(0.0, refund - tail)
    dr["net_refund"] = round(net, 2)
    if tail > 0:
        outstanding = max(0.0, tail - refund)
        note = (
            f"学员已支付 {paid:.0f} 元，合同尚有未付尾款 {tail:.0f} 元；"
            f"应退 {refund:.0f} 元冲抵尾款后实退 {net:.0f} 元"
        )
        if outstanding > 0:
            note += f"（不足冲抵的 {outstanding:.0f} 元仍为学员应付）"
        dr["warnings"] = list(dr.get("warnings") or []) + [note]
    return dr


# ── 文本入口（已知文本，跳过 PDF 提取；测试与契约复用） ───────────────

def analyze_upload_contract_text(
    contract_text: str,
    ticket: dict,
    *,
    text_source: str = "pdf_text",
    source_path: str = "",
) -> dict:
    """已知合同正文的入口。生产由 analyze_upload_contract_file 调用；测试直接用。"""
    extraction = {
        "text": contract_text or "",
        "source": text_source or "",
        "can_confirm_fee_plan": bool(contract_text),
    }
    return _run_pipeline(extraction, ticket, source_path=source_path, image_paths=[])


# ── 文件入口（生产路径：完整提取降级链） ──────────────────────────────

def analyze_upload_contract_file(
    filepath: str,
    ticket: dict,
    *,
    image_paths: list | None = None,
) -> dict:
    """上传件分析管线（生产入口）。"""
    extraction = extract_contract_text_from_file(
        filepath,
        image_paths=image_paths or [],
    )
    return _run_pipeline(
        extraction,
        ticket,
        source_path=filepath,
        image_paths=image_paths or [],
    )


# ── 主流程（私有） ────────────────────────────────────────────────────


def _append_fee_items_pending(deductions_result: dict, fees: dict) -> None:
    """降级时补全扣费项目：把合同提取到、但扣费引擎未列的费用项追加为 pending（待填）。

    fees 为 extract_contract_fees 的返回；已有同名项跳过（避免与引擎已列项重复）。
    """
    existing = {it.get("item") for it in deductions_result.get("items", [])}

    def add(name: str, category: str) -> None:
        if name in existing:
            return
        deductions_result["items"].append({
            "category": category,
            "item": name,
            "amount": 0.0,
            "basis": "合同金额有错漏，待人工填写",
            "source": "pending",
            "confidence": "pending",
            "pending": True,
        })
        existing.add(name)

    for key, label in (("subject1", "科目一"), ("subject2", "科目二"), ("subject3", "科目三")):
        if (fees.get("exam_fees") or {}).get(key):
            add(f"{label}考试费", "依实")
    if fees.get("material_fee"):
        add("工本费", "依实")
    bd = fees.get("service_breakdown") or {}
    if bd.get("enroll_card"):
        add("报名/学员卡费", "依实")
    if bd.get("subject1_service"):
        add("科目一服务费", "依实")
    if bd.get("subject23_service"):
        add("科目二三服务费", "依实")


def _run_pipeline(
    extraction: dict,
    ticket: dict,
    *,
    source_path: str,
    image_paths: list,
) -> dict:
    """提取后：档位识别 → 扣费引擎 → 缓存键 → 正文落库。失败以字段表达，不抛。"""
    if extraction.get("error"):
        return {
            "tier_id": "",
            "tier_result": {},
            "deductions_result": None,
            "cache_key": "",
            "text_source": str(extraction.get("source") or ""),
            "extraction_error": str(extraction["error"]),
            "contract_set": attach_contract_text({}, extraction, source_path or ""),
        }

    contract_text = extraction.get("text", "") or ""
    text_source = str(extraction.get("source") or "")

    # 1) 档位识别（ADR-0001：权威数据收敛 + 特征句打分 + 冲突告警）
    tier_result = identify_tier(
        registration_date=str(ticket.get("registration_date") or ""),
        org_unit_type=str(ticket.get("organization_unit_type") or ""),
        contract_text=contract_text,
        # P0-2：把提取来源透传，本地 OCR 文本禁止自动定档
        text_source=text_source,
    )
    tier_id = str(tier_result.get("tier_id") or "")

    # 2) 扣费引擎（仅档位可定时；不可定档时 deductions_result=None 让上层让用户手选）
    deductions_result = None
    if tier_id in TIERS_BY_ID:
        try:
            # 合同费用提取（旧模板兼容 + OCR 噪声容错 + 一致性校验）
            fees = extract_contract_fees(contract_text)
            # 用合同提取值构造 manual_amounts：理论费、实操单价、服务费拆分采信合同正文
            manual_amounts: dict[str, Any] = {}
            if fees.get("theory_fee"):
                manual_amounts["theory_fee"] = fees["theory_fee"]
            if fees.get("practical_unit_price"):
                manual_amounts["practical_unit_price"] = fees["practical_unit_price"]
            if fees.get("service_breakdown"):
                manual_amounts["service_breakdown"] = fees["service_breakdown"]

            # ISS-VC-01 P0-4：合同金额优先取工单录入值；未录入时回退合同正文抽取的手写
            # 总额（手写金额如「培训费用总额合计人民币【手写】3580 元」现已支持抽取）。
            ticket_total = _as_float(ticket.get("total_fee"))
            extracted_total = float(fees.get("total_fee") or 0)
            if ticket_total:
                total_fee = ticket_total
            elif extracted_total:
                total_fee = extracted_total
            else:
                # 两边都没有 → 保持 None（不是 0）：None 让引擎把理论培训费/违约金
                # 标记为 pending 而不是「0 元」，避免假装已算清楚
                total_fee = None
            # 退费基数口径（用户拍板 2026-09-14）：以「已支付金额」为基数；未录入则
            # 回退合同总金额（=培训费+代交费+服务费）。违约金基数恒为合同金额。
            # 本地 OCR 数字错识率高（实测把手写「3580」读成「3.0」），明显不合理的
            # 金额不得进入扣费引擎，改判 pending 交经办人补录。
            implausible_amount = (
                str(text_source) in LOW_QUALITY_TEXT_SOURCES
                and total_fee is not None
                and 0 < float(total_fee) < _MIN_PLAUSIBLE_TOTAL_FEE
            )
            if implausible_amount:
                total_fee = None

            paid_amount = _as_float(ticket.get("actual_paid")) or 0.0
            tail_due = max(0.0, float(total_fee or 0) - paid_amount)
            refund_base = paid_amount if paid_amount > 0 else (fees.get("total_amount") or float(total_fee or 0))

            deductions_result = calculate_deductions(
                tier=TIERS_BY_ID[tier_id],
                stage=str(ticket.get("exam_stage") or "已受理"),
                progress=_resolve_progress(ticket),
                total_fee=total_fee,
                manual_amounts=manual_amounts,
                service_fee=_as_float(ticket.get("service_fee")),
                training_mode=str(ticket.get("training_mode") or ""),
                total_amount=refund_base,
            )
            # 合同错漏校验（合计≠拆分等，如罗炳灿服务费 460≠300+130+130）：
            # 检测到错漏 → 降级为「列扣费项目 + 金额人工填写」，不自动算具体金额。
            if fees.get("warnings"):
                for item in deductions_result["items"]:
                    item["pending"] = True
                    item["amount"] = 0
                # 补全合同提取到、但扣费引擎未列的费用项（考试费/工本费/服务费拆分）
                _append_fee_items_pending(deductions_result, fees)
                deductions_result["total_deduction"] = 0.0
                deductions_result["refund_pending"] = True
                deductions_result["refund"] = 0.0
                deductions_result["warnings"] = (
                    list(deductions_result.get("warnings") or []) + fees["warnings"]
                )
            deductions_result = _attach_payment_context(
                deductions_result, total_fee, paid_amount, tail_due,
                low_quality_amount=implausible_amount,
            )
        except Exception as exc:  # pragma: no cover — 防御性兜底
            deductions_result = {
                "items": [],
                "total_deduction": 0,
                "refund": 0,
                "refund_pending": True,
                "warnings": [f"扣费引擎异常：{exc}"],
                "tier_id": tier_id,
                "stage": str(ticket.get("exam_stage") or ""),
            }

    # 2.5) 锚定 + 冲突告警（工单 05：每条明细解析 [start, end)；
    #   文本与档位默认冲突 → warnings 追加，但不修改金额）
    if deductions_result is not None:
        tier = TIERS_BY_ID[tier_id]
        deductions_result["items"] = resolve_anchors_for_items(
            deductions_result["items"], contract_text, tier=tier,
        )
        conflict_warnings = detect_text_conflicts(contract_text, tier)
        if conflict_warnings:
            deductions_result["warnings"] = list(deductions_result.get("warnings") or []) + conflict_warnings

        # 静默降级是这套流程最危险的失效模式：视觉识别不可用时管线会回退本地 OCR，
        # 而本地 OCR 的手写数字错识率极高（实测 3580 → 3.0），面板上却看不出差别。
        # 必须在明细里明示文本来源，逼经办人回看合同原件（ISS-VC-01）。
        if text_source in LOW_QUALITY_TEXT_SOURCES:
            deductions_result["warnings"] = list(deductions_result.get("warnings") or []) + [
                "本次未能使用视觉识别，已回退本地 OCR：文字与金额均可能不准，"
                "请务必对照合同原件核对后确认费用方案。"
            ]

    # 3) 缓存键：文件指纹 + 档位 + 进度哈希
    cache_key = _compute_cache_key(
        source_path,
        tier_id,
        ticket,
        image_paths=image_paths,
    )

    # 4) 正文随结果落库（ADR-0002）—— 即使 source_path 为空（文本入口）也走 attach，
    # 让 attach 用占位 file="" 创建一条目，便于测试与契约复用
    contract_set = attach_contract_text({}, extraction, source_path or "")

    return {
        "tier_id": tier_id,
        "tier_result": tier_result,
        "deductions_result": deductions_result,
        "cache_key": cache_key,
        "text_source": text_source,
        "extraction_error": None,
        "contract_set": contract_set,
    }
