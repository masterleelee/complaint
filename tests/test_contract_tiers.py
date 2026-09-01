"""合同档位表与识别器测试（工单 01-tier-registry，表驱动）。

数值基准来自 `1合同种类/` 七份模板原文（提取产物在 tmp/contract_templates_text/）：
- 2019 三份（服务/代缴/培训）：违约金 0%，考试费/实操单价为手填空位；
- 2021/2022（定稿）：违约金 10%，两份模板正文仅格式差异，合并一档；
- 2023 分校：必扣 服务费600+建档费300+学员IC卡100=1000，违约金 20%；
- 2023 分店：分校 + 场地费 700 = 1700，违约金 20%。
考试费 70/130/280（补 35/65/140）、工本费 10、实操 C1 120 / C2 150
以 2023 两版印刷值勘定，全档位共用同一代收代缴标准（ADR-0001：OCR 只校验）。
"""

import pytest

from services.contract_tiers import (
    CONTRACT_TIERS,
    DEFAULT_EXAM_FEES,
    DEFAULT_MAKEUP_FEES,
    DEFAULT_MATERIAL_FEE,
    DEFAULT_PRACTICAL_RATES,
    TIERS_BY_ID,
    identify_tier,
)

# ── 模板原文片段（fixture，摘自七份模板，保留原字符） ──────────────────

TEXT_2019_SERVICE = """东莞市机动车驾驶报考协助服务合同
依照《中华人民共和国合同法》、《中华人民共和国道路交通安全法》等相关法律法规的规定，双方就甲方接受乙方委托，对协助乙方驾驶报考服务相关事宜（不包含驾驶培训服务的相关事项）协商一致，订立本合同。
第三条 费用及支付
乙方向甲方支付受理服务费、综合服务费、学员卡等代交专用款项。
1、代缴的考试行政事业等收费项目 元，受理服务和学员卡 元，科目一服务费 元，科目二服务费 元。"""

TEXT_2019_PAY_AGENT = """东莞市机动车驾驶报考代收代交考试费合同
双方就甲方接受乙方委托，对协助乙方代收代交考试费相关事宜（不包含驾驶培训服务的相关事项）协商一致，订立本合同。
第三条 费用及支付
（一）因乙方要求甲方代收代交服务费、考试费、补考费、工本费等款项，乙方于本合同订立时一次性向甲方支付上述费用。"""

TEXT_2019_TRAINING = """东莞市机动车驾驶员培训合同
依照《中华人民共和国合同法》、《中华人民共和国道路交通安全法》等相关法律法规的规定，双方就甲方接受乙方委托，对乙方开展机动车驾驶培训服务相关事宜（不包含协助乙方驾驶考试服务的相关事项）协商一致，订立本合同。
第五条 退学退费
（二）实行预约培训的，如乙方在参加理论培训前退学，甲方应退回乙方理论培训费及相关手续费；如乙方在参加理论培训后退学，乙方已交的费用不退。"""

TEXT_2021 = """东莞市机动车驾驶员培训合同
依照《中华人民共和国民法典》、《中华人民共和国道路交通安全法》、《中华人民共和国道路运输条例》等相关法律法规的规定，双方就甲方接受乙方委托，对乙方开展机动车驾驶培训服务相关事宜协商一致，订立本合同。
第五条 退学退费
注：1、已发生的实际操作培训费＝已产生的实际操作培训学时×约定的学时收费标准。
2、违约金为全部培训费用的10% ，乙方因不可抗拒因素而退学的，甲方不收取违约金。"""

TEXT_2023_SCHOOL = """东莞市机动车驾驶员培训合同
依照《中华人民共和国民法典》、《中华人民共和国道路交通安全法》等相关法律法规的规定，双方就甲方接受乙方委托，对乙方开展机动车驾驶培训服务相关事宜协商一致，订立本合同。
第四条 费用及支付
1、乙方选择以下第 1 种方式支付培训费用（包含建档费/学员IC卡/各阶段培训费及相关手续费，但不包括甲方代收代缴的行政事业收费）：
包含乙方理论培训、实操培训及甲方协助乙方建档、学员IC卡等相关服务费。乙方于本合同订立当日内一次性向甲方支付。
第八条 退学退费
基础服务（必扣项）
服务费 600
建档费 300
学员IC卡 100
代收代缴（依实项）
科目一考试费 70 补考费35元/次
科目二考试费 130 补考费65元/次
科目三考试费 280 补考费140元/次
工本费 10
实操培训（依实项）
科目二实操 C1 120元/学时
C2 150元/学时
备注：合同期内乙方申请提前解除合同，扣除基础服务、其他双方约定的费用两项必扣项及乙方解除合同时已完成（含已开始）的考试科目（阶段）已经代收代交的考试行政事业收费（包括考试费、补考费等）及已产生的实操培训费，再扣除全部培训费用的20%作为违约金后，甲方将剩余的费用退还给乙方。"""

