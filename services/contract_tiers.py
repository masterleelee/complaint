"""合同档位表与识别器（工单 01-tier-registry，ADR-0001）。

六档合同（源自 `1合同种类/` 七份模板，2021/2022 两份正文仅格式差异合并一档）：

| 档位           | 违约金 | 必扣项                                   | 种类     |
|----------------|--------|------------------------------------------|----------|
| 2019·服务      | 0%     | 无                                       | 服务     |
| 2019·代缴      | 0%     | 无                                       | 代缴     |
| 2019·培训      | 0%     | 无                                       | 培训     |
| 2021-2022      | 10%    | 无                                       | 单一培训 |
| 2023·分校      | 20%    | 服务费600+建档费300+学员IC卡100 = 1000   | 单一培训 |
| 2023·分店      | 20%    | 分校 + 场地费700 = 1700                  | 单一培训 |

考试费 70/130/280（补考 35/65/140）、工本费 10、实操单价 C1 120 / C2 150
为东莞驾培统一代收代缴标准（2023 两版印刷值勘定），全档位共用；
2019/2021-2022 版合同中该金额为手填，由扣费引擎按 OCR/人工置信处理并以此交叉校验。

识别策略（ADR-0001）：报名日期 + 网点类型（权威数据）先收敛候选档，
特征句匹配打分辨别；文本与权威数据冲突时输出告警证据、不静默采信。
本模块为纯函数，不改任何现有行为。
"""

import re

# ── 统一代收代缴标准（全档位共用） ──────────────────────────────────

DEFAULT_EXAM_FEES = {"subject1": 70, "subject2": 130, "subject3": 280}
DEFAULT_MAKEUP_FEES = {"subject1": 35, "subject2": 65, "subject3": 140}
DEFAULT_MATERIAL_FEE = 10  # 工本费
DEFAULT_PRACTICAL_RATES = {"C1": 120, "C2": 150}  # 元/学时

# 2023 两版共有的身份特征（印在合同正文，OCR 可核对）
_F_2023_COMMON = [
    ("全部培训费用的20%作为违约金", 3),
    ("（必扣项）", 2),
    ("建档费", 2),
    ("包含乙方理论培训、实操培训及甲方协助乙方建档、学员IC卡等相关服务费", 2),
]

CONTRACT_TIERS = [
    {
        "id": "2019_service",
        "display_name": "2019·服务",
        "kind": "服务",
        "penalty_rate": 0,
        "mandatory_items": [],
        "exam_fees": dict(DEFAULT_EXAM_FEES),
        "makeup_fees": dict(DEFAULT_MAKEUP_FEES),
        "material_fee": DEFAULT_MATERIAL_FEE,
        "practical_rates": dict(DEFAULT_PRACTICAL_RATES),
        "features": [
            ("东莞市机动车驾驶报考协助服务合同", 3),
            ("协助乙方驾驶报考服务相关事宜", 2),
            ("受理服务和学员卡", 1),
        ],
        "required_features": [],
        "absent_features": [],
        "year_from": None,
        "year_to": 2019,
        "org_types": None,
    },
    {
        "id": "2019_pay_agent",
        "display_name": "2019·代缴",
        "kind": "代缴",
        "penalty_rate": 0,
        "mandatory_items": [],
        "exam_fees": dict(DEFAULT_EXAM_FEES),
        "makeup_fees": dict(DEFAULT_MAKEUP_FEES),
        "material_fee": DEFAULT_MATERIAL_FEE,
        "practical_rates": dict(DEFAULT_PRACTICAL_RATES),
        "features": [
            ("东莞市机动车驾驶报考代收代交考试费合同", 3),
            ("协助乙方代收代交考试费相关事宜", 2),
        ],
        "required_features": [],
        "absent_features": [],
        "year_from": None,
        "year_to": 2019,
        "org_types": None,
    },
    {
        "id": "2019_training",
        "display_name": "2019·培训",
        "kind": "培训",
        "penalty_rate": 0,
        "mandatory_items": [],
        "exam_fees": dict(DEFAULT_EXAM_FEES),
        "makeup_fees": dict(DEFAULT_MAKEUP_FEES),
        "material_fee": DEFAULT_MATERIAL_FEE,
        "practical_rates": dict(DEFAULT_PRACTICAL_RATES),
        "features": [
            ("不包含协助乙方驾驶考试服务的相关事项", 3),
            ("如乙方在参加理论培训前退学，甲方应退回乙方理论培训费", 2),
        ],
        "required_features": [],
        "absent_features": [],
        "year_from": None,
        "year_to": 2019,
        "org_types": None,
    },
    {
        "id": "2021_2022",
        "display_name": "2021-2022",
        "kind": "单一培训",
        "penalty_rate": 10,
        "mandatory_items": [],
        "exam_fees": dict(DEFAULT_EXAM_FEES),
        "makeup_fees": dict(DEFAULT_MAKEUP_FEES),
        "material_fee": DEFAULT_MATERIAL_FEE,
        "practical_rates": dict(DEFAULT_PRACTICAL_RATES),
        "features": [
            ("违约金为全部培训费用的10%", 3),
            ("《中华人民共和国民法典》", 1),
        ],
        "required_features": [],
        "absent_features": [],
        "year_from": 2020,
        "year_to": 2022,
        "org_types": None,
    },
    {
        "id": "2023_branch_school",
        "display_name": "2023·分校",
        "kind": "单一培训",
        "penalty_rate": 20,
        "mandatory_items": [
            {"item": "服务费", "amount": 600},
            {"item": "建档费", "amount": 300},
            {"item": "学员IC卡", "amount": 100},
        ],
        "exam_fees": dict(DEFAULT_EXAM_FEES),
        "makeup_fees": dict(DEFAULT_MAKEUP_FEES),
        "material_fee": DEFAULT_MATERIAL_FEE,
        "practical_rates": dict(DEFAULT_PRACTICAL_RATES),
        "features": list(_F_2023_COMMON),
        # 分校版退费表没有场地费行——文本若连「场地费」都没有，反而佐证分校版
        "required_features": [],
        "absent_features": [("场地费", 3)],
        "year_from": 2023,
        "year_to": None,
        "org_types": ["分校"],
    },
    {
        "id": "2023_branch_store",
        "display_name": "2023·分店",
        "kind": "单一培训",
        "penalty_rate": 20,
        "mandatory_items": [
            {"item": "服务费", "amount": 600},
            {"item": "建档费", "amount": 300},
            {"item": "学员IC卡", "amount": 100},
            {"item": "场地费", "amount": 700},
        ],
        "exam_fees": dict(DEFAULT_EXAM_FEES),
        "makeup_fees": dict(DEFAULT_MAKEUP_FEES),
        "material_fee": DEFAULT_MATERIAL_FEE,
        "practical_rates": dict(DEFAULT_PRACTICAL_RATES),
        "features": list(_F_2023_COMMON) + [("场地费", 3), ("700", 1)],
        # 场地费是分店版身份条款：文本缺失即告警（可能传错合同/分店代用分校版）
        "required_features": ["场地费"],
        "absent_features": [],
        "year_from": 2023,
        "year_to": None,
        "org_types": ["分店"],
    },
]

