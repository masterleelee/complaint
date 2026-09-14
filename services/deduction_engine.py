"""扣费计算引擎（工单 02-deduction-engine，ADR-0003）。

纯函数，无 IO、无 Flask、无 DB。按档位 + 阶段 + 进度 + 手写金额输出有序扣费明细。

设计要点：
- 沿用现网 float 金额口径（生产 `services.contract_service` 走的就是 float），不引入 Decimal。
- 不继承 `core.refund_engine` 契约：不要求 `contract_id`、不分制（ADR-0003）。
- 每条明细带来源（tier_default / ocr / manual / pending）与置信度（high / medium / low / pending）。
- pending 项不计入合计，应退合计若任一上游缺失则置 refund_pending=True 不给出数字。
- 输出顺序稳定：必扣 → 考试费/补考费 → 实操费/理论培训费 → 违约金（恒排最后）。
- 多份并行时违约金基数（拍板口径，见 ADR-0003 + CONTEXT.md）：Σ 各份手写总额之和、
  代缴份不计入（服务份计入）；当前六档矩阵无触发场景，本函数按单份合同计算，调用方做多份归并。
"""

from __future__ import annotations

from typing import Any

# ── 来源 ↔ 置信度映射 ──────────────────────────────────────────────

SOURCE_CONFIDENCE: dict[str, str] = {
    "tier_default": "high",   # 档位常量（考试费标准、违约金率等）
    "manual": "high",         # 人工录入
    "ocr": "medium",          # OCR/LLM 抽取
    "pending": "pending",     # 上游缺失
}

# 学科标签（与档位考试费/补考费键名对齐）
_SUBJECT_LABELS: dict[str, str] = {
    "subject1": "科目一",
    "subject2": "科目二",
    "subject3": "科目三",
}

# 培训档判定：只有这两类合同有「理论培训费视为已发生」的口径
_TRAINING_KINDS: frozenset[str] = frozenset({"培训", "单一培训"})


def _is_training_kind(kind: str) -> bool:
    return kind in _TRAINING_KINDS


def _make_item(
    category: str,
    name: str,
    amount: float,
    basis: str,
    source: str = "tier_default",
    pending: bool = False,
) -> dict[str, Any]:
    """构造一条扣费明细。pending=True 时 amount 仅作占位（0），不计入合计。"""
    return {
        "category": category,
        "item": name,
        "amount": float(amount),
        "basis": basis,
        "source": "pending" if pending else source,
        "confidence": SOURCE_CONFIDENCE["pending"] if pending else SOURCE_CONFIDENCE.get(source, "low"),
        "pending": pending,
    }


def _mandatory_items(tier: dict) -> list[dict]:
    items: list[dict] = []
    for entry in tier.get("mandatory_items") or []:
        items.append(_make_item(
            category="必扣",
            name=entry["item"],
            amount=entry["amount"],
            basis=f"{tier['display_name']} 合同必扣项",
            source="tier_default",
        ))
    return items


def _exam_items(tier: dict, exam_counts: dict[str, int]) -> list[dict]:
    items: list[dict] = []
    for key, label in _SUBJECT_LABELS.items():
        attempts = int(exam_counts.get(key, 0) or 0)
        if attempts <= 0:
            continue
        # 首考 + (attempts - 1) 次补考
        exam_fee = float(tier.get("exam_fees", {}).get(key, 0))
        makeup_fee = float(tier.get("makeup_fees", {}).get(key, 0))
        if exam_fee > 0:
            items.append(_make_item(
                category="依实",
                name=f"{label}考试费",
                amount=exam_fee,
                basis=f"已考 1 次 × 档位标准 {exam_fee:.0f} 元",
            ))
        extra = attempts - 1
        if extra > 0 and makeup_fee > 0:
            items.append(_make_item(
                category="依实",
                name=f"{label}补考费",
                amount=extra * makeup_fee,
                basis=f"补考 {extra} 次 × 档位标准 {makeup_fee:.0f} 元",
            ))
    return items


