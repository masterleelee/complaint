# DRD 实现差距分析与实施计划（修订版）

> 审查日期：2026-05-11
> 基于用户确认，已排除不修改的项（手动建单、流水编号、状态改名、渠道顺序、技能证时间、自动查询）

---

## 排除项（用户确认不修改）

| # | DRD 需求 | 排除原因 |
|---|---------|---------|
| 1 | 手动新建工单（3.1.1） | 保持当前"上传/粘贴→AI提取→查询"流程 |
| 2 | 工单编号 YYYYMM+3位（3.1.4） | 保持 UUID |
| 3 | 状态名"未处理"（3.1.4） | 保持"待处理" |
| 4 | 投诉渠道排序（3.1.3） | 保持当前选项 |
| 5 | 技能证时间提取（3.2.1） | 代码已实现，需用户验证 |
| 6 | 自动触发查询（3.2） | 保持手动点击"查询三系统" |

---

## 待实现清单（按优先级）

### P0：核心流程缺失 — 回访管理 + 文档生成（3.4）

**当前状态**：完全未实现。DRD 规定的流程是：
```
AI分析完成 → 用户线下协商 → 选择回访状态 → 按规则生成文档 → 处理完成
```

**回访状态面板**（3.4.1）
- 新增一个卡片面板，在"处理流程"区域展示
- 4 个单选项：a.拒绝协商 / b.无法联系 / c.同意协商 / d.其他情况
- 必填备注框（1-200 字符）
- 提交后存入数据库（新增字段：`visit_status`, `visit_remark`, `visit_time`）
- 关键技术：Vue 3 响应式表单 + `POST /api/tickets/:id/visit` API

**文档自动生成**（3.4.2）
- 根据回访状态决定生成哪些文档：
  | 回访状态 | 自动生成 |
  |---------|---------|
  | a.拒绝协商 | 投诉登记表 + 投诉回复函 |
  | b.无法联系 | 投诉登记表 |
  | c.同意协商 | 投诉登记表 |
  | d.其他情况 | 用户自由勾选 |
- 关键技术：`python-docx` 模板 + 变量替换

**在线预览/导出**（3.4.4）
- 文档生成后展示预览面板（用 iframe 或直接下载链接）
- 支持下载 Word（.docx），PDF 用浏览器打印功能
- 关键技术：Flask send_file + 前端 `<a download>`

### P1：特殊退费检测（3.3.4）

**当前状态**：完全未实现。这是法律合规需求。

4 条检测规则：
| 条件 | 警告文案 | 回复函文案 |
|------|---------|-----------|
| 合同报名时间 > 3年 | 该学员合同报名已超3年，无费用退还 | 同上 |
| 技能证时间 > 3年 | 该学员技能证已超3年，无费用退还 | 同上 |
| 科二不合格 > 5次 | 该学员科二已超过5次不合格，无费用退还 | 同上 |
| 科三不合格 > 5次 | 该学员科三已超过5次不合格，无费用退还 | 同上 |

- 在 AI 分析结果面板中**标红**显示警告
- 在生成回复函时自动添加说明段落
- 关键技术：`contract_service.py` 新增 `_detect_special_cases()`，输入：registration_date / skill_cert_date / exam_counts，返回警告数组

### P1：LLM 输出格式优化（3.3.2）

**当前状态**：LLM 返回自然语言，`_parse_ai_response` 用正则提取。可靠性取决于 LLM 输出。

**优化方案**：
- 在 LLM prompt 中明确要求输出结构化的扣费项目列表（保留现有自然语言分析作为 `raw_analysis`）
- 增强 `_parse_ai_response` 的正则覆盖更多场景（如手写备注、分期付款等）
- 关键技术：优化 prompt 模板 + 扩展 `_parse_ai_response` 正则匹配

### P2：状态自动流转（3.5.1）

**当前状态**：完全手动。用户需要确认流转触发时机：

| 触发时机 | 自动变更为 | 是否合理？需用户确认 |
|---------|-----------|-------------------|
| 合同分析完成 + 扣费明细生成 | 处理中 | 表示"已经有结论，进入处理阶段" |
| 回访状态提交 + 文档生成完成 | 已完结 | 表示"所有材料齐备，工单完结" |

**注意**：DRD 原文是"三系统查询完成、LLM分析完成、扣费明细生成后，自动变更为'处理中'"。但三系统查询在投诉受理时就已经完成，LLM 分析需要用户手动触发。所以触发点应为：**LLM 分析 + 扣费核算完成 → 自动处理中**

