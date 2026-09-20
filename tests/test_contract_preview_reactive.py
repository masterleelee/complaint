"""合同三栏预览（cp 面板）必须以 Vue.reactive 包裹 —— 白屏护栏。

## 为什么需要它

`templates/index.html` 的「合同三栏预览」面板（`v-if="ar && ar.deductions_result"`，
上传合同分析成功后渲染）大量使用 `cp.groupedDeductions.find(...)`、`cp.summary.totalFee`
这类「普通对象内嵌 computed」的表达式。

Vue 3 模板**只解包**两类 ref/computed：
  1. setup 返回值顶层的 ref/computed；
  2. `reactive()` 对象内部嵌的 ref/computed。

`useContractPreview()` 返回的是**普通对象**（内含一批 computed），如果直接暴露给模板，
`cp.groupedDeductions` 在模板里拿到的是 **ComputedRef 本体**而非数组，`.find(...)` 必然
抛 `TypeError: cp.groupedDeductions.find is not a function`。

后果（2026-09-20 尹金辉工单实锤复现两次）：TypeError 在根组件渲染函数内抛出 →
整棵渲染树崩溃 → `#app` 被清成单个注释节点 → **整页白屏**；错误只留一条
console.error，`window.onerror` 不触发，用户侧表现为「分析十几秒后页面全白」，
刷新后 cp 面板因 `deductions_result` 未随工单落库而不再渲染 → 现场消失，极难排查。

## 断言

1. app.js 中每处 `useContractPreview(` 实例化都必须包在 `Vue.reactive(...)` 内；
2. index.html 的 cp 面板确实存在（护栏与被护对象共存，模板改名时同步更新本测试）；
3. setup 返回里 `contractText` 别名不得直接取 `cp.contractText` 的**即时值**
   （cp 已 reactive，直接取值会变成静态快照，必须走 computed）。
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


def test_cp_panel_template_exists(index_source):
    """护栏共存断言：cp 面板模板不在了 → 本护栏对象已消失，应同步删除/更新。"""
    assert 'v-if="ar && ar.deductions_result"' in index_source, (
        "合同三栏预览面板（cp-panel）已从 index.html 移除？请同步更新本护栏"
    )
    assert "cp.groupedDeductions.find(" in index_source, (
        "cp.groupedDeductions.find( 表达式已不在模板中？请同步更新本护栏"
    )


def test_use_contract_preview_must_be_reactive_wrapped(app_source):
    """核心断言：每处 `useContractPreview(` 都必须包在 `Vue.reactive(` 里。"""
    instantiation = re.finditer(r"(?<![\w.])useContractPreview\s*\(", app_source)
    hits = list(instantiation)
    assert hits, "app.js 已不再实例化 useContractPreview？请同步删除本护栏"
    for m in hits:
        prefix = app_source[max(0, m.start() - 40): m.start()]
        assert "Vue.reactive(" in prefix, (
            "useContractPreview(...) 返回普通对象（内含 computed），未经 Vue.reactive "
            "包裹直接暴露给模板时，cp.groupedDeductions 等 ref 不解包 → "
            "模板 .find(...) 抛 TypeError → 整页白屏（2026-09-20 实锤）。"
            f"实例化点上下文：…{prefix[-30:]!r}"
        )


def test_contract_text_alias_must_not_snapshot(app_source):
    """cp 已 reactive：`contractText: cp.contractText` 是**静态快照**（setup 时的空串），
    必须保持响应式（computed / toRef）。"""
    assert not re.search(r"contractText\s*:\s*cp\.contractText\s*,", app_source), (
        "contractText 别名直接取了 cp.contractText 的即时值（reactive 对象取值即解包 → "
        "静态快照），请改为 Vue.computed(() => cp.contractText)"
    )
