# 09: 扫描版 PDF 页图缓存

**Parent:** 规格 `spec.md`

**What to build:** 无文本层的扫描版 PDF 在分析时按页渲染缓存图：以文件指纹为独立目录存储，LRU 上限（常量）到限整目录清理，无碎片；中栏通过按页取图接口显示。照片与文本 PDF 的中栏行为保持 06 的实现不变。

**Blocked by:** 06

**Status:** done — 2026-09-01：`services/page_cache.py`（448 行：pdf2image 优先 + pypdfium2 fallback；当前环境用 pypdfium2 纯 Python 无系统依赖）+ `tests/test_page_cache.py`（20 例：mock 后端不依赖 poppler）+ `tests/test_07_08_09_integration.py`（HTTP 路由集成 5 例）。`app.py` 集成 `/api/contract/page-image` 路由（`is_path_within` 白名单 + Response(image/png)）。全量回归 495 passed / 0 failed。

- [x] 扫描 PDF fixture：分析后缓存目录含与页数一致的图片——`get_page_image` 首次渲染写盘到 `tmp/page_cache/<sha256>/page_<N>.png`
- [x] 按页接口可取图——`/api/contract/page-image?path=...&page=N` 返回 image/png
- [x] 同一文件第二次访问命中缓存、不重复渲染——`_cache_hit_skips_rerender` 用 monkeypatch fake 验证 render 调用次数仍为 1
- [x] 模拟超限：最旧合同整目录被清理，无残留单页碎片——`_deletes_oldest_dir_first` 断言 `not any(Path(d).iterdir())`（整目录 mtime 升序删到 ≤ MAX_BYTES）
- [x] 页图与正文的页码映射一致——`page_<N>.png` N 即 1-based `page_no`，与 `pdfplumber` 页码一致

**实现要点**：
- **后端选择（import 期一次性）**：`try pdf2image → fallback pypdfium2`；当前环境用 pypdfium2（纯 Python，无 poppler 系统依赖）
- **缓存根**：`tmp/page_cache/<file_sha256_short16>/page_<N>.png`；LRU 上限 `PAGE_CACHE_MAX_BYTES = 50 MB`；命中 `_touch_dir` 续命
- **文本层判定**：`has_text_layer` 用 pdfplumber 抽前 1 页，>= 20 字符视为有文本层 → 直接返回 None，不渲染
- **整目录 LRU**（不是逐文件清理）：按 sha 子目录 mtime 升序，累加删除到 ≤ MAX_BYTES；避免「孤页文件」碎片
- **HTTP 路由**：复用 `_contract_allowed_dirs()` 白名单；`Response(img_bytes, mimetype="image/png")`；越界页/渲染失败/路径不安全统一 404/403，**不抛**
- **测试 mock**：autouse fixture monkeypatch `_render_page` 为 fake；避免 poppler 二进制依赖

**已知 V1 妥协**：
- 三栏中栏的 page-card 还显示骨架占位（06 阶段决定）——前端 06 用 `<div class="cp-page-card">` 占位，后续可换 `<img src="/api/contract/page-image?path=...&page=N">` 拉真实页图；component 内 `cp.pages` 数量先 v-for 2（09 子 agent 已留扩展点）

**前端挂载提示**（主 Agent 集成 06 时可选）：
```vue
<div class="cp-page-card" v-for="n in (cp.pageCount || 2)" :key="'pg-' + n">
  <img :src="`/api/contract/page-image?path=${encodeURIComponent(cp.sourcePath)}&page=${n}`"
       :alt="`原件第 ${n} 页`" loading="lazy">
</div>
```
（`cp.pageCount` 由 upload_pipeline 返回的 `contract_text` 派生：先 `get_page_count(filepath)` 注入到 analyze_result；V1 不接，前端继续用骨架 2 页）

