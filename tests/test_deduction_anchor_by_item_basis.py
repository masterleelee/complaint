"""按 item_basis 定位扣费锚点的测试（合同原文对照弹窗 v4.4 · S1）。

覆盖：
- 2023·分校档 **全 15 条** basis 逐条定位：落点为**内容行**（不以「第」开头，即不落在条款标题）
- 【演示案例专项】服务费/建档费/学员IC卡/违约金/科目二实操培训费/科目三实操培训费 的 anchor_text
- 【诚实性】分校退费表无「场地费」行 → `(None, None)`；同项在分店版必须命中
- **反向证据**：服务费/建档费/学员IC卡 新定位 ≠ 旧「全文首次命中」，且旧结果落在错误条款
- 东城自制档 6 条**逐条落内容行**（含第六条（三）实操行、第十一条（一）违约金行；
  咨询/服务费、理论培训费 经 v4.4·S4 补的「裸序号行」步锚到第四条（一）1 的「1、…」行）
- 扣费项的 basis 解析字段（basis_clause/basis_section/basis_row）逐档全覆盖、与
  parse_item_basis 严格一致（None→""）；无 basis 项三键为空串
- 无关文本 / 未知档位 → `(None, None)` 且不抛
- 条款窗口必须止于「下一条标题」，不得越界到后续条款
- parse_item_basis 各形态逐条断言
- 向后兼容：tier=None 与「无 basis 项」仍走原 hints 路径
"""

import re

import pytest

from services.contract_template_text import find_clause_window, template_text
from services.contract_tiers import TIERS_BY_ID
from services.anchor_resolver import (
    find_by_basis,
    find_phrase,
    parse_item_basis,
    resolve_anchors_for_items,
)

# 条款标题行：行首（容忍缩进/空格排版）的「第X条」
_CLAUSE_TITLE_LINE = re.compile(r"^[ \t\u3000]*第\s*[一二三四五六七八九十百零〇]+\s*条")


def _line_containing(text: str, pos: int) -> str:
    """返回 pos 所在整行（不含换行符）。"""
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start:end if end != -1 else len(text)]


def _assert_on_content_line(text: str, start: int, name: str) -> None:
    """强判据：锚点所在的**整行**不得是条款标题行。

    比 `not anchor.startswith("第")` 强：标题行内的子串（如标题行「第五条  代交费用及支付」
    里的「代交费」）不以「第」开头，弱断言放行，强断言拦下。
    """
    assert start is not None, f"{name} 未定位"
    line = _line_containing(text, start)
    assert not _CLAUSE_TITLE_LINE.match(line), f"{name} 落进条款标题行：{line!r}"


TIER = TIERS_BY_ID["2023_branch_school"]
TEXT = template_text(TIER)
EIGHT_START = find_clause_window(TEXT, "第八条")[0]

DONGCHENG = TIERS_BY_ID["2019_dongcheng"]
DONGCHENG_TEXT = template_text(DONGCHENG)

PAY_AGENT = TIERS_BY_ID["2019_pay_agent"]
PAY_AGENT_TEXT = template_text(PAY_AGENT)


# ── 全 15 条逐条定位（落点为内容行，不落在条款标题） ──────────────────

# 2023·分校档 item_basis 的权威清单（逐条列举，不依赖 dict 顺序）
ITEM_NAMES_2023 = [
    "服务费",
    "建档费",
    "学员IC卡",
    "场地费",
    "工本费",
    "科目一考试费",
    "科目二考试费",
    "科目三考试费",
    "科目一补考费",
    "科目二补考费",
    "科目三补考费",
    "科目二实操培训费",
    "科目三实操培训费",
    "理论培训费",
    "违约金",
]

# 显式豁免白名单：2023·分校退费表本就没有「场地费」行（分店版才多这一行），
# 条款窗口内无任何含「场地费」的整行/短语 → 诚实返回 (None, None)，绝不退回条款标题。
SCHOOL_ABSENT_WITHIN_CLAUSE = {"场地费"}


def test_item_basis_covers_exactly_15_items():
    assert len(TIER["item_basis"]) == 15
    assert set(TIER["item_basis"]) == set(ITEM_NAMES_2023)


