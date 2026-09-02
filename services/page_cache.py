"""扫描版 PDF 页图缓存（工单 09-page-image-cache）。

设计要点：
  * 缓存根目录：``<BASE_DIR>/tmp/page_cache``。每个合同一个子目录
    ``<sha256 前 16>/``，子目录里放 ``page_<N>.png``，N 为 1-based。
  * LRU 语义：按子目录的 mtime 升序排序后**整目录**删除（不是逐页文件清理），
    直到缓存总字节数 ``<= PAGE_CACHE_MAX_BYTES``。完全删除后无单页碎片残留。
  * 缓存命中会刷新目录 mtime（相当于 touch 续命）；下一次 LRU 跑时它会重新
    排到末尾。
  * 只缓存「扫描版 PDF」：pdfplumber 能抽出非空白文本则视为有文本层，直接
    ``None`` 跳过（与 ``services.contract_service._extract_pdf_text`` 一致的阈值）。
  * 渲染后端 import 期一次性决定（pdf2image → pypdfium2）。环境两者都缺则
    模块 import 不抛，但实际渲染会优雅返回 ``None`` + 警告日志。
  * ``_render_page`` 是模块级函数指针；测试可以 ``monkeypatch`` 替换为
    fake，避免依赖 poppler / pypdfium2 二进制差异。

公开 API：
  ``PAGE_CACHE_ROOT: Path``
  ``PAGE_CACHE_MAX_BYTES: int``
  ``PAGE_CACHE_DPI: int``
  ``has_text_layer(filepath) -> bool``
  ``get_page_image(filepath, page_no) -> bytes | None``
  ``get_page_count(filepath) -> int | None``
  ``cleanup_lru() -> dict``
  ``invalidate(filepath=None, *, all_=False) -> int``
  ``stats() -> dict``
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from config import BASE_DIR


logger = logging.getLogger(__name__)


# ── 常量（工单明确「缓存目录大小上限必须用常量」） ────────────────────────────


PAGE_CACHE_ROOT: Path = BASE_DIR / "tmp" / "page_cache"
PAGE_CACHE_MAX_BYTES: int = 50 * 1024 * 1024  # 50 MB
PAGE_CACHE_DPI: int = 144  # 清晰度 vs 单页体积的折中；144 DPI 单页约 200-500 KB

# 文本层判定阈值（与 services.contract_service.extract_contract_text_from_file 的
# 「< 20 字符视为无文本」对齐）。
TEXT_LAYER_MIN_CHARS = 20


# ── 渲染后端选择（import 期一次性决定；测试 monkeypatch _render_page） ────────


_BACKEND: str = ""  # "pdf2image" / "pypdfium2" / ""（都没装）


def _render_page_pdf2image(filepath: str, page_no: int, dpi: int):
    """pdf2image 后端：依赖系统已安装 poppler。"""
    from pdf2image import convert_from_path  # type: ignore

    images = convert_from_path(
        filepath,
        dpi=dpi,
        first_page=page_no,
        last_page=page_no,
    )
    if not images:
        raise RuntimeError("pdf2image returned empty result")
    return images[0]


def _render_page_pypdfium2(filepath: str, page_no: int, dpi: int):
    """pypdfium2 后端：纯 Python，无需系统 poppler。scale = dpi / 72。"""
    import pypdfium2 as pdfium  # type: ignore

    pdf = pdfium.PdfDocument(filepath)
    try:
        if page_no < 1 or page_no > len(pdf):
            raise IndexError(f"page {page_no} out of range 1..{len(pdf)}")
        page = pdf[page_no - 1]
        bitmap = page.render(scale=max(dpi / 72.0, 0.5))
        return bitmap.to_pil()
    finally:
        # pypdfium2 v1/v2 都支持 close(); 老版本没有就忽略
        close = getattr(pdf, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # pragma: no cover - defensive
                pass


def _pick_backend() -> str:
    """检测可用的渲染后端；不强制 raise，让模块 import 永远成功。"""
    try:
        import pdf2image  # type: ignore  # noqa: F401

        return "pdf2image"
    except Exception:
        pass
    try:
        import pypdfium2  # type: ignore  # noqa: F401

        return "pypdfium2"
    except Exception:
        return ""


_BACKEND = _pick_backend()
if _BACKEND == "pdf2image":
    _render_page = _render_page_pdf2image
elif _BACKEND == "pypdfium2":
    _render_page = _render_page_pypdfium2
else:
    def _render_page(filepath: str, page_no: int, dpi: int):  # pragma: no cover
        raise RuntimeError(
            "无可用 PDF 渲染后端：请 pip install pypdfium2 或 pdf2image"
        )


# ── 路径/指纹工具 ────────────────────────────────────────────────────────


def _file_sha256(filepath: str) -> str:
    """16 字符短哈希。足够区分文件、目录名简短。

    用文件元数据（realpath + size + mtime_ns）做指纹，避免读大文件 IO。
    与 ``services.contract_cache.compute_cache_key`` 的「文件指纹」语义对齐——
    文件本身没变 → 命中同一缓存目录。
    """
    try:
        st = os.stat(filepath)
        payload = f"{os.path.realpath(filepath)}|{st.st_size}|{st.st_mtime_ns}"
    except OSError:
        # 文件不存在时退化为基于路径的稳定哈希，避免 hash 抛异常
        payload = f"missing|{filepath}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _cache_dir(filepath: str) -> Path:
    return PAGE_CACHE_ROOT / _file_sha256(filepath)


def _ensure_root() -> Path:
    PAGE_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    return PAGE_CACHE_ROOT


# ── 文本层判定（与 contract_service._extract_pdf_text 同思路） ────────────────


def has_text_layer(filepath: str) -> bool:
    """pdfplumber 抽非空文本 → True；否则 False。

    - 非 .pdf 后缀：False；
    - 文件不存在或 pdfplumber 抛异常：False（视为扫描版兜底，由后续渲染路径接住）；
    - 文本字符数 < TEXT_LAYER_MIN_CHARS：False（典型扫描版抽出零碎字符）。
    """
    if not filepath or not isinstance(filepath, str):
        return False
    if not filepath.lower().endswith(".pdf"):
        return False
    try:
        import pdfplumber  # type: ignore
    except Exception:
        # pdfplumber 不可用 → 保守地视作扫描版（让渲染路径兜底，不在 has_text_layer 抛错）
        return False
    try:
        with pdfplumber.open(filepath) as pdf:
            chunks = []
            for page in pdf.pages:
                t = page.extract_text() or ""
                if t.strip():
                    chunks.append(t)
        joined = "\n".join(chunks).strip()
        return len(joined) >= TEXT_LAYER_MIN_CHARS
    except Exception as e:
        logger.warning("[page_cache] pdfplumber 文本层检测失败 %s: %s", filepath, e)
        return False


# ── 元数据查询 ────────────────────────────────────────────────────────────


def get_page_count(filepath: str) -> int | None:
    """PDF 页数；非 PDF 或失败 → None。优先用 pypdfium2（与渲染后端无关）。"""
    if not filepath or not filepath.lower().endswith(".pdf"):
        return None
    if not os.path.exists(filepath):
        return None
    try:
        import pypdfium2 as pdfium  # type: ignore

        doc = pdfium.PdfDocument(filepath)
        try:
            return len(doc)
        finally:
            close = getattr(doc, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # pragma: no cover
                    pass
    except Exception as e:
        logger.warning("[page_cache] get_page_count 失败 %s: %s", filepath, e)
        return None


# ── 缓存读写 ──────────────────────────────────────────────────────────────


def _page_path(filepath: str, page_no: int) -> Path:
    return _cache_dir(filepath) / f"page_{page_no}.png"


def _touch_dir(dirpath: Path) -> None:
    """更新目录 mtime：刷新最近访问时间，供 LRU 排序使用。"""
    try:
        now = os.stat(dirpath).st_mtime_ns
        os.utime(dirpath, ns=(now, now))
    except OSError:
        try:
            dirpath.mkdir(parents=True, exist_ok=True)
            os.utime(dirpath)
        except OSError:
            pass


def _dir_size(dirpath: Path) -> int:
    """递归统计目录占用字节数。"""
    total = 0
    try:
        for root, _, files in os.walk(dirpath):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _read_png(path: Path) -> bytes | None:
    try:
        with open(path, "rb") as fp:
            return fp.read()
    except OSError:
        return None


def get_page_image(filepath: str, page_no: int) -> bytes | None:
    """命中缓存或渲染后返回 PNG bytes；否则 None。

    返回 ``None`` 的几种情况（皆为优雅，不抛）：
      - filepath 为空 / 不存在；
      - 非 .pdf 后缀；
      - 文本层 PDF（有文本层则不需要页图，前端走浏览器 PDF 渲染）；
      - 渲染失败（后端不可用 / 文件结构损坏 / page 越界）；
    """
    if not filepath or not isinstance(filepath, str):
        return None
    if not filepath.lower().endswith(".pdf"):
        return None
    if not os.path.exists(filepath):
        return None
    if page_no is None or int(page_no) < 1:
        return None
    page_no = int(page_no)

    if has_text_layer(filepath):
        return None

    # 1. 缓存命中
    cache_dir = _cache_dir(filepath)
    page_file = cache_dir / f"page_{page_no}.png"
    if page_file.exists():
        data = _read_png(page_file)
        if data:
            _touch_dir(cache_dir)
            return data

    # 2. 缓存未命中 → 渲染
    try:
        img = _render_page(filepath, page_no, PAGE_CACHE_DPI)
    except Exception as e:
        logger.warning("[page_cache] 渲染失败 %s page=%d: %s", filepath, page_no, e)
        return None

    if img is None:
        return None

    # 3. 写盘（原子：临时文件 → rename，避免读到半写文件）
    _ensure_root()
    cache_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".page_", suffix=".png", dir=str(cache_dir))
    try:
        with os.fdopen(fd, "wb") as fp:
            # PIL Image 可直接 save 到文件对象；非 PIL 对象则用 BytesIO 兜底
            if hasattr(img, "save"):
                img.save(fp, "PNG")
            else:
                buf = io.BytesIO()
                img.save(buf, "PNG")
                fp.write(buf.getvalue())
        os.replace(tmp_path, page_file)
    except Exception as e:
        logger.warning("[page_cache] 写盘失败 %s page=%d: %s", filepath, page_no, e)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return None

    # 4. LRU 触发（异步 N/A，进程内同步跑；开销可控）
    try:
        cleanup_lru()
    except Exception as e:  # pragma: no cover - 防御
        logger.warning("[page_cache] cleanup_lru 失败: %s", e)

    return _read_png(page_file)


# ── LRU 整目录清理 ───────────────────────────────────────────────────────


def cleanup_lru() -> dict:
    """按目录 mtime 升序**整目录**删除，直到总字节数 ``<= PAGE_CACHE_MAX_BYTES``。

    返回：``{"deleted_dirs": [...], "deleted_bytes": int,
             "remaining_dirs": int, "remaining_bytes": int}``
    """
    _ensure_root()
    root = PAGE_CACHE_ROOT
    if not root.exists():
        return {
            "deleted_dirs": [],
            "deleted_bytes": 0,
            "remaining_dirs": 0,
            "remaining_bytes": 0,
        }

    # 列目录：每个 sha 子目录视为一个缓存单元
    entries: list[tuple[float, int, str]] = []
    total = 0
    for name in os.listdir(root):
        sub = root / name
        if not sub.is_dir():
            continue
        size = _dir_size(sub)
        try:
            mtime = sub.stat().st_mtime
        except OSError:
            mtime = 0.0
        entries.append((mtime, size, str(sub)))
        total += size

    # 按 mtime 升序：最旧在前
    entries.sort(key=lambda x: x[0])

    deleted_dirs: list[str] = []
    deleted_bytes = 0
    if total > PAGE_CACHE_MAX_BYTES:
        for mtime, size, sub_path in entries:
            if total <= PAGE_CACHE_MAX_BYTES:
                break
            try:
                shutil.rmtree(sub_path, ignore_errors=True)
                deleted_dirs.append(sub_path)
                deleted_bytes += size
                total -= size
            except OSError as e:
                logger.warning("[page_cache] 删除缓存目录失败 %s: %s", sub_path, e)

    remaining = [e for e in entries if e[2] not in deleted_dirs]
    return {
        "deleted_dirs": deleted_dirs,
        "deleted_bytes": deleted_bytes,
        "remaining_dirs": len(remaining),
        "remaining_bytes": sum(e[1] for e in remaining),
    }


# ── invalidate / stats ────────────────────────────────────────────────────


def invalidate(filepath: str | None = None, *, all_: bool = False) -> int:
    """按文件清一个目录（按 sha256 精确匹配）；``all_=True`` 清空全部。"""
    _ensure_root()
    root = PAGE_CACHE_ROOT
    if all_:
        n = 0
        for name in os.listdir(root):
            sub = root / name
            if sub.is_dir():
                try:
                    shutil.rmtree(sub, ignore_errors=True)
                    n += 1
                except OSError:
                    pass
        return n

    if not filepath:
        return 0
    target = _cache_dir(filepath)
    if target.is_dir():
        try:
            shutil.rmtree(target, ignore_errors=True)
            return 1
        except OSError as e:
            logger.warning("[page_cache] invalidate 失败 %s: %s", target, e)
            return 0
    return 0


def stats() -> dict:
    """缓存统计：根目录路径、上限字节、已用字节、子目录数、文件数。"""
    _ensure_root()
    root = PAGE_CACHE_ROOT
    used = 0
    dirs = 0
    files = 0
    if root.exists():
        for name in os.listdir(root):
            sub = root / name
            if sub.is_dir():
                dirs += 1
                for r, _, fs in os.walk(sub):
                    for f in fs:
                        try:
                            used += os.path.getsize(os.path.join(r, f))
                            files += 1
                        except OSError:
                            pass
    return {
        "root": str(root),
        "max_bytes": PAGE_CACHE_MAX_BYTES,
        "used_bytes": used,
        "dirs": dirs,
        "files": files,
        "backend": _BACKEND,
    }