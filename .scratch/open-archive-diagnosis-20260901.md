# 「打开归档」远程失效 诊断报告（2026-09-01）

## 现象

投诉列表行内/批量条「打开归档」在局域网 Windows 电脑上点击后，**页面提示"已在你的电脑打开归档文件夹"，但那台电脑上没有任何文件夹弹出**。
同一时间在服务器本机（Mac）点击是正常的。

## 现状链路（已实现的方案）

```
点击 → POST /api/tickets/<id>/open-archive
     → 后端判断来源（_request_from_server_host）：
         本机  → 服务器 open/startfile 打开 → mode=local → toast「已打开」
         远程  → 不打开，转 UNC 路径返回 → mode=remote + unc
     → 前端（useWorkbench.js openArchive）：
         unc 有值 → _watchBlurThenResolve() 注册 blur 监听 + 1.5s 定时器
                  → _tryKjProtocol()  location.href = kjfolder://<base64url(UNC)>
                  → 1.5s 内 window 失焦 → 判定"已装助手" → toast「已在你的电脑打开」
                  → 1.5s 内未失焦     → 兜底弹窗（复制 UNC / 下载助手 / 网页文件面板）
         unc 为空 → 兜底弹窗
```

## 已确认的事实（证据）

1. **后端正确，测试全绿**：`tests/test_remote_archive_helper.py` + `tests/test_open_archive_api.py`
   共 **31 项通过**（2.15s）。远程/本机分流、UNC 转换、helper 脚本生成均 OK。
2. **归档根在 SMB 共享盘上**：`data/config.json` → `archive_root = /Volumes/File/综合办公室/内部共用/投诉文件/投诉处理系统`；
   挂载来源 `//all@kj-server/File`（账号 **all**）。
3. **UNC 能正常生成**，样例：
   `\\kj-server\File\综合办公室\内部共用\投诉文件\投诉处理系统\...`
4. **⚠️ 主机名 kj-server 在 DNS 层解析不了**：`ping kj-server` → `cannot resolve kj-server: Unknown host`；
   只能靠 SMB 的 NetBIOS 广播解析（`smbutil lookup kj-server` → **192.0.2.199**）。
   → Windows 端若 NetBIOS 被防火墙/公用网络配置挡住，`\\kj-server\...` 直接是无效路径。
5. **客户端日志（data/logs/system.log，192.168.1.188）**：
   12:01:29 点击 → 12:01:36 下载助手 → 12:01:52 拉取 vbs（装完 v1）→
   12:02~12:03 连续点击 7 次 → 14:09:00 又拉一次 vbs（v2 重装）→ 14:25~14:26 再点 3 次。
   → **助手装了、甚至重装过 v2，仍然打不开**。故障不在"没装助手"这一层。
6. 用户看到的是**成功 toast**，不是兜底弹窗 → 走的是 `blur → onFinish(true)` 分支。

## 假设（按可能性排序，均带判别预测）

- **H1 UNC 路径本身在 Windows 上无效（最可能）**
  - H1a 主机名 `kj-server` 解析失败（DNS 无记录，NetBIOS 未通）；
  - H1b 共享盘需账号 `all`，Windows 用当前登录凭据访问被拒（弹凭据框/拒绝访问）。
  - 预测：在 Windows 资源管理器地址栏**手动粘贴** UNC 回车 → 同样打不开（报"找不到网络路径"/"拒绝访问"）；
    改成 `\\192.0.2.199\File\...` 后能打开 → 证实 H1a。
- **H2 blur 判据误报成功（已可从代码判定，必然成立）**
  失焦与"资源管理器已打开"无因果关系：浏览器弹「是否允许打开 kjfolder」授权框、用户点别的窗口、
  甚至切换标签页都会 blur → 报成功。它把 H1/H3/H4 的全部失败**伪装成了成功**。
  - 预测：完全不装助手 + 点击后 1.5s 内点一下别的窗口，也会提示"已在你的电脑打开"。
- **H3 用户手势丢失**：`openArchive` 先 `await fetch` 再 `location.href = kjfolder://...`，
  已脱离用户手势上下文；Chrome/Edge 对非手势触发的外部协议可能只弹授权框或静默拦截。
  - 预测：把 UNC 随列表数据预下发、点击时同步触发（不 await），授权框行为改变。
