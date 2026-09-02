"""扣费明细闸门判定（工单 07-confidence-gates，spec Implementation Decisions 第 4 段）。

纯函数，无 IO、无 Flask、无 DB、无全局状态、无时间/随机性，便于测试与多端复用。
专门负责「金额置信度闸门」：存在 pending 项时拒绝 fee-confirm 与 archive 两道闸门，
与现有归档三闸（archive_service.archive_gate_errors）语义对齐——补齐 pending 后放行。

设计要点：
- 不重写 deduction_engine 的 pending 判定（02 阶段已在引擎内落地 total_deduction 排除 pending
  与 refund_pending=True）；07 只判定「上游 deductions_result 里有没有 pending 明细」并据此开关。
- 不接管 archive_gate_errors 的实现；check_archive 在其结果上**追加** pending 错误信息，
  上层 app.py 可同时拿到「处理情况/配合度/费用确认/待确认」四种缺失的合并报告。
- 返回值统一为 (ok: bool, err: str)，与上层 _err() 风格一致；err 为空串表示通过。
"""

from __future__ import annotations

from typing import Any


# ── 异常：闸门函数不抛，只返回 (False, 错误信息)；保留异常类便于上层 if needed ──

class GatingError(Exception):
    """闸门模块的统一异常类。当前纯函数实现不主动抛，保留给调用方（如路由）按需包装。"""


# ── 内部 helpers ────────────────────────────────────────────────────────

def _items_of(deductions_result: Any) -> list[dict]:
    """从 deductions_result 安全取出明细列表。None / 非 dict / items 缺失 → []."""
    if not isinstance(deductions_result, dict):
        return []
    raw = deductions_result.get("items")
    if not isinstance(raw, list):
        return []
    return [it for it in raw if isinstance(it, dict)]


def _pending_reason(item: dict) -> str:
    """提取 pending 明细的人类可读原因。优先 basis（引擎已写「待人工补录」类文案），否则回退 source。"""
    basis = str(item.get("basis") or "").strip()
    if basis:
        return basis
    src = str(item.get("source") or "").strip() or "pending"
    return f"来源 {src} 缺失"


# ── 公开 API ────────────────────────────────────────────────────────────

def has_pending_items(deductions_result: dict | None) -> bool:
    """任一明细 pending=True → True；deductions_result None / items 空 → False.

    仅依赖 item.pending 布尔字段（02 阶段引擎已正确标记）；不依赖 refund_pending 顶字段，
    以便单条明细独立可观测（UI 可用同一定义给「待确认」徽标着色）。
    """
    return any(bool(it.get("pending")) for it in _items_of(deductions_result))


def pending_items_summary(deductions_result: dict | None) -> list[dict]:
    """返回 pending 明细的摘要列表，结构固定为 ``{item, category, source, pending_reason}``.

    - 顺序与原 items 列表一致（便于 UI 与日志对齐）。
    - 空场景（None / items 空 / 无 pending）→ []。
    - 供前端徽标、fee-confirm 拒绝文案、archive 错误聚合共同使用。
    """
    return [
        {
            "item": str(it.get("item") or "").strip(),
            "category": str(it.get("category") or "").strip(),
            "source": str(it.get("source") or "").strip(),
            "pending_reason": _pending_reason(it),
        }
        for it in _items_of(deductions_result)
        if bool(it.get("pending"))
    ]


def check_fee_plan_confirm(deductions_result: dict | None) -> tuple[bool, str]:
    """fee-confirm 端点的闸门判定：存在 pending → (False, 错误信息)；否则 (True, '')。

    错误信息格式：「存在 N 项待确认: 服务费(ocr), 违约金(pending)...」；
    N=1 时省略数量前缀单数（避免「存在 1 项待确认」读起来别扭），保持中文流畅。
    """
    pendings = pending_items_summary(deductions_result)
    if not pendings:
        return (True, "")
    parts = [f"{p['item']}({p['source'] or 'pending'})" for p in pendings if p["item"]]
    # 任一 item 为空字符串（理论上引擎不会产出，但防御性兜底）也带出 source
    parts += [f"({p['source'] or 'pending'})" for p in pendings if not p["item"]]
    summary = "、".join(parts) if parts else "见明细"
    n = len(pendings)
    prefix = f"存在 {n} 项待确认" if n > 1 else "存在 1 项待确认"
    return (False, f"{prefix}: {summary}")


def check_archive(
    deductions_result: dict | None,
    fee_plan_status: str | None,
    **gates: Any,
) -> tuple[bool, str]:
    """归档闸门聚合：archive_service 三闸门 + 07 pending 闸门。

    Args:
        deductions_result: 上一次分析的扣费结果（含 items 列表；可 None）。
        fee_plan_status: 工单的 fee_plan_status 字段（'confirmed' / 'draft' / None）。
        **gates: 兼容 archive_service.archive_gate_errors 的两个闸门条件。
            约定键名（其余忽略）：
              - handling_notes: str | None
              - branch_cooperation: str | None

    Returns:
        (ok, err) — ok=True 全通过（err 为 ''），ok=False 任一闸门缺失（err 含全部错误，
        用全角分号「；」分隔，与 archive_gate_errors 的中文逗号风格一致但避免与
        fee-confirm 文案中的半角冒号冲突）。

    设计取舍：
        - **不直接调用** archive_service.archive_gate_errors，以保持本模块零外部依赖
          （避免「services 内互相 import 引发循环」与「单元测试需 monkeypatch 库函数」的成本）。
        - fee_plan_status 同时出现在位置参数与 **gates 时，**gates 优先生效；上层的
          `app.py` 通常把工单字段 dict 透传，因此约定 `gates={"handling_notes": ...,
          "branch_cooperation": ...}`，fee_plan_status 仍走位置参数（必传、便于阅读）。
    """
    handling_notes = gates.get("handling_notes", "")
    branch_cooperation = gates.get("branch_cooperation", "")

    errors: list[str] = []
    if not str(handling_notes or "").strip():
        errors.append("处理情况未填写")
    if not str(branch_cooperation or "").strip():
        errors.append("配合度未评定")
    if str(fee_plan_status or "") != "confirmed":
        errors.append("费用明细未确认")

    pendings = pending_items_summary(deductions_result)
    if pendings:
        parts = [f"{p['item']}({p['source'] or 'pending'})" for p in pendings if p["item"]]
        parts += [f"({p['source'] or 'pending'})" for p in pendings if not p["item"]]
        summary = "、".join(parts) if parts else "见明细"
        n = len(pendings)
        prefix = f"存在 {n} 项待确认" if n > 1 else "存在 1 项待确认"
        errors.append(f"{prefix}: {summary}")

    return (len(errors) == 0, "；".join(errors))
