"""归档服务 - 归档三闸门校验、归档目录构建与两件套写入（纯函数，无 Flask 路由）

唯一路径规则（所有归档入口统一）：
    {archive_root}/{单位类型}/{代号}-{单位名}/{complaint_date}_{姓名}_{身份证}_{代号}/
        投诉登记表.docx
        投诉回复函.docx
        {姓名}_合同_*.pdf
        {date}_{姓名}_撤诉说明.txt

- archive_root：可由用户在界面配置（持久化到 data/config.json 的 archive_root），
  默认 BASE_DIR / "案件归档"。允许指向任意可写目录（如 ~/Documents/投诉档案/）。
- 单位类型：organization_unit_type（分校/分店），未知时「未归属」。
- 代号：school_short，单字代号（如「常」），未知时「未归属」。
- 单位名：organization_unit_name（如「常平横江厦分校」），未知时「未归属」。
- 学员段：complaint_date / student_name / id_card / code，任意缺失用「未归属」兜底。
"""
import os
import re
import shutil
import subprocess
import sys
import warnings

from config import BASE_DIR, load_config


REGISTER_FILENAME = "投诉登记表.docx"
REPLY_FILENAME = "投诉回复函.docx"
FALLBACK_UNIT_TYPE = "未归属"
FALLBACK_UNIT_NAME = "未归属"
FALLBACK_CODE = "未归属"
FALLBACK_DATE = "1970-01-01"
FALLBACK_NAME = "未命名学员"
FALLBACK_ID_CARD = "无证件"
DEFAULT_ARCHIVE_ROOT = str(BASE_DIR / "案件归档")


def archive_gate_errors(ticket: dict) -> list[str]:
    """归档三闸门：处理情况已填 + 配合度已评 + 费用明细已确认。

    返回缺失项中文描述列表，全部通过时返回空列表。
    """
    errors = []
    if not str(ticket.get("handling_notes") or "").strip():
        errors.append("处理情况未填写")
    if not str(ticket.get("branch_cooperation") or "").strip():
        errors.append("配合度未评定")
    if ticket.get("fee_plan_status") != "confirmed":
        errors.append("费用明细未确认")
    return errors


def _normalize_archive_root(root: str | None) -> str:
    """把用户输入或 config 里的 root 规范成「绝对路径、可写」。

    - 空值/None → DEFAULT_ARCHIVE_ROOT（BASE_DIR/案件归档）
    - 展开 ~ 与相对路径 → 绝对路径
    - 目录不存在则自动创建（创建失败抛 ValueError）
    - 不可写抛 ValueError
    """
    raw = (root or "").strip()
    if not raw:
        raw = DEFAULT_ARCHIVE_ROOT
    expanded = os.path.expanduser(raw)
    # 历史配置里可能存有相对路径（如「案件归档」）：统一锚定到项目根目录，
    # 不随服务进程 CWD 漂移
    if not os.path.isabs(expanded):
        expanded = os.path.join(str(BASE_DIR), expanded)
    expanded = os.path.abspath(expanded)
    if not os.path.isdir(expanded):
        try:
            os.makedirs(expanded, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"无法创建归档根目录: {expanded} ({exc})") from exc
    if not os.access(expanded, os.W_OK):
        raise ValueError(f"归档根目录不可写: {expanded}")
    return expanded


def _assert_within(child: str, parent: str) -> None:
    """断言 child 是 parent 的子路径（含自身），否则抛 ValueError。

    用 realpath 防止符号链接绕路。"""
    real_child = os.path.realpath(child)
    real_parent = os.path.realpath(parent)
    if real_child != real_parent and not real_child.startswith(real_parent + os.sep):
        raise ValueError(f"路径越界: {real_child} 不在 {real_parent} 内")


