# 多 Agent P0 交付实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用五组真实合同建立唯一后端费用规则，并完成费用确认、查询语义、安全边界和端到端回归的P0闭环。

**Architecture:** AI/OCR仅产生逐合同结构化事实，`core/refund_engine.py`根据合同事实和三系统事实计算草案；确认后由案件工作流保存不可变快照。现有Flask、Vue 3和SQLite保持不变。

**Tech Stack:** Python 3、Flask、SQLite、Vue 3、pytest

---

## 文件责任

- `core/refund_engine.py`：纯函数费用规则、阶段归一化、多合同汇总。
- `services/contract_service.py`：文本/图片提取和旧分析结果到合同集合的适配，不再拥有正式金额口径。
- `core/case_workflow.py`：费用确认、完结和正式快照不变量。
- `app.py`：API参数校验和调用上述模块，不重复计算。
- `static/js/composables/useWorkflow.js`：展示后端草案和提交人工覆盖，不自行计算正式金额。
- `templates/index.html`：逐合同扣费依据、警告和阻断项展示。
- `tests/test_refund_engine.py`：五组真实合同黄金规则测试。
- `tests/test_contract_pipeline.py`：提取适配、缺页、派生页去重和旧接口回归。
- `tests/test_case_workbench.py`：确认快照、完结、回复函和飞书一致性。
- `tests/test_query_performance_instrumentation.py`：查询错误语义与缓存完整性。

## Task 1：建立五组真实合同黄金测试

**Files:**
- Create: `tests/test_refund_engine.py`
- Reference: `docs/acceptance/2026-07-05-contract-golden-cases.md`

- [ ] **Step 1: 写方梓林和金凯悦失败测试**

测试必须直接构造结构化合同集合，不调用外部模型：

```python
from core.refund_engine import calculate_fee_plan


def test_fang_zilin_golden_case():
    result = calculate_fee_plan(FANG_CONTRACT_SET, FANG_PROGRESS, {})
    assert result["total_contract_fee"] == 3280
    assert result["total_deduction"] == 1726
    assert result["refund"] == 1554


def test_jin_kaiyue_three_contracts_accumulate_by_stage():
    result = calculate_fee_plan(JIN_CONTRACT_SET, JIN_PROGRESS, {})
    assert result["total_contract_fee"] == 4490
    assert result["total_deduction"] == 1620
    assert result["refund"] == 2870
    assert {item["contract_id"] for item in result["deductions"]} == {
        "training", "exam_assistance", "exam_collection"
    }
```

- [ ] **Step 2: 运行测试并确认RED**

Run: `pytest -q tests/test_refund_engine.py`

Expected: FAIL，因为`core.refund_engine`不存在。

- [ ] **Step 3: 写谢婷、王家睿和黄运辉失败测试**

```python
def test_xie_ting_explicit_expiry_no_refund():
    result = calculate_fee_plan(XIE_CONTRACT_SET, XIE_PROGRESS, {})
    assert result["refund"] == 0
    assert result["decision"] == "expired_no_refund"


def test_wang_jiarui_keeps_rules_but_blocks_final_amount():
    result = calculate_fee_plan(WANG_CONTRACT_SET, WANG_PROGRESS, {})
    assert "合同总额" in result["blockers"]
    assert result["can_confirm"] is False


def test_huang_yunhui_two_contract_penalties_accumulate():
    result = calculate_fee_plan(HUANG_CONTRACT_SET, HUANG_PROGRESS, {})
    assert result["total_contract_fee"] == 5480
    assert result["total_deduction"] == 1056
    assert result["refund"] == 4424
```

- [ ] **Step 4: 再次运行并记录全部预期失败**

Run: `pytest -q tests/test_refund_engine.py`

Expected: 5个黄金案例均因实现缺失而失败。

## Task 2：实现唯一费用规则引擎

**Files:**
- Create: `core/refund_engine.py`
- Test: `tests/test_refund_engine.py`

- [ ] **Step 1: 定义输入校验和阶段归一化**

```python
def resolve_stage_rank(student_status: str, exam_stage: str) -> int:
    """0=报名，1=科一，2=科二，3=科三，4=科四。内部状态优先。"""


def calculate_fee_plan(contract_set: dict, progress_facts: dict, manual_overrides: dict | None = None) -> dict:
    """逐合同计算并返回可审计草案。"""
```

阶段映射至少覆盖报名、科目一待考、科一未通过、科二收、科目二待考、科三收、科目三待考、科四收、科目四待考、已结业和已领证。

- [ ] **Step 2: 实现逐规则计算**

支持最小规则集合：

```text
fixed                  固定扣费
stage_fee              达到阶段后扣费
exam_fee               首次考试发生后扣费
makeup_fee             max(考试次数-1, 0) × 单价
training_hour_fee      审核学时 × 单价，并受单合同上限约束
percentage_penalty     单合同总额 × 明确比例
fixed_penalty          单合同固定违约金
expiry_no_refund       报名满三年且合同明确到期不退
```

每个明细返回：

```python
{
    "contract_id": "...",
    "contract_title": "...",
    "item": "...",
    "amount": 0.0,
    "clause": "...",
    "trigger": "...",
    "formula": "...",
}
```

- [ ] **Step 3: 实现警告、阻断和人工覆盖**

- 缺页进入`warnings`，不自动阻断。
- 合同总额缺失进入`blockers`。
- 未写违约比例时不产生违约金。
- `actual_paid`默认合同金额总和，可由`manual_overrides.actual_paid`替换。
- 金额必须有限且非负。
- 单合同扣费和总扣费均不得超过允许上限。

- [ ] **Step 4: 运行黄金测试并确认GREEN**

Run: `pytest -q tests/test_refund_engine.py`

