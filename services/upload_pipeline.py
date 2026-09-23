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
import re
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


# ── 规则文案摘要（v4.4 · S3b）：上传件「🤖 AI 摘要（额外约定等）」 ──────────
#
# 用户拍板（Q2）：上传件「AI 摘要」**不走 LLM**，改为规则文案（纯函数），照抄原型
# `demo/contract-preview-v4-demo.html` 的 `aiSummary`，只按数据有无裁剪。所有数字取
# `deductions_result` 自身字段（不另算一份）；金额千分位取整（`f"{v:,.0f}"`），与前端
# `_money` 一致。纯文本，行以 `\n` 分隔，`**x**` 表示加粗（前端先转义再转 `<b>`）。
# 电子合同（下载）路径不产出此字段：本函数只在 upload_pipeline 里被调用。

# 实操项名后缀：「科目二实操培训费」→ 科目标签「科目二实操」。
_PRACTICAL_ITEM_SUFFIX = "实操培训费"
_PENALTY_ITEM_NAME = "违约金"
# 从实操项 basis（`审核学时 {hours:g} × {单价标签} {rate:.0f} 元/学时（{车型}）`）里解析
# 「学时」与「单价」——该串由 deduction_engine 写入，**已反映实际采用的单价**（合同正文
# 单价或档位默认），比另查档位表更准。
_PRACTICE_BASIS_RE = re.compile(
    r"审核学时\s*(?P<hours>\d+(?:\.\d+)?)\s*×.*?(?P<rate>\d+(?:\.\d+)?)\s*元/学时"
)
# 从违约金项 basis（`… × 档位默认 {rate}%（…）`）里取费率百分数。
_PENALTY_RATE_IN_BASIS_RE = re.compile(r"([0-9]{1,3})\s*%")


def _money(v: Any) -> str:
    """千分位取整（元）：`f"{v:,.0f}"`；None / 非法 / 空 → "0"。"""
    f = _as_float(v)
    return f"{f:,.0f}" if f is not None else "0"


def _fmt_num(v: Any) -> str:
    """数字归一显示（去掉多余的 `.0`）：`f"{float(v):g}"`；非法 → 原样字符串。"""
    try:
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return str(v)


def _display_name(tier_result: Any) -> str:
    """从 tier_result 取档位显示名；缺失返回 ""（调用方据此整条降级）。"""
    if isinstance(tier_result, dict):
        return str(tier_result.get("display_name") or "").strip()
    return ""


def _practical_label(item_name: Any) -> str:
    """「科目二实操培训费」→「科目二实操」；非该后缀则原样返回。"""
    name = str(item_name or "")
    if name.endswith(_PRACTICAL_ITEM_SUFFIX):
        return name[: -len(_PRACTICAL_ITEM_SUFFIX)] + "实操"
    return name


def _practical_subject(item_name: Any) -> str:
    """「科目二实操培训费」→「科目二」（去掉尾部「实操培训费」）。"""
    name = str(item_name or "")
    if name.endswith(_PRACTICAL_ITEM_SUFFIX):
        return name[: -len(_PRACTICAL_ITEM_SUFFIX)]
    return name


def _practical_hours_formula(practical_items: list) -> str | None:
    """把实操项还原为「科目二 16 小时 + 科目三 4 小时 = 20 小时 × 120 元/小时」。

    学时与单价从各实操项的 `basis`（`审核学时 16 × 档位单价 120 元/学时（C1）`）解析；
    该串已反映实际采用的单价（合同正文单价 / 档位默认）。

    - 各项单价**全部相同** → 合并为「{各项} = {总学时} 小时 × {单价} 元/小时」；
    - 各项单价**不同** → 各项各自带单价（不合并，不出 `=`）；
    - 任一项解析不出学时/单价 → 返回 `None`（调用方退回简写，**绝不**写 0 / None 小时）。
    """
    parsed: list[tuple[str, str, str]] = []
    for it in practical_items:
        m = _PRACTICE_BASIS_RE.search(str(it.get("basis") or ""))
        if not m:
            return None
        parsed.append((_practical_subject(it.get("item")), m.group("hours"), m.group("rate")))
    if not parsed:
        return None

    rates = {_fmt_num(r) for _, _, r in parsed}
    hours_parts = " + ".join(f"{subj} {_fmt_num(h)} 小时" for subj, h, _ in parsed)
    if len(rates) == 1:
        rate = next(iter(rates))
        total_hours = sum(float(h) for _, h, _ in parsed)
        return f"{hours_parts} = {_fmt_num(total_hours)} 小时 × {rate} 元/小时"
    return " + ".join(
        f"{subj} {_fmt_num(h)} 小时 × {_fmt_num(r)} 元/小时" for subj, h, r in parsed
    )