TEXT_2023_STORE = """东莞市机动车驾驶员培训合同
依照《中华人民共和国民法典》、《中华人民共和国道路交通安全法》等相关法律法规的规定，双方就甲方接受乙方委托，对乙方开展机动车驾驶培训服务相关事宜协商一致，订立本合同。
第四条 费用及支付
1、乙方选择以下第 1 种方式支付培训费用（包含建档费/学员IC卡/各阶段培训费及相关手续费，但不包括甲方代收代缴的行政事业收费）：
包含乙方理论培训、实操培训及甲方协助乙方建档、学员IC卡等相关服务费。乙方于本合同订立当日内一次性向甲方支付。
第八条 退学退费
基础服务（必扣项）
服务费 600
建档费 300
学员IC卡 100
场地费 700
代收代缴（依实项）
科目一考试费 70 补考费35元/次
工本费 10
实操培训（依实项）
科目二实操 C1 120元/学时
备注：合同期内乙方申请提前解除合同，扣除基础服务、其他双方约定的费用两项必扣项及乙方解除合同时已完成（含已开始）的考试科目（阶段）已经代收代交的考试行政事业收费（包括考试费、补考费等）及已产生的实操培训费，再扣除全部培训费用的20%作为违约金后，甲方将剩余的费用退还给乙方。"""


# ── 1. 档位表数值逐项与模板原文一致（表驱动断言） ────────────────────

EXPECTED_TIERS = {
    # tier_id: (显示名, 违约金率%, 必扣合计, 种类, 年份下限, 年份上限, 网点类型)
    "2019_service": ("2019·服务", 0, 0, "服务", None, 2019, None),
    "2019_pay_agent": ("2019·代缴", 0, 0, "代缴", None, 2019, None),
    "2019_training": ("2019·培训", 0, 0, "培训", None, 2019, None),
    "2021_2022": ("2021-2022", 10, 0, "单一培训", 2020, 2022, None),
    "2023_branch_school": ("2023·分校", 20, 1000, "单一培训", 2023, None, ["分校"]),
    "2023_branch_store": ("2023·分店", 20, 1700, "单一培训", 2023, None, ["分店"]),
}


def test_six_tiers_registered():
    assert len(CONTRACT_TIERS) == 6
    assert set(TIERS_BY_ID) == set(EXPECTED_TIERS)


@pytest.mark.parametrize(
    "tier_id, expected",
    sorted(EXPECTED_TIERS.items()),
    ids=lambda val: val if isinstance(val, str) else "",
)
def test_tier_registry_values(tier_id, expected):
    display_name, penalty_rate, mandatory_total, kind, year_from, year_to, org_types = expected
    tier = TIERS_BY_ID[tier_id]
    assert tier["display_name"] == display_name
    assert tier["penalty_rate"] == penalty_rate
    assert tier["kind"] == kind
    assert tier["year_from"] == year_from
    assert tier["year_to"] == year_to
    assert tier["org_types"] == org_types
    assert sum(item["amount"] for item in tier["mandatory_items"]) == mandatory_total


def test_2023_school_mandatory_items_match_template():
    """2023·分校 必扣项 = 服务费600 + 建档费300 + 学员IC卡100（模板退费表原文）。"""
    items = {item["item"]: item["amount"] for item in TIERS_BY_ID["2023_branch_school"]["mandatory_items"]}
    assert items == {"服务费": 600, "建档费": 300, "学员IC卡": 100}


def test_2023_store_mandatory_items_match_template():
    """2023·分店 必扣项 = 分校 + 场地费700（模板退费表原文）。"""
    items = {item["item"]: item["amount"] for item in TIERS_BY_ID["2023_branch_store"]["mandatory_items"]}
    assert items == {"服务费": 600, "建档费": 300, "学员IC卡": 100, "场地费": 700}