def _safe_segment(value: str, fallback: str) -> str:
    """把任意字符串清洗成可作为路径段的形式。"""
    import re

    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", str(value or "").strip(), flags=re.UNICODE)
    cleaned = cleaned.strip("_-")
    return cleaned[:80] or fallback


def _unit_segment(code: str, unit_name: str) -> str:
    """{代号}-{单位名} 路径段；任一为空时退回 FALLBACK_UNIT_NAME。"""
    safe_name = _safe_segment(unit_name, FALLBACK_UNIT_NAME)
    if not code.strip():
        return f"{FALLBACK_CODE}-{safe_name}"
    return f"{code}-{safe_name}"


def _case_segment(complaint_date: str, name: str, id_card: str, code: str) -> str:
    """{date}_{name}_{id_card}_{code} 路径段；空值用兜底。"""
    parts = [
        _safe_segment(complaint_date, FALLBACK_DATE),
        _safe_segment(name, FALLBACK_NAME),
        _safe_segment(id_card, FALLBACK_ID_CARD),
        _safe_segment(code, FALLBACK_CODE),
    ]
    return "_".join(parts)


def build_archive_dir(ticket: dict, root: str | None = None) -> tuple[str, str, str]:
    """按统一规则构建归档目录与两件套目标路径（只拼路径，不建目录）。

    Returns:
        (case_dir, register_target, reply_target) — 全部为绝对路径。
    Raises:
        ValueError: root 不可用或计算结果越界。
    """
    if root is None:
        root = load_config().get("archive_root") or DEFAULT_ARCHIVE_ROOT
    normalized_root = _normalize_archive_root(root)

    code = str(ticket.get("school_short") or "").strip()
    unit_type = str(ticket.get("organization_unit_type") or "").strip() or FALLBACK_UNIT_TYPE
    unit_name = str(ticket.get("organization_unit_name") or "").strip() or FALLBACK_UNIT_NAME
    complaint_date = str(ticket.get("complaint_date") or "").strip()
    name = str(ticket.get("student_name") or "").strip()
    id_card = str(ticket.get("id_card") or "").strip()

    case_dir = os.path.join(
        normalized_root,
        unit_type,
        _unit_segment(code, unit_name),
        _case_segment(complaint_date, name, id_card, code),
    )
    register_target = os.path.join(case_dir, REGISTER_FILENAME)
    reply_target = os.path.join(case_dir, REPLY_FILENAME)

    _assert_within(case_dir, normalized_root)
    return case_dir, register_target, reply_target


def archive_case(ticket: dict, files: dict, open_folder: bool = False) -> dict:
    """一键归档：闸门校验 → 建夹 → 复制两件套（存在者）→ 可选打开文件夹。

    files 形如 {"register_form": 源路径或None, "reply": 源路径或None}，至少一个真实存在。
    成功返回 {"success":True,"dir":...,"files":[...],"opened":bool}。
    """
    errors = archive_gate_errors(ticket)
    if errors:
        return {"success": False, "errors": errors}

    case_dir, register_target, reply_target = build_archive_dir(ticket)
    targets = {"register_form": register_target, "reply": reply_target}
    present = {k: v for k, v in files.items() if v and os.path.isfile(v)}
    if not present:
        return {"success": False, "errors": ["无可归档文件：登记表与回复函均不存在"]}

    os.makedirs(case_dir, exist_ok=True)
    copied = []
    for key in ("register_form", "reply"):
        src = present.get(key)
        if src:
            # 登记表生成时已直接写入归档目录，源与目标相同时跳过复制避免自覆盖损坏
            if os.path.abspath(src) == os.path.abspath(targets[key]):
                copied.append(targets[key])
                continue
            shutil.copyfile(src, targets[key])
            copied.append(targets[key])

    opened = False
    if open_folder:
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", case_dir], check=True)
            elif sys.platform == "win32":
                os.startfile(case_dir)
            else:
                subprocess.run(["xdg-open", case_dir], check=True)
            opened = True
        except Exception as exc:
            warnings.warn(f"打开归档文件夹失败: {exc}")

    return {"success": True, "dir": case_dir, "files": copied, "opened": opened}