def _penalty_rate_from_basis(basis: Any) -> str:
    m = _PENALTY_RATE_IN_BASIS_RE.search(str(basis or ""))
    return m.group(1) if m else ""


def build_rule_summary(deductions_result: Any, tier_result: Any) -> str:
    """上传件「🤖 AI 摘要」规则文案（纯函数，不走 LLM）。

    逐行口径见 `.scratch/contract-preview-v44-landing/s4-contract.md` §1.4（照抄原型
    `aiSummary`，只按数据有无裁剪）：

    1. 本合同为 **{档位显示名}** 档位标准合同（纸质照片识别）。合同总额 ¥…，实缴 ¥…，应付尾款 ¥…。
    2. 额外约定（手写）：**首付 {实缴} 元，欠款 {尾款} 元**。        ← 实缴>0 且 尾款>0 才出
    3. 已产生实操学时（计时平台）：科目二 16 小时 + 科目三 4 小时 = 20 小时 × 120 元/小时 → 依实扣费 **¥…**。  ← 仅当有实操项
    4. 扣费合计 ¥… = {项} {额} + …
    5. 违约金按全部培训费用 {总额} × {率}% = {额}。               ← 仅当有违约金项
    6. 应退 = 实缴 {实缴} − 扣费合计 {合计} = **¥{应退}**（…）。   ← 括号半句仅当应退<=0

    「应退」口径（§1.4）：`max(paid_amount − Σitem.amount, 0)`，由 `deductions_result`
    的 `paid_amount` 与 `items` 组合得出（不重跑引擎），保证与展示公式自洽、不出负数。

    Args:
        deductions_result: 上传件扣费结果（`_attach_payment_context` 之后的 dict）。
        tier_result: `identify_tier` 输出；`display_name` 为空时整条降级。

    Returns:
        纯文本摘要（`\n` 分隔）；无 items / 无档位显示名 / 入参非法 → `""`（不抛）。
    """
    dr = deductions_result if isinstance(deductions_result, dict) else {}
    items = dr.get("items") or []
    if not items:
        return ""
    display = _display_name(tier_result)
    if not display:
        # 档位显示名取不到 → 整条降级为空串，避免出现 "None" 字样。
        return ""

    paid = _as_float(dr.get("paid_amount")) or 0.0
    tail = _as_float(dr.get("tail_due")) or 0.0
    total = dr.get("total_fee")

    ded_sum = sum((_as_float(it.get("amount")) or 0.0) for it in items if isinstance(it, dict))
    refund = max(paid - ded_sum, 0.0)

    practical_items = [
        it for it in items
        if isinstance(it, dict) and str(it.get("item") or "").endswith(_PRACTICAL_ITEM_SUFFIX)
    ]
    penalty_item = next(
        (it for it in items if isinstance(it, dict) and it.get("item") == _PENALTY_ITEM_NAME),
        None,
    )

    lines: list[str] = []
    # 1) 档位 + 三个金额
    lines.append(
        f"本合同为 **{display}** 档位标准合同（纸质照片识别）。"
        f"合同总额 ¥{_money(total)}，实缴 ¥{_money(paid)}，应付尾款 ¥{_money(tail)}。"
    )
    # 2) 手写额外约定（有首付且有欠款才出）
    if paid > 0 and tail > 0:
        lines.append(f"额外约定（手写）：**首付 {_money(paid)} 元，欠款 {_money(tail)} 元**。")
    # 3) 已产生实操学时（仅当有实操项）：优先还原「学时 × 单价」算式（原型 ⑤）；
    #    任一项 basis 解析不出 → 退回简写（不写 0/None 小时、不丢行）。
    if practical_items:
        practice_sum = sum((_as_float(it.get("amount")) or 0.0) for it in practical_items)
        formula = _practical_hours_formula(practical_items)
        body = formula if formula is not None else " + ".join(
            _practical_label(it.get("item")) for it in practical_items
        )
        lines.append(f"已产生实操学时（计时平台）：{body} → 依实扣费 **¥{_money(practice_sum)}**。")
    # 4) 扣费合计 = 各项逐条相加
    breakdown = " + ".join(f"{it.get('item')} {_money(it.get('amount'))}" for it in items if isinstance(it, dict))
    lines.append(f"扣费合计 ¥{_money(ded_sum)} = {breakdown}。")
    # 5) 违约金（仅当有违约金项）
    if penalty_item is not None:
        rate = _penalty_rate_from_basis(penalty_item.get("basis"))
        if not rate:
            total_f = _as_float(total)
            if total_f:
                rate = str(round((_as_float(penalty_item.get("amount")) or 0.0) / total_f * 100))
        lines.append(
            f"违约金按全部培训费用 {_money(total)} × {rate}% = {_money(penalty_item.get('amount'))}。"
        )
    # 6) 应退（扣费超实缴 → 兜底 0 + 说明半句）
    suffix = "（扣费已超实缴，按公式兜底为 0，不出现负数）" if refund <= 0 else ""
    lines.append(
        f"应退 = 实缴 {_money(paid)} − 扣费合计 {_money(ded_sum)} = **¥{_money(refund)}**{suffix}。"
    )
    return "\n".join(lines)


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
    extraction: dict | None = None,
) -> dict:
    """上传件分析管线（生产入口）。

    extraction：外部共享的提取结果（app 层同一文件只 OCR 一次，2026-09-23 提效）；
    缺省时自行走完整提取降级链。
    """
    if extraction is None:
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