@pytest.mark.parametrize("name", ITEM_NAMES_2023)
def test_each_item_lands_on_content_line(name):
    start, end = find_by_basis(name, TIER, TEXT)
    if name in SCHOOL_ABSENT_WITHIN_CLAUSE:
        assert (start, end) == (None, None), f"{name} 应为诚实缺席，却命中 {TEXT[start:end]!r}"
        return
    assert start is not None and end is not None, f"{name} 在模板里定位失败"
    assert 0 <= start < end <= len(TEXT)
    assert TEXT[start:end].strip() != ""
    # 强判据：锚点所在整行不得是条款标题行（见模块头部说明）
    _assert_on_content_line(TEXT, start, name)
    # basis 写第八条的项，落点必须在第八条区间内
    if "第八条" in TIER["item_basis"][name]:
        assert start >= EIGHT_START, f"{name} 未落在第八条窗口内"


def test_exam_fee_items_land_in_fourth_clause():
    four_start = find_clause_window(TEXT, "第四条")[0]
    for name in ("科目一考试费", "科目二考试费", "科目三考试费", "工本费", "理论培训费"):
        start, end = find_by_basis(name, TIER, TEXT)
        assert start >= four_start, f"{name} 未落在第四条窗口内"
        _assert_on_content_line(TEXT, start, name)


# ── 演示案例专项 ──────────────────────────────────────────────────────

def test_demo_case_anchors_contain_expected_tokens():
    expectations = {
        "服务费": "服务费",
        "建档费": "建档费",
        "学员IC卡": "IC卡",
        "违约金": "违约金",
        "科目二实操培训费": "科目二实操",
        "科目三实操培训费": "科目三实操",
    }
    for name, token in expectations.items():
        start, end = find_by_basis(name, TIER, TEXT)
        assert start is not None, f"{name} 未定位"
        anchor = TEXT[start:end]
        assert token in anchor, f"{name} 落点 {anchor!r} 不含 {token!r}"


def test_venue_fee_honest_absence_and_store_hit():
    # 分校：退费表无场地费行 → 诚实返回 None（不指向条款标题）
    assert find_by_basis("场地费", TIER, TEXT) == (None, None)

    # 分店：退费表有「场地费 700」行 → 必须命中
    store = TIERS_BY_ID["2023_branch_store"]
    store_text = template_text(store)
    start, end = find_by_basis("场地费", store, store_text)
    assert start is not None
    assert "场地费" in store_text[start:end]


def test_handbook_fee_and_penalty_anchor_precision():
    """工本费/违约金 应精确锚到词本身（item_name 命中），而非所在整行或条款标题。"""
    for name in ("工本费", "违约金"):
        start, end = find_by_basis(name, TIER, TEXT)
        assert start is not None, f"{name} 未定位"
        assert TEXT[start:end] == name, f"{name} 落点过宽：{TEXT[start:end]!r}"


# ── 反向证据：新定位 ≠ 旧「全文首次命中」 ─────────────────────────────

@pytest.mark.parametrize("name", ["服务费", "建档费", "学员IC卡"])
def test_new_locate_differs_from_old_first_hit(name):
    new_start, new_end = find_by_basis(name, TIER, TEXT)
    old_start, old_end = find_phrase(name, TEXT)

    assert new_start is not None
    assert old_start is not None
    assert new_start != old_start, f"{name} 新定位未与旧首次命中区分"
    assert old_start < EIGHT_START, f"{name} 旧首次命中竟已在第八条内"
    assert new_start >= EIGHT_START
    assert TEXT[new_start:new_end] == name


def test_old_first_hit_for_service_and_file_fee_in_fourth_clause():
    """服务费/建档费的旧首次命中落在第四条（一）1 的枚举句——本次要修的错位。"""
    four = find_clause_window(TEXT, "第四条")
    for name in ("服务费", "建档费"):
        old_start, _ = find_phrase(name, TEXT)
        assert four[0] <= old_start < four[1], f"{name} 旧首次命中不在第四条窗口"


def test_old_first_hit_for_ic_card_before_eighth():
    """学员IC卡旧首次命中落在第二条（三）办理义务句（早于第八条）——同样是错位的证据。

    注意：本档模板正文里「学员IC卡」的全文首次出现在第二条（三）
    「甲方为乙方办理学员IC卡、建立培训档案」，不是第三条；无论落在哪条，
    只要早于第八条退费表即证明旧行为定位错误。
    """
    old_start, _ = find_phrase("学员IC卡", TEXT)
    assert old_start < EIGHT_START


# ── 东城自制档 ────────────────────────────────────────────────────────

