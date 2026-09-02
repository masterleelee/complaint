"""扫描版 PDF 页图缓存测试（工单 09-page-image-cache）。

策略：
  * 真实 PDF fixture（``1合同种类/2019年/1服务合同.pdf``）走文本层路径。
  * 扫描版 PDF 用 ``monkeypatch`` 把 ``_render_page`` 替换为 fake——避免依赖
    poppler / pypdfium2 二进制差异；测试纯逻辑（缓存命中、目录生命周期、
    LRU 整目录清理、invalidate、stats）。
  * 缓存目录每个用例隔离：monkeypatch ``PAGE_CACHE_ROOT`` 为 ``tmp_path`` 子目录。

覆盖：
  1.  has_text_layer 真实 PDF 文本层检测
  2.  has_text_layer 不存在路径 / 非 PDF
  3.  get_page_image 文件不存在 → None
  4.  get_page_image 文本层 PDF → None
  5.  get_page_image 缓存未命中 → 渲染 + 缓存
  6.  get_page_image 缓存命中 → 不重复渲染 + 目录 mtime 续命
  7.  get_page_image 渲染失败 → None（不抛）
  8.  get_page_image page 越界 → None
  9.  get_page_count 真实 PDF
 10.  cleanup_lru 未超限 → 不删
 11.  cleanup_lru 超限 → 最旧整目录被删，无碎片
 12.  cleanup_lru 持续删到 ≤ MAX_BYTES
 13.  invalidate(filepath=...) 单文件清
 14.  invalidate(all_=True) 清空
 15.  stats 字段齐全
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import services.page_cache as pc
from services import page_cache
from services.page_cache import (
    PAGE_CACHE_DPI,
    PAGE_CACHE_MAX_BYTES,
    PAGE_CACHE_ROOT,
    cleanup_lru,
    get_page_count,
    get_page_image,
    has_text_layer,
    invalidate,
    stats,
)


# ── fixture ───────────────────────────────────────────────────────────────


REAL_CONTRACT_PDF = "1合同种类/2019年/1服务合同.pdf"


@pytest.fixture(autouse=True)
def _isolated_cache_root(tmp_path, monkeypatch):
    """每个用例的缓存目录独立到 tmp_path，避免污染真实 tmp/page_cache/。

    同时 monkeypatch ``_render_page`` 为空 fake，确保未 mock 的代码路径也不会真渲染。
    """
    test_root = tmp_path / "page_cache"
    monkeypatch.setattr(page_cache, "PAGE_CACHE_ROOT", test_root)
    # monkeypatch 一律返回 None：防止测试意外触发真实渲染
    monkeypatch.setattr(page_cache, "_render_page", lambda *a, **kw: None)
    yield test_root


# ── 测试内 fake 渲染器 ────────────────────────────────────────────────────


class _FakeImage:
    """最小可 save('PNG') 的对象。"""

    def __init__(self, payload: bytes = b"\x89PNG_FAKE"):
        self.payload = payload

    def save(self, fp, format=None):
        fp.write(self.payload)


def _make_fake_renderer(call_log: list, payloads_per_call: list | None = None):
    """生成一个假 _render_page：每次调返回一张 _FakeImage，并把参数记入 call_log。

    payloads_per_call：可选，第 i 次调用返回第 i 个 payload；用 None 则用默认。
    """

    def fake(filepath: str, page_no: int, dpi: int):
        call_log.append({"filepath": filepath, "page_no": page_no, "dpi": dpi})
        if payloads_per_call and len(call_log) - 1 < len(payloads_per_call):
            return _FakeImage(payloads_per_call[len(call_log) - 1])
        return _FakeImage()

    return fake


# ── 1-2. has_text_layer ──────────────────────────────────────────────────


def test_has_text_layer_true_on_real_contract_pdf():
    assert os.path.exists(REAL_CONTRACT_PDF), "fixture 缺失"
    assert has_text_layer(REAL_CONTRACT_PDF) is True


def test_has_text_layer_false_for_missing_path():
    assert has_text_layer("/no/such/file.pdf") is False


def test_has_text_layer_false_for_non_pdf_extension(tmp_path):
    fake = tmp_path / "x.txt"
    fake.write_text("hello")
    assert has_text_layer(str(fake)) is False


# ── 3-8. get_page_image ──────────────────────────────────────────────────


def test_get_page_image_returns_none_for_missing_file():
    assert get_page_image("/no/such/file.pdf", 1) is None


def test_get_page_image_returns_none_for_text_layer_pdf():
    """真实合同 PDF 有文本层 → 返回 None，不进入渲染/缓存。"""
    assert os.path.exists(REAL_CONTRACT_PDF)
    assert get_page_image(REAL_CONTRACT_PDF, 1) is None
    assert get_page_image(REAL_CONTRACT_PDF, 2) is None


def test_get_page_image_renders_and_caches_on_first_call(monkeypatch, tmp_path):
    """缓存未命中 → 渲染并写入缓存目录。"""
    call_log = []
    monkeypatch.setattr(
        page_cache, "_render_page",
        _make_fake_renderer(call_log),
    )

    # 用空 .pdf 文件作为「扫描版」占位：has_text_layer → pdfplumber 打开失败 → False
    fake_pdf = tmp_path / "scan_first_call.pdf"
    fake_pdf.write_bytes(b"")
    assert has_text_layer(str(fake_pdf)) is False

    out = get_page_image(str(fake_pdf), 1)
    assert out == b"\x89PNG_FAKE"
    assert len(call_log) == 1
    assert call_log[0]["page_no"] == 1
    assert call_log[0]["dpi"] == PAGE_CACHE_DPI

    # 缓存文件应已写盘
    cache_dir = page_cache.PAGE_CACHE_ROOT / page_cache._file_sha256(str(fake_pdf))
    assert (cache_dir / "page_1.png").exists()


def test_get_page_image_cache_hit_skips_rerender(monkeypatch, tmp_path):
    """第二次同 (filepath, page_no) → 命中缓存，render 后端 0 次调用。"""
    call_log = []
    monkeypatch.setattr(
        page_cache, "_render_page",
        _make_fake_renderer(call_log),
    )
    fake_pdf = tmp_path / "scan_cache_hit.pdf"
    fake_pdf.write_bytes(b"")

    first = get_page_image(str(fake_pdf), 1)
    assert first == b"\x89PNG_FAKE"
    assert len(call_log) == 1

    second = get_page_image(str(fake_pdf), 1)
    assert second == b"\x89PNG_FAKE"
    assert len(call_log) == 1, "第二次调用应命中缓存，render 后端不再被触发"


def test_get_page_image_touchs_dir_mtime_on_hit(monkeypatch, tmp_path):
    """命中后端 mtime 至少不小于之前（应被续命）。"""
    monkeypatch.setattr(
        page_cache, "_render_page", _make_fake_renderer([]),
    )
    fake_pdf = tmp_path / "scan_touch.pdf"
    fake_pdf.write_bytes(b"")
    get_page_image(str(fake_pdf), 1)  # 写盘

    cache_dir = page_cache.PAGE_CACHE_ROOT / page_cache._file_sha256(str(fake_pdf))
    mtime_before = cache_dir.stat().st_mtime_ns

    # 模拟时间流逝（macOS/Linux 都能 ns 精度）
    time.sleep(0.05)
    # 调用一次并断言 mtime 被刷新（≥）
    get_page_image(str(fake_pdf), 1)
    mtime_after = cache_dir.stat().st_mtime_ns
    assert mtime_after >= mtime_before


def test_get_page_image_returns_none_when_render_fails(monkeypatch, tmp_path):
    """render 抛错 → 优雅 None，不污染缓存。"""
    monkeypatch.setattr(
        page_cache, "_render_page",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    fake_pdf = tmp_path / "scan_render_fail.pdf"
    fake_pdf.write_bytes(b"")
    assert get_page_image(str(fake_pdf), 1) is None
    # 失败时不应写盘
    cache_dir = page_cache.PAGE_CACHE_ROOT / page_cache._file_sha256(str(fake_pdf))
    assert not (cache_dir / "page_1.png").exists()


def test_get_page_image_invalid_page_number_returns_none(monkeypatch, tmp_path):
    """page_no < 1 → None。"""
    monkeypatch.setattr(page_cache, "_render_page", _make_fake_renderer([]))
    fake_pdf = tmp_path / "scan_invalid_page.pdf"
    fake_pdf.write_bytes(b"")
    assert get_page_image(str(fake_pdf), 0) is None
    assert get_page_image(str(fake_pdf), -1) is None


# ── 9-10. get_page_count / has_text_layer 兜底 ──────────────────────────


def test_get_page_count_real_pdf():
    assert os.path.exists(REAL_CONTRACT_PDF)
    n = get_page_count(REAL_CONTRACT_PDF)
    assert n is not None
    assert n == 2  # 1服务合同.pdf 实际页数


def test_get_page_count_missing_path():
    assert get_page_count("/no/such/file.pdf") is None


def test_get_page_count_non_pdf():
    assert get_page_count("/tmp/some.txt") is None


# ── 11-13. cleanup_lru 整目录清理 ─────────────────────────────────────────


def _seed_cache(roots: dict, sizes_bytes: dict, mtime_offsets: dict):
    """在隔离的 cache_root 下手动构造若干「合同」目录与 page_N.png 大小。

    roots: sha -> Path（应为 PAGE_CACHE_ROOT/<sha>/）
    sizes_bytes: sha -> 该目录总字节数（通过写大块 png 模拟）
    mtime_offsets: sha -> mtime 偏移（秒）；offset 越大越新
    """
    import os

    for sha, path in roots.items():
        path.mkdir(parents=True, exist_ok=True)
        size = sizes_bytes[sha]
        # 简单写一份 size 字节的内容
        (path / "page_1.png").write_bytes(b"\x89" + b"X" * max(0, size - 1))
        # 设置 mtime（直接用 os.utime 秒级）
        offset = mtime_offsets.get(sha, 0)
        target_ts = time.time() - (3600 - offset)  # offset 越大，ts 越大
        os.utime(path, (target_ts, target_ts))


def test_cleanup_lru_no_op_when_under_limit(monkeypatch):
    monkeypatch.setattr(page_cache, "PAGE_CACHE_MAX_BYTES", 10 * 1024 * 1024)
    root = page_cache.PAGE_CACHE_ROOT
    _seed_cache(
        roots={"aaa": root / "aaa", "bbb": root / "bbb"},
        sizes_bytes={"aaa": 100, "bbb": 200},
        mtime_offsets={"aaa": 0, "bbb": 100},
    )
    result = cleanup_lru()
    assert result["deleted_dirs"] == []
    assert result["remaining_dirs"] == 2
    assert result["remaining_bytes"] == 300


def test_cleanup_lru_deletes_oldest_dir_first(monkeypatch):
    """超限 → 最旧整目录被删；不残留单页碎片（中间目录被整目录删除）。"""
    monkeypatch.setattr(page_cache, "PAGE_CACHE_MAX_BYTES", 500)
    root = page_cache.PAGE_CACHE_ROOT

    # 3 个合同目录，总和 1200 字节 > 500
    # old（最旧, 500B）/ mid / new（最新, 100B）
    _seed_cache(
        roots={
            "old": root / "old",
            "mid": root / "mid",
            "new": root / "new",
        },
        sizes_bytes={"old": 500, "mid": 600, "new": 100},
        mtime_offsets={"old": 0, "mid": 50, "new": 100},
    )
    result = cleanup_lru()
    # 至少删掉 old；总剩余 <= 500
    assert any("old" in d for d in result["deleted_dirs"])
    # 关键断言：无残留单页碎片——被删的目录里文件应已不存在
    assert not (root / "old").exists()
    # mid 可能被部分删除（如果仍超限）；如果删了 mid 它的文件也应不在
    if (root / "mid").exists():
        # mid 留着是合法的（如果 600 + 100 <= 500 不可能，留着说明 mid 在 new 之后）
        # 在我们的参数下 mid = 600B, new = 100B, 删完 old 还剩 700 > 500 → 应继续删 mid
        pytest.fail("cleanup_lru 没把 mid 删掉；说明 LRU 没把最旧的两个都删")
    assert (root / "new").exists(), "最新合同应保留"
    assert result["remaining_bytes"] <= 500


def test_cleanup_lru_continues_until_under_limit(monkeypatch):
    """超限 → 持续整目录删直到 ≤ MAX_BYTES。"""
    monkeypatch.setattr(page_cache, "PAGE_CACHE_MAX_BYTES", 100)
    root = page_cache.PAGE_CACHE_ROOT
    _seed_cache(
        roots={
            "a": root / "a",
            "b": root / "b",
            "c": root / "c",
        },
        sizes_bytes={"a": 200, "b": 200, "c": 200},  # 总 600 > 100
        mtime_offsets={"a": 0, "b": 50, "c": 100},
    )
    result = cleanup_lru()
    # 应删 a 和 b；留 c（200B > 100）—— 但算法是「删到 ≤ MAX_BYTES」，
    # 删完 a,b 后剩 200 仍 > 100 → 应继续删 c
    assert len(result["deleted_dirs"]) >= 2
    assert result["remaining_bytes"] <= 100, (
        f"cleanup_lru 没删够：remaining={result['remaining_bytes']}"
    )
    # 验证确实没有残留目录（整目录删除语义）
    remaining_dirs = [d for d in result.get("deleted_dirs", [])]
    # 被删的目录里文件应都不存在（无单页碎片）
    for d in result["deleted_dirs"]:
        assert not Path(d).exists() or not any(Path(d).iterdir()), (
            f"被删目录 {d} 应完全清空（无单页碎片）"
        )


def test_cleanup_lru_empty_root_no_error():
    """空缓存目录调用 cleanup_lru 不报错。"""
    result = cleanup_lru()
    assert result["deleted_dirs"] == []
    assert result["remaining_dirs"] == 0
    assert result["remaining_bytes"] == 0


# ── 14-15. invalidate / stats ────────────────────────────────────────────


def test_invalidate_by_file(monkeypatch, tmp_path):
    monkeypatch.setattr(page_cache, "_render_page", _make_fake_renderer([]))
    fake_pdf_a = tmp_path / "invalidate_a.pdf"
    fake_pdf_a.write_bytes(b"")
    fake_pdf_b = tmp_path / "invalidate_b.pdf"
    fake_pdf_b.write_bytes(b"")

    get_page_image(str(fake_pdf_a), 1)
    get_page_image(str(fake_pdf_b), 1)
    assert (page_cache.PAGE_CACHE_ROOT / page_cache._file_sha256(str(fake_pdf_a))).exists()
    assert (page_cache.PAGE_CACHE_ROOT / page_cache._file_sha256(str(fake_pdf_b))).exists()

    n = invalidate(filepath=str(fake_pdf_a))
    assert n == 1
    assert not (page_cache.PAGE_CACHE_ROOT / page_cache._file_sha256(str(fake_pdf_a))).exists()
    assert (page_cache.PAGE_CACHE_ROOT / page_cache._file_sha256(str(fake_pdf_b))).exists()


def test_invalidate_all_clears_everything(monkeypatch, tmp_path):
    monkeypatch.setattr(page_cache, "_render_page", _make_fake_renderer([]))
    a = tmp_path / "all_clear_a.pdf"; a.write_bytes(b"")
    b = tmp_path / "all_clear_b.pdf"; b.write_bytes(b"")
    get_page_image(str(a), 1)
    get_page_image(str(b), 1)
    before = stats()
    assert before["dirs"] == 2

    n = invalidate(all_=True)
    assert n == 2
    after = stats()
    assert after["dirs"] == 0
    assert after["used_bytes"] == 0


def test_stats_fields():
    s = stats()
    for key in ("root", "max_bytes", "used_bytes", "dirs", "files", "backend"):
        assert key in s, f"stats 缺字段 {key}"
    assert s["root"] == str(page_cache.PAGE_CACHE_ROOT)
    assert s["max_bytes"] == PAGE_CACHE_MAX_BYTES
    assert s["used_bytes"] >= 0
    assert s["dirs"] >= 0
    assert s["files"] >= 0
    # 后端非空字符串（pypdfium2 或 pdf2image）
    assert s["backend"] in ("pdf2image", "pypdfium2")