# 技术方案 - 手动填写流程重构

## 需求整理

### 1. 考试费计算逻辑

```python
def calc_exam_fees(exam_counts, contract_includes_exam=False):
    """
    计算考试费
    
    exam_counts: { "subject1": 2, "subject2": 1, "subject3": 0 }
    contract_includes_exam: 合同是否包含考试费
    
    费用标准：
    - 科目一：70元/次，补考35元
    - 科目二：130元/次，补考65元  
    - 科目三：280元/次，补考140元
    - 工本费：10元（固定）
    
    规则：
    - 第一次考试 = 正常费用
    - 第二次及以后 = 补考费（一半）
    - 如果合同包含考试费，则不计入应扣
    """
    if contract_includes_exam:
        return 0, []
    
    fees = {
        "subject1": (70, 35),  # (正常, 补考)
        "subject2": (130, 65),
        "subject3": (280, 140),
    }
    
    total = 10  # 工本费
    details = [{"item": "工本费", "amount": 10}]
    
    for subject, count in exam_counts.items():
        if count <= 0:
            continue
        normal, retake = fees[subject]
        # 第一次正常费用，后续补考费
        subject_total = normal + (count - 1) * retake
        total += subject_total
        details.append({
            "item": f"{subject}考试费({count}次)",
            "amount": subject_total
        })
    
    return total, details
```

### 2. 手动填写页面重构

**现状**：简单的几个输入框
**目标**：和AI分析后的扣费明细表一致

**页面结构**：
```
┌─────────────────────────────────────┐
│ 手动填写退费计算                      │
├─────────────────────────────────────┤
│ 合同信息                              │
│   - 合同编号（可选）                   │
│   - 合同总金额（用户填写）              │
│   - 已交费用（用户填写）                │
│   - 合同是否包含考试费（复选框）         │
├─────────────────────────────────────┤
│ 培训时长（从第三系统自动获取）          │
│   - 科目二培训时长：XX小时             │
│   - 科目三培训时长：XX小时             │
├─────────────────────────────────────┤
│ 考试情况（从内部系统自动获取）          │
│   - 科目一考试次数：X次               │
│   - 科目二考试次数：X次               │
│   - 科目三考试次数：X次               │
├─────────────────────────────────────┤
│ 扣费明细表（用户可增删改）              │
│   - 必扣项：报名费、档案费、IC卡费等    │
│   - 培训费：按实际培训时长计算          │
│   - 考试费：根据考试次数自动计算        │
│   - 违约金：合同总金额 × 比例          │
├─────────────────────────────────────┤
│ 计算结果                              │
│   - 应扣总额：XXX元                   │
│   - 应退金额：XXX元                   │
└─────────────────────────────────────┘
```

### 3. 扣费明细表字段

| 字段 | 说明 | 来源 |
|------|------|------|
| 扣费项目 | 下拉选择或自定义 | 用户 |
| 金额 | 数字 | 用户填写 |
| 计算依据 | 文本说明 | 自动或用户填写 |
| 是否必扣 | 是/否 | 标记 |
| 操作 | 删除/编辑 | 用户 |

### 4. 自动计算逻辑

```javascript
// 应扣总额 = 所有扣费项目金额之和
// 应退金额 = 已交费用 - 应扣总额 - 违约金

function calcRefund(paid, deductions, penaltyRate, totalFee) {
    const totalDeduction = deductions.reduce((sum, d) => sum + d.amount, 0);
    const penalty = totalFee * penaltyRate;
    const refund = paid - totalDeduction - penalty;
    return { totalDeduction, penalty, refund };
}
```

## 实施步骤

1. 修改手动填写页面结构（复用AI分析后的组件）
2. 添加考试费自动计算逻辑
3. 添加合同是否包含考试费的选项
4. 修改扣费明细表支持用户编辑
5. 添加培训时长自动获取
6. 添加计算结果实时更新

## API 修改

```python
# 获取培训时长和考试次数
@app.route("/api/student/exam-info", methods=["GET"])
def api_student_exam_info():
    id_card = request.args.get("id_card")
    # 从内部系统查询考试次数
    # 从第三系统查询培训时长
    return _ok({
        "subject1_exams": 2,
        "subject2_exams": 1,
        "subject3_exams": 0,
        "subject2_training_hours": 10,
        "subject3_training_hours": 5,
    })
```
