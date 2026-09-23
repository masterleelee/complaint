"""合同原文对照弹窗 v4.4（S4）静态护栏。

权威：`demo/contract-preview-v4-demo.html`（v4.4）· 冻结契约
`.scratch/contract-preview-v44-landing/s4-contract.md`。

本护栏逐条把契约 §2（左栏渲染）/§3（CSS）/§4（前端 API）钉进源码，
并用「变异验证」证明每条断言真的会失败（见交付报告）。

⚠️ 断言查**语义**（选择器/取值/结构），不查示例字符串 —— 旧护栏
`'saveText.value' in html` 那种写法把雷区（模板对顶层 ref 写 .value）钉成了「正确」。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "templates" / "index.html"
JS = ROOT / "static" / "js" / "composables" / "useContractCompare.js"


@pytest.fixture(scope="module")
def html() -> str:
    return HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js() -> str:
    return JS.read_text(encoding="utf-8")


def _region(source: str, start: str, end: str) -> str:
    i = source.find(start)
    assert i != -1, f"未找到区间起点：{start!r}"
    j = source.find(end, i)
    assert j != -1, f"未找到区间终点：{end!r}"
    return source[i:j]


def _code(js: str) -> str:
    """剥离注释后的可执行代码（注释里出现被禁字符串不算违规）。"""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"//[^\n]*", "", js)


MODAL_START = "合同原文对照弹窗 v4.4"
MODAL_END = "登记表预览弹窗"


# ── §3 mark.cc-kw 不再常驻黄底（原型第 3 点）──────────────────────────────
def test_mark_cc_kw_is_transparent(html):
    light = re.search(r"mark\.cc-kw\s*\{([^}]*)\}", html)
    assert light, "index.html 缺 mark.cc-kw 规则"
    assert re.search(r"background\s*:\s*transparent", light.group(1)), (
        f"mark.cc-kw 必须透明（不再常驻黄底）：{light.group(1)!r}"
    )
    dark = re.search(r"html\.dark\s+mark\.cc-kw\s*\{([^}]*)\}", html)
    assert dark, "index.html 缺 html.dark mark.cc-kw 规则"
    assert re.search(r"background\s*:\s*transparent", dark.group(1)), (
        f"html.dark mark.cc-kw 也必须透明：{dark.group(1)!r}"
    )


# ── §2.2 汇总格：只有 .k 与 .v 两个子元素，无第三行小字、无 📍 ──────────────
def test_sum_cell_has_no_third_line_and_no_pin_icon(html):
    region = _region(html, 'class="cc-sum-cell"', 'class="cc-pane-body"')
    assert "📍" not in region, "汇总格里出现了 📍（原型 v4.2 第 2 点已去掉）"
    assert "bi-geo" not in region, "汇总格里出现了定位图标（原型 v4.2 第 2 点已去掉）"
    # 只有 .k 与 .v 两个 div 子元素（没有第三个小字行）
    cell_divs = re.findall(r'<div\s+class="', region)
    assert len(cell_divs) == 2, f"汇总格内应只有 .k/.v 两个 div，实际 {len(cell_divs)} 个：{region!r}"


# ── §2.2 扣费合计不得挂 dom（原型 v4.1 第 1/2 点）──────────────────────────
def test_deduction_total_cell_has_no_dom(js):
    hits = [ln for ln in js.splitlines() if "扣费合计" in ln and "k:" in ln]
    assert hits, "JS 里找不到「扣费合计」汇总格"
    for ln in hits:
        assert not re.search(r"\bdom\s*:", ln), f"「扣费合计」不得挂 dom：{ln.strip()!r}"


# ── §3 新增 CSS 类齐备 ────────────────────────────────────────────────────
@pytest.mark.parametrize("selector", [
    ".blk{", ".b-head{", ".b-sub{", ".b-para{", ".fill{", ".fill.hand{",
    ".tgt{", ".tgt.cc-hit{", ".grid-table{", ".raw{", "details{", "details>summary{",
])
def test_new_css_classes_present(html, selector):
    assert selector in html, f"index.html 缺 CSS 类：{selector}"


@pytest.mark.parametrize("selector", [
    "html.dark .b-head", "html.dark .b-sub", "html.dark .grid-table .c",
    "html.dark .grid-table .c.h", "html.dark .raw", "html.dark .fill",
    "html.dark .fill.hand", "html.dark .tgt.cc-hit",
    "html.dark .grid-table .r:hover .c", "html.dark .grid-table .r.cc-hit .c",
])
def test_dark_overrides_present(html, selector):
    assert selector in html, f"缺暗色覆盖（暗色下不可读）：{selector}"


# ── §3 退费表列宽契约（原型 v4.2 第 2 点）────────────────────────────────
def test_grid_table_column_budget(html):
    m = re.search(r"\.grid-table\s*\{([^}]*)\}", html)
    assert m, "缺 .grid-table 规则"
    body = m.group(1).replace(" ", "")
    assert "grid-template-columns:100pxminmax(0,.8fr)max-contentminmax(0,1.9fr)" in body, (
        f".grid-table 列宽不符合契约（100px / .8fr / max-content / 1.9fr）：{m.group(1)!r}"
    )


# ── §2.4 折叠区恰好 3 个 + 文案 ───────────────────────────────────────────
def test_details_exactly_three(html, js):
    """折叠区恰好 3 个 = 2 个数据驱动（JS `type:"details"`，由模板 v-for 渲染）
    + 1 个静态（index.html 的「查看 OCR 识别原文」）。模板里 <details> 标签共 2 个。"""
    modal = _region(html, MODAL_START, MODAL_END)
    n_html = modal.count("<details")
    n_js = js.count('type: "details"')
    assert n_html == 2, f"弹窗内 <details> 标签应为 2 个（1 数据驱动 + 1 静态 OCR），实际 {n_html}"
    assert n_js == 2, f"JS 内数据驱动 details 部件应为 2 个，实际 {n_js}"
    assert n_js + 1 == 3, "折叠区总数必须恰好 3 个（2 数据驱动 + 1 静态 OCR）"
    for text in ("代收代交考试费", "模板退费表的原始排版", "查看 OCR 识别原文"):
        assert text in (html + js), f"折叠区文案缺失：{text}"


# ── §4.1 禁止 scrollIntoView ──────────────────────────────────────────────
def test_no_scroll_into_view(js):
    assert "scrollIntoView" not in _code(js), (
        "useContractCompare.js 的**可执行代码**里出现了 scrollIntoView —— 契约 §4.1 明令禁止"
        "（display:contents 的行没有盒子，scrollIntoView 是空操作）。"
    )


# ── S3a 回归：不得回潮 Math.round(score*100)+"%" ─────────────────────────
def test_no_percent_score_regression(js):
    code = _code(js)
    assert not re.search(r'Math\.round\([^)]*\)\s*\+\s*"%"', code), (
        "出现 Math.round(...) + \"%\" 旧百分比写法（S3a 回归，会把特征分显示成 1000%）"
    )
    body = re.search(r"function _confLabel\([^)]*\)\s*\{(.*?)\n\}", code, re.S).group(1)
    assert "%" not in body, "_confLabel 里出现了「%」——档位置信度必须是人话标签（高/中/低）"


# ── importmap 版本号 bump（否则浏览器吃旧缓存）────────────────────────────
def test_importmap_bumped(html):
    assert 'useContractCompare.js?v=4"' in html, (
        "importmap 里 useContractCompare 未 bump 到 ?v=4 —— 浏览器会继续用旧缓存"
    )


# ── 模板：无重复 class 属性（原型 v4.1 第 4 点根因）────────────────────────
def test_no_duplicate_class_attribute_in_modal(html):
    modal = _region(html, MODAL_START, MODAL_END)
    for tag in re.findall(r"<[a-zA-Z][^>]*>", modal):
        assert len(re.findall(r"(?<![:\w])class\s*=", tag)) <= 1, (
            f"同一标签出现两次 class 属性（HTML 解析器会丢掉第二个）：{tag!r}"
        )
        assert len(re.findall(r"(?<![:\w])data-dom\s*=", tag)) <= 1, (
            f"同一标签出现两次 data-dom 属性：{tag!r}"
        )


# ── 左栏目标元素用 :ref registerTarget + :class isHit ─────────────────────
def test_target_elements_wired(html):
    modal = _region(html, MODAL_START, MODAL_END)
    assert "cmp.registerTarget(" in modal, "左栏目标元素未挂 :ref registerTarget"
    assert "cmp.isHit(" in modal, "目标元素未用 cmp.isHit 驱动 cc-hit"
    assert "cmp.hoverDom(" in modal and "cmp.pinDom(" in modal, "右栏未接 hoverDom/pinDom"


# ── 无 data-page-node-id 注入 ─────────────────────────────────────────────
def test_no_injection_attribute(html):
    assert "data-page-node-id" not in html, "index.html 残留 data-page-node-id 注入属性"


# ── 护栏与被护对象共存 ────────────────────────────────────────────────────
def test_guard_subject_exists(html, js):
    assert 'v-if="contractModalOpen"' in html
    assert "leftSource" in js and "clauseBlocks" in js and "sumCells" in js
    assert "apiLoading" in js and "apiError" in js, "异步加载/失败态未实现（弹窗可能空白）"
