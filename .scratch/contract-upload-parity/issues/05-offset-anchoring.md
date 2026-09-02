# 05: 偏移量锚定 + 抽取升级

**Parent:** 规格 `spec.md`

**What to build:** 抽取 prompt 升级：每条扣费项附**原文锚点短语**、手写金额附位置描述；印刷标准值（违约金率/考试费）不再要求 LLM 给金额结论，仅与档位默认交叉校验、冲突输出告警。后端把锚点短语解析为正文字符区间 [start, end) 并连同条款号、页码随明细落库；解析失败标 anchor_missing。锚点只对产生它的那份正文有效——正文重新提取即视为旧锚点失效（由缓存键约束保证）。

**Blocked by:** 04

**Status:** done — 2026-09-01：`services/anchor_resolver.py`（123 行纯函数）+ `tests/test_anchor_resolver.py`（20 例）全绿；`upload_pipeline.py` 接入锚定 + 冲突告警（`_run_pipeline` 步骤 2.5）；3 例 upload_pipeline 集成测试通过；全量回归 411 passed / 0 failed。

- [x] 2023·分店 fixture：服务费/建档费/学员IC卡/场地费/科二考试费/科目二实操培训费/违约金各项均有合法区间，且按区间回读的文本与锚点短语一致（`test_resolve_2023_branch_store_full_set`）
- [x] OCR 噪声（错字/断行）：部分项 `anchor_missing=True`、其余正常，整管线不抛错（`test_resolve_ocr_noise_partial_missing` + `test_pipeline_marks_anchor_missing_without_throwing`）
- [x] 合同文本写 10% 而档位默认 20% → `warnings` 出现冲突告警，金额仍按档位默认 1200（`test_pipeline_warns_on_penalty_rate_conflict`）
- [x] 旧格式 items（无 anchor_phrase）走通过：不抛，原字段保留，找不到锚点的项 `anchor_missing=True`（`test_resolve_handles_legacy_items_without_anchor_phrase` + `test_resolve_legacy_items_phrase_absent_just_marks_missing`）

**实现要点**：
- `find_phrase(phrase, text) → (start, end)`：构建 normalize 索引→原文本索引映射，**返回原文本的字符位置**（前端用原文本渲染、mark 落在原区间）。空白容忍：OCR/Vision 引入的换行/空格不阻断匹配。
- `resolve_anchors_for_items(items, text, tier=None) → enriched_list`：先看 item.anchor_phrase（LLM 抽取），否则查 `ANCHOR_PHRASE_HINTS` 表按候选短语匹配。**深拷贝不修改原 items**——便于上游引擎保持纯函数语义。
- `detect_text_conflicts(text, tier) → list[str]`：V1 只扫 "全部培训费用的X%" 与 `tier.penalty_rate` 对比。2019 三档 0% 一律不告警（penalty=0 即无违约金额预期）。
- `upload_pipeline._run_pipeline` 步骤 2.5：扣费引擎后跑锚定 + 冲突检测；warnings 追加，金额不动。

**LLM prompt 升级（spec 05 工单第 1 验收）暂留 06 阶段**：本期未调 LLM，引擎的 tier_default 项自带 hints 表里的候选短语可直接命中；后续 prompt 升级后，LLM 抽取的 anchor_phrase 会优先于 hints（resolve_anchors_for_items 的 explicit-first 语义已就绪）。