# 东城自制档 item_basis 的权威清单（逐条列举）：v4.4·S4 起**全部 6 条**均可定位到内容行
# （此前「咨询/服务费」「理论培训费」因该档第四条用裸序号「1、2、3、」落空）。
DONGCHENG_CONTENT_ITEMS = [
    "违约金",
    "代交费",
    "科目二实操培训费",
    "科目三实操培训费",
    "咨询/服务费",
    "理论培训费",
]

# 靠 v4.4·S4「裸序号行」补位步锚定的两条（第四条（一）1 的「1、…」行）。
DONGCHENG_BARE_ORDINAL_ITEMS = ["咨询/服务费", "理论培训费"]


@pytest.mark.parametrize("name", DONGCHENG_CONTENT_ITEMS)
def test_dongcheng_items_land_on_content_line(name):
    start, end = find_by_basis(name, DONGCHENG, DONGCHENG_TEXT)
    assert start is not None, f"{name} 未定位"
    _assert_on_content_line(DONGCHENG_TEXT, start, name)


@pytest.mark.parametrize("name", DONGCHENG_BARE_ORDINAL_ITEMS)
def test_dongcheng_bare_ordinal_items_land_in_fourth_clause(name):
    """「咨询/服务费」「理论培训费」：东城第四条用裸序号（1、2、3、）→ 锚到第四条窗口内
    的裸序号内容行（不再诚实缺席，也不落在条款标题行）。"""
    four = find_clause_window(DONGCHENG_TEXT, "第四条")
    start, end = find_by_basis(name, DONGCHENG, DONGCHENG_TEXT)
    assert start is not None, f"{name} 未定位（裸序号补位步未生效）"
    assert four[0] <= start < four[1], f"{name} 未落在第四条窗口"
    _assert_on_content_line(DONGCHENG_TEXT, start, name)


def test_dongcheng_practical_and_penalty_windows():
    six = find_clause_window(DONGCHENG_TEXT, "第六条")
    for name in ("科目二实操培训费", "科目三实操培训费"):
        start, end = find_by_basis(name, DONGCHENG, DONGCHENG_TEXT)
        assert start is not None and six[0] <= start < six[1], f"{name} 未落在第六条窗口"
        # 第六条（三）：已发生实操培训费学时单价
        assert "已发生的实际操作培训费" in DONGCHENG_TEXT[start:end]

    eleven = find_clause_window(DONGCHENG_TEXT, "第十一条")
    start, end = find_by_basis("违约金", DONGCHENG, DONGCHENG_TEXT)
    assert start is not None and eleven[0] <= start < eleven[1], "违约金未落在第十一条窗口"
    assert DONGCHENG_TEXT[start:end] == "违约金"


# ── 2019·代缴档：整档逐条定位（行首匹配步在该档承重） ────────────────

# 2019·代缴档 item_basis 的权威清单（逐条列举）
PAY_AGENT_ITEM_NAMES = [
    "科目一考试费",
    "科目二考试费",
    "科目三考试费",
    "工本费",
    "科目一补考费",
    "科目二补考费",
    "科目三补考费",
]


@pytest.mark.parametrize("name", PAY_AGENT_ITEM_NAMES)
def test_pay_agent_item_lands_on_content_line(name):
    start, end = find_by_basis(name, PAY_AGENT, PAY_AGENT_TEXT)
    assert start is not None and end is not None, f"{name} 未定位"
    _assert_on_content_line(PAY_AGENT_TEXT, start, name)


def test_pay_agent_item_basis_covers_expected_names():
    assert set(PAY_AGENT["item_basis"]) == set(PAY_AGENT_ITEM_NAMES)


# ── 精确位置（把「行首匹配」步钉死：删掉它位置会漂移） ────────────────

def test_pay_agent_and_dongcheng_exact_positions():
    # 2019·代缴「工本费」：整行精确命中（删掉行首匹配步会漂到 (526,529)）
    assert find_by_basis("工本费", PAY_AGENT, PAY_AGENT_TEXT) == (553, 556)
    # 东城「代交费」：锚点在第五条正文行内（删掉行首匹配步会漂进标题行 → (1443,1446)）
    assert find_by_basis("代交费", DONGCHENG, DONGCHENG_TEXT) == (1451, 1454)