def resolve_case_dir(ticket: dict, root: str | None = None) -> dict:
    """定位案件归档夹（只拼路径+探测磁盘，不做任何打开动作，不校验三闸门）。

    返回值不抛出：
        {"success": True, "dir": ...}
        {"success": False, "code": "root_unavailable"|"dir_missing",
         "errors": [...], "dir": ...?}
    """
    try:
        case_dir, _, _ = build_archive_dir(ticket, root=root)
    except ValueError as exc:
        return {"success": False, "code": "root_unavailable",
                "errors": [f"归档根目录不可用: {exc}"]}
    if not os.path.isdir(case_dir):
        return {
            "success": False, "code": "dir_missing", "dir": case_dir,
            "errors": ["案件归档夹不存在：该学员尚未生成登记表/回复函，或归档根目录不可用（共享盘未挂载？）"],
        }
    return {"success": True, "dir": case_dir}


def open_case_dir(ticket: dict, root: str | None = None) -> dict:
    """在「运行本系统的这台机器」的文件管理器中打开案件归档夹。

    ⚠️ 仅限请求来自服务器本机时调用——局域网远程客户端点击「打开归档」时，
    绝不能在服务器端执行本函数（否则文件夹会弹在服务器的屏幕上）。
    远程场景由路由返回 UNC 路径、浏览器端经 kjfolder:// 协议在客户端打开。

    案件夹在受理生成登记表/回复函或终归档时已落盘——只要夹子在磁盘上即可打开，
    在途工单（闸门未过）也能查看已生成的文档。返回值不抛出：
        {"success": True, "dir": ...}
        {"success": False, "code": "root_unavailable"|"dir_missing"|"launch_failed",
         "errors": [...], "dir": ...?}
    """
    resolved = resolve_case_dir(ticket, root=root)
    if not resolved.get("success"):
        return resolved
    case_dir = resolved["dir"]
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", case_dir], check=True)
        elif sys.platform == "win32":
            os.startfile(case_dir)  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", case_dir], check=True)
    except Exception as exc:
        return {"success": False, "code": "launch_failed", "dir": case_dir,
                "errors": [f"打开归档文件夹失败: {exc}"]}
    return {"success": True, "dir": case_dir}


# ── SMB 共享映射：服务器路径 ↔ Windows UNC 路径 ────────────────────────
# 归档根目录通常在 SMB 共享盘上（服务器挂载于 /Volumes/...，来源 //user@server/share）。
# 远程客户端（局域网 Windows 电脑）拿不到服务器挂载点，必须转成 UNC 网络路径
# （\\\\server\\share\\...）才能在自己的资源管理器里打开。
_SMB_CACHE: dict = {"at": 0.0, "maps": []}


def get_smb_mappings() -> list[dict]:
    """当前可用的 SMB 挂载映射 [{"server","share","mount_point"}]。

    优先读 config.json 的 "smb_share": {"server","share","mount_point"}（人工指定，
    服务器不挂载时也能转换）；否则解析 macOS `mount` 输出里的 smbfs 行，
    结果缓存 60 秒。任何失败返回空列表（to_unc_path 会返回 None 走兜底）。
    """
    cfg = load_config().get("smb_share")
    if (isinstance(cfg, dict) and cfg.get("server") and cfg.get("share")
            and cfg.get("mount_point")):
        return [{
            "server": str(cfg["server"]),
            "share": str(cfg["share"]),
            "mount_point": os.path.abspath(os.path.expanduser(str(cfg["mount_point"]))),
        }]

    import time as _time
    now = _time.time()
    if now - _SMB_CACHE["at"] < 60 and _SMB_CACHE["maps"]:
        return _SMB_CACHE["maps"]

    maps: list[dict] = []
    try:
        out = subprocess.run(["mount"], capture_output=True, text=True, timeout=5).stdout
        for m in re.finditer(r"//(?:[^@/\s]+@)?([^/\s]+)/([^\s]+)\s+on\s+(\S+)\s+\(smbfs", out):
            maps.append({"server": m.group(1), "share": m.group(2), "mount_point": m.group(3)})
    except Exception:
        maps = []
    maps.sort(key=lambda x: -len(x["mount_point"]))  # 最长前缀优先
    _SMB_CACHE.update({"at": now, "maps": maps})
    return maps


