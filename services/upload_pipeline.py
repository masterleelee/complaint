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
    )
    tier_id = str(tier_result.get("tier_id") or "")

    # 2) 扣费引擎（仅档位可定时；不可定档时 deductions_result=None 让上层让用户手选）
    deductions_result = None
    if tier_id in TIERS_BY_ID:
        try:
            deductions_result = calculate_deductions(
                tier=TIERS_BY_ID[tier_id],
                stage=str(ticket.get("exam_stage") or "已受理"),
                progress=_resolve_progress(ticket),
                total_fee=_as_float(ticket.get("total_fee")),
                service_fee=_as_float(ticket.get("service_fee")),
                training_mode=str(ticket.get("training_mode") or ""),
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
