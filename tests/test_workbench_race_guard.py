"""工作台「快速打开工单」渲染竞态护栏（BUG-05）。

## 为什么需要它

`templates/index.html` 工作台视图：`<div v-if="!selectedTicketId">`（空态+快速打开下拉）
与 `<div v-else>`（工单详情）同级。`selectedTicketId` 由下拉 `@change="openCase(id)"`
经 v-model 立即置位，但 `selectedTicket` 要等异步 `openCase(id)` → `await openTicket`
返回后才赋值。中间存在一个渲染窗口期：Vue 先渲染 v-else 分支，而 `selectedTicket`
仍是 null → `TypeError: Cannot read properties of null (reading 'student_name')`
（控制台实测捕获，页面靠后续响应自愈，但属真实渲染错误）。

修复：把 `<div v-else>` 改成 `<div v-else-if="selectedTicket">`，在 `selectedTicket`
尚未就绪时回退到空态，消除竞态窗口。

## 断言

1. index.html 工作台分支已加守卫 `v-else-if="selectedTicket"`；
2. index.html 中不再存在 `<div v-else>` 紧邻 `class="case-head"` 的相邻结构
   （即裸 `v-else>` + `case-head` 的竞态分支已消除）；
3. 系统中「合同视觉」文案已删除（视觉 OCR 本迭代移除，文案已更正为「合同文本识别」）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = ROOT / "templates" / "index.html"


@pytest.fixture(scope="module")
def index_source() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def test_workbench_branch_has_guard(index_source):
    """护栏共存断言：工作台详情分支必须已加 selectedTicket 守卫。"""
    assert 'v-else-if="selectedTicket"' in index_source, (
        "工作台工单详情分支未加 `v-else-if=\"selectedTicket\"` 守卫？"
        "BUG-05 竞态窗口仍在：selectedTicketId 置位而 selectedTicket 为 null 时"
        "会渲染 v-else 分支并抛 TypeError（reading 'student_name'）。"
    )


def test_no_bare_v_else_case_head_race(index_source):
    """核心断言：`<div v-else>` 紧邻 `class="case-head"` 的竞态分支不得存在。"""
    assert '<div v-else>\n          <div class="case-head">' not in index_source, (
        "仍存在 `<div v-else>` 紧邻 `class=\"case-head\"` 的相邻结构："
        "selectedTicket 为 null 时该分支即抛 TypeError。应改为 "
        "`v-else-if=\"selectedTicket\"`。"
    )


def test_contract_visual_copy_removed(index_source):
    """P3 文案：视觉 OCR 已删除，『合同视觉』文案不得残留。"""
    assert "合同视觉" not in index_source, (
        "index.html 仍残留『合同视觉』文案？视觉 OCR 已在本迭代删除，"
        "应更正为『合同文本识别』。"
    )
