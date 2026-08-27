# 测试数据清单（Agent-A 数据准备官R）

> 更新时间：2026-08-23 12:40 · 数据源：`data/complaints.db`（生产库）
> 本轮操作前快照：`.scratch/backup/complaints.db.20260823-123918.bak`（507KB，含 163 条工单）

## 一、数据安全与隔离（红线执行记录）

| 项目 | 状态 |
|---|---|
| 操作前备份 | ✅ `.scratch/backup/complaints.db.20260823-123918.bak` |
| 配置文件 | ✅ 未改动 `data/config.json`（三系统凭据原样） |
| 写入方式 | 直连 SQLite 显式 ID 插入，未触碰任何既有工单 |
| 脏数据标识 | 全部新增工单 `id/ticket_no` 以 `QA-` 前缀 + `source_channel='QA测试'` 双重标记 |

**清理命令（回归结束后由 lead 决定执行）：**
```sql
DELETE FROM complaint_tickets WHERE id LIKE 'QA-%' OR source_channel LIKE 'QA测试%';
```
清理后核对：总工单数应回到 **163**。

## 二、现有数据盘点（插入前快照，共163条）

### 表清单

| 表 | 行数 | 说明 |
|---|---|---|
| complaint_tickets | 163 → 现176 | 主工单表（60+字段，含费用/合同/归档全字段） |
| operation_logs | 1097+ | 操作日志 |
| org_vehicle_counts | 38 | 网点车辆数配置（分校38个，已初始化） |
| contract_analysis_jobs | 5 | 合同分析异步任务表 |
| reply_templates | 1 | 默认回复模板 |
| complaints_v1 / migration_flags / visit_records / contract_cache | 少量 | 辅助表 |

### 关键分布（插入前）

- 状态：待处理57 · 处理中37 · 已完结63 · （无"已归档"独立状态，用 archive_status 字段表达）
- 合同覆盖：有合同仅10条 / 无合同153条 → **合同缺失场景天然充足**
- 费用锁定：fee_plan_status=confirmed 6条 · draft 4条 · 空153条
- 渠道：交通部门60 / 邮件27 / 其他27 / 驾培协会25 / 电话19

### 可复用的真实样例工单（生产既有，只读使用）

| 编号 | 工单ID | 学员 | 用途 | 覆盖场景 |
|---|---|---|---|---|
| R-01 | fa8aac03-…b682 | 测试学员乙 | 正常全流程参照（有合同码 DGJP202508240171、真实三系统档案、已有回复函） | query→contract→fee→reply 全链路正向样本 |
| R-02 | e7e2c589-…ef19 | 王勇标 | 费用已确认（confirmed） | 幂等锁费/重复确认拦截 |
| R-03 | eecbd13d-…2585 | 测试学员乙(副本) | 同证件号双工单 | 重复投诉识别、同日去重逻辑 |
| R-04 | eb1698f6… | 黄鉴溏 | total_fee=0 | 零金额边界（存量） |
| R-05 | fd5f0ac2… | 李四 | 已完结 refund=3000 | 完结单只读校验 |
| R-06 | fba01bdb… | 王五 | 中途退费1000/2680 | 部分消费历史案例 |

## 三、本轮新增构造数据（13条，均已入库且API可见✅）

> 所在位置均为 `data/complaints.db` 表 `complaint_tickets`，按 `id` 精确查询即可。
> 物理测试文件位于 `.scratch/test-files/`。

### A. 正常学员全流程（1条）

| 编号 | 类型 | 用途 | 覆盖场景 | 所在位置 |
|---|---|---|---|---|
| D-01 | QA-FLOW-001 | 全流程正向主样本 | 复用真实证件号5128…4055（测试学员乙），三系统可返回真实学籍；待处理+无合同，供 intake→query→contract→fee→reply→archive 全链路实测 | DB·complaint_tickets·QA-FLOW-001；物理文件无需 |

### B. 合同异常（3条）

| 编号 | 类型 | 用途 | 覆盖场景 | 所在位置 |
|---|---|---|---|---|
| D-02 | QA-HT-MISS-001 | 合同缺失分支 | contract_path 为空 → 查询/分析/回复函对缺合同的降级处理 | DB·QA-HT-MISS-001 |
| D-03 | QA-HT-BAD-001 | 合同损坏分支 | contract_path 指向截断损坏PDF（有头无体）→ 解析失败报错路径 | DB·QA-HT-BAD-001 + 文件`.scratch/test-files/corrupted-contract.pdf` |
| D-04 | QA-HT-NOTPDF-001 | 非合同文件分支 | contract_path 指向纯文本txt → 格式校验拒绝 | DB·QA-HT-NOTPDF-001 + 文件`.scratch/test-files/not-a-contract.txt` |
| D-04b | （辅助）正常上传样本 | 上传链路正向对照 | 最小合法单页PDF，供 `/api/contract/upload` 正常上传实测 | 文件`.scratch/test-files/qa-sample-contract.pdf` |