TIERS_BY_ID = {tier["id"]: tier for tier in CONTRACT_TIERS}

# 特征打分所需的最短正文长度（规范化后字符数），防止空文本/碎片文本误判
_MIN_SCORABLE_LEN = 10

# 只认「全部培训费用」基数的违约金率句式——与档位 penalty_rate 语义一致。
# 注意 2021/2022 版预约培训段另有「已交理论培训费及相关服务费的20%」条款，
# 基数不同（非全部培训费用），不得参与冲突比对。
_TEXT_PENALTY_PATTERNS = (
    re.compile(r"全部培训费用的(\d{1,2})%"),
)


def _norm(text: str) -> str:
    """去全部空白：OCR/提取常在字间插换行与空格，短语匹配按无空白进行。"""
    return re.sub(r"\s+", "", text or "")


def _parse_year(registration_date: str) -> int | None:
    match = re.search(r"(\d{4})", str(registration_date or ""))
    return int(match.group(1)) if match else None


def _text_penalty_rates(norm_text: str) -> set[int]:
    rates = set()
    for pattern in _TEXT_PENALTY_PATTERNS:
        for match in pattern.finditer(norm_text):
            try:
                rates.add(int(match.group(1)))
            except ValueError:
                continue
    return rates


def _candidate_ids(year: int | None, org_type: str) -> list[str]:
    """权威数据收敛候选：年份先过滤，网点类型只对声明了 org_types 的档位二分。"""
    ids = []
    for tier in CONTRACT_TIERS:
        if year is not None:
            if tier["year_from"] is not None and year < tier["year_from"]:
                continue
            if tier["year_to"] is not None and year > tier["year_to"]:
                continue
        ids.append(tier["id"])

    org_type = (org_type or "").strip()
    if org_type and org_type != "未入字典":
        constrained = [tid for tid in ids if TIERS_BY_ID[tid]["org_types"]]
        keep = [
            tid
            for tid in constrained
            if org_type in (TIERS_BY_ID[tid]["org_types"] or [])
        ]
        if constrained and keep:
            # 只剔除「声明了网点类型但不匹配」的档位；2019/2021 系不受网点约束
            ids = [tid for tid in ids if not TIERS_BY_ID[tid]["org_types"] or tid in keep]
    return ids or [tier["id"] for tier in CONTRACT_TIERS]


