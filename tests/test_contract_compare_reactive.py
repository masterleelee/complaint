"""合同原文对照弹窗（cmp / useContractCompare）必须以 Vue.reactive 包裹 —— 白屏护栏。

## 为什么需要它

`templates/index.html` 的「合同原文对照弹窗 v2」（`v-if="contractModalOpen"`，2026-09-21
替换旧对照弹窗与三栏预览）大量使用 `cmp.summaryCells`、`cmp.dedRows`、`cmp.hoverRow(...)`
这类「普通对象内嵌 computed/函数」的表达式。

Vue 3 模板**只解包**两类 ref/computed：
  1. setup 返回值顶层的 ref/computed；
  2. `reactive()` 对象内部嵌的 ref/computed。

`useContractCompare()` 返回的是**普通对象**（内含一批 computed），如果直接暴露给模板，
`cmp.dedRows` 在模板里拿到的是 ComputedRef 本体而非数组，`v-for` 必然渲染异常或抛
TypeError → 根渲染函数崩溃 → 整页白屏（2026-09-20 三栏预览 cp.groupedDeductions 实锤，
同一教训在新弹窗上原样适用）。

## 断言

1. app.js 中每处 `useContractCompare(` 实例化都必须包在 `Vue.reactive(...)` 内；
2. index.html 的新弹窗模板确实存在（护栏与被护对象共存，改名时同步更新本测试）；
3. 旧三栏预览（cp / useContractPreview / cp-panel）不得回潮。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "static" / "js" / "app.js"
INDEX_HTML = ROOT / "templates" / "index.html"


@pytest.fixture(scope="module")
def app_source() -> str:
    return APP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_source() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def test_compare_modal_template_exists(index_source):
    """护栏共存断言：新弹窗模板不在了 → 本护栏对象已消失，应同步删除/更新。"""
    assert 'v-if="contractModalOpen"' in index_source, (
        "合同原文对照弹窗（contractModalOpen）已从 index.html 移除？请同步更新本护栏"
    )
    assert "cmp.dedRows" in index_source, (
        "cmp.dedRows 表达式已不在模板中？请同步更新本护栏"
    )
    assert "cc-pane" in index_source, (
        "cc-* 样式/结构已不在模板中？请同步更新本护栏"
    )


def test_use_contract_compare_must_be_reactive_wrapped(app_source):
    """核心断言：每处 `useContractCompare(` 都必须包在 `Vue.reactive(` 里。"""
    instantiation = re.finditer(r"(?<![\w.])useContractCompare\s*\(", app_source)
    hits = list(instantiation)
    assert hits, "app.js 已不再实例化 useContractCompare？请同步删除本护栏"
    for m in hits:
        prefix = app_source[max(0, m.start() - 40): m.start()]
        assert "Vue.reactive(" in prefix, (
            "useContractCompare(...) 返回普通对象（内含 computed），未经 Vue.reactive "
            "包裹直接暴露给模板时，cmp.dedRows 等 ref 不解包 → 模板 v-for 抛 "
            "TypeError → 整页白屏（2026-09-20 三栏预览实锤，同一教训）。"
            f"实例化点上下文：…{prefix[-30:]!r}"
        )


def _css_rule(index_source: str, selector: str) -> str:
    """取 index.html 内联 <style> 里某条规则的声明体（选择器按字面匹配，取第一条）。"""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", index_source)
    return m.group(1) if m else ""


def test_contract_body_must_not_override_grid_display(index_source):
    """`.m-body.contract-body` 不得声明 display —— 特异性(0,2,0) 高于 `.cc-body`(0,1,0)。

    2026-09-22 实测（真实浏览器 1333×811）：
      `.modal-content .m-body.contract-body{...;display:flex;...}` 把 `.cc-body` 的
      `grid-template-columns:1fr 400px` 整个架空 → 两栏退化成 flex:0 1 auto：
        · 原文 tab：右栏 263px（设计 400）
        · 原图 tab：左栏被 iframe 撑到 1178px，右栏被压到 **100px**
          —— 汇总格 25px 宽、内容溢出 2.5 倍（金额重叠）、footer 文字竖排、
          `.cc-body` scrollWidth 1317 > clientWidth 1280（右栏右侧被 overflow:hidden 裁掉）
    去掉这几个字后同一次量测：body 回到 grid 878.672px/400px、右栏 400px、无横向溢出。
    """
    body_rule = _css_rule(index_source, ".modal-content .m-body.contract-body")
    assert body_rule, (
        "index.html 已不存在 `.modal-content .m-body.contract-body` 规则？请同步更新本护栏"
    )
    assert "display" not in body_rule, (
        "`.m-body.contract-body` 里出现了 display 声明 —— 它会覆盖 `.cc-body` 的 "
        f"grid 行列模板，右栏会被压扁（实测 100px）。当前规则体：{body_rule!r}"
    )


def test_cc_body_keeps_grid_two_column_budget(index_source):
    """`.cc-body` 必须是 grid 且右栏 400px；右栏另有 width 兜底，防止再被压扁。"""
    cc_body = _css_rule(index_source, ".cc-body")
    assert "display:grid" in cc_body.replace(" ", ""), (
        f"`.cc-body` 不再是 grid 两栏：{cc_body!r}"
    )
    assert re.search(r"grid-template-columns\s*:\s*minmax\(0,\s*1fr\)\s+400px", cc_body), (
        f"`.cc-body` 右栏预算不是 400px（左栏须 minmax(0,1fr) 才能被压缩）：{cc_body!r}"
    )
    right = _css_rule(index_source, ".cc-pane.cc-right")
    assert "width:400px" in right.replace(" ", ""), (
        f"右栏缺少 width:400px 兜底：{right!r}"
    )


def test_legacy_three_column_preview_must_not_return(app_source, index_source):
    """旧三栏预览不得回潮：useContractPreview / cp 面板 / cp-panel 标记应保持清零。"""
    assert "useContractPreview" not in app_source, (
        "app.js 重新引入了 useContractPreview？三栏预览已于 2026-09-21 按评审移除"
    )
    assert "cp-panel" not in index_source, (
        "index.html 重新出现了 cp-panel？三栏预览已于 2026-09-21 按评审移除"
    )
    assert "useContractPreview" not in index_source, (
        "index.html importmap 重新登记了 useContractPreview？"
    )
