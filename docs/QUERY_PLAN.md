# 投诉材料查询流程优化方案

> 目标：让"蔡振华 + 13800000000"类案（手机号在内部系统没绑到本学员）能多一次召回，避免直接掉到姓名模糊匹配。

## 现状

### 现有查询路径（`app.py:654-687` + `app.py:694-760`）

```
步骤 1：用户提供身份证 → 内部系统精确查 → 命中返回
步骤 2：仅手机号 → 内部系统反查身份证号（lookup_id_card_by_phone）
        ├─ 命中唯一身份证号 → 用该证号走三系统完整查询
        └─ 查无 / 多个 → 直接返回 not_found
```

### 关键问题

`crawlers/internal.py:250-273` 的 `lookup_id_card_by_phone` 只反查"手机号 → 身份证号"，**完全丢弃了姓名信息**。当本单学员手机号没绑到自己、绑到了别人（共用/前任/录入错误）时，步骤 2 直接判 not_found，丢失该手机号下的所有候选线索。

## 修复方案：步骤 2 拆分为 2a + 2b

### 步骤 2a：姓名 + 手机号 双字段精确（现有逻辑）
- 输入：投诉材料里的 `student_name` + `phone`
- 调用：内部系统手机号反查 → 拿到唯一证号 → 用该证号走三系统
- 命中且系统返回的姓名 = 输入姓名 → 直接返回
- 命中但系统返回的姓名 ≠ 输入姓名 → **打 `name_mismatch=True`，继续走步骤 3 降级给候选列表**

### 步骤 2b（新增）：仅手机号查（无姓名或步骤 2a 未命中）
- 输入：仅 `phone`
- 调用：内部系统手机号反查 → 返回所有匹配学员的 `(id_card, name)` 列表
- 命中 1 个 → 走三系统，结果打 `name_mismatch=True`（强制人工核验）
- 命中多个 → 返回候选列表，让用户选
- 未命中 → 走步骤 3

### 步骤 3：姓名模糊查（现状）
- 内部系统姓名 LIKE 模糊，返回列表（不变）

## 数据流改动

### 后端爬虫层（`crawlers/internal.py`）

**新增方法 `lookup_students_by_phone(phone)`**：

```python
def lookup_students_by_phone(self, phone: str, _retry: int = 0) -> list[dict]:
    """通过手机号反查所有匹配的学员信息（含姓名+证号），不做去重/不报错。
    返回 [{id_card, name, phone}, ...]，空列表表示查无。
    """
    # 内部用 listXyxx.action + sjhm 参数
    # 解析 rows，提取 sfzh+name+sjhm，过滤有效证号
    # 与现有 lookup_id_card_by_phone 复用同一接口，区别是不抛 PhoneLookupAmbiguityError
```

**保留 `lookup_id_card_by_phone`**：作为内部使用，向后兼容。

### 后端服务层（`app.py`）

修改 `query_all_systems_by_phone(phone, expected_name=None, ...)`：

```python
def query_all_systems_by_phone(phone, expected_name="", timeout=60.0):
    crawler = query_engine._crawlers.get(SystemType.INTERNAL)
    # 步骤 2a: 期望姓名匹配
    if expected_name:
        id_card = crawler.lookup_id_card_by_phone(phone)  # 现有逻辑
        if id_card:
            merged = query_all_systems_sync(id_card, timeout=...)
            if merged and merged.get("name"):
                # 系统返回姓名 vs 期望姓名比对
                if str(merged["name"]).strip() == expected_name.strip():
                    return merged  # 强匹配
                # 姓名不一致 → 仍返回但打 name_mismatch 标记
                merged["name_mismatch"] = True
                merged["name_mismatch_reason"] = (
                    f"输入姓名「{expected_name}」与系统返回「{merged['name']}」不一致"
                )
                return merged

    # 步骤 2b: 仅手机号查（无姓名 或 步骤2a未命中/姓名不一致）
    candidates = crawler.lookup_students_by_phone(phone)
    if candidates:
        if len(candidates) == 1:
            # 唯一命中 → 用该证号走三系统
            cand = candidates[0]
            merged = query_all_systems_sync(cand["id_card"], timeout=...)
            if merged and merged.get("name"):
                if expected_name and str(merged["name"]).strip() != expected_name.strip():
                    merged["name_mismatch"] = True
                    merged["name_mismatch_reason"] = (
                        f"输入姓名「{expected_name}」与系统返回「{merged['name']}」不一致"
                    )
                return merged
        else:
            # 多个候选 → 返回候选列表让用户选
            return {
                "name": "", "phone": phone,
                "sources": {"internal": "ambiguous", "third": "not_found", "driving": "not_found"},
                "candidates": candidates,
                "error": "手机号匹配到多个学员，请人工选择",
            }
    # 步骤 3 兜底：未命中 → 由前端走姓名模糊搜索
    return {
        "name": "", "phone": phone,
        "sources": {"internal": "not_found", "third": "not_found", "driving": "not_found"},
        "error": "手机号在内部系统未查到学员，请改用姓名搜索",
    }
```