Expected: 全部通过。

## Task 3：合同提取结果适配为多合同事实

**Files:**
- Modify: `services/contract_service.py`
- Modify: `tests/test_contract_pipeline.py`
- Modify: `tests/test_contract_extraction.py`

- [ ] **Step 1: 写失败测试**

覆盖：

1. 多页图片可分成多份合同；
2. `*_temp_processed.*`不计入原始页面；
3. 缺第2页只产生警告；
4. 合同总额空白时保留已识别规则；
5. 未识别违约比例时为`None/0`，不得默认20%。

- [ ] **Step 2: 运行专项测试确认RED**

Run: `pytest -q tests/test_contract_extraction.py tests/test_contract_pipeline.py`

Expected: 新增测试失败，旧测试结果记录为基线。

- [ ] **Step 3: 实现适配层**

新增：

```python
def build_contract_set(extracted_contracts: list[dict]) -> dict:
    ...


def analyze_contract_set(files: list[str], progress_facts: dict) -> dict:
    ...
```

`_parse_ai_response()`仅保留兼容适配，最终调用`calculate_fee_plan()`；删除默认20%违约金行为。

- [ ] **Step 4: 运行专项测试确认GREEN**

Run: `pytest -q tests/test_contract_extraction.py tests/test_contract_pipeline.py tests/test_refund_engine.py`

Expected: 全部通过。

## Task 4：API只调用后端规则引擎

**Files:**
- Modify: `app.py`
- Modify: `tests/test_case_workbench.py`

- [ ] **Step 1: 写失败测试**

验证：

- `student_status`、`exam_stage`、`exam_counts`、`training_hours`完整传入规则引擎；
- API返回逐合同明细；
- 前端提交的`total_deduction`和`refund`不能覆盖后端重算结果；
- 有`warnings`仍可确认，有`blockers`不可确认。

- [ ] **Step 2: 运行失败测试**

Run: `pytest -q tests/test_case_workbench.py -k 'fee or contract'`

Expected: 新增断言失败。

- [ ] **Step 3: 最小化修改路由**

`/api/contract/analyze*`返回`fee_plan`；`/api/ticket/<id>/confirm-fee`重新校验结构化草案，不信任浏览器汇总金额。

- [ ] **Step 4: 运行专项测试**

Run: `pytest -q tests/test_case_workbench.py tests/test_contract_pipeline.py`

Expected: 全部通过。

## Task 5：页面展示逐合同依据

**Files:**
- Modify: `static/js/composables/useWorkflow.js`
- Modify: `templates/index.html`
- Modify: `static/css/style.css`

- [ ] **Step 1: 删除前端正式金额计算路径**

保留人工修改明细的输入，但每次修改都提交后端重新计算。`recalc()`不得成为正式金额来源。

- [ ] **Step 2: 增加逐合同分组展示**

每组显示合同名称、小计和明细；每条显示条款、触发事实和公式。警告与阻断使用不同视觉状态。

- [ ] **Step 3: 验证手机和桌面布局**

使用浏览器检查1440×900和390×844；不得出现横向溢出、按钮遮挡或明细截断。

## Task 6：正式快照与完结一致性

**Files:**
- Create: `core/case_workflow.py`
- Modify: `app.py`
- Modify: `database.py`
- Modify: `services/reply_service.py`
- Modify: `services/feishu_service.py`
- Test: `tests/test_case_workbench.py`

- [ ] **Step 1: 写确认后不可覆盖的失败测试**
- [ ] **Step 2: 实现`official_snapshot()`和确认/重开状态转换**
- [ ] **Step 3: 让回复函、飞书和完结统一读取快照**
- [ ] **Step 4: 运行`pytest -q tests/test_case_workbench.py`**

Expected: 确认、重开、完结、回复函和飞书测试全部通过。

## Task 7：查询正确性和缓存完整性

**Files:**
- Modify: `core/query_engine.py`
- Modify: `crawlers/internal.py`
- Modify: `crawlers/third.py`
- Modify: `crawlers/driving.py`
- Test: `tests/test_driving_query_performance.py`
- Test: `tests/test_query_performance_instrumentation.py`

- [ ] **Step 1: 写精确匹配、错误分类和部分失败不缓存测试**
- [ ] **Step 2: 实现规范状态和缓存闸门**
- [ ] **Step 3: 修复手机号多匹配与闰年日期**
- [ ] **Step 4: 运行两组查询测试**

## Task 8：合同文件和配置安全

**Files:**
- Modify: `app.py`
- Modify: `config.py`
- Modify: `services/contract_service.py`
- Test: `tests/test_llm_error_messages.py`
- Create: `tests/test_security_boundaries.py`

- [ ] **Step 1: 写目录跳转、符号链接、恶意模型主机和日志泄露测试**
- [ ] **Step 2: 使用`realpath + commonpath`校验合同路径**
- [ ] **Step 3: 恢复TLS验证并限制模型主机**
- [ ] **Step 4: 运行安全专项测试**

## Task 9：真实流程与发布回归

**Files:**
- Modify: `docs/acceptance/v1-requirements-matrix.md`
- Create: `docs/acceptance/2026-07-05-p0-release-report.md`

- [ ] **Step 1: 运行完整自动化回归**

```bash
pytest -q
python -m compileall -q app.py core crawlers services
node --check static/js/app.js
node --check static/js/composables/useWorkflow.js
```

- [ ] **Step 2: 浏览器回测**

覆盖受理、查询、三份合同、费用确认、沟通、归档、回复函和历史恢复；同时检查桌面与手机。

- [ ] **Step 3: 更新验收矩阵和发布报告**

只将有自动化证据和人工证据的项目标记为已覆盖；任何失败保持`NO-GO`。
