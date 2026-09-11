# ISS-AP-09 · 「复制路径」应复制 Windows UNC 路径，而非服务器本机挂载路径

**Status:** done —— 2026-09-11 主 Agent 实现
**Priority:** P1（复制出去的路径在目标电脑上**打不开**，功能等于没做对）
**依赖:** ISS-AP-04 ✅ / ISS-AP-05 ✅（面板已是主路径，本单只换复制载荷）
**并行:** ❌ 触碰 `config.py` / `app.py` / `useWorkbench.js` 的工单互斥

## 用户上报（2026-09-11 原话）

> 「复制路径」正确的路径应该是：`\\192.0.2.199\File\综合办公室\内部共用\投诉文件\投诉处理系统\分校\.....`
> 而不是现在的 `/Volumes/File/.../2026-02-25_唐福兵_110101199003070011_栅D`。

（实测确认：`192.0.2.199` 可达、挂载点给出的主机名 `kj-server` 解析不了，按用户给的 IP 实现。）

## 根因（两层）

1. **接口根本没返回 UNC**。`to_unc_path()` 早已实现（`services/archive_service.py:335`，
   ISS-AP-05 收敛 `/open-archive` 后保留未删），但 `archive-files` 只回 `dir`
   （服务器本机挂载路径 `/Volumes/File/...`）。
   **`dir` 只有服务器这台 Mac 认识**：局域网 Windows 同事拿到它，贴进资源管理器是死路径。
2. **就算配了 UNC 也读不到**（更隐蔽的一层）：`config.load_config()` 的合并逻辑**只遍历
   `DEFAULT_CONFIG` 的顶层键**，文件里多出来的键会被**静默丢弃**。所以往
   `data/config.json` 写 `smb_share` 是无效的——这是设计上就存在的坑。

实测还发现第三层：SMB 挂载点给出的主机名是 `kj-server`（`//all@kj-server/File`），
而 **`kj-server` 在这台机器上解析不了**（`ping: cannot resolve kj-server`），
`192.0.2.199` 才可达。所以 UNC 的 server 段**必须用固定 IP**，不能依赖 mount 解析。

## 改了什么

| 文件 | 改动 | 为什么 |
|---|---|---|
| `config.py` | `DEFAULT_CONFIG` 新增 `smb_share` 段（`SMB_SERVER`/`SMB_SHARE`/`SMB_MOUNT_POINT` 环境变量可覆盖，默认空） | 让该键**能活过** `load_config()` 的合并；留空则回退解析 `mount`。已加注释说明「不写进 DEFAULT_CONFIG 就会被丢」 |
| `data/config.json` | 写入 `smb_share: {server: "192.0.2.199", share: "File", mount_point: "/Volumes/File"}`（**该文件在 .gitignore 里，机器本地**） | 指定 IP 而非主机名；备份在 `tmp/config.json.bak-pre-smbshare` |
| `app.py` | `/archive-files` 成功响应追加 `unc`（`to_unc_path(case_dir) or ""`）；docstring 更新契约并改 raw string（消除 `\F` SyntaxWarning） | 追加键，不动既有 `dir`/`files`/`is_server_host` |
| `useWorkbench.js` | 新增 `apUncPath`（加载前重置、成功时赋值）；`apCopyPath()` 改为**优先复制 UNC**、无 UNC 回退 `dir`；新增 `apCopyHint`（tooltip 直接亮出将要复制的字符串） | 配错了当场悬停就能看见，而不是贴进资源管理器打不开才发现 |
| `index.html` | 复制路径按钮 `:disabled="!apUncPath && !apDir"`、`:title="apCopyHint"`；版本号 bump `useWorkbench.js?v=35` / `app.js?v=48` | |
| 测试 | +3：UNC 追加键（有映射/无映射）、`load_config()` 保留 `smb_share` 段（合并丢键回归） | |

## 验收标准（全过）

- [x] `to_unc_path()` 对用户给的样例输出**逐字符一致**：
      `\\192.0.2.199\File\综合办公室\内部共用\投诉文件\投诉处理系统\分校\栅D-沙田分校\2026-02-25_唐福兵_110101199003070011_栅D`
- [x] 真实接口 `/archive-files` 返回 `unc`（张三工单实测 `\\192.0.2.199\File\...\未入字典\南城-南城\...`）
- [x] 真机点「复制路径」→ 捕获到的剪贴板字符串 == 上述 UNC（用 `navigator.clipboard.writeText` 打桩捕获）
- [x] tooltip 实测显示「复制共享路径（可直接粘到资源管理器）：\\192.0.2.199\File\…」
- [x] 无映射时 `unc` 为空串、按钮回退复制 `dir`（不编造路径）
- [x] 全量回归 `pytest tests/ -q` → **664 passed**（661 + 3 新增）
- [x] 两个 JS 文件 `node --input-type=module --check` 通过；`verify_frontend.mjs` 四项全绿
- [x] 真机 E2E `errors`/`console` 全空

## 注意事项

1. **`data/config.json` 不入库**（.gitignore）。换机器/重装部署时需要重写这一段，
   或设环境变量 `SMB_SERVER` / `SMB_SHARE` / `SMB_MOUNT_POINT`。
   `config.py` 的注释已写明字段含义与「server 用 IP」的原因。
2. 若哪天 UNC 突然变回主机名形态（`\\kj-server\File\...`），优先怀疑
   **`config.json` 的 `smb_share` 段被整文件重建掉了**——tooltip 里能直接看出来。
3. 失败态（`dir_missing`/`root_unavailable`）**不提供**复制路径：失败响应不含 `dir`，
   且夹子不存在时贴路径也无意义（ISS-AP-08 已定）。