**实现方式**：
- `useWorkflow.js` 的 `doAnalyze` 成功后 → 调用 `updateTicketStatus(id, '处理中')`
- 回访状态提交 + 文档生成后 → 调用 `updateTicketStatus(id, '已完结')`
- 关键技术：复用已有的 `updateTicketStatus` 函数

### P2：状态回退（3.5.1）

- 增加一个"回退"按钮（仅"已完结"状态显示）
- 点击弹出回退原因输入框（必填）
- `PUT /api/tickets/:id` → `handle_status = '处理中'`
- 关键技术：弹窗组件 + API 调用

### P2：操作记录完善（3.5.2）

**当前状态**：`add_log` 已记录操作类型，但未记录状态变更前的值。

**优化**：状态变更时记录"从 X 变更为 Y，备注：Z"

### P3：数据看板增强（3.6）

| 子项目 | 说明 | 技术要点 |
|--------|------|---------|
| 趋势折线图（3.6.1） | 替换模拟数据为真实每日统计 | SQL GROUP BY complaint_date, echarts 折线 |
| 回访分布统计（3.6.1） | 依赖 3.4 回访数据 | SQL GROUP BY visit_status, echarts 饼图 |
| 数据导出 Excel（3.6.2） | `/api/tickets/export` 已有后端，前端按钮接入 | `openpyxl` 后端 + `<a>` 下载 |
| 明细联动（3.6.2） | 点击统计卡片 → 带筛选条件跳转列表 | `loadHist()` 传参 |
| 处理效率统计（3.6.1） | 已完结工单的平均处理时长 | SQL 算 `updated_at - created_at` |

### P3：系统配置完善（3.7）

| 子项目 | 说明 | 技术要点 |
|--------|------|---------|
| LLM 配置测试（3.7.1） | 保存前发测试请求验证 | 后端 `/api/feishu/test` 类似机制 |
| 业务参数：超时天数（3.7.2） | 默认 3 个工作日 | 新增 config 表字段 + 前端表单 |
| 业务参数：文档存储路径（3.7.2） | 可修改存储目录 | 字段已存在 `config.paths` |
| 管理员密码修改（3.7.4） | 原密码 + 新密码 + 确认 | 新增 `/api/config/password` API |

---

## 总时间预估

| 优先级 | 内容 | 预估 | 依赖 |
|--------|------|------|------|
| P0 | 回访管理 + 文档生成（3.4） | 3.5h | 需要附件2模板参考 |
| P1 | 特殊退费检测（3.3.4） | 1h | 无 |
| P1 | LLM 输出格式优化（3.3.2） | 1h | 需要用户确认输出格式 |
| P2 | 状态自动流转（3.5.1） | 0.5h | 需确认触发时机 |
| P2 | 状态回退（3.5.1） | 0.5h | 无 |
| P2 | 操作记录完善（3.5.2） | 0.3h | 无 |
| P3 | 数据看板增强（3.6） | 2h | 依赖 3.4 回访数据 |
| P3 | 系统配置完善（3.7） | 1h | 无 |
| **合计** | | **~10h** | |

---

---

## 技术实施细节

### 一、数据库 Schema 变更

当前 `complaint_tickets` 表需新增回访和新文档字段：

```sql
ALTER TABLE complaint_tickets ADD COLUMN visit_status TEXT DEFAULT '';     -- 回访状态: a/b/c/d
ALTER TABLE complaint_tickets ADD COLUMN visit_remark TEXT DEFAULT '';     -- 回访备注
ALTER TABLE complaint_tickets ADD COLUMN visit_time TEXT DEFAULT '';       -- 回访时间
ALTER TABLE complaint_tickets ADD COLUMN registration_form_path TEXT DEFAULT ''; -- 投诉登记表路径
ALTER TABLE complaint_tickets ADD COLUMN special_warnings TEXT DEFAULT '[]'; -- 特殊退费警告 JSON
ALTER TABLE complaint_tickets ADD COLUMN completed_at TEXT DEFAULT '';     -- 完结时间（计算处理时长用）
```

**迁移方式**：在 `database.py` 的 `init_db()` 中用 `PRAGMA table_info` 检查列是否存在，不存在则 ALTER TABLE ADD。**零风险，不丢失数据**。

---

### 二、新增 API 端点