- **H4 助手调用层失败**：`Shell.Application.Open` 对 UNC 有时不生效，或被安全软件/UAC 拦。
  - 预测：VBS 改 `explorer.exe` + `Shell.Application` 双通道后恢复。
- **H5 中文 UNC 传输损坏**：v2 已做 base64url 加固（保留合法字符、percent-decode），可能性较低。
  - 预测：诊断脚本「测试A（直接 wscript）」能解出正确路径即排除。

## 无法在本机构建红测的说明

失败点在**客户端 Windows 电脑**上（名字解析 / 共享凭据 / 注册表 / 浏览器授权），
本环境（macOS + 服务器本机）永远走 mode=local 分支，复现不了。
已在服务器侧完成的验证：31 项 API 测试全绿 + UNC 生成正确 + 主机名解析实测。
剩余判别需要一次 HITL 实验（见下）。

## 判别实验（HITL，10 秒，决定性）

在那台 Windows 电脑上，把下面两条路径**分别粘进资源管理器地址栏回车**：

```
\\kj-server\File\综合办公室\内部共用\投诉文件\投诉处理系统
\\192.0.2.199\File\综合办公室\内部共用\投诉文件\投诉处理系统
```

| 结果 | 结论 |
|---|---|
| 两条都打不开（找不到网络路径/拒绝访问） | H1b 凭据问题，或该机根本无共享盘访问权限 |
| 主机名不行、IP 行 | **H1a 确认** → 配置改用 IP 立即可修 |
| 两条都能打开 | 排除 H1，问题在 H2/H3/H4（助手/浏览器层） |

另可运行 `/kopen-diagnose`（一键诊断 .bat）区分「助手层」与「浏览器层」。

## 建议（路线调整，不是补丁）

**核心判断：不应该再把"自动弹出资源管理器"作为主路径。**
它同时依赖 5 个外部环节（名字解析 → 共享凭据 → 客户端装机 → 注册表 → 浏览器授权）同时成立，
任一环断就**静默失败**，而当前 blur 判据还会把失败报成成功——用户被误导，故障被掩盖。

- **方案 A（推荐主线）｜网页内归档文件面板，零安装**
  点「打开归档」直接弹出该学员归档夹的文件列表：逐个下载 + **打包 ZIP 下载** + PDF/图片在线预览。
  100% 由服务器控制，任何电脑/浏览器/权限都能用。
  现状：`/api/tickets/<id>/archive-files` 与 `/archive-files/download` 已存在，前端面板已写好
  （藏在兜底弹窗里），只差提升为主路径 + 加打包下载。工作量小、风险低。
- **方案 B（可选增强）｜保留"在我的电脑打开"为次要按钮，但先修掉误报**
  ① 去掉 blur 猜测 → 改"已请求打开"＋"没打开？"显式反馈；
  ② UNC 随列表数据下发，点击同步触发（保住用户手势），用隐藏 iframe 而非 location.href；
  ③ VBS 加 `explorer.exe` 兜底 + 明确报错；
  ④ 装完助手后写标记（安装脚本最后打开 `/kopen-helper/installed` 写 localStorage），
     未装则直接给复制路径 + 下载助手，不再假装成功。
- **方案 C（最省事的两步法）｜下载"打开文件夹.bat"**
  内容为 `explorer "\\192.0.2.199\File\..."`：下载 → 双击即开。
  零装机、零注册表、零浏览器授权，可靠性远高于协议助手，代价是两步操作 + 首次 SmartScreen 提示。
- **立即项（改配置，不改代码）**：在 `data/config.json` 加
  `smb_share: {server: "192.0.2.199", share: "File", mount_point: "/Volumes/File"}`，
  UNC 不再依赖主机名解析。

## 待用户决策

1. 主线走 A / A+B / C / 全都要？
2. 能否在 Windows 端跑上面的 10 秒判别实验并反馈结果？
3. 共享盘在该 Windows 电脑上是否已映射盘符（Z:）？访问是否需要账号密码（Mac 用的是 all）？