def to_unc_path(path: str) -> str | None:
    """把服务器上的绝对路径转换为 Windows UNC 路径（\\\\server\\share\\rest）。

    不在任何 SMB 挂载点之下时返回 None（调用方走「无法远程打开」兜底）。
    内部按挂载点最长前缀优先匹配，不依赖 get_smb_mappings 的返回顺序。
    """
    p = os.path.realpath(str(path or ""))
    if not p:
        return None
    maps = sorted(get_smb_mappings(), key=lambda x: -len(x["mount_point"]))
    for m in maps:
        mp = m["mount_point"].rstrip("/")
        if not mp:
            continue
        if p == mp:
            return f"\\\\{m['server']}\\{m['share']}"
        if p.startswith(mp + "/"):
            rest = p[len(mp) + 1:].replace("/", "\\")
            return f"\\\\{m['server']}\\{m['share']}\\{rest}"
    return None


# ── Windows 归档助手：kjfolder:// 自定义协议，远程客户端一键弹出文件夹 ──
# 浏览器安全策略禁止网页直接打开本地/网络文件夹；助手一次性注册 kjfolder:// 协议
# （仅写 HKCU 当前用户注册表 + 一个 .vbs 到 LOCALAPPDATA，无需管理员、无常驻进程），
# 之后页面调 kjfolder://<base64url(UNC路径)> 即可在客户端弹出资源管理器。
HELPER_VBS = """' kjfolder-open.vbs - open a case archive folder from a kjfolder:// URL
' Installed by the KuaiJie Complaint System archive helper.
' URL format: kjfolder://<base64url-encoded UNC path>
' v2 (2026-09-01): transport-hardened. Browsers/Explorer may append a
' trailing "/" or percent-encode parts of the URL when launching a custom
' protocol; MSXML6 rejects such non-canonical base64 with 80004005. We now
' keep ONLY base64url characters after the scheme, so any transport junk
' (trailing slash, %xx, quotes, whitespace) is stripped before decoding.
Option Explicit
Dim url, p, i, c, out
If WScript.Arguments.Count = 0 Then WScript.Quit 1
url = WScript.Arguments(0)
On Error Resume Next
' strip scheme up to "://" (fallback: anything up to the first ":")
If InStr(url, "://") > 0 Then
  p = Mid(url, InStr(url, "://") + 3)
ElseIf InStr(url, ":") > 0 Then
  p = Mid(url, InStr(url, ":") + 1)
Else
  p = url
End If
' keep only base64url characters (drops "/", "+", "%xx", spaces, quotes...)
' step 1: percent-decode so junk like "%2F" becomes "/" (then stripped below)
out = ""
i = 1
Do While i <= Len(p)
  c = Mid(p, i, 1)
  If c = "%" And i + 2 <= Len(p) Then
    out = out & Chr(CLng("&H" & Mid(p, i + 1, 2)) And &HFF&)
    i = i + 3
  ElseIf c = "%" Then
    i = i + 1
  Else
    out = out & c
    i = i + 1
  End If
Loop
p = out
' step 2: keep only base64url characters
out = ""
For i = 1 To Len(p)
  c = Mid(p, i, 1)
  If InStr("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_", c) > 0 Then out = out & c
Next
p = out
p = Replace(p, "-", "+")
p = Replace(p, "_", "/")
Do While Len(p) Mod 4 <> 0
  p = p & "="
Loop
p = B64ToUtf8(p)
If Err.Number <> 0 Then
  MsgBox "解码失败：请重新运行安装脚本（.bat）更新归档助手后重试。" & vbCrLf & _
         "(Decode failed - please re-run the installer .bat to update the helper.)", _
         48, "Archive Folder Helper"
  WScript.Quit 1
End If
On Error GoTo 0
If Left(p, 2) <> "\\\\" Then
  MsgBox "路径异常：" & vbCrLf & p & vbCrLf & vbCrLf & _
         "请重新运行安装脚本（.bat）更新归档助手。", 48, "Archive Folder Helper"
  WScript.Quit 1
End If
On Error Resume Next
CreateObject("Shell.Application").Open p
If Err.Number <> 0 Then
  MsgBox "Cannot open folder:" & vbCrLf & p, 48, "Archive Folder Helper"
End If
WScript.Quit 0

Function B64ToUtf8(b64)
  Dim xml, node, stm
  Set xml = CreateObject("MSXML2.DOMDocument.6.0")
  Set node = xml.createElement("b64")
  node.dataType = "bin.base64"
  node.text = b64
  Set stm = CreateObject("ADODB.Stream")
  stm.Type = 1
  stm.Open
  stm.Write node.nodeTypedValue
  stm.Position = 0
  stm.Type = 2
  stm.Charset = "utf-8"
  B64ToUtf8 = stm.ReadText
  stm.Close
End Function
"""