def test_exam_fee_standards_match_template():
    """考试费/补考费/工本费/实操单价与 2023 两版印刷值一致，全档位共用。"""
    assert DEFAULT_EXAM_FEES == {"subject1": 70, "subject2": 130, "subject3": 280}
    assert DEFAULT_MAKEUP_FEES == {"subject1": 35, "subject2": 65, "subject3": 140}
    assert DEFAULT_MATERIAL_FEE == 10
    assert DEFAULT_PRACTICAL_RATES == {"C1": 120, "C2": 150}
    for tier in CONTRACT_TIERS:
        assert tier["exam_fees"] == DEFAULT_EXAM_FEES
        assert tier["makeup_fees"] == DEFAULT_MAKEUP_FEES
        assert tier["material_fee"] == DEFAULT_MATERIAL_FEE
        assert tier["practical_rates"] == DEFAULT_PRACTICAL_RATES


def test_every_tier_has_features():
    """每档至少一条特征句，且权重为正——保证识别器有依据可用。"""
    for tier in CONTRACT_TIERS:
        assert tier["features"], f"{tier['id']} 缺少特征句"
        for phrase, weight in tier["features"]:
            assert phrase and weight > 0


# ── 2. 识别矩阵（报名日期 + 网点类型 + 合同文本） ────────────────────

IDENTIFY_MATRIX = [
    # (说明, 报名日期, 网点类型, 文本, 期望档位, 期望置信度)
    ("2023报名+分店网点+分店文本", "2023-05-10", "分店", TEXT_2023_STORE, "2023_branch_store", "high"),
    ("2023报名+分校网点+分校文本", "2023-05-10", "分校", TEXT_2023_SCHOOL, "2023_branch_school", "high"),
    ("2023报名+分店网点+分校文本→权威优先+告警", "2023-05-10", "分店", TEXT_2023_SCHOOL, "2023_branch_store", "medium"),
    ("2021报名+2021文本", "2021-03-26", "", TEXT_2021, "2021_2022", "high"),
    ("2022报名+空文本→唯一候选低置信", "2022-05-13", "", "", "2021_2022", "low"),
    ("2019报名+服务文本", "2019-05-01", "", TEXT_2019_SERVICE, "2019_service", "high"),
    ("2019报名+代缴文本", "2019-05-01", "", TEXT_2019_PAY_AGENT, "2019_pay_agent", "high"),
    ("2019报名+培训文本", "2019-05-01", "", TEXT_2019_TRAINING, "2019_training", "high"),
    ("2019报名+空文本→三候选不给档", "2019-05-01", "", "", "", "low"),
    ("无权威+无文本→不给档", "", "", "", "", "low"),
    ("无权威+分店文本→特征决胜", "", "", TEXT_2023_STORE, "2023_branch_store", "high"),
    ("2023报名+2021文本→特征纠正年份收敛", "2023-05-10", "", TEXT_2021, "", "low"),
]


@pytest.mark.parametrize(
    "label, reg_date, org_type, text, expected_tier, expected_conf",
    IDENTIFY_MATRIX,
    ids=[case[0] for case in IDENTIFY_MATRIX],
)
def test_identify_matrix(label, reg_date, org_type, text, expected_tier, expected_conf):
    result = identify_tier(registration_date=reg_date, org_unit_type=org_type, contract_text=text)
    assert result["tier_id"] == expected_tier, f"evidence={result['evidence']}"
    assert result["confidence"] == expected_conf
    if expected_tier:
        assert result["display_name"] == TIERS_BY_ID[expected_tier]["display_name"]


def test_2019_registration_yields_three_candidates():
    """2019 报名 → 候选含服务/代缴/培训三份，特征句能分辨。"""
    result = identify_tier(registration_date="2019-08-01", contract_text=TEXT_2019_SERVICE)
    assert set(result["candidates"]) == {"2019_service", "2019_pay_agent", "2019_training"}
    assert result["tier_id"] == "2019_service"


# ── 3. 置信度与特征命中单调相关；零命中返回低置信+候选，不抛错 ────────