修改 `api_query`（`app.py:654`）：把 `student_name` 字段透传给 `query_all_systems_by_phone`。

修改 `_query_job_worker`（`app.py:762`）：同上。

### 前端层（`static/js/composables/useComplaint.js`）

1. `applyIntakeResult` 中 `studentName` 已被回填到 `studentName.value`，会在 `queryAll` 时随工单数据提交。
2. 修改 `queryAll`（`useComplaint.js:639`）：把 `studentName.value` 放入 payload → 服务端 `/api/query/start` 接收 → 传给 worker → 传给 `query_all_systems_by_phone`。
3. 在 `qr.value` 命中后增加红字提示：当 `qr.value.name_mismatch === true` 时，显示：
   > ⚠️ 输入姓名「蔡振华」与系统返回「XXX」不一致，请人工核验

UI 渲染位置：候选结果区上方（与现有 `phoneMismatch` 提示同位）。

## 影响面

- **新增 API 行为**：`/api/query/start` 接受 `expected_name` 字段（向后兼容，缺省不传）
- **新增响应字段**：`result.name_mismatch`（bool）、`result.name_mismatch_reason`（str）
- **不改动**：
  - 步骤 1（身份证精确查）逻辑
  - 步骤 3（姓名模糊查）逻辑
  - 三系统查询引擎（`query_all`）
  - 数据库 schema
  - 工单保存逻辑

## 回测场景

| 场景 | 输入 | 期望输出 |
|---|---|---|
| A. 蔡振华 + 13800000000（手机号绑别人） | name=蔡振华, phone=13800000000 | 走 2b，返回 name_mismatch=True，前端红字提示 |
| B. 蔡振华 + 13800000000（手机号未录入） | name=蔡振华, phone=13800000000 | 走 2b 未命中 → 走 3 姓名模糊 |
| C. 蔡振华 + 13800000000（完全一致） | name=蔡振华, phone=13800000000 | 走 2a，name_mismatch=False |
| D. 仅手机号 13800000000（无姓名） | phone=13800000000 | 走 2b |
| E. 仅有身份证 | id_card=XXX | 走步骤 1，不变 |

## 风险评估

| 风险 | 等级 | 缓解 |
|---|---|---|
| 手机号共用/前任号导致误命中 | 中 | name_mismatch 红字 + 不自动建案，保留人工核验 |
| lookup_students_by_phone 增加内部系统调用 | 低 | 与现有 `lookup_id_card_by_phone` 复用同一 HTTP 接口，仅多解析 name 字段 |
| 多个候选时未走三系统 | 低 | 已有"用户选后再查询"模式（`chooseCandidate`），复用 |
| 前端数据流（studentName → payload → 后端）漏传 | 低 | 测试用例覆盖 |

## 实施顺序

1. 后端爬虫：新增 `lookup_students_by_phone`（含单元测试）
2. 后端 service：拆分 `query_all_systems_by_phone` 为 2a+2b（含单元测试）
3. 前端：透传 expected_name + 红字提示
4. 回测 5 个场景
5. 启动服务做端到端联调