_HELPER_BAT_TEMPLATE = """@echo off
setlocal
echo ==================================================
echo  KuaiJie Complaint System - Archive Folder Helper
echo  This installs the kjfolder:// protocol (per-user).
echo  No admin rights required. Nothing runs in the
echo  background - Windows opens folders on demand.
echo ==================================================
echo.
echo [1/3] Downloading opener script from the server...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try{Invoke-WebRequest -UseBasicParsing -Uri '__BASE_URL__/kopen-helper.vbs' -OutFile ($env:LOCALAPPDATA+'\\kjfolder-open.vbs')}catch{exit 1}"
if errorlevel 1 goto fail
if not exist "%LOCALAPPDATA%\\kjfolder-open.vbs" goto fail

echo [2/3] Registering the kjfolder:// protocol for the current user...
reg add "HKCU\\Software\\Classes\\kjfolder" /ve /d "URL:KuaiJie Archive Folder" /f >nul
if errorlevel 1 goto fail
reg add "HKCU\\Software\\Classes\\kjfolder" /v "URL Protocol" /f >nul
if errorlevel 1 goto fail
reg add "HKCU\\Software\\Classes\\kjfolder\\shell\\open\\command" /ve /d "wscript.exe \\"%LOCALAPPDATA%\\kjfolder-open.vbs\\" \\"%%1\\"" /f >nul
if errorlevel 1 goto fail

echo.
echo [3/3] Done!
echo.
echo You can now click "Open Archive" in the complaint system.
echo The folder will open in Windows Explorer directly.
echo.
echo Press any key to close this window...
pause >nul
exit /b 0

:fail
echo.
echo Installation FAILED. Please contact the administrator.
echo Press any key to close...
pause >nul
exit /b 1
"""


def generate_helper_bat(base_url: str) -> str:
    """生成 Windows 安装脚本（.bat）内容；base_url 形如 http://192.168.1.192:5003。

    内容纯 ASCII（避免 cmd 代码页乱码），CRLF 行尾由编码端保证。
    """
    base = str(base_url or "").strip().rstrip("/")
    return _HELPER_BAT_TEMPLATE.replace("__BASE_URL__", base)