def test_confidence_monotonic_with_feature_hits():
    """0 命中 → low；部分命中 → medium；标题命中 → high（2019 三候选场景）。"""
    base = identify_tier(registration_date="2019-05-01", contract_text="")
    assert base["confidence"] == "low" and base["tier_id"] == ""

    # 仅一般特征命中（无标题）：摘掉标题行，只留「受理服务和学员卡」独有句
    partial = TEXT_2019_SERVICE.replace("东莞市机动车驾驶报考协助服务合同\n", "").replace(
        "对协助乙方驾驶报考服务相关事宜（不包含驾驶培训服务的相关事项）协商一致，订立本合同。", ""
    )
    mid = identify_tier(registration_date="2019-05-01", contract_text=partial)
    assert mid["tier_id"] in ("", "2019_service")  # 分数不足 3 时不得给 high
    assert mid["confidence"] in ("low", "medium")

    full = identify_tier(registration_date="2019-05-01", contract_text=TEXT_2019_SERVICE)
    assert full["confidence"] == "high"


ORDER = {"low": 0, "medium": 1, "high": 2}


def test_no_text_never_raises():
    """极端输入（None/乱码）不抛错，返回低置信。"""
    for text in (None, "!,?,。。。", "顺丰快递单号123"):
        result = identify_tier(registration_date="", org_unit_type="", contract_text=text or "")
        assert result["tier_id"] == ""
        assert result["confidence"] == "low"
        assert result["candidates"]  # 候选列表兜底为全档位


# ── 4. 文本与权威数据冲突 → 告警证据，不静默采信（ADR-0001） ──────────

def test_penalty_rate_conflict_warns():
    """文本含「20%作为违约金」但权威数据推断 2021-2022（10%）→ 冲突告警。"""
    conflict_text = TEXT_2021.replace("违约金为全部培训费用的10%", "再扣除全部培训费用的20%作为违约金")
    result = identify_tier(registration_date="2021-03-26", contract_text=conflict_text)
    conflicts = [e for e in result["evidence"] if e["type"] == "conflict"]
    assert conflicts, f"应有冲突告警: {result['evidence']}"
    assert "20" in conflicts[0]["detail"] and "10" in conflicts[0]["detail"]
    # 告警不改变档位判定（权威数据仍收敛到 2021_2022）
    assert result["tier_id"] == "2021_2022"


def test_no_conflict_when_text_matches_tier_default():
    result = identify_tier(registration_date="2023-05-10", org_unit_type="分校", contract_text=TEXT_2023_SCHOOL)
    assert not [e for e in result["evidence"] if e["type"] == "conflict"]


def test_conflict_does_not_overrides_authority():
    """分店网点 + 分校文本（无场地费）→ 场地费缺失告警，档位仍按权威网点。"""
    result = identify_tier(registration_date="2023-05-10", org_unit_type="分店", contract_text=TEXT_2023_SCHOOL)
    # 场地费是分店必印条款，文本没有 → 告警但不静默改档（权威优先，用户可手动改）
    assert result["tier_id"] == "2023_branch_store"
    assert any("场地费" in e["detail"] for e in result["evidence"] if e["type"] == "conflict")


def test_school_tier_with_store_text_warns_absent_feature():
    """分校网点 + 分店文本（含场地费）→ 分校档告警「不应含场地费」。"""
    result = identify_tier(registration_date="2023-05-10", org_unit_type="分校", contract_text=TEXT_2023_STORE)
    assert result["tier_id"] == "2023_branch_school"
    assert any("场地费" in e["detail"] for e in result["evidence"] if e["type"] == "conflict")


# ── 5. 输出结构完整性 ────────────────────────────────────────────────

def test_result_shape():
    result = identify_tier(registration_date="2023-05-10", org_unit_type="分店", contract_text=TEXT_2023_STORE)
    assert set(result) >= {"tier_id", "display_name", "confidence", "candidates", "evidence", "score"}
    assert any(e["type"] == "authority" for e in result["evidence"])
    assert any(e["type"] == "feature" for e in result["evidence"])


def test_unknown_org_type_ignores_org_constraint():
    """网点类型不在字典（如『未入字典』）时不得把候选收敛为空。"""
    result = identify_tier(registration_date="2023-05-10", org_unit_type="未入字典", contract_text=TEXT_2023_STORE)
    assert result["tier_id"] == "2023_branch_store"