def _service_fee_items(
    service_breakdown: dict | None,
    exam_counts: dict[str, int],
) -> list[dict]:
    """协助报考服务费按进度扣（旧模板 2021-2022 特有费用项）。

    服务费拆分（报名/学员卡 + 科一服务费 + 科二三服务费）按学员进度扣：
    - 报名/学员卡费：已报名建档 → 全额扣；
    - 科目一服务费：已考科目一 → 扣；未考 → 退；
    - 科目二三服务费：已考科目二或科目三 → 扣；都未考 → 退。
    """
    items: list[dict] = []
    bd = service_breakdown or {}
    if bd.get("enroll_card"):
        items.append(_make_item(
            category="依实", name="报名/学员卡费", amount=float(bd["enroll_card"]),
            basis="已报名建档（服务费按进度扣）", source="manual",
        ))
    if bd.get("subject1_service") and int(exam_counts.get("subject1", 0) or 0) > 0:
        items.append(_make_item(
            category="依实", name="科目一服务费", amount=float(bd["subject1_service"]),
            basis="已考科目一（服务费按进度扣）", source="manual",
        ))
    if bd.get("subject23_service") and (
        int(exam_counts.get("subject2", 0) or 0) > 0 or int(exam_counts.get("subject3", 0) or 0) > 0
    ):
        items.append(_make_item(
            category="依实", name="科目二三服务费", amount=float(bd["subject23_service"]),
            basis="已考科目二/三（服务费按进度扣）", source="manual",
        ))
    return items


def _practical_items(
    tier: dict,
    training_hours: dict[str, float],
    license_type: str,
    total_fee: float | None,
    manual_amounts: dict | None = None,
) -> tuple[list[dict], list[str]]:
    """返回 (明细, 告警)。仅培训档 + 学时 > 0 才出。封顶校验只告警不截断。

    实操单价优先取 manual_amounts.practical_unit_price（合同正文采信值，如罗炳灿 75 元/学时），
    缺省回退档位表 practical_rates（C1=120/C2=150）。
    """
    items: list[dict] = []
    warnings: list[str] = []
    if not _is_training_kind(tier.get("kind", "")):
        return items, warnings

    rate_table = tier.get("practical_rates") or {}
    rate = float((manual_amounts or {}).get("practical_unit_price") or rate_table.get(license_type, 0))
    if rate <= 0:
        return items, warnings

    for key, label in _SUBJECT_LABELS.items():
        if key == "subject1":
            continue  # 科目一是理论培训，不走实操单价
        hours = float(training_hours.get(key, 0) or 0)
        if hours <= 0:
            continue
        amount = hours * rate
        rate_label = "合同正文单价" if (manual_amounts or {}).get("practical_unit_price") else "档位单价"
        basis = f"审核学时 {hours:g} × {rate_label} {rate:.0f} 元/学时（{license_type}）"
        items.append(_make_item(
            category="依实",
            name=f"{label}实操培训费",
            amount=amount,
            basis=basis,
            source="manual" if (manual_amounts or {}).get("practical_unit_price") else "tier_default",
        ))
        # 封顶校验：仅告警不截断
        if total_fee is not None and amount > float(total_fee):
            warnings.append(
                f"{label}实操培训费 {amount:.0f} 元超出培训费总额 {float(total_fee):.0f} 元，请人工核对"
            )
    return items, warnings


