# ISS-AP-05 · 前端：入口改造 + `open-archive` 收敛

**Status:** pending（等 ISS-AP-04）
**Priority:** P1
**依赖:** ISS-AP-04
**并行:** ❌ 串行

## 需求

入口文案与行为要跟新语义对齐：按钮说「查看归档文件」，点了就在网页里看到文件。
现状文案是「打开归档」/「打开该学员的归档文件夹」/「在系统文件管理器中打开该学员的归档文件夹」——**全部承诺了"会弹出资源管理器"**，而方案 A 不再弹。不改就是预期背叛（人类判据第 3 条）。

## 改动点

### A. 模板文案与图标（`templates/index.html`）

| 位置 | 现状 | 改为 |
|---|---|---|
| L989 行内按钮 | `class="row-open"` `title="打开该学员的归档文件夹"` `bi-folder2-open` | `class="row-view-files"` `title="查看该学员的归档文件"` `bi-files` |
| L1012 批量条按钮 | `打开归档` / title「在系统文件管理器中打开该学员的归档文件夹」 | `查看归档文件` / title「查看该学员的归档文件（下载 / 打包 / 预览）」 |
| L597-598 `.row-open` 样式 | — | 重命名为 `.row-view-files`（保留原有 hover 配色） |

### B. 行为（`useWorkbench.js` `openArchiveSelected`）

保持"单选生效"的既有约束不变（`clSelectedIds.length !== 1` 时按钮已置灰），只把最终动作指向新面板。

### C. `POST /open-archive` 收敛（`app.py:3842-3880`）

改为**仅本机可用**：
- 保留 `_request_from_server_host()` 判断（L3824-3839）；
- 非本机来源 → `403` + `{"success":false,"error":"该功能仅限服务器本机使用"}`；
- **删掉** `to_unc_path()` 调用与 `mode=remote` 返回分支（前端已不再消费）；
- 本机分支保持 `open_case_dir()` → Finder。

> 依 spec.md **D2** 决策：面板里**仅本机**显示「在服务器上打开文件夹」次要按钮，远程不显示。
> 若 D2 选 (a) 纯面板，则本单连 `/open-archive` 一起删除。

## 验收标准

- [ ] 列表行按钮文案/图标/title 均已更新，`.row-open` 旧类名全项目 0 残留
- [ ] 批量条文案更新，单选时行为正确
- [ ] 远程来源调 `/open-archive` → 403（用 `test_client` 伪造 `remote_addr` 断言）
- [ ] 本机来源调 `/open-archive` → 200 + 实际打开（用 monkeypatch 打桩 `open_case_dir`，不真弹 Finder）
- [ ] 面板里「在服务器上打开文件夹」按钮：本机可见、远程不可见（若 D2 选 b）
- [ ] `pytest tests/test_open_archive_api.py -v` 全绿（用例随语义重写）
- [ ] 静态版本号 bump + 提醒强刷

## 风险

- **中**：`.row-open` 类名在 `templates/index.html` 内联样式（L597）与按钮（L989）两处，改名必须同步；建议用一次性 Python 脚本 `count==1` 校验后替换（项目既有做法，见 `tmp/patch_*.py`）。
- **低**：`open-archive` 收敛只影响本机路径。

## 备注

D2 若选 (a)（不留"打开文件夹"按钮），则 `open_case_dir()` / `_request_from_server_host()` 一并进入 ISS-AP-06 的清理清单。
