# ISS-AP-06 · 清理：kjfolder 链路冻结/删除 + 死代码

**Status:** pending（等 ISS-AP-05 上线验证通过）
**Priority:** P2
**依赖:** ISS-AP-05（必须确认方案 A 覆盖全部场景后才动）
**并行:** ❌ 串行，且**高风险必须单独审批**

## ⚠️ 前置

本单是**能力移除**，不是重构。生产上已有 Windows 客户端装过归档助手
（`.scratch/open-archive-diagnosis-20260901.md` §5：客户端 192.168.1.188 于 12:01 下载助手、
14:09 重装 v2）。**删掉后那些机器的一键弹文件夹能力将消失。**

因此本单分两级，**按 spec.md D1 决策执行**：

- **D1=(b) 冻结（推荐）** → 只做 §B 死代码清理，§A 全部保留不删（代码留着、路由留着、不再维护、不再出现在 UI 上）
- **D1=(a) 全删** → §A + §B 全做

---

## §A 冻结/删除清单（仅 D1=(a) 时执行）

### 后端（约 226 行）

| 资产 | 位置 | 行数 |
|---|---|---|
| `HELPER_VBS` | `services/archive_service.py:311-397` | ~87 |
| `_HELPER_BAT_TEMPLATE` | `services/archive_service.py:399-437` | ~39 |
| `generate_helper_bat()` | `services/archive_service.py:440-446` | 7 |
| `_DIAGNOSE_BAT_TEMPLATE` | `services/archive_service.py:449-524` | ~76 |
| `generate_diagnose_bat()` | `services/archive_service.py:527-536` | 10 |
| `GET /kopen-helper` | `app.py:3883-3902` | 20 |
| `GET /kopen-helper.vbs` | `app.py:3905-3917` | 13 |
| `GET /kopen-diagnose` | `app.py:3920-3939` | 20 |
| `app.py` 顶部 import | `app.py:69-74`（`generate_helper_bat` / `generate_diagnose_bat` / `HELPER_VBS`） | 3 行 |

### 前端（`useWorkbench.js`）

| 资产 | 位置 |
|---|---|
| `_utf8ToBase64Url()` | L493-498 |
| `_tryKjProtocol()` | L500-506 |
| `_watchBlurThenResolve()` | L508-521 |
| `kjHelperModalOpen` / `kjUncPath` / `kjServerDir` | L489-491 |
| `copyKjUnc()` | L523-536（若面板还要"复制路径"则改造保留，否则删） |
| 兜底弹窗模板 | `templates/index.html:2550-2585`（36 行） |
| 对应 return 项 | `useWorkbench.js:1053` / `app.js:290` / `app.js:946` |

### 测试

| 文件 | 处理 |
|---|---|
| `tests/test_remote_archive_helper.py`（**20 例**） | 整体删除（被测对象已不存在）；其中 `to_unc_path` 相关若保留 UNC 能力则迁移保留 |
| `tests/test_archive_service.py::test_open_folder_darwin` | 随 `open_folder` 参数删除 |

---

## §B 死代码清理（**两级都执行**，低风险）

| 资产 | 位置 | 证据 |
|---|---|---|
| `archive_case(..., open_folder=)` 的 `open_folder=True` 分支 | `services/archive_service.py:178-190`（13 行） | **确证死代码**：唯一调用点 `app.py:3774` 恒传 `open_folder=False`；仅 `tests/test_archive_service.py:122` 在跑 |
| `kjFilesDir` 死变量 | `useWorkbench.js:547`（+564 赋值 +1054 return） | 全项目无引用，`audit.md` §1.3 已确证 |

**处理方式**：删除 `open_folder` 形参与分支，同步删除 `test_archive_service.py::test_open_folder_darwin`，
并更新 `app.py:3774` 的调用（去掉冗余实参）。

---

## 验收标准

- [ ] D1=(b)：上述 §A 资产**全部仍在**且 `pytest tests/ -v` 全绿，UI 上无任何入口暴露它们
- [ ] D1=(a)：`grep -rn "kopen\|kjfolder\|HELPER_VBS" app.py services/ static/ templates/ tests/` → **0 命中**
- [ ] 两种情况下 `pytest tests/ -v` 全绿
- [ ] 两种情况下浏览器 E2E：`errors` 与 `console` 全空（重点验证没删到 `useWorkbench` 的 return 项导致模板绑定失败）
- [ ] `node --check` 通过（JS 语法）
- [ ] 单独 commit，message 说明"移除了什么能力、为什么"

## 风险

- **高（D1=a）**：删除 `useWorkbench.js` 的 return 项若漏改 `app.js` 两处解构注入，会导致运行时属性缺失（不一定报错，可能静默失效）。**必须 grep 全量核对三处**：`useWorkbench.js` return / `app.js:290` / `app.js:946`。
- **中**：删源码会连带 20+ 例测试失效，需同步删除，否则 pytest 直接收集失败。
- **中**：`git` 历史仍可恢复，但生产环境已装助手的客户端无法回滚 → **这是选 (b) 冻结的核心理由**。

## 备注

本单**不做**任何顺手重构（`app.py` 已 5428 行，拆 blueprint 是另一轮的事，见 `.scratch/refactor-audit-20260910.md`）。
