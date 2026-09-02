"""多份合同逐份分析 + 归并（工单 10-multi-set-summary，ADR-0003 多份口径）。

一套合同模型（spec）：一次上传多份（2019 常见 2–3 份并行）时各份独立分析，
明细汇总，摘要条标注「已识别 N 份」。本模块是 deduction_engine docstring 预留的
「调用方做多份归并」落点：

- 逐份：对 contract_set 每个有文件的条目跑 analyze_fn（默认 upload_pipeline），
  份类型 kind 解析为 条目登记值 > 档位表 kind > 空串。
- 考试费归属：多份并行时考试费/补考费只计在「代缴 > 单一培训 > 培训 > 首份」
  的第一份上，其余份的同名项剔除（代收代交考试费合同的职责；跨份不重复扣）。
- 逐份手写总额：多份模式下每份的「培训费总额」未知（ticket.total_fee 是整套
  合同的合计，不是单份值），逐份传 None → 理论培训费/违约金按引擎规则置
  pending（待人工补录，对接 07 闸门）；单份模式沿用 ticket.total_fee 原行为。
- 汇总：明细拼接打份标签（contract_index/contract_kind/contract_file），
  同名项不去重；pending 不计入合计；任一 pending → refund_pending=True。

失败语义：analyze_fn 抛异常按该份 extraction_error 记录，不影响其他份。
"""

from __future__ import annotations

import os
from typing import Any, Callable

from services.contract_tiers import TIERS_BY_ID


# 考试费/补考费归属份的优先序（代收代交考试费合同优先）
_EXAM_KIND_PRIORITY = ("代缴", "单一培训", "培训")

AnalyzeFn = Callable[..., dict]


def resolve_entry_kind(entry: dict, tier_id: str) -> str:
    """份类型：条目登记值优先（改档/人工标注不丢），档位表 kind 兜底。"""
    kind = str((entry or {}).get("kind") or "").strip()
    if kind:
        return kind
    tier = TIERS_BY_ID.get(tier_id or "")
    return str(tier.get("kind") or "") if tier else ""


def pick_exam_fee_index(kinds: list[str]) -> int:
    """考试费/补考费归属份下标：按 _EXAM_KIND_PRIORITY 找第一份；否则首份；空表返回 -1。"""
    if not kinds:
        return -1
    for kind in _EXAM_KIND_PRIORITY:
        for i, k in enumerate(kinds):
            if k == kind:
                return i
    return 0


def _is_exam_item(item: dict) -> bool:
    name = str(item.get("item") or "")
    return ("考试费" in name) or ("补考费" in name)


def analyze_contract_set(
    ticket: dict,
    entries: list[dict],
    *,
    analyze_fn: AnalyzeFn | None = None,
) -> dict:
    """逐份分析 + 归并。entries 为 contract_set 规范化条目（须含 file）。

    Returns:
        {
          "analyses": [逐份记录],
          "aggregated": {items/total_deduction/refund/refund_pending/warnings/
                         recognized_count/kinds},
        }
    """
    if analyze_fn is None:
        from services.upload_pipeline import analyze_upload_contract_file as analyze_fn

    file_entries = [e for e in (entries or []) if isinstance(e, dict) and (e.get("file") or "")]
    multi = len(file_entries) > 1
    analyses: list[dict] = []

    for i, entry in enumerate(file_entries):
        filepath = str(entry.get("file") or "")
        per_ticket = dict(ticket or {})
        if multi:
            # 多份：ticket.total_fee 是整套合计，不是单份手写总额 → 逐份置 None
            per_ticket["total_fee"] = None
        try:
            pipe = analyze_fn(filepath, per_ticket, image_paths=[])
        except Exception as exc:  # 防御性：analyze_fn 自身异常按提取失败记录
            pipe = {
                "tier_id": "", "tier_result": {}, "deductions_result": None,
                "cache_key": "", "text_source": "",
                "extraction_error": str(exc),
                "contract_set": {"contracts": []},
            }

        tier_id = str(pipe.get("tier_id") or "")
        tier_result = pipe.get("tier_result") or {}
        text = ""
        for c in (pipe.get("contract_set") or {}).get("contracts") or []:
            if (c.get("file") or "") == filepath and c.get("text"):
                text = str(c["text"])
                break

        analyses.append({
            "index": len(analyses),
            "filepath": filepath,
            "filename": os.path.basename(filepath),
            "kind": resolve_entry_kind(entry, tier_id),
            "tier_id": tier_id,
            "tier_display_name": str(tier_result.get("display_name") or ""),
            "tier_confidence": str(tier_result.get("confidence") or ""),
            "tier_result": tier_result,
            "text": text,
            "text_source": str(pipe.get("text_source") or ""),
            "cache_key": str(pipe.get("cache_key") or ""),
            "deductions_result": pipe.get("deductions_result"),
            "extraction_error": pipe.get("extraction_error"),
            "contract_set": pipe.get("contract_set") or {"contracts": []},
        })

    set_total_fee = ticket.get("total_fee") if not multi else None
    aggregated = aggregate_analyses(analyses, set_total_fee=set_total_fee)
    return {"analyses": analyses, "aggregated": aggregated}


def aggregate_analyses(analyses: list[dict], *, set_total_fee: float | None = None) -> dict:
    """把逐份分析归并成一套合同的汇总结果（纯函数）。

    - 明细拼接 + 份标签，同名项不去重；
    - 多份（len>1）时考试费/补考费只保留归属份的；
    - pending 项不计入合计；任一 pending → refund_pending=True（不给出应退数字）；
    - set_total_fee 提供且无 pending 时应退 = max(0, 总额 − 合计)。
    """
    analyses = [a for a in (analyses or []) if isinstance(a, dict)]
    kinds = [str(a.get("kind") or "") for a in analyses]
    multi = len(analyses) > 1
    exam_idx = pick_exam_fee_index(kinds) if multi else -1

    items: list[dict] = []
    warnings: list[str] = []
    refund_pending = False
    for a in analyses:
        idx = int(a.get("index", 0))
        kind = str(a.get("kind") or "")
        filepath = str(a.get("filepath") or "")
        dr = a.get("deductions_result")
        for w in (dr or {}).get("warnings") or []:
            warnings.append(f"[{kind or a.get('filename') or '未识别份'}] {w}")
        for it in (dr or {}).get("items") or []:
            if not isinstance(it, dict):
                continue
            if multi and _is_exam_item(it) and idx != exam_idx:
                continue  # 考试费只计归属份
            if it.get("pending"):
                refund_pending = True
            items.append({
                **it,
                "contract_index": idx,
                "contract_kind": kind,
                "contract_file": filepath,
            })

    total_deduction = sum(float(it.get("amount") or 0) for it in items if not it.get("pending"))
    refund = 0.0
    if not refund_pending and set_total_fee is not None:
        refund = max(0.0, float(set_total_fee) - total_deduction)

    return {
        "items": items,
        "total_deduction": total_deduction,
        "refund": refund,
        "refund_pending": refund_pending,
        "warnings": warnings,
        "recognized_count": sum(1 for k in kinds if k),
        "kinds": kinds,
    }
