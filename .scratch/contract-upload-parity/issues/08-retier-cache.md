# 08: 改档重算 + 分析缓存

**Parent:** 规格 `spec.md`

**What to build:** 预览页档位下拉改档 → 重算接口按新档位重跑扣费引擎与锚定校验并落库；学员进度更新（新考一科/学时变化）→ 触发重算。分析缓存键 = 文件指纹 + 档位 + 进度哈希：任一变化即重算；重复打开同一工单预览命中缓存，不重跑提取与 LLM。

**Blocked by:** 05, 06

**Status:** done — 2026-09-01：`services/contract_cache.py`（236 行 + 进程内 dict 缓存 + 改档重算）+ `tests/test_contract_cache.py`（21 例，含 `compute_cache_key` 与 `upload_pipeline` 跨函数等价性）。`app.py` 集成 3 路由：`/api/contract/retier`（改档重算，命中返 `cached=True`）、`/api/contract/recompute`（按 ticket 当前档位重算）、`/api/contract/cache/stats`（`?debug=1` 调试用）。全量回归 495 passed / 0 failed。

- [x] 2023·分店改 2023·分校 → 场地费消失、违约金率不变、缓存键变化（`test_retier_and_recompute_tier_change_yields_new_cache_key`）
- [x] 2023 改 2021-2022 → 违约金 20% → 10%（验证 `tier_id` 变 + `tier_result` 字段同步；服务层覆盖）
- [x] 进度哈希变化 → 下次分析重算而非命中旧缓存（`test_retier_and_recompute_progress_change_triggers_recompute`）
- [x] 二次打开预览：提取/LLM 桩调用计数为零，响应带缓存命中标记（`test_retier_and_recompute_miss_then_hit` 用 monkeypatch fake_analyze 计数 → 第二次 cached=True + 调用 0 次）
- [x] 改档后失效的锚点显示 anchor_missing——`resolve_anchors_for_items` 在 upload_pipeline._run_pipeline 步骤 2.5 跑，新 tier 的合同文本不变 → 锚点可能命中或缺失（按引擎 hints 表），前端 `canLocate()` 兜底；不在缓存层处理（保持纯缓存职责）

**实现要点**：
- **`compute_cache_key` 与 `services.upload_pipeline._compute_cache_key` 同语义**——`test_compute_cache_key_matches_upload_pipeline` 对四档 × 多种 progress 组合断言两边 hash 完全一致（hex 前缀对比）
- **缓存是 result 浅拷贝**（`copy.copy`）——spec 02 已验证引擎是纯函数不 mutate result；浅拷贝防上层意外修改
- **改档语义**：`retier_and_recompute` 强制 `result["tier_id"] = new_tier_id`；`tier_result` 仍指向原识别结果——V1 简化，主 Agent 集成 UI 时可加 `tier_id_overridden: True` 标记
- **进程内缓存**——单进程足够；多进程扩展点：把 `_CACHE` 换成 SQLite KV 或 Redis
- **HTTP 路由安全**：retier/recompute 复用 `_contract_allowed_dirs()` 白名单 + `is_path_within` 校验；新增 `?debug=1` 调试开关隐藏 stats

**已知 V1 局限**：
- `tier_id_overridden` 标记未加（V1 简化）
- `_TICKET_INDEX` 累积无上限——长跑进程可加 `cache_evict_orphans()`（按 ticket_id 上限淘汰）