_DIAGNOSE_BAT_TEMPLATE = """@echo off
chcp 936 >nul
setlocal enabledelayedexpansion
title kjfolder 归档助手一键诊断
echo ==============================================
echo   kjfolder:// 归档助手 一键诊断
echo   本脚本只做只读检查和两次调用测试，不改任何设置
echo ==============================================
echo.
set "VBS=%LOCALAPPDATA%\\kjfolder-open.vbs"

echo [1/4] 检查助手脚本文件
if not exist "%VBS%" (
  echo   X 不存在 —— 助手未安装或安装失败，请先双击运行安装脚本
  set "RESULT1=缺失"
) else (
  for /f %%V in ('powershell -NoProfile -Command "$t=Get-Content -Raw -LiteralPath '%VBS%'; if($t -match 'transport-hardened'){'V2新版'}elseif($t -match 'bin.base64'){'V1旧版'}else{'异常'}"') do set "RESULT1=%%V"
  echo   结果: !RESULT1!
  if "!RESULT1!"=="V1旧版" echo   X 是旧版 —— 请重新运行安装脚本升级后再试
)
echo.

echo [2/4] 检查 kjfolder 协议注册
reg query "HKCU\\Software\\Classes\\kjfolder" /v "URL Protocol" >nul 2>&1
if !errorlevel! neq 0 (
  echo   X 未注册 —— 请重新运行安装脚本
  set "RESULT2=未注册"
) else (
  reg query "HKCU\\Software\\Classes\\kjfolder\\shell\\open\\command" /ve 2>nul | find /i "wscript" >nul
  if !errorlevel! equ 0 (
    echo   √ 已注册且指向 wscript
    set "RESULT2=正常"
  ) else (
    echo   X 已注册但打开命令异常，实际内容：
    reg query "HKCU\\Software\\Classes\\kjfolder\\shell\\open\\command" /ve
    set "RESULT2=异常"
  )
)
echo.

echo [3/4] 调用测试（会创建并打开临时文件夹 %TEMP%\\kjfolder-diag-test）
md "%TEMP%\\kjfolder-diag-test" >nul 2>&1
for /f "delims=" %%P in ('powershell -NoProfile -Command "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes('%TEMP%\\kjfolder-diag-test')).TrimEnd('=').Replace('+','-').Replace('/','_')"') do set "PAYLOAD=%%P"

echo   测试A：直接调助手（绕过浏览器）
wscript.exe "%VBS%" "kjfolder://%PAYLOAD%"
echo   —— 如果刚才弹出资源管理器并打开了 kjfolder-diag-test 文件夹，选 Y
choice /c YN /t 30 /d N /m "   测试A是否成功弹出文件夹"
if errorlevel 2 (set "DIRECT=没弹出") else (set "DIRECT=成功")
echo.

echo   测试B：走浏览器协议（等同页面点击「打开归档」）
start "" "kjfolder://%PAYLOAD%"
echo   —— 若浏览器弹出「要允许此网站打开 kjfolder 吗」，勾选始终允许再点打开
choice /c YN /t 30 /d N /m "   测试B是否成功弹出文件夹"
if errorlevel 2 (set "BROWSER=没弹出") else (set "BROWSER=成功")
echo.

echo [4/4] 诊断结论
echo   助手文件: !RESULT1!    协议注册: !RESULT2!
echo   直接调用: !DIRECT!    浏览器调用: !BROWSER!
echo.
if not "!DIRECT!"=="成功" (
  echo   结论：助手调用本身失败，与浏览器无关。
  echo   处理：重新运行安装脚本；仍失败请把本窗口完整截图发回。
) else if not "!BROWSER!"=="成功" (
  echo   结论：助手正常，是浏览器拦截了协议调用。
  echo   处理：点「打开归档」时在浏览器询问框勾选「始终允许」；
  echo         或换 Edge 打开系统；之前勾过「不允许」的浏览器需换用或清设置。
) else (
  echo   结论：两条链路都正常！请刷新系统页面再点「打开归档」。
)
echo.
echo   安装脚本下载地址: __BASE_URL__/kopen-helper
pause
"""


