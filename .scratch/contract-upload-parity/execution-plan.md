# 执行章程：上传合同功能落地（contract-upload-parity）

**Status:** active — 2026-09-01 拍板放行（四项决定：引擎另起炉灶 / 违约金基数求和除代缴 / 补「合同份」词条 / 节奏=阶段 0+0.5 后停下审批）
**来源：** spec.md + 2026-09-01 进度审计（基线 `pytest tests/ -q` = 342 passed / 3 failed）+ grill-with-docs 拍板
**角色：** 主 Agent（GLM）= 监工：拆解、分配、审查 diff、跑回归、维护工单状态、向用户汇报。用户 = 最终审批人，每阶段收尾放行下一阶段。

## 1. Done 的定义（「能用、好用、无 bug」的可验证形态）

- [ ] 六档 × 场景矩阵表驱动测试全绿（02 的验收矩阵）
- [ ] spec.md 22 条 User Stories 逐条可演示（E2E 或人工走查）
- [ ] 全量 pytest 0 failed（基线 342，逐阶段只增不减）
- [ ] 浏览器 E2E：errors/console 全空、importmap 差集为空、静态版本号已 bump
- [ ] 下载链路护栏用例全绿（「本期不碰下载链路」是可验证承诺）
- [ ] 真实样张人工验收：2019 三份、2023 分店扫描件至少各一单，由经办人实际走一遍

## 2. 阶段划分（每阶段收尾 = 全量回归 + 汇报表 + 用户审批放行）

| 阶段 | 内容 | 依赖 | 分工 |
|---|---|---|---|
| 0 | 拍板落地：ADR-0003、CONTEXT.md 词典补丁（合同份/违约金基数）、spec 措辞统一、tracker 状态校正 | 用户拍板 | 主 Agent |
| 0.5 | 00-preflight：img2pdf 依赖、测试隔离、护栏换对象 → 3 条失败转绿 | 0 | 主 Agent（+subagent 复核） |
| 1 | 02 扣费引擎（纯函数，表驱动六档矩阵，TDD）——按 ADR-0003 另起炉灶 | 0.5 | **主 Agent 亲自写（涉钱）** |
| 2 | 04 管线串联：`identify_tier` 接入 `analyze_contract_from_file` + 缓存键产出 + 下载链路护栏 | 1 | 主 Agent |
| 3 | 05 偏移量锚定 + 抽取升级（prompt 锚点短语、anchor_missing） | 2 | subagent(default) 实现 + 主 Agent 审查 |
| 4 | 06 三栏预览（demo 像素基准） | 3 | **单一前端 subagent 串行**（importmap 陷阱） |
| 5 | 07 闸门 / 08 改档缓存 / 09 页图缓存 | 4 | 可并行 2-3 个 subagent |
| 6 | 10 多份汇总 + 端到端验收（跑完 Done 门六条） | 5 | 主 Agent |

## 3. 每张工单的三道门（缺一不算完成，主 Agent 复核后改 Status）

1. **TDD**：先红后绿；新增测试数写入交付表
2. **code-review 双轴**：Standards + Spec——对照工单验收标准逐条勾
3. **全量回归**：`env -u PYTHONPATH ./venv/bin/python3 -m pytest tests/ -q` 0 failed；前端另加 `node --check` + 浏览器 E2E（errors/console 清空）

交付体例（项目铁律）：**改了什么 / 有什么用 / 注意事项** + 证据（文件:行号、断言数、命令输出），不接受口头确认。

## 4. 团队拓扑与模型现实

- **subagent 可用**：探索（Explore）、实现（general-purpose）、审查均可分配；模型档位只有 default / lite / reasoning 三档，**无法指定 Deepseek-V4-Flash 等第三方模型**。若业务要用 Deepseek，正确接入位置是应用层 LLM provider（`services/contract_service._llm_config` 换 provider），不是 subagent 模型。
- **档位分配**：机械任务（fixture、文档、依赖修复）→ lite；实现任务（05/07/08/09 后端）→ default；复杂判定/审查 → reasoning 或主 Agent 自任。**涉钱核心（02/04）主 Agent 亲自写。**
- **并行度真相**：02→04→05→06 是串行主干，能并行的只有阶段 5；`app.py` 单文件 + 前端 importmap 陷阱 → 多 subagent 同时改 `app.py`/`index.html` 必然冲突。团队价值在分工与审查，不在主链提速。
- **subagent 纪律**：每次 spawn 是全新上下文，prompt 必须自包含（工单文件路径 + 本章程第 6 节红线 + 验收标准）；交付回传后主 Agent 必须复核实际 diff，不信摘要。

## 5. 回测策略（「做好回测」的落地）

- 每工单：新增测试 + 全量 pytest（基线 342，只增不减）
- 每前端工单：`node --check` + importmap 差集自检 + agent-browser E2E（先 `console --clear`/`errors --clear`，操作后两者皆空）
- 里程碑（阶段 2/4/6 收尾）：真实合同样张验收（`1合同种类/` 七份模板 + 至少一份真实扫描件）
- 下载链路护栏用例常驻测试套件

## 6. 红线（subagent prompt 必须内嵌）

- 一切 import config 的脚本/测试必须 `env -u PYTHONPATH`；启动 `run_in_background:true env -u PYTHONPATH ./venv/bin/python3 app.py`
- 改 `templates/index.html` 前查 `data-page-node-id` 残留；改 CSS/JS 必须 bump 静态版本号 + importmap 自检
- `.scratch/` 被 git 跟踪，**禁止批量删**；共享盘禁递归遍历、禁删
- 生产 DB = `data/complaints.db`；备份必须 SQLite `backup()` API；配置表（users/org_vehicle_counts/reply_templates/migration_flags）永远不清
- 服务器端 OS 操作只作用于服务器本机（远程访问者走浏览器端方案）
- 变量名避开 `sc =`；Agent HTTP 绕代理 `curl --noproxy '*'`