# ── 无 basis / 未知：安静返回 ─────────────────────────────────────────
def test_unrelated_text_returns_none_without_raise():
    assert find_by_basis("服务费", TIER, "这是完全无关的文本，没有条款标题") == (None, None)


def test_unknown_item_name_returns_none():
    assert find_by_basis("不存在的项", TIER, TEXT) == (None, None)


def test_basis_item_missing_window_marks_missing():
    """basis 项在无条款结构的文本里定位不到 → anchor_missing=True（不回落全文命中）。"""
    items = [{"item": "服务费"}, {"item": "违约金"}]
    enriched = resolve_anchors_for_items(items, "服务费 违约金 600", tier=TIER)
    for it in enriched:
        assert it["anchor_missing"] is True
        assert it["anchor_start"] is None


def test_window_end_is_clause_bound_not_document_end():
    """条款窗口必须止于「下一条标题」：目标词若只出现在**后续条款**里，绝不能越界命中。

    构造：违约金 basis 属第八条，但第八条窗口内没有「违约金」字样，只在第九条之后出现
    → 正确定位返回 (None, None)；越界到第九条即 FAIL。
    """
    text = (
        "第八条  退学退费\n"
        "（本节无该行）\n"
        "第九条  合同的变更、终止\n"
        "违约金 999 元\n"
    )
    assert find_by_basis("违约金", TIER, text) == (None, None)


# ── parse_item_basis 各形态 ───────────────────────────────────────────

@pytest.mark.parametrize("basis,clause_no,section,row_name", [
    ("第八条 退费表「基础服务（必扣项）·服务费」", "八", "基础服务（必扣项）", "服务费"),
    ("第八条 退费表「实操培训（依实项）·科目二实操」", "八", "实操培训（依实项）", "科目二实操"),
    ("第八条 备注（违约金为全部培训费用的 20%）", "八", "备注", None),
    ("第四条（三）1 代收代交工本费", "四", "（三）1", "代收代交工本费"),
    ("第四条（一）1（3）第一阶段 理论培训费用", "四", "（一）1（3）", "第一阶段 理论培训费用"),
    ("第三条", "三", None, None),
    ("第十一条（一）（违约金为总培训费用的 20%）", "十一", "（一）", "（违约金为总培训费用的 20%）"),
    ("第六条（三）已发生实操培训费学时单价", "六", "（三）", "已发生实操培训费学时单价"),
])
def test_parse_item_basis_forms(basis, clause_no, section, row_name):
    assert parse_item_basis(basis) == (clause_no, section, row_name)


def test_parse_item_basis_garbage():
    assert parse_item_basis("") == (None, None, None)
    assert parse_item_basis("没有条款号") == (None, None, None)
    assert parse_item_basis("第八条 退费表「基础服务（必扣项）·服务费」")[0] == "八"


# ── 向后兼容 ──────────────────────────────────────────────────────────

def test_empty_basis_text_falls_back_to_contract_text():
    """`basis_text=""`（如模板资产缺失）→ 回退到 contract_text，定位不被静默关闭。"""
    items = [{"item": "服务费"}]
    enriched = resolve_anchors_for_items(items, TEXT, tier=TIER, basis_text="")
    assert enriched[0]["anchor_missing"] is False
    assert enriched[0]["anchor_text"] == "服务费"


def test_empty_basis_text_without_clause_structure_marks_missing():
    """`basis_text=""` 且 contract_text 无条款结构 → anchor_missing=True 且不抛。"""
    items = [{"item": "服务费"}]
    enriched = resolve_anchors_for_items(items, "服务费600元", tier=TIER, basis_text="")
    assert enriched[0]["anchor_missing"] is True
    assert enriched[0]["anchor_start"] is None


def test_tier_none_keeps_hints_behaviour():
    items = [{"item": "服务费", "amount": 600}]
    enriched = resolve_anchors_for_items(items, "应收服务费600元", tier=None)
    assert enriched[0]["anchor_missing"] is False
    assert enriched[0]["anchor_phrase"] == "服务费"
    assert enriched[0]["anchor_text"] == "服务费"
    assert enriched[0]["amount"] == 600


def test_no_basis_item_still_uses_hints():
    """tier 已给但该项无 basis → 走 hints（如 2019·服务档没有「科目二实操培训费」条目）。"""
    tier = TIERS_BY_ID["2019_service"]
    items = [{"item": "科目二实操培训费"}]
    enriched = resolve_anchors_for_items(items, "科目二实操培训费：120 元/学时", tier=tier)
    assert enriched[0]["anchor_missing"] is False
    assert enriched[0]["anchor_text"] == "科目二实操培训费"