def _theory_fee_items(
    tier: dict,
    total_fee: float | None,
    manual_amounts: dict | None,
) -> list[dict]:
    """理论培训费（仅培训档）。manual_amounts.theory_fee 优先（高置信 manual），
    否则用 total_fee（中置信 ocr），再否则 pending。

    打包价合同例外（ISS-VC-01 P0-5）：2023 分校/分店第四条（一）1（1）的总额
    「包含建档费/学员IC卡/各阶段培训费及相关手续费」，没有独立的理论培训费科目。
    若把总额整额当作理论培训费扣除，会与必扣项、违约金重复计算
    （实例：必扣 1000 + 总额 3580 + 违约金 716 = 5296 > 合同额，应退恒为 0）。
    第八条退费表只列「基础服务必扣 + 其他必扣 + 已代收考试费 + 已产生实操培训费 +
    违约金 20%」，故打包价合同不出理论培训费项。
    """
    if not _is_training_kind(tier.get("kind", "")):
        return []

    manual = (manual_amounts or {}).get("theory_fee")
    if manual is not None and manual != "":
        return [_make_item(
            category="依实",
            name="理论培训费",
            amount=float(manual),
            basis="合同正文理论培训费（采信合同）",
            source="manual",
        )]

    if total_fee is None or total_fee == "":
        return [_make_item(
            category="依实",
            name="理论培训费",
            amount=0,
            basis="培训费总额缺失（待人工补录）",
            pending=True,
        )]

    if tier.get("mandatory_items"):
        return []

    return [_make_item(
        category="依实",
        name="理论培训费",
        amount=float(total_fee),
        basis="培训费总额（OCR/LLM 抽取）",
        source="ocr",
    )]


def _penalty_item(tier: dict, total_fee: float | None) -> list[dict]:
    """违约金（恒排最后）。tier.penalty_rate 是整数百分比（10 = 10%），计算时除以 100；
    penalty_rate=0 直接跳过；total_fee 缺失时 pending。"""
    rate_pct = float(tier.get("penalty_rate", 0) or 0)
    if rate_pct <= 0:
        return []
    rate = rate_pct / 100.0
    if total_fee is None or total_fee == "":
        return [_make_item(
            category="违约金",
            name="违约金",
            amount=0,
            basis=f"{tier['display_name']} 档默认违约金率 {rate_pct:.0f}%，基数缺失（pending）",
            pending=True,
        )]
    amount = float(total_fee) * rate
    return [_make_item(
        category="违约金",
        name="违约金",
        amount=amount,
        basis=f"全部培训费用 × 档位默认 {rate_pct:.0f}%（{tier['display_name']}）",
    )]


