"""合同模板正文只读访问（合同原文对照弹窗 v4.4 · S0）。

资产目录：`data/contract_template_text/`，由 `scripts/extract_contract_templates.py`
从 `1合同种类/` 的源文件再生（源文件在 git 里，资产可一键重生，随库入库）。

文件格式（模块 docstring 即格式契约）：

- `<tier_id>.txt`         档位模板全文（纯文本，段落一行，与源文件的段落
                          一一对应；.docx 表格展开为「单元格一行」）。
- `<tier_id>.refund.tsv`  退费表结构化还原（**可选**，仅含退费表的档位才有）。
                          TSV：每行 = 退费表的一行，单元格以单个 TAB 分隔；
                          空单元格为空串（行尾 TAB 保留，故列数可自检）。
                          「无退费表」用**文件不存在**表达（例如东城自制档）。

本模块为**只读访问**：纯函数 + 模块级缓存；不 import `config` / `database` / `app`，
无副作用。未知档位一律安静返回空值（`""` / `[]`），不抛异常。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# 资产目录（相对仓库根；本文件位于 services/ 下）。
_ASSET_DIR = Path(__file__).resolve().parent.parent / "data" / "contract_template_text"

_TIERS_WITHOUT_TEXT = frozenset()  # 占位：当前全档位均有正文

# 缓存：tier_id -> 正文 / 退费行列表
_TEXT_CACHE: dict[str, str] = {}
_REFUND_CACHE: dict[str, list[list[str]] | None] = {}

# 条款标题：行首（容忍行首缩进与「第 八 条」这类空格排版）的「第X条」
_CLAUSE_TITLE_RE = re.compile(
    r"(?m)^[ \t\u3000]*(?P<title>第\s*(?P<num>[一二三四五六七八九十百零〇]+)\s*条)"
)

_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


# ── 档位标识解析 ──────────────────────────────────────────────────────

def _tier_id(tier: Any) -> str:
    """接受 tier_id 字符串或 tier dict；其余一律视为未知档位（""）。"""
    if isinstance(tier, str):
        return tier.strip()
    if isinstance(tier, dict):
        return str(tier.get("id") or "").strip()
    return ""


# ── 正文 ──────────────────────────────────────────────────────────────

def _load_text(tier_id: str) -> str:
    if not tier_id or tier_id in _TIERS_WITHOUT_TEXT:
        return ""
    if tier_id not in _TEXT_CACHE:
        path = _ASSET_DIR / f"{tier_id}.txt"
        _TEXT_CACHE[tier_id] = path.read_text(encoding="utf-8") if path.is_file() else ""
    return _TEXT_CACHE[tier_id]


def template_text(tier: Any) -> str:
    """返回该档模板全文；未知档位返回 ""。"""
    return _load_text(_tier_id(tier))


# ── 条款定位 ──────────────────────────────────────────────────────────

def _cn_to_int(text: str) -> int | None:
    s = (text or "").strip()
    if not s:
        return None
    if "十" in s:
        head, _, tail = s.partition("十")
        if head and head not in _CN_DIGITS:
            return None
        if tail and tail not in _CN_DIGITS:
            return None
        tens = _CN_DIGITS[head] if head else 1
        ones = _CN_DIGITS[tail] if tail else 0
        return tens * 10 + ones
    if s in _CN_DIGITS:
        return _CN_DIGITS[s]
    return None


def _clause_no_int(no: Any) -> int | None:
    """把 "第四" / "四条" / "第四条" / "4" 归一为整数条款序号；无法识别返回 None。"""
    s = str(no).strip().replace(" ", "").replace("\u3000", "")
    if s.startswith("第"):
        s = s[1:]
    if s.endswith("条"):
        s = s[:-1]
    if not s:
        return None
    if s.isdigit():
        return int(s)
    return _cn_to_int(s)


def find_clause_window(text: str | None, no: Any) -> tuple[int, int] | None:
    """在任意文本里切出条款窗口 [start, end)：标题起 → 下一条标题起（末条到文末）。

    `no` 容忍 "第四" / "四条" / "第四条" / "4" 及「第 四 条」排版。切不出返回 None。
    """
    if not text:
        return None
    target = _clause_no_int(no)
    if target is None:
        return None
    matches = list(_CLAUSE_TITLE_RE.finditer(text))
    for i, m in enumerate(matches):
        if _cn_to_int(m.group("num")) == target:
            start = m.start("title")
            end = matches[i + 1].start("title") if i + 1 < len(matches) else len(text)
            return start, end
    return None


def clause(tier: Any, no: Any) -> str:
    """返回该档模板中指定条款正文（含标题行），到下一条标题行为止；找不到返回 ""。"""
    text = template_text(tier)
    window = find_clause_window(text, no)
    if window is None:
        return ""
    return text[window[0]:window[1]]


def template_clauses(tier: Any) -> list[dict]:
    """档位模板切成条款块：`[{"no": "四", "title": "费用及支付", "body": "（一）…"}]`。

    - `no`    = 中文数字（不带「第」「条」，与 `build_contract_clauses` 同口径）
    - `title` = 标题行「第X条」之后的剩余文本（strip；与 `build_contract_clauses` 同切法）
    - `body`  = 标题行换行之后 → 下一条标题行之前（strip）
    - **无前导块**（不像 `build_contract_clauses` 那样产出 `no=""` 的 preamble 条目）
    - 未知档位 / 无资产 → `[]`（保持本模块「只读、安静返回空、不抛异常」的风格）
    """
    text = template_text(tier)
    if not text:
        return []

    matches = list(_CLAUSE_TITLE_RE.finditer(text))
    if not matches:
        return []

    clauses: list[dict] = []
    for i, match in enumerate(matches):
        start = match.end("title")  # 「第X条」之后
        end = matches[i + 1].start("title") if i + 1 < len(matches) else len(text)
        segment = text[start:end]
        if "\n" in segment:
            title, body = segment.split("\n", 1)
            title, body = title.strip(), body.strip()
        else:
            title, body = "", segment.strip()
        clauses.append({"no": match.group("num"), "title": title, "body": body})
    return clauses



# ── 退费表 ────────────────────────────────────────────────────────────

def _load_refund_rows(tier_id: str) -> list[list[str]] | None:
    if not tier_id:
        return None
    if tier_id not in _REFUND_CACHE:
        path = _ASSET_DIR / f"{tier_id}.refund.tsv"
        if path.is_file():
            rows: list[list[str]] = []
            for line in path.read_text(encoding="utf-8").splitlines():
                rows.append(line.split("\t"))
            _REFUND_CACHE[tier_id] = rows
        else:
            _REFUND_CACHE[tier_id] = None
    return _REFUND_CACHE[tier_id]


def refund_rows(tier: Any) -> list[list[str]]:
    """退费表还原为「行列表」，每行为该行的单元格文本列表。

    返回到退费表（如东城自制档）或未知档位 → `[]`。返回深拷贝，调用方可安全修改。
    """
    rows = _load_refund_rows(_tier_id(tier))
    if not rows:
        return []
    return [list(row) for row in rows]
