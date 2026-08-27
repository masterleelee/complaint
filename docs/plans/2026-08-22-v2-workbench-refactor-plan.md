# v2 工作台改造实施计划（多Agent执行版）

> 版本：2026-08-22 · 依据：`docs/plans/2026-08-22-v2-simplification-plan.md`（16项决议）+ 原型 `static/prototype/v2-demo.html` 定稿 + 两轮用户修订
> 目标：将现有系统改造为原型所示的「布局C三栏工作台」，真实落地投诉处理全流程。
> 执行模式：统筹Agent（会话主控）+ 实施Agent×N（Task general，按文件所有权分派）+ 回测Agent（独立验收）。所有Agent遵守 AGENTS.md 的 karpathy-guidelines：最小改动、不越界重构、每步可验证。

---

## 1. 目标蓝图（验收基准 = 原型）

### 1.1 页面结构（四视图 SPA）
| 视图 | 内容 | 对应原型 |
|---|---|---|
| 案件列表 | KPI卡（累计/在途/已归档/撤诉）、状态筛选、工单表、导出Excel | vList |
| 新增受理 | 粘贴/上传→提取身份证手机号→登记表单→「查询三系统并建案」 | vIntake+vQuery |
| 三系统查询过程 | 内部/第三/驾培三卡并行动画 → 自动进入工作台 | vQuery |
| 案件工作台（核心） | 中央：②查询五页签 ③明细表 ④函件编辑器 ⑤处理情况+配合度 ⑥归档；右栏：学员档案 / 办理时间线 / 在途案件折叠 | caseC |
| 统计看板 | KPI、单位排行(有效+撤诉)、月趋势、类型环图、配合度汇总、百车投诉率、明细表 | vDash |

### 1.2 关键行为决议（不可偏离）
- 受理即入统计；撤诉≠有效投诉但记录保留；撤诉按钮常驻任意状态可用
- 归档三闸门：处理情况已填 + 配合度已评 + 明细已确认；回复函非必需
- 归档一键直达：写入 `{root}/{分校|分店}/{代号-全称}/{日期}_{姓名}_{身份证}_{代号}/` 并打开文件夹；夹内两件套 `日期_姓名_投诉登记表.docx`、`日期_姓名_投诉回复函.docx`
- 回复函：致东莞市交通运输局；标题居中加粗19px；正文首行缩进2em；落款右对齐=撰写当天日期；扣费编号清单动态读取明细行；富文本工具栏(B/I/U/对齐/字号/撤销)；AI润色；明细变更自动同步或提示重排
- 明细表：屏显可编辑+手动兜底+确认/解锁+预览合同对照弹窗（点击扣费项高亮条款）
- 处理情况：随手记→AI优化为四点式正式表述；配合度 ✓绿/—黄/✕红 圆标
- 删除项：飞书、操作日志页、登记表LLM总结、多轮沟通记录、费用方案三态版本、六类固定结果强制

## 2. 现状 → 目标 差距清单（代码映射）