def calculate_deductions(
    tier: dict,
    stage: str,
    progress: dict,
    total_fee: float | None = None,
    manual_amounts: dict | None = None,
    service_fee: float | None = None,
    training_mode: str = "",
    total_amount: float | None = None,
) -> dict:
    """按档位 + 阶段 + 进度 + 手写金额算一份合同的有序扣费明细。

    Args:
        tier: 一份合同的档位 dict（来自 `services.contract_tiers.TIERS_BY_ID[tier_id]`）。
        stage: "已受理" | "实操中"（与 CONTEXT.md「阶段」一致）。
        progress: {"exam_counts": {subjectN: attempts}, "training_hours": {subjectN: hours}, "license_type": "C1"|"C2"}。
        total_fee: 该份合同的「培训费总额」手写值；缺失 → 违约金 pending、理论费 pending（培训档）。
        manual_amounts: 人工覆盖字典，可含 {"theory_fee": float, "practical_unit_price": float,
            "service_breakdown": dict}；高置信 manual 优先于档位默认。
        service_fee: 东城自制档「咨询/服务费」金额（第六条（二）基数），仅东城自制档使用。
        training_mode: 东城自制档「培训方式」（普通培训 / 先培后付），仅东城自制档使用。
        total_amount: 退费基数「总金额」（培训费 + 代交费 + 服务费）；缺失回退 total_fee。
            违约金/理论费基数仍用 total_fee（培训费总额），只有应退金额用 total_amount。

    Returns:
        {
          "items": [扣费明细，按 必扣 → 考试费 → 实操费 → 理论费 → 违约金 排序],
          "total_deduction": float,    # 仅非 pending 项求和
          "refund": float,             # 0 若 refund_pending；否则 = total_amount - total_deduction
          "refund_pending": bool,      # 任一上游缺失时为 True，不给出 refund 数字
          "warnings": [str],           # 封顶校验等告警
          "tier_id": str,
          "stage": str,
        }
    """
    # 东城自制档走第六条专属分支（退费模型与线性「必扣+违约金」不同）
    if tier.get("id") == "2019_dongcheng":
        return calculate_dongcheng_refund(
            tier=tier,
            stage=stage,
            progress=progress,
            total_fee=total_fee,
            service_fee=service_fee,
            training_mode=training_mode,
            manual_amounts=manual_amounts,
        )

    exam_counts = (progress or {}).get("exam_counts") or {}
    training_hours = (progress or {}).get("training_hours") or {}
    license_type = (progress or {}).get("license_type") or "C1"

    items: list[dict] = []
    warnings: list[str] = []

    items.extend(_mandatory_items(tier))
    items.extend(_exam_items(tier, exam_counts))
    items.extend(_service_fee_items((manual_amounts or {}).get("service_breakdown"), exam_counts))
    practical, w_practical = _practical_items(tier, training_hours, license_type, total_fee, manual_amounts)
    items.extend(practical)
    warnings.extend(w_practical)
    theory_items = _theory_fee_items(tier, total_fee, manual_amounts)
    items.extend(theory_items)
    if not theory_items and tier.get("mandatory_items"):
        warnings.append(
            f"{tier['display_name']} 为打包价合同（总额已含建档费/学员IC卡及各阶段培训费），"
            "理论培训费不单独扣除；如需另扣请人工补录"
        )
    items.extend(_penalty_item(tier, total_fee))

    total_deduction = sum(it["amount"] for it in items if not it["pending"])

    refund_pending = any(it["pending"] for it in items)
    refund = 0.0
    # 退费基数优先用 total_amount（总金额=培训费+代交费+服务费），缺省回退 total_fee（培训费总额）
    refund_base = total_amount if total_amount is not None else total_fee
    if not refund_pending and refund_base is not None and refund_base != "":
        refund = max(0.0, float(refund_base) - total_deduction)

    return {
        "items": items,
        "total_deduction": total_deduction,
        "refund": refund,
        "refund_pending": refund_pending,
        "warnings": warnings,
        "tier_id": tier.get("id", ""),
        "stage": stage,
    }


# ── 东城自制合同第六条退费分支（2026-09-02 新增，工单 11-dongcheng-tier） ──

# 受理前阶段集合：这些 exam_stage 表示学员尚未完成档案受理。
# 第六条（二）以「档案受理」为分界：受理前退学退 50% 服务费，受理后退学不退。
# 注：生产环境 exam_stage 实际取值（来自内部系统事件标题归一化）为「科目一/二/三/四」
# 「待考科目X」「待补考科目X」等短词，均已进入考试流程 → 档案必已受理；故下方用
# 「已受理关键词」+「明确未受理信号」双向判定，而非穷举白名单（避免漏判/误判）。
_ACCEPTED_STAGES: frozenset[str] = frozenset({
    "已受理", "已缴费", "已缴费1190(申请)", "已毕业", "已领证",
    "科一约考", "科一约成功",
    "科一已缴费(申请)", "科一未通过", "科一通过", "科二收", "科二约考",
    "科二约成功", "科二已缴费(申请)", "科二未通过", "科二通过", "科三收",
    "科三约考", "科三约成功", "科三已缴费(申请)", "科三未通过", "科三通过",
    "科四收", "科四约考", "科四约成功", "科四未通过", "科四通过",
})

# 已进入考试/培训流程的关键词：凡命中任一，即视为档案已受理（要考试必先建档受理）。
# 注意：不含「受理」一词——「受理中/待受理」均含「受理」但语义是「未完成受理」，
# 会与「已受理」混淆；「已受理/已缴费/已毕业」等精确值已由 _ACCEPTED_STAGES 白名单覆盖。
_ACCEPTED_KEYWORDS: tuple[str, ...] = (
    "科目一", "科目二", "科目三", "科目四",
    "科一", "科二", "科三", "科四",
    "约考", "通过", "已领证",
)