# ── basis 解析字段（v4.4 · S3b/S4）：逐档全覆盖 ─────────────────────────

ALL_TIER_IDS = [
    "2019_service",
    "2019_pay_agent",
    "2019_training",
    "2021_2022",
    "2023_branch_school",
    "2023_branch_store",
    "2019_dongcheng",
]

_CN_NUM_RE = re.compile(r"[一二三四五六七八九十百零〇]+")


@pytest.mark.parametrize("tier_id", ALL_TIER_IDS)
def test_basis_fields_match_parser_for_every_item(tier_id):
    """每档 item_basis 的每一项：basis_clause/section/row 严格 == parse_item_basis（None→""），
    且 basis_clause 恒为**非空中文数字**（与 template_clauses()[].no 同口径）。"""
    tier = TIERS_BY_ID[tier_id]
    assert tier["item_basis"], f"{tier_id} 无 item_basis"
    items = [{"item": name} for name in tier["item_basis"]]
    enriched = {it["item"]: it for it in resolve_anchors_for_items(items, template_text(tier), tier=tier)}
    for name, basis in tier["item_basis"].items():
        clause, section, row = parse_item_basis(basis)
        it = enriched[name]
        assert it["basis_clause"] == (clause or ""), f"{tier_id} {name}"
        assert it["basis_section"] == (section or ""), f"{tier_id} {name}"
        assert it["basis_row"] == (row or ""), f"{tier_id} {name}"
        assert _CN_NUM_RE.fullmatch(it["basis_clause"]), f"{tier_id} {name} clause={it['basis_clause']!r}"


@pytest.mark.parametrize("tier_id", ALL_TIER_IDS)
def test_refund_table_items_have_nonempty_basis_row(tier_id):
    """basis 走退费表「组·行」的项，basis_row 必须非空（备注类条目无行名，见 §1.3「无则 ''」）。"""
    tier = TIERS_BY_ID[tier_id]
    table_items = [n for n, b in tier["item_basis"].items() if "退费表" in b]
    if not table_items:
        pytest.skip(f"{tier_id} 无退费表 basis 项")
    items = [{"item": n} for n in table_items]
    enriched = {it["item"]: it for it in resolve_anchors_for_items(items, template_text(tier), tier=tier)}
    for name in table_items:
        assert enriched[name]["basis_row"] != "", f"{tier_id} {name} basis_row 为空"


def test_basis_fields_demo_example_2023():
    """§1.3 示例：服务费 → basis_clause='八' / basis_section='基础服务（必扣项）' / basis_row='服务费'。"""
    items = [{"item": "服务费"}]
    it = resolve_anchors_for_items(items, TEXT, tier=TIER)[0]
    assert it["basis_clause"] == "八"
    assert it["basis_section"] == "基础服务（必扣项）"
    assert it["basis_row"] == "服务费"


def test_no_basis_item_gets_empty_strings():
    """无 item_basis 映射的项 → basis_clause/section/row 三键**一律空串**（不留 None）。"""
    tier = TIERS_BY_ID["2019_service"]  # 该档无「科目二实操培训费」条目
    items = [{"item": "科目二实操培训费"}]
    it = resolve_anchors_for_items(items, "科目二实操培训费：120 元/学时", tier=tier)[0]
    assert it["basis_clause"] == ""
    assert it["basis_section"] == ""
    assert it["basis_row"] == ""


def test_tier_none_all_basis_fields_empty():
    """tier=None（无档位）→ 三个 basis 键均为空串。"""
    items = [{"item": "服务费"}]
    it = resolve_anchors_for_items(items, "应收服务费600元", tier=None)[0]
    assert (it["basis_clause"], it["basis_section"], it["basis_row"]) == ("", "", "")


def test_basis_fields_present_even_when_anchor_missing():
    """有 basis 映射但文本无条款结构 → anchor_missing=True，但 basis 三键仍是**已解析真值**。"""
    items = [{"item": "服务费"}]
    it = resolve_anchors_for_items(items, "服务费 违约金 600", tier=TIER)[0]
    assert it["anchor_missing"] is True
    assert it["basis_clause"] == "八"
    assert it["basis_section"] == "基础服务（必扣项）"
    assert it["basis_row"] == "服务费"
