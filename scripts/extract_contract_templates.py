"""把 `1合同种类/` 的合同模板正文抽成代码库内的只读资产（合同原文对照弹窗 v4.4 · S0）。

产物（写入 `data/contract_template_text/`，`data/` 未被 gitignore，随之入库）：

- `<tier_id>.txt`            该档模板全文（纯文本，段落一行）。
- `<tier_id>.refund.tsv`     退费表结构化还原（仅当该档模板含退费表时生成）。
                            TSV：每行 = 退费表的一行，单元格以单个 TAB 分隔；
                            空单元格为空串（行尾的 TAB 保留）。无退费表 → 不生成该文件。

用法（幂等、可重复执行）：

    env -u PYTHONPATH venv/bin/python3 scripts/extract_contract_templates.py

档位 id ↔ 源文件对应关系是本脚本的权威定义，与 `services/contract_tiers.py` 的
`CONTRACT_TIERS` 一致（2021/2022 两份正文仅格式差异，合并为一档 `2021_2022`，
**以 2022 版为准**）。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "1合同种类"
OUT = ROOT / "data" / "contract_template_text"

# 权威对应表：tier_id -> 相对 1合同种类/ 的源文件
TIER_SOURCES: dict[str, str] = {
    "2019_service": "2019年/1服务合同.pdf",
    "2019_pay_agent": "2019年/2代缴合同.pdf",
    "2019_training": "2019年/3培训合同.pdf",
    "2021_2022": "2022年/东莞市机动车驾驶员培训合同-20220513（定稿）.doc",
    "2023_branch_school": "2023年/东莞市机动车驾驶员培训合同-20231227（分校）.docx",
    "2023_branch_store": "2023年/东莞市机动车驾驶员培训合同-20231227（分店）.docx",
    "2019_dongcheng": "东城自制培训合同.docx",
}

# 判定「这是退费表」的特征串：整表拼接文本命中其一即认定。
_REFUND_TABLE_MARKERS = ("必扣项", "退费时所处阶段")

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


# ── 纯文本抽取 ────────────────────────────────────────────────────────

def pdf_text(path: Path) -> str:
    import pdfplumber

    chunks: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text() or "")
    return "\n".join(chunks)


def docx_text(path: Path) -> str:
    with zipfile.ZipFile(str(path)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    # 在每个段落起始插换行，便于逐段成行；用零宽前瞻，避免改坏裸 `<w:p>`（无属性）标签
    xml = re.sub(r"<w:p(?=[ >])", "\n<w:p", xml)
    return re.sub(r"<[^>]+>", "", xml)


def doc_text(path: Path) -> str:
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        subprocess.run(
            ["textutil", "-convert", "txt", "-output", str(tmp_path), str(path)],
            check=True, capture_output=True,
        )
        return tmp_path.read_text(encoding="utf-8", errors="replace")
    finally:
        tmp_path.unlink(missing_ok=True)


# ── 退费表抽取（保留真实行结构） ──────────────────────────────────────

def docx_tables(path: Path) -> list[list[list[str]]]:
    """解析 `.docx` 的 `word/document.xml`，返回每张表的「行 → 单元格文本」结构。"""
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(str(path)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    root = ET.fromstring(xml)
    tables: list[list[list[str]]] = []
    for tbl in root.iter(f"{{{_W_NS}}}tbl"):
        rows: list[list[str]] = []
        for tr in tbl.iter(f"{{{_W_NS}}}tr"):
            cells = [
                "".join(t.text or "" for t in tc.iter(f"{{{_W_NS}}}t")).strip()
                for tc in tr.iter(f"{{{_W_NS}}}tc")
            ]
            rows.append(cells)
        tables.append(rows)
    return tables


def doc_html_tables(path: Path) -> list[list[list[str]]]:
    """`.doc` 经 textutil 转 HTML 后解析 `<table>`，保留真实行结构。"""
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        subprocess.run(
            ["textutil", "-convert", "html", "-output", str(tmp_path), str(path)],
            check=True, capture_output=True,
        )
        html = tmp_path.read_text(encoding="utf-8", errors="replace")
    finally:
        tmp_path.unlink(missing_ok=True)

    tables: list[list[list[str]]] = []
    for tbl_m in re.finditer(r"<table\b[\s\S]*?</table>", html, re.I):
        rows: list[list[str]] = []
        for tr_m in re.finditer(r"<tr\b[\s\S]*?</tr>", tbl_m.group(0), re.I):
            cells = [
                re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip()
                for c in re.findall(r"<t[dh]\b[^>]*>([\s\S]*?)</t[dh]>", tr_m.group(0), re.I)
            ]
            rows.append(cells)
        tables.append(rows)
    return tables


def pdf_tables(path: Path) -> list[list[list[str]]]:
    import pdfplumber

    tables: list[list[list[str]]] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            for t in page.extract_tables():
                tables.append([[(c or "").replace("\n", " ").strip() for c in row] for row in t])
    return tables


def _pick_refund_table(tables: list[list[list[str]]]) -> list[list[str]] | None:
    """从候选表里挑出退费表：整表拼接文本命中 `_REFUND_TABLE_MARKERS`。"""
    for rows in tables:
        flat = " ".join(" ".join(cell for cell in row) for row in rows)
        if any(marker in flat for marker in _REFUND_TABLE_MARKERS):
            return rows
    return None


def _sanitize_cell(cell: str) -> str:
    return cell.replace("\t", " ").replace("\r", "").replace("\n", "").strip()


def _write_refund_tsv(rows: list[list[str]], dest: Path) -> None:
    lines = ["\t".join(_sanitize_cell(c) for c in row) for row in rows]
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── 主流程 ────────────────────────────────────────────────────────────

def compute_tier(src_rel: str) -> tuple[str, list[list[str]] | None]:
    """从源文件抽取 (模板正文, 退费表行列表或 None)。不写盘。"""
    src = SRC / src_rel
    if not src.exists():
        raise FileNotFoundError(f"源文件不存在：{src}")

    suffix = src.suffix.lower()
    if suffix == ".pdf":
        text, refund = pdf_text(src), _pick_refund_table(pdf_tables(src))
    elif suffix == ".docx":
        text, refund = docx_text(src), _pick_refund_table(docx_tables(src))
    elif suffix == ".doc":
        text, refund = doc_text(src), _pick_refund_table(doc_html_tables(src))
    else:
        raise ValueError(f"不支持的源文件类型：{suffix}")
    # 统一换行：避免读写时的 newline 归一化造成资产与再生结果一字之差
    return text.replace("\r\n", "\n").replace("\r", "\n"), refund


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="抽取合同模板正文为只读资产")
    parser.add_argument(
        "--check", action="store_true",
        help="只校验资产是否与源文件一致（不写盘，返回码非 0 表示有差异）",
    )
    args = parser.parse_args(argv)

    if not args.check:
        OUT.mkdir(parents=True, exist_ok=True)

    has_diff = False
    for tier_id, src_rel in TIER_SOURCES.items():
        text, refund = compute_tier(src_rel)
        txt_dest = OUT / f"{tier_id}.txt"
        tsv_dest = OUT / f"{tier_id}.refund.tsv"

        if args.check:
            cur_txt = txt_dest.read_text(encoding="utf-8") if txt_dest.exists() else None
            cur_tsv = tsv_dest.read_text(encoding="utf-8") if tsv_dest.exists() else None
            want_tsv = (
                "\n".join("\t".join(_sanitize_cell(c) for c in row) for row in refund) + "\n"
                if refund else None
            )
            if cur_txt != text or cur_tsv != want_tsv:
                has_diff = True
                print(f"DIFF {tier_id}", file=sys.stderr)
        else:
            txt_dest.write_text(text, encoding="utf-8")
            if refund:
                _write_refund_tsv(refund, tsv_dest)
            elif tsv_dest.exists():
                tsv_dest.unlink()  # 幂等：本档无退费表 → 清掉历史残留

        refund_note = f"退费表 {len(refund)} 行" if refund else "无退费表"
        print(f"OK  {tier_id:<20} <- {src_rel}  {len(text)} chars  {refund_note}")

    if args.check:
        print("CHECK: 一致" if not has_diff else "CHECK: 资产与源文件不一致（有差异）",
              file=sys.stderr)
        return 1 if has_diff else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