### 后端（app.py ~2400行 / database.py ~1500行 / services/ / core/）
| 现状 | 动作 | 涉及 |
|---|---|---|
| feishu_service + /api/feishu/* | **删除** | services/feishu_service.py、app.py 路由、config 节、前端引用 |
| /api/logs/recent + 日志页 | **删除**（utils/logger 保留给后端） | app.py、index.html 导航 |
| visit_llm.py（LLM总结） | **删除**；visit_service 改读 handling_notes | services/visit_llm.py、visit_service.py |
| communications 五字段多轮 | **删除**路由与表；存量迁入 handling_notes | app.py 3条路由、database.py、前端区块 |
| fee-confirm/fee-reopen 版本留痕 | 简化为保存行集+confirmed 标记；新增 unlock | app.py、core/case_workflow.py、database.py |
| final_outcome 六类强制闸门 | 取消强制；归档闸门改为三条件 | app.py、case_workflow.py |
| withdraw-status 独立字段 | 改造为常驻撤诉标记（任意状态可标） | app.py、database.py |
| statistics 接口 | 有效投诉排除撤诉单独列；新增类型维度；保留时长与百车投诉率 | app.py /api/statistics* |
| 登记表生成 | 数据源改 handling_notes；受理时生成 | services/visit_service.py（更名 register_form_service 可选） |
| （新增）reply_docx 服务 | 按 default_template.docx 占位符生成致交通局函（含格式） | services/reply_docx.py 新建 |
| （新增）archive 服务 | 闸门校验→建夹→写两件套→打开文件夹 | services/archive_service.py 新建 |

### 前端（templates/index.html + static/js/composables/）
| 现状 | 动作 |
|---|---|
| 旧多页签工作台（stepper 5步旧流程） | 重构为四视图；工作台采用布局C骨架 |
| composables 现状未逐一盘点 | 先由回测Agent盘点输出清单，再按新职责拆分：useCaseList/useIntake/useQueryPanel(5页签)/useFeeDetail(编辑+合同对照)/useReplyLetter(富文本+同步)/useNotesCoop/useArchiveGate/useWithdraw/useDashboard |
| 视觉 | 沿用 DESIGN.md Token（原型CSS可直接移植） |

## 3. 阶段划分（Wave）与并行策略

| Wave | 任务 | 文件所有权（防冲突） | 并行度 | DoD |
|---|---|---|---|---|
| W1 | 后端删除飞书+日志路由；配置清理 | 实施A：app.py/config.py/services/*.py | A∥B（B只碰前端） | grep 无 feishu/logs-recent 残留；compileall 通过；pytest 全绿 |
| W1 | 前端删除对应入口与页面区块 | 实施B：templates/index.html + static/js/** | 同上 | 同上 + node --check 所有改动 js |
| W2 | database.py：tickets 加 handling_notes/withdraw 字段；fee_details 单表化；迁移脚本（communications→notes） | 实施C：database.py + 新 migrations | 单线 | 迁移脚本幂等；pytest 全绿 |
| W2 | app.py：费用接口简化/unlock、withdraw PUT、statistics 口径 | 实施D：app.py（依赖W2-C合并后启动） | 单线 | 新旧接口冒烟；pytest 全绿 |
| W3 | 前端四视图重构：先由统筹搭 Workbench 主骨架与路由壳，再分派 | 统筹：index.html 骨架；实施E/F/G/H 分领 composables | E∥F∥G∥H（不同 js 文件） | 四视图可达；无 console 错误；桌面/窄幅不破版 |
| W4 | reply_docx.py 与 archive_service.py 两个新服务 | 实施I：reply_docx；实施J：archive_service | I∥J（不同新文件） | 用样例案件生成 docx 打开核对格式；归档目录结构与命名符合规范 |
| W5 | 看板六模块接入真实统计接口 | 实施K：statistics 前端+接口联调 | 单线 | 看板数字与明细表一致；撤诉口径正确 |
| W6 | 全量回测 | 回测Agent（只读）：需求矩阵逐条核对 + pytest + 冒烟清单 | — | 缺陷清单清零或挂账说明 |

## 4. Agent 编制

| 角色 | 职责 | 约束 |
|---|---|---|
| 统筹（主控会话） | 拆解派发、依赖排序、合并验收、上下文分发、DoD 把关 | 不直接写业务代码（除骨架搭建） |
| 实施A~K（general Task） | 按所有权领取单文件/模块任务 | karpathy 最小改动；禁越权改他人文件；完成必须跑测试 |
| 回测（explore/general 只读） | 对照本计划 §1.1/§1.2 逐条核验 diff 与运行时行为；产出缺陷清单 | 只报告不修改 |

冲突规则：同一文件同一 Wave 只允许一个实施Agent持有；跨 Wave 串行。

## 5. 全局验收标准（交付定义）
1. 原型 §1.1 四视图全部可用，工作台与原型视觉一致（信赖蓝 Token）
2. 一个案件从受理到归档全程无死路：无合同→手动明细；AI失败→手改；撤诉→随时可标
3. 归档产物严格两件套且目录命名100%符合规范；归档后自动打开文件夹
4. 函件格式：致交通局、标题居中加粗、正文缩进2em、落款右对齐当天日期、明细清单动态同步
5. 看板六模块数据与明细一致，撤诉不计有效投诉
6. 全量 pytest 绿；被删功能零残留（grep 审计）；JS 语法检查通过

## 6. 风险与回退
- index.html 为单体大文件，W3 采用「骨架先行、组件分领」降低冲突；每次合并后跑冒烟
- 数据迁移（communications→handling_notes）提供幂等脚本与回退说明
- 三系统爬虫/OCR/规则引擎为既有资产，本计划不改其内部逻辑，仅换调用入口