| 方法 | 路径 | 用途 | 请求体 | 响应 |
|------|------|------|--------|------|
| `POST` | `/api/tickets/<id>/visit` | 保存回访状态 | `{visit_status: "a", visit_remark: "..."}` | `{success: true}` |
| `POST` | `/api/tickets/<id>/register-form` | 生成投诉登记表 | `{}`（从 ticket 数据自动填充） | `{success: true, filepath: "..."}` |
| `GET` | `/api/tickets/<id>/special-check` | 检测特殊退费情况 | 无 | `{warnings: [{type, message, reply_text}]}` |
| `POST` | `/api/config/check-llm` | 测试 LLM 配置 | `{api_url, api_key, model}` | `{success: true/false, message}` |
| `POST` | `/api/config/password` | 修改管理员密码 | `{old_pwd, new_pwd, confirm_pwd}` | `{success: true}` |
| `GET` | `/api/tickets/export` | 导出 Excel | `?start_date=&end_date=&status=` | 返回 `.xlsx` 文件流 |

已有接口直接复用：
- `PUT /api/tickets/<id>` — 状态更新，已实现
- `POST /api/reply/generate` — 回复函生成，已实现

---

### 三、文件变更清单

| 优先级 | 文件 | 操作 | 说明 |
|--------|------|------|------|
| **P0** | `database.py` | 修改 | `init_db()` 加 6 列；`add_log()` 加 `old_status → new_status` |
| **P0** | `app.py` | 修改 | 新增 3 个路由：visit / register-form / check-llm |
| **P0** | `services/visit_service.py` | **新建** | 回访状态保存 + 投诉登记表生成逻辑 |
| **P0** | `services/reply_service.py` | 修改 | `generate_reply` 支持传入 `special_warnings` 列表 |
| **P0** | `templates/index.html` | 修改 | 新增回访面板（处理流程第4步后）+ 文档预览/下载区 |
| **P0** | `static/js/composables/useWorkflow.js` | 修改 | 新增 `visitStatus`/`visitRemark` 响应式变量 + 提交/生成方法 |
| **P0** | `static/js/app.js` | 修改 | 暴露新增的变量和方法到模板 |
| **P1** | `services/contract_service.py` | 修改 | 新增 `_detect_special_cases()` + `_parse_ai_response()` 增强正则 |
| **P1** | `templates/index.html` | 修改 | AI 分析结果面板增加红色警告区 |
| **P2** | `services/contract_service.py` | 修改 | 优化 `analyze_contract()` 的 prompt 模板 |
| **P2** | `templates/index.html` | 修改 | 状态回退按钮 + 弹窗 |
| **P3** | `database.py` | 修改 | 新增每日统计 API 的真实数据 SQL |
| **P3** | `useHistory.js` | 修改 | 折线图替换模拟数据、Excel 导出按钮接入 |
| **P3** | `templates/index.html` | 修改 | 设置页增加业务参数和管理员密码表单 |
| **P3** | `config.py` | 修改 | 增加超时天数、文档路径等可配置字段 |

---

### 四、特殊退费检测实现细节

```python
# services/contract_service.py 新增
def _detect_special_cases(registration_date, exam_counts, skill_cert_date=None):
    """检测 4 种特殊退费情况，返回警告列表"""
    warnings = []
    today = datetime.now().date()
    three_years_ago = today - timedelta(days=3*365)

    # 注册日期 → 从 registration_date 解析
    reg_date = _parse_date(registration_date)
    
    # 1. 合同报名 > 3年
    if reg_date and reg_date < three_years_ago:
        warnings.append({"type": "contract_expired", "message": "⚠️ 合同报名时间已超3年，无费用退还", "reply_text": "合同报名已超3年，根据约定无退费"})
    
    # 2. 技能证 > 3年
    if skill_cert_date:
        cert_date = _parse_date(skill_cert_date)
        if cert_date and cert_date < three_years_ago:
            warnings.append({"type": "skill_expired", "message": "⚠️ 技能证时间已超3年，无费用退还", "reply_text": "技能证已超3年，根据约定无退费"})
    
    # 3. 科二不合格 > 5次
    k2_fails = exam_counts.get("科目二", 0)
    if k2_fails >= 5:
        warnings.append({"type": "k2_exceed", "message": "⚠️ 科目二已超5次不合格，无费用退还", "reply_text": "科二超过5次不合格，根据约定无退费"})
    
    # 4. 科三不合格 > 5次
    k3_fails = exam_counts.get("科目三", 0)
    if k3_fails >= 5:
        warnings.append({"type": "k3_exceed", "message": "⚠️ 科目三已超5次不合格，无费用退还", "reply_text": "科三超过5次不合格，根据约定无退费"})
    
    return warnings
```