def generate_diagnose_bat(base_url: str) -> str:
    """生成 Windows 一键诊断脚本（.bat）：逐环检查「打开归档」链路。

    判定矩阵（HITL 反馈环，区分浏览器层/助手层故障）：
      直接调用成功 + 浏览器调用失败 → 浏览器拦截（授权/记住拒绝）
      直接调用失败                 → 助手层（文件缺失/旧版/注册异常/解码失败）
    内容含中文，由路由端按 GBK 编码（chcp 936）。
    """
    base = str(base_url or "").strip().rstrip("/")
    return _DIAGNOSE_BAT_TEMPLATE.replace("__BASE_URL__", base)


def validate_archive_root(root: str | None) -> str:
    """公开入口：校验并规范归档根目录，返回绝对路径；不可用抛 ValueError。"""
    return _normalize_archive_root(root)


def contract_target_name(student_name: str, src_filename: str) -> str:
    """合同归档统一命名：{姓名}_合同_{原始文件名}{扩展名}。

    - 已符合「{姓名}_合同_」前缀的文件保持原名（避免重复拼接）；
    - 原始文件名经 _safe_segment 清洗，姓名缺失用「未命名学员」兜底。
    """
    stem, ext = os.path.splitext(os.path.basename(src_filename))
    safe_name = _safe_segment(student_name, FALLBACK_NAME)
    if stem.startswith(f"{safe_name}_合同_"):
        return f"{stem}{ext}"
    return f"{safe_name}_合同_{_safe_segment(stem, '合同')}{ext}"


def archive_contract(ticket: dict, sources: list, case_dir: str | None = None) -> dict:
    """把合同源文件统一归入案件归档夹（命名 {姓名}_合同_*）。

    - sources：合同源路径列表（contract_path + manifest 里的文件），自动去重、忽略不存在项；
    - case_dir：调用方已构建好的案件夹（终归档流程传入），缺省时按 ticket 自建；
    - 源在案件夹外 → 复制（保留原件供分析链路引用）；源已在案件夹内但命名不规范 → 原地重命名；
    - 返回 {"copied": [目标路径...], "errors": [失败描述...], "mapping": {源路径: 目标路径}}，失败不抛出。
    """
    copied: list[str] = []
    errors: list[str] = []
    mapping: dict[str, str] = {}
    seen: set[str] = set()
    srcs: list[str] = []
    for src in sources or []:
        s = str(src or "").strip()
        if not s or not os.path.isfile(s):
            continue
        real = os.path.realpath(s)
        if real in seen:
            continue
        seen.add(real)
        srcs.append(s)
    if not srcs:
        return {"copied": [], "errors": [], "mapping": {}}

    try:
        if not case_dir:
            case_dir, _, _ = build_archive_dir(ticket)
        os.makedirs(case_dir, exist_ok=True)
    except (ValueError, OSError) as exc:
        return {"copied": [], "errors": [f"合同归档目录构建失败: {exc}"], "mapping": {}}

    real_case_dir = os.path.realpath(case_dir)
    name = str(ticket.get("student_name") or "").strip()
    for src in srcs:
        try:
            target = os.path.join(case_dir, contract_target_name(name, os.path.basename(src)))
            real_src = os.path.realpath(src)
            if real_src == os.path.realpath(target):
                copied.append(target)
                mapping[src] = target
                continue
            if real_src.startswith(real_case_dir + os.sep):
                # 已在案件夹内（如旧命名的历史文件）→ 原地改名归位
                if os.path.exists(target):
                    os.remove(target)
                os.replace(src, target)
            else:
                shutil.copyfile(src, target)
            copied.append(target)
            mapping[src] = target
        except OSError as exc:
            errors.append(f"合同归档失败 {os.path.basename(src)}: {exc}")
    return {"copied": copied, "errors": errors, "mapping": mapping}