def _score_tier(tier: dict, norm_text: str, scorable: bool) -> int:
    if not scorable:
        return 0
    score = sum(weight for phrase, weight in tier["features"] if _norm(phrase) in norm_text)
    # absent 特征（如分校版无场地费行）只做佐证：正文先命中该档正向特征才有资格加分，
    # 否则「无场地费」会把 2019/2021 等无关文本也判成 2023·分校。
    if score > 0:
        score += sum(weight for phrase, weight in tier.get("absent_features", []) if _norm(phrase) not in norm_text)
    return score


def _hit_features(tier: dict, norm_text: str) -> list[dict]:
    hits = []
    for phrase, weight in tier["features"]:
        if _norm(phrase) in norm_text:
            hits.append({"type": "feature", "tier": tier["id"], "phrase": phrase, "weight": weight})
    for phrase, weight in tier.get("absent_features", []):
        if _norm(phrase) not in norm_text:
            hits.append({"type": "feature", "tier": tier["id"], "phrase": f"（无）{phrase}", "weight": weight})
    return hits


def identify_tier(registration_date: str = "", org_unit_type: str = "", contract_text: str = "") -> dict:
    """识别合同档位（纯函数）。

    输入：报名日期、网点类型（分校/分店等，来自 org_unit_service 字典）、合同文本。
    输出：{tier_id, display_name, confidence, score, candidates, evidence}。
    tier_id 为 "" 表示无法定档（调用方应让用户手动选择）；
    confidence: high（权威收敛+特征确认）/ medium（特征分不足或存在冲突告警）/ low（无特征依据）。
    """
    year = _parse_year(registration_date)
    norm_text = _norm(contract_text)
    scorable = len(norm_text) >= _MIN_SCORABLE_LEN

    candidates = _candidate_ids(year, org_unit_type)

    evidence: list[dict] = []
    if year is not None:
        evidence.append({
            "type": "authority",
            "detail": f"报名年份 {year} → 候选 {[TIERS_BY_ID[tid]['display_name'] for tid in candidates]}",
        })
    if (org_unit_type or "").strip() and (org_unit_type or "").strip() != "未入字典":
        evidence.append({
            "type": "authority",
            "detail": f"网点类型「{org_unit_type.strip()}」参与候选收敛",
        })

    scores = {tier["id"]: _score_tier(tier, norm_text, scorable) for tier in CONTRACT_TIERS}
    in_scores = {tid: scores[tid] for tid in candidates}
    best = max(in_scores.values())
    winners = [tid for tid, s in in_scores.items() if s == best]

    tier_id = ""
    confidence = "low"
    score = 0
    if len(candidates) == 1:
        tier_id = candidates[0]
        score = scores[tier_id]
        if score >= 1:
            confidence = "high"
    elif best > 0 and len(winners) == 1:
        tier_id = winners[0]
        score = best
        confidence = "high" if best >= 3 else "medium"

    if tier_id:
        evidence.extend(_hit_features(TIERS_BY_ID[tier_id], norm_text))

    # ── 冲突检测：告警呈现，不静默采信（ADR-0001） ──────────────────
    conflicts: list[str] = []
    if scorable and tier_id:
        tier = TIERS_BY_ID[tier_id]
        for phrase in tier["required_features"]:
            if _norm(phrase) not in norm_text:
                conflicts.append(f"文本缺少{tier['display_name']}合同应有的「{phrase}」条款，请核对是否传错合同")
        for phrase, _weight in tier.get("absent_features", []):
            if _norm(phrase) in norm_text:
                conflicts.append(f"文本出现「{phrase}」，而{tier['display_name']}版合同不含该条款，请核对是否传错合同")
        for rate in sorted(_text_penalty_rates(norm_text)):
            if rate != tier["penalty_rate"]:
                conflicts.append(
                    f"文本违约金率 {rate}% 与 {tier['display_name']} 档默认 {tier['penalty_rate']}% 不符，请人工核对"
                )
    if scorable and not tier_id:
        # 候选内全零但候选外特征明确 → 文本与权威数据矛盾，告警不采信
        outside = {tid: s for tid, s in scores.items() if tid not in candidates}
        outside_best = max(outside.values(), default=0)
        if outside_best >= 3 and outside_best > best:
            top = [tid for tid, s in outside.items() if s == outside_best][0]
            conflicts.append(
                f"文本特征指向 {TIERS_BY_ID[top]['display_name']}，与报名日期/网点类型推断的候选不符，请人工核对"
            )

    for message in conflicts:
        evidence.append({"type": "conflict", "tier": tier_id, "detail": message})
    if conflicts and confidence == "high":
        confidence = "medium"

    return {
        "tier_id": tier_id,
        "display_name": TIERS_BY_ID[tier_id]["display_name"] if tier_id else "",
        "confidence": confidence,
        "score": score,
        "candidates": candidates,
        "evidence": evidence,
    }


def get_tier(tier_id: str) -> dict | None:
    return TIERS_BY_ID.get(tier_id)