**调用位置**：在 `analyze_contract_from_file()` 返回结果之前，将 `warnings` 注入到返回 dict 中。

---

### 五、LLM Prompt 优化方案

**当前 prompt**（`contract_service.py:263-281`）：要求 LLM 用自然语言分析合同。

**优化后 prompt**：在自然语言分析基础上，额外要求 LLM 输出一个结构化 JSON 片段：

```
【输出要求】
请按以下格式输出分析结果：
1. 合同内容分析（自然语言，保留当前格式）
2. 在分析完成后，单独输出一个 JSON 代码块：
```json
{
  "报名总费用": "4380元",
  "实际交费金额": "1500元", 
  "交费方式": "全款|分期付款",
  "扣费标准": [
    {"阶段": "未参加考试", "金额": "1700元", "项目明细": ["综合服务费1100元", "理论培训费600元"]}
  ],
  "合同有效期": "3年",
  "退费约定": "..."
}
```
```

**后端解析**：`_parse_ai_response()` 优先从 JSON 块提取结构化数据作为权威来源，自然语言分析保留为 `raw_analysis`。

---

### 六、回访面板 UI 定位

建议在处理流程卡片中新增 **"步骤 5：回访与归档"**，放在现有 4 步之后：

```
┌─ 处理流程 ────────────────────────────────────────┐
│  ①合同 → ②AI分析 → ③回复函 → ④飞书 → [⑤回访归档] │
│                                                       │
│  ┌─ 回访状态 ──────────────────────────────────┐   │
│  │  [a.拒绝协商] [b.无法联系] [c.同意协商] [d.其他] │   │
│  │  备注：[___________________________] (必填)   │   │
│  │  [提交回访]                                    │   │
│  └──────────────────────────────────────────────┘   │
│                                                       │
│  ┌─ 文档生成 ──────────────────────────────────┐   │
│  │  [📄 生成投诉登记表]  [📄 生成投诉回复函]    │   │
│  │  ✅ 投诉登记表.docx  [下载]                  │   │
│  └──────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────┘
```

**逻辑**：只有在回访状态提交后才显示文档生成区；根据回访状态自动勾选需生成的文档。

---

### 七、错误处理策略

| 场景 | 处理方式 |
|------|---------|
| 回访提交 API 失败 | Toast 提示"保存失败"，不清空表单 |
| 投诉登记表生成失败 | 返回 JSON error，前端红色提示 |
| LLM 配置测试超时 | 10 秒超时，提示"配置校验超时，请检查 API 地址" |
| 特殊退费检测中日期解析失败 | 跳过该条检测，不报告警告（不误报） |
| Excel 导出数据为空 | 返回带表头的空白 .xlsx，不报错 |

---

### 八、集成测试清单

| 测试项 | 预期行为 |
|--------|---------|
| 回访提交 | 选择状态 + 填写备注 → 提交 → 数据库更新 → 文档生成区显示 |
| 投诉登记表生成 | 点击"生成" → 等待 → 显示下载链接 → 下载的文件内容正确 |
| 特殊退费检测 | 用合同>3年的学员测试 → 显示红色警告 → 回复函自动附带说明 |
| 状态自动流转 | AI分析完成 → 状态变"处理中" → 回访+文档完成 → 变"已完结" |
| 状态回退 | 已完结工单 → 点击回退 → 输入原因 → 状态变回"处理中" |
| 趋势图数据 | 选择 "本月" → 折线图显示真实每日统计 |
| LLM 配置测试 | 输入正确配置 → 点击测试 → 显示"配置有效"；输入错误 → 显示错误原因 |

---

## 需用户确认的问题

1. **状态自动流转的触发时机**：是否按"LLM分析+扣费完成→处理中，回访+文档完成→已完结"？
2. **回访状态面板放在哪个位置**？放在处理流程卡片的第 3 步（回复函）与第 4 步（飞书）之间？
3. **投诉登记表（附件2）模板**：是否有参考文件？还是按 DRD 3.4.3 描述从代码中构建模板？
4. **投诉回复函（附件3）模板**：当前系统已有回复函模板，是否和 DRD 的附件 3 一致？还是需要按 DRD 描述重建？