### C. 三系统查询异常（2条）

| 编号 | 类型 | 用途 | 覆盖场景 | 所在位置 |
|---|---|---|---|---|
| D-05 | QA-SYS-FAKE-001 | 查无此人分支 | 虚构合法格式证件号110101199003070011 → 三系统业务层"未查询到"错误 | DB·QA-SYS-FAKE-001 |
| D-06 | QA-SYS-BADID-001 | 参数校验失败 | 非法格式证件号ABC12345XYZ → 入参校验拦截 | DB·QA-SYS-BADID-001 |
| C-注 | 超时场景 | 说明项 | 生产配置不可安全模拟网络超时；建议 Agent-B 在 `tests/` 用无效 base_url 隔离单测模拟，或以 D-05 观察慢查询路径，不另造数据行 | — |

### D. 特殊退费计算场景（6条，预置 deduction_detail JSON 已验证合法）

| 编号 | 类型 | 用途 | 覆盖场景 | 预置数值(total/paid/deduction/refund) | 所在位置 |
|---|---|---|---|---|---|
| D-07 | QA-FEE-KM-001 | 已考科目扣费 | 科目一培训600+考试100+科目二培训800=扣1500退2380 | 3880/3880/1500/2380 | DB·QA-FEE-KM-001 |
| D-08 | QA-FEE-STAGE-001 | 超期未学扣费 | 报名超期收超期管理费500 | 3700/3700/500/3200 | DB·QA-FEE-STAGE-001 |
| D-09 | QA-FEE-INSTALL-001 | 分期付款 | 仅缴2000/4200，扣700退1300（paid<total 分支） | 4200/2000/700/1300 | DB·QA-FEE-INSTALL-001 |
| D-10 | QA-FEE-FULL-001 | 全额退款 | 扣费0元全额退3000（deductions=[amount:0]） | 3000/3000/0/3000 | DB·QA-FEE-FULL-001 |
| D-11 | QA-FEE-ZERO-001 | 零金额边界 | total=0且paid=0 → fee-confirm 应400拒绝（actual_paid≤0校验） | 0/0/0/0 | DB·QA-FEE-ZERO-001 |
| D-12 | QA-FEE-OVER-001 | 扣费超额钳制 | 扣费合计2500>总额2000 → min()钳制后退0 | 2000/2000/2500/0 | DB·QA-FEE-OVER-001 |

> 计算规则依据 app.py:863-865：`total_deduction=min(sum(deductions), total_fee)`；`refund=max(0, paid-deduction)`。

### E. 撤诉/闭环状态（1条）

| 编号 | 类型 | 用途 | 覆盖场景 | 所在位置 |
|---|---|---|---|---|
| D-13 | QA-WITHDRAW-001 | 已撤诉工单 | withdraw_status=已撤诉+withdrawn_at 有值 → 统计口径（有效投诉剔除）、撤诉展示、已完结时长统计 | DB·QA-WITHDRAW-001 |

## 四、遗留脏数据登记（上一轮残留，待统一清理）

| 工单ID | 学员名 | 问题 | 处置建议 |
|---|---|---|---|
| QA-API-001~004 | QAAPI测试一/二/三 | 上一轮 API 测试残留，其中 QA-API-004 姓名/电话为空疑似残缺记录 | 回归结束一并删除（上面清理SQL已覆盖） |
| 6c8d42a5-b824-4525-97a2-a6aa156129b7 | （空） | source_channel='QA测试渠道' 残留 | 同上 |

## 五、数据统计口径（插入后）

- 总工单数：**176**（163 原始 + 13 新增）
- QA 标识工单：17（13 新增 + 4 上轮残留）；另有 1 条 QA 测试渠道残留
- 新增数据覆盖测试场景数：**12 类**（全流程正向、合同缺失、合同损坏、非合同文件、查无此人、参数非法、科目扣费、超期扣费、分期付款、全额退、零金额边界、超额钳制）＋撤诉状态 1 类 = **13 类**