def _resolve_paid_and_tail(
    ticket: dict,
    payment_plan: dict | None,
    total_fee: Any,
    fees_total_amount: Any = None,
) -> tuple[float, float, float]:
    """实缴 / 应付尾款 / 退费基数口径（用户拍板 2026-09-14，S2 实缴回落）。

    背景：工单 `actual_paid`（实缴）经常为 0，而合同手写「首付 2000 元，欠款 1580 元」
    已由 `contract_service._extract_payment_plan` 抽出并挂在 `extraction["payment_plan"]`
    （形状 `{down_payment, balance, balance_source}`）。此前只读 `ticket.actual_paid`
    → 实缴显示 0、尾款误为合同全额、退费基数误取合同总额。此函数把付款计划回落进来。

    三条优先级（严格）：
      1. 实缴 `paid_amount`：`ticket.actual_paid` > 0 用之；否则回落付款计划首付
         （`payment_plan.down_payment`）；再否则 `0.0`。
      2. 应付尾款 `tail_due`：
         - 实缴来自 `ticket.actual_paid`（权威值）时：`max(0, total_fee − paid_amount)`。
         - 实缴来自回落（付款计划首付）时：**优先**用付款计划 `balance`；
           `balance` 缺失 / 为 None 时才回退 `max(0, total_fee − paid_amount)`。
      3. 退费基数 `refund_base`：`paid_amount` > 0 用之；否则回落 `fees_total_amount`
         （合同总金额），仍无则 `total_fee`（保持既有兜底不变）。

    Args:
        ticket: 工单字典，读取 `actual_paid`。
        payment_plan: 付款计划 `{down_payment, balance, balance_source}`，可为 None / 缺键。
        total_fee: 合同总额（工单录入优先，其次合同正文抽取）；可为 None。
        fees_total_amount: 合同正文抽取的「合同总金额」，作退费基数兜底。

    Returns:
        `(paid_amount, tail_due, refund_base)`，三者均为 float。
    """
    actual_paid = _as_float(ticket.get("actual_paid"))
    plan = payment_plan if isinstance(payment_plan, dict) else {}
    plan_down = _as_float(plan.get("down_payment"))
    plan_balance = _as_float(plan.get("balance"))
    total = float(total_fee or 0)

    if actual_paid is not None and actual_paid > 0:
        # 实缴是权威值：尾款一律由实缴侧算出，不受付款计划 balance 影响
        paid_amount = actual_paid
        tail_due = max(0.0, total - paid_amount)
    elif plan_down is not None and plan_down > 0:
        # 实缴未录入（0/None）→ 回落付款计划首付
        paid_amount = plan_down
        if plan_balance is not None:
            tail_due = max(0.0, plan_balance)
        else:
            # 付款计划缺 balance（如只抽到首付）→ 用总额 − 首付兜底
            tail_due = max(0.0, total - paid_amount)
    else:
        paid_amount = 0.0
        tail_due = max(0.0, total)

    if paid_amount > 0:
        refund_base = paid_amount
    else:
        refund_base = fees_total_amount or total

    return paid_amount, tail_due, refund_base


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

            # S2：实缴未录入（ticket.actual_paid=0）时回落付款计划首付，尾款优先用
            # 付款计划 balance；口径见 _resolve_paid_and_tail（纯函数，单测直打）。
            paid_amount, tail_due, refund_base = _resolve_paid_and_tail(
                ticket,
                extraction.get("payment_plan"),
                total_fee,
                fees.get("total_amount"),
            )

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

        # 2.6) 规则文案摘要（v4.4 · S3b）：上传件「🤖 AI 摘要」纯文本（不走 LLM）。
        # 所有金额字段（total_fee/paid_amount/tail_due/net_refund）已在 2) 里就位、
        # items 亦已锚定，此处统一产出，避免另算一份。
        deductions_result["rule_summary"] = build_rule_summary(deductions_result, tier_result)

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