# 明确的「未受理」信号（优先级高于关键词：即便含「科」字也按未受理）。
_NOT_ACCEPTED_SIGNALS: tuple[str, ...] = ("未报名", "待受理", "受理中")

# 代交费用（第五条）：东城自制合同一次性代收代交 490 元（考试费+补考费+工本费）。
_DONGCHENG_AGENCY_FEE = 490.0

# 先培后付补交款（第六条（四））：退首期咨询服务费需向甲方补交 800 元。
_DONGCHENG_PAYBACK = 800.0


def _is_accepted(stage: str) -> bool:
    """判断学员是否已完成档案受理（第六条（二）的分界）。

    语义规则（2026-09-02 修正，生产 exam_stage 为「科目一/二/三/四」等短词）：
    - 明确的未受理信号（未报名/待受理/空/无）→ 未受理；
    - 命中已受理关键词（科目一/二/三/四、科一~科四、已受理/已缴费/已毕业/约考/通过等）→ 已受理；
    - 其余（含历史白名单精确值）→ 已受理。
    """
    s = str(stage or "").strip()
    if not s or s == "无":
        return False
    for signal in _NOT_ACCEPTED_SIGNALS:
        if signal in s:
            return False
    if s in _ACCEPTED_STAGES:
        return True
    for kw in _ACCEPTED_KEYWORDS:
        if kw in s:
            return True
    # 兜底：非空、非明确未受理，保守视为已受理（避免少扣服务费致多退）
    return True


def calculate_dongcheng_refund(
    tier: dict,
    stage: str,
    progress: dict,
    total_fee: float | None,
    service_fee: float | None = None,
    training_mode: str = "",
    manual_amounts: dict | None = None,
) -> dict:
    """东城自制合同第六条「退学退费」专属口径（与线性「必扣+违约金」模型不同）。

    第六条分支：
    - （二）普通培训：档案受理前退学 → 咨询/服务费退回 50%（即扣 50%）；
      档案受理后退学 → 已交费用不退（服务费全额扣）。
    - （三）已发生实操培训费 = 学时 × 档位单价（80 元/学时，C1/C2 统一，用户
      拍板采信合同正文第六条（三）「约定的学时收费标准是 80 元/学时」）。
    - （四）先培后付：退首期咨询服务费需补交 800 元；约考科一二三前退学互不退补
      （违约金除外）。该分支数据难以自动获取 → 以告警提示人工核对，不臆造金额。
    - （五）代交费用（490 元）：扣除已完成（含已开始）考试科目的考试费后退剩余。
    - 第十一条：违约金 = 总培训费用 × 20%。

    返回结构与 calculate_deductions 一致，便于上层统一消费。
    """
    exam_counts = (progress or {}).get("exam_counts") or {}
    training_hours = (progress or {}).get("training_hours") or {}
    license_type = (progress or {}).get("license_type") or "C1"

    items: list[dict] = []
    warnings: list[str] = []
    mode = str(training_mode or "").strip()
    accepted = _is_accepted(stage)
    svc = float(service_fee or 0)

    # ── 1) 咨询/服务费：受理前退 50%，受理后退学不退 ──
    if mode == "先培后付":
        # 先培后付：首期咨询服务费退学补交 800；约考科一二三前退学互不退补。
        # 具体金额依赖「首期支付额/是否已约考」等字段（系统未采集）→ 告警人工核对。
        items.append(_make_item(
            category="必扣",
            name="先培后付退学补交款",
            amount=_DONGCHENG_PAYBACK,
            basis=f"第六条（四）退首期咨询服务费需向甲方补交 {_DONGCHENG_PAYBACK:.0f} 元",
            source="tier_default",
        ))
        warnings.append(
            "东城自制·先培后付：约考科一/二/三前退学互不退补（违约金除外），"
            "具体补交/退补金额请人工按第六条（四）核对"
        )
    elif svc > 0:
        if accepted:
            items.append(_make_item(
                category="必扣",
                name="服务费",
                amount=svc,
                basis=f"第六条（二）档案已受理，退学服务费全额不退（{svc:.0f} 元）",
                source="tier_default",
            ))
        else:
            items.append(_make_item(
                category="必扣",
                name="服务费（扣50%）",
                amount=round(svc * 0.5, 2),
                basis=f"第六条（二）档案受理前退学，服务费扣 50%（{svc:.0f}×50%）",
                source="tier_default",
            ))

    # ── 2) 已发生实操培训费（学时 × C1/C2 单价）──
    rate_table = tier.get("practical_rates") or {}
    rate = float(rate_table.get(license_type, 0))
    if rate > 0:
        for key, label in _SUBJECT_LABELS.items():
            if key == "subject1":
                continue
            hours = float(training_hours.get(key, 0) or 0)
            if hours <= 0:
                continue
            amount = hours * rate
            items.append(_make_item(
                category="依实",
                name=f"{label}实操培训费",
                amount=amount,
                basis=f"审核学时 {hours:g} × 档位单价 {rate:.0f} 元/学时（{license_type}）",
            ))
            if total_fee is not None and amount > float(total_fee):
                warnings.append(
                    f"{label}实操培训费 {amount:.0f} 元超出培训费总额 {float(total_fee):.0f} 元，请人工核对"
                )

    # ── 3) 已代收代交考试费（已完成科目）──
    paid_exam = 0.0
    for key, label in _SUBJECT_LABELS.items():
        attempts = int(exam_counts.get(key, 0) or 0)
        if attempts <= 0:
            continue
        exam_fee = float(tier.get("exam_fees", {}).get(key, 0))
        if exam_fee > 0:
            items.append(_make_item(
                category="依实",
                name=f"{label}考试费",
                amount=exam_fee,
                basis=f"已考 1 次 × 档位标准 {exam_fee:.0f} 元",
            ))
            paid_exam += exam_fee
        makeup_fee = float(tier.get("makeup_fees", {}).get(key, 0))
        extra = attempts - 1
        if extra > 0 and makeup_fee > 0:
            items.append(_make_item(
                category="依实",
                name=f"{label}补考费",
                amount=extra * makeup_fee,
                basis=f"补考 {extra} 次 × 档位标准 {makeup_fee:.0f} 元",
            ))
            paid_exam += extra * makeup_fee

    # ── 4) 代交费用退款说明（第六条（五）：490 元中扣除已完成科目后剩余退还）──
    remaining_agency = max(0.0, _DONGCHENG_AGENCY_FEE - paid_exam)
    if remaining_agency > 0:
        warnings.append(
            f"代交费用 490 元扣除已完成科目考试费 {paid_exam:.0f} 元后，"
            f"剩余 {remaining_agency:.0f} 元应退还（第六条（五））"
        )

    # ── 5) 违约金（第十一条：总培训费用 20%，恒排最后）──
    rate_pct = float(tier.get("penalty_rate", 0) or 0)
    if rate_pct > 0:
        if total_fee is None or total_fee == "":
            items.append(_make_item(
                category="违约金",
                name="违约金",
                amount=0,
                basis=f"{tier['display_name']} 档默认违约金率 {rate_pct:.0f}%，基数缺失（pending）",
                pending=True,
            ))
        else:
            amount = float(total_fee) * rate_pct / 100.0
            items.append(_make_item(
                category="违约金",
                name="违约金",
                amount=amount,
                basis=f"总培训费用 × 档位默认 {rate_pct:.0f}%（{tier['display_name']}）",
            ))

    total_deduction = sum(it["amount"] for it in items if not it["pending"])
    refund_pending = any(it["pending"] for it in items)
    refund = 0.0
    if not refund_pending and total_fee is not None and total_fee != "":
        refund = max(0.0, float(total_fee) - total_deduction)

    return {
        "items": items,
        "total_deduction": total_deduction,
        "refund": refund,
        "refund_pending": refund_pending,
        "warnings": warnings,
        "tier_id": tier.get("id", ""),
        "stage": stage,
    }
