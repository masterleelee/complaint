"""SMB 共享盘挂载自愈服务。

背景（2026-09 交付总监现场审计）
--------------------------------
本系统部署在 Mac mini 上作局域网服务器，归档文件写到 macOS SMB 挂载点
`/Volumes/File`（来源 `//192.0.2.199/File`）。该挂载点会**不定时自行消失**
（`mount` 输出里 smbfs 行消失、挂载点目录不存在），但 SMB 会话仍残留
（`smbutil statshares -a` 仍显示共享存活）。系统原先**没有任何自动重挂机制**，
于是 11 处依赖归档目录的功能同时失效（登记表/回复函生成、归档列表、预览/下载、
受理建夹、合同上传/下载、回访服务……）。

本模块提供「检测 → 重挂 → 复验 → 单飞 → 限时」的自愈闭环，供归档链路前置调用，
并由 launchd 定时巡检托管。

⚠️ 两条**不可违背**的实测事实（决定实现正确性）
-----------------------------------------------
1. **挂载原语以 `open` 为主**：本机实测 `osascript -e 'mount volume ...'` **不可用**
   （rc=1、stderr `-5014` = `errAEEventNotPermitted`，TCC 自动化权限被拒，挂载点未恢复），
   而 `open -g "smb://server/share"` 可用（rc=0，约 5s 后异步完成挂载）。因此策略链为
   `open -g`（主）→ `osascript`（备，换机器/授权后可能可用）→ `mount_smbfs -N`（兜底）。
   **任何子进程退出码都不可信**——`open` 触发的挂载是异步的，成败一律「复验挂载点真实状态」
   再下结论（`open` 成功 rc=0 但此刻尚未挂载是常态，必须轮询）。
2. **挂载判据**：`os.path.ismount(mount_point)` 可靠；补充判据是解析 `mount` 输出里的
   smbfs 行。`stat -f %T` 对 SMB 挂载点返回 `/`，**完全不可用，禁止使用**。

设计约束
--------
* 未挂载时**不得**用 `makedirs` 兜底去「造」挂载点：`/Volumes` 权限是 root:wheel 755，
  普通用户创建目录会 `EACCES`。归档层 `_normalize_archive_root` 会因此抛 `ValueError`，
  上层正确返回 `root_unavailable`——**这个失败行为是安全且正确的，不要"优化"掉**
  （它保证不会把文件静默写到本机磁盘）。
* 超时保护：stale mount 状态下访问挂载点可能 hang，会拖死整个 Flask 请求线程。
  所有探测/子进程都受 `timeout` 约束，超时返回 `reason="timeout"`。
* 并发单飞：多个 Windows 同事同时点归档会并发触发重挂，用 `threading.Lock` 避免竞态。
"""
import os
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime

from config import load_config
from utils.logger import system_logger


# ── 常量 ────────────────────────────────────────────────────────────
#: 重挂/探测事件日志（含时间戳、触发来源），便于后续观察掉载规律。
LOG_PATH = os.getenv("SMB_MOUNT_LOG", "/tmp/complaint_smbmount.log")
#: 免密挂载使用的 SMB 账号（钥匙串里该共享凭证 acct="all"）。
SMB_USER = os.getenv("SMB_USER", "all")

#: 复验轮询间隔（秒）。`open`/`osascript` 触发的挂载是**异步**的（实测 open 约 5s 完成），
#: 固定短暂 sleep 会过早判失败，故改为轮询。
_POLL_INTERVAL = 0.75
#: 单个重挂方案最多轮询等待的秒数（防止首选方案耗尽整个 deadline 而饿死兜底方案）。
_STRATEGY_WAIT = 8.0

#: 事件环形缓冲（供 /api/smb/status 展示最近掉载/重挂记录）。
_RECENT_EVENTS: deque = deque(maxlen=100)

#: 并发单飞锁：保证同一时刻只有一个重挂动作在跑。
_MOUNT_LOCK = threading.Lock()

#: 测试/运维禁用开关。None = 跟随环境变量 SMB_AUTOMOUNT_DISABLED；True/False = 显式覆盖。
_ENABLED_OVERRIDE: bool | None = None

_TRUTHY = ("1", "true", "yes", "on")


# ═══════════════════════════════════════════════════════════════
#  启用开关（测试期必备：绝不真的执行挂载子进程）
# ═══════════════════════════════════════════════════════════════
def set_enabled(enabled: bool) -> None:
    """显式覆盖启用开关（True=启用自愈，False=禁用）。供测试/运维使用。"""
    global _ENABLED_OVERRIDE
    _ENABLED_OVERRIDE = bool(enabled)


def reset_enabled() -> None:
    """清除显式覆盖，恢复为「跟随环境变量」。"""
    global _ENABLED_OVERRIDE
    _ENABLED_OVERRIDE = None


def is_enabled() -> bool:
    """自愈是否启用。显式覆盖优先；否则读环境变量 `SMB_AUTOMOUNT_DISABLED`。"""
    if _ENABLED_OVERRIDE is not None:
        return _ENABLED_OVERRIDE
    return os.getenv("SMB_AUTOMOUNT_DISABLED", "").strip().lower() not in _TRUTHY


# ═══════════════════════════════════════════════════════════════
#  配置与底层命令封装
# ═══════════════════════════════════════════════════════════════
def get_mount_config() -> dict | None:
    """读取 `load_config()["smb_share"]`，返回 {server, share, mount_point}；未配置返回 None。

    mount_point 做 abspath/expanduser 归一，避免 `~` 或相对路径参与比较。
    """
    try:
        cfg = load_config() or {}
    except Exception:
        return None
    raw = cfg.get("smb_share")
    if not isinstance(raw, dict):
        return None
    server = str(raw.get("server") or "").strip()
    share = str(raw.get("share") or "").strip()
    mount_point = str(raw.get("mount_point") or "").strip()
    if not (server and share and mount_point):
        return None
    return {
        "server": server,
        "share": share,
        "mount_point": os.path.abspath(os.path.expanduser(mount_point)),
    }


def _run_command(cmd: list, timeout: float) -> tuple[int, str, str, bool]:
    """运行子进程，返回 (rc, stdout, stderr, timed_out)。任何异常都不抛出。"""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=max(0.1, float(timeout)))
        return proc.returncode, proc.stdout or "", proc.stderr or "", False
    except subprocess.TimeoutExpired:
        return -1, "", "timeout", True
    except FileNotFoundError as exc:
        return -1, "", f"not found: {exc}", False
    except Exception as exc:  # noqa: BLE001 —— 自愈链路不得因底层异常中断
        return -1, "", str(exc), False


def parse_smb_mounts() -> list[dict]:
    """解析 `mount` 输出里的 smbfs 行 → [{"server","share","mount_point"}]。

    复用 `services.archive_service.parse_smbfs_mount_output`（延迟导入，规避循环依赖），
    保证挂载判据口径与 UNC 转换链路**逐字一致**。任何失败返回空列表。
    """
    try:
        from services.archive_service import parse_smbfs_mount_output
    except Exception:
        return []
    rc, out, _err, timed_out = _run_command(["mount"], timeout=5.0)
    if timed_out:
        return []
    try:
        return parse_smbfs_mount_output(out)
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
#  健康检测
# ═══════════════════════════════════════════════════════════════
def _mount_point_mounted(mount_point: str) -> bool:
    """挂载点是否处于「smbfs 已挂载」状态。

    主判据 `os.path.ismount`（实测挂载后=True、掉载后=False，可靠）；
    补充判据：`mount` 输出里能看到该挂载点的 smbfs 行（双保险）。
    """
    try:
        if os.path.ismount(mount_point):
            return True
    except OSError:
        pass
    try:
        mp_real = os.path.realpath(mount_point)
        for m in parse_smb_mounts():
            if os.path.realpath(m.get("mount_point", "")) == mp_real:
                return True
    except Exception:
        pass
    return False


def _probe_target_path(mount_point: str) -> str:
    """选择探测路径：archive_root 在挂载点之下则用 archive_root，否则退回挂载点。"""
    try:
        cfg = load_config() or {}
    except Exception:
        cfg = {}
    root = str(cfg.get("archive_root") or "").strip()
    if not root:
        return mount_point
    root = os.path.abspath(os.path.expanduser(root))
    mp = os.path.abspath(os.path.expanduser(mount_point))
    mp_norm = mp.rstrip(os.sep)
    if root == mp or (mp_norm and root.startswith(mp_norm + os.sep)):
        return root
    return mp


def _probe_reachable(path: str, timeout: float) -> tuple[bool, bool]:
    """在线程中探测 path 是否可达（读权限内）。返回 (reachable, timed_out)。

    stale mount 下访问挂载点可能 hang —— 放到 daemon 线程 + join(timeout)，超时立即
    返回，绝不拖死调用方（Flask 请求线程）。
    """
    state = {"ok": False, "done": False}

    def _work():
        try:
            state["ok"] = os.path.isdir(path) and os.access(path, os.R_OK)
        except OSError:
            state["ok"] = False
        finally:
            state["done"] = True

    th = threading.Thread(target=_work, name="smb-probe", daemon=True)
    th.start()
    th.join(max(0.0, float(timeout)))
    if not state["done"]:
        return False, True
    return bool(state["ok"]), False


def check_mount_health(probe_timeout: float = 3.0) -> dict:
    """共享盘挂载健康检测。

    Returns:
        {"healthy": bool, "mounted": bool, "probe_ok": bool, "reason": str}

        reason ∈ {ok, not_mounted, probe_missing, timeout}。
        健康 = 挂载点被识别为 smbfs 挂载 **且** archive_root 探测路径可达。
        未配置 `smb_share` 时视为「不适用」，返回 healthy=True/mounted=False/reason=ok
        （此时归档根是本机路径，自愈逻辑应保持中立、不介入）。
    """
    cfg = get_mount_config()
    if not cfg:
        return {"healthy": True, "mounted": False, "probe_ok": True, "reason": "ok"}

    mount_point = cfg["mount_point"]
    if not _mount_point_mounted(mount_point):
        return {"healthy": False, "mounted": False, "probe_ok": False, "reason": "not_mounted"}

    probe_path = _probe_target_path(mount_point)
    ok, timed_out = _probe_reachable(probe_path, probe_timeout)
    if timed_out:
        return {"healthy": False, "mounted": True, "probe_ok": False, "reason": "timeout"}
    if not ok:
        return {"healthy": False, "mounted": True, "probe_ok": False, "reason": "probe_missing"}
    return {"healthy": True, "mounted": True, "probe_ok": True, "reason": "ok"}


# ═══════════════════════════════════════════════════════════════
#  重挂动作（策略链：open → osascript → mount_smbfs）
# ═══════════════════════════════════════════════════════════════
def _remaining(deadline: float) -> float:
    return deadline - time.monotonic()


def _cmd_timeout(deadline: float) -> float:
    return max(0.5, min(15.0, _remaining(deadline)))


def _verify_until_healed(deadline: float, wait_budget: float | None = None
                         ) -> tuple[bool, dict]:
    """轮询复验，直到共享盘恢复可用、或本轮等待预算耗尽。

    `open`/`osascript` 触发的挂载是**异步**的（实测 `open -g` 约 5.1s 完成），
    固定短暂 sleep 会导致「刚触发 → 立刻复验 → 误判失败 → 跳到下一方案」。
    这里按 `_POLL_INTERVAL` 轮询，且受 min(deadline, now+wait_budget) 约束，
    绝不越过总 timeout 预算，也不会让首选方案饿死兜底方案。
    """
    if wait_budget is None:
        wait_budget = _STRATEGY_WAIT
    sub_deadline = min(deadline, time.monotonic() + max(0.0, float(wait_budget)))
    verify = check_mount_health()
    if _is_healed(verify):
        return True, verify
    while time.monotonic() < sub_deadline:
        time.sleep(min(_POLL_INTERVAL, max(0.0, sub_deadline - time.monotonic())))
        verify = check_mount_health()
        if _is_healed(verify):
            return True, verify
    return False, verify


def _run_open(server: str, share: str, deadline: float) -> str:
    """主方案：`open -g "smb://<server>/<share>"`（后台打开，由系统 URL 处理器挂载）。

    实测结论（2026-09 受控复现，本机）：
      * `diskutil unmount force` 后，`open -g smb://...` rc=0，约 **5.1s** 后
        `mount` 出现 smbfs 行、/Volumes/File 恢复且归档根可达；
      * `-g`（后台打开）**不影响挂载能力**——用它避免在服务器屏幕上弹出 Finder 窗口。

    返回诊断字符串（空串=命令本身无异常）；成功与否一律由调用方**轮询复验**决定。
    """
    if sys.platform != "darwin":
        return "非 macOS，跳过 open"
    url = f"smb://{server}/{share}"
    rc, _out, err, timed_out = _run_command(["/usr/bin/open", "-g", url],
                                            _cmd_timeout(deadline))
    if timed_out:
        return "open 超时"
    if rc != 0:
        return f"rc={rc} {err.strip()[:200]}".strip()
    return ""


def _run_osascript(server: str, share: str, deadline: float) -> str:
    """备选方案：`osascript -e 'mount volume "smb://server/share"'`。

    ⚠️ 本机实测**不可用**：退出码 1、stderr 报 `-5014` = `errAEEventNotPermitted`
    （TCC 自动化权限被拒），挂载点并未恢复。因此它已从主方案降级为备选——
    保留是因为换机器、或给该进程授予 AppleEvent 自动化权限后仍可能可用。

    返回**诊断字符串**（空串=命令本身无异常）。**不解读退出码**——见模块头事实①。
    """
    if sys.platform != "darwin":
        return "非 macOS，跳过 osascript"
    cmd = ["/usr/bin/osascript", "-e", f'mount volume "smb://{server}/{share}"']
    rc, _out, err, timed_out = _run_command(cmd, _cmd_timeout(deadline))
    if timed_out:
        return "osascript 超时"
    if rc != 0:
        return f"rc={rc} {err.strip()[:200]}".strip()
    return ""


def _run_mount_smbfs(server: str, share: str, mount_point: str, deadline: float) -> str:
    """最后兜底：`mount_smbfs -N //user@server/share mount_point`（-N = 用钥匙串免密）。

    仅在挂载点目录**已存在**时可用；不存在时**不强行 makedirs**（/Volumes 无写权限），
    直接回报不可创建，交由上层维持 root_unavailable 语义。
    """
    if sys.platform != "darwin":
        return "非 macOS，跳过 mount_smbfs"
    if not os.path.isdir(mount_point):
        try:
            os.makedirs(mount_point, exist_ok=True)
        except OSError as exc:
            return f"挂载点不可创建: {exc}"
    if not os.path.isdir(mount_point):
        return f"挂载点不存在: {mount_point}"
    url = f"//{SMB_USER}@{server}/{share}"
    cmd = ["/sbin/mount_smbfs", "-N", url, mount_point]
    rc, _out, err, timed_out = _run_command(cmd, _cmd_timeout(deadline))
    if timed_out:
        return "mount_smbfs 超时"
    if rc != 0:
        return f"rc={rc} {err.strip()[:200]}".strip()
    return ""


def _is_healed(health: dict) -> bool:
    """重挂是否已把共享盘恢复到「可用」：挂载点在、且不是探测超时。"""
    return bool(health.get("mounted")) and health.get("reason") != "timeout"


def _remount_and_verify(deadline: float) -> tuple[bool, str, dict]:
    """按策略链依次尝试重挂，每轮尝试后**轮询复验**真实挂载状态。

    策略链（主 → 备 → 兜底）：
        1. `open -g smb://server/share`   —— 本机实测可用（约 5s）
        2. `osascript mount volume`        —— 本机 TCC 被拒（-5014），降级备选
        3. `mount_smbfs -N`                —— 仅挂载点目录已存在时可用

    Returns:
        (healed, diagnostics, verify_health)
        healed 只看轮询复验结果，不看子进程退出码。
    """
    cfg = get_mount_config()
    if not cfg:
        return False, "未配置 smb_share", check_mount_health()
    server, share, mount_point = cfg["server"], cfg["share"], cfg["mount_point"]

    attempts = (
        ("open", lambda: _run_open(server, share, deadline)),
        ("osascript", lambda: _run_osascript(server, share, deadline)),
        ("mount_smbfs", lambda: _run_mount_smbfs(server, share, mount_point, deadline)),
    )

    errors: list[str] = []
    for name, run in attempts:
        if _remaining(deadline) <= 0:
            errors.append(f"{name}: 跳过（时间预算已耗尽）")
            break
        err = run()
        if err:
            errors.append(f"{name}: {err}")
        healed, verify = _verify_until_healed(deadline)
        if healed:
            return True, "; ".join(errors), verify

    return False, "; ".join(errors), check_mount_health()


# ═══════════════════════════════════════════════════════════════
#  事件记录
# ═══════════════════════════════════════════════════════════════
def record_event(kind: str, detail: str, source: str = "unknown",
                 health: dict | None = None) -> None:
    """记录一次掉载/重挂事件（内存环形缓冲 + 日志文件），供观察掉载规律。"""
    event = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "kind": kind,
        "detail": detail,
        "source": source,
        "health": health,
    }
    _RECENT_EVENTS.append(event)
    _append_log(f"[{event['ts']}] {kind} source={source} detail={detail} health={health}")


def _append_log(line: str) -> None:
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line.rstrip("\n") + "\n")
    except OSError:
        pass
    try:
        system_logger.info("[SMB自愈] %s", line)
    except Exception:
        pass


def get_recent_events(limit: int = 20) -> list[dict]:
    """最近的事件记录（新→旧，最多 limit 条）。"""
    events = list(_RECENT_EVENTS)
    if limit and limit > 0:
        events = events[-limit:]
    return list(reversed(events))


# ═══════════════════════════════════════════════════════════════
#  对外主入口：ensure_mount（幂等 + 单飞 + 限时）
# ═══════════════════════════════════════════════════════════════
def ensure_mount(timeout: float = 20.0, source: str = "unknown",
                 force: bool = False) -> dict:
    """确保共享盘可用；不健康时尝试重挂并复验。**幂等**。

    Args:
        timeout: 整个自愈动作的时间预算（秒），超时返回 reason="timeout"。
        source:  触发来源（写日志用，如 "build_archive_dir"/"watchdog"/"api_remount"）。
        force:   True 时即使当前健康也强制尝试一次重挂（供「一键重挂」按钮使用）。

    Returns:
        {"ok": bool, "action": "none"|"remounted"|"failed"|"disabled",
         "reason": str, "error": str?, "health": dict, "source": str}

    成功判定**只看复验结果**，不看子进程退出码（见模块头事实①）。
    """
    if not is_enabled():
        return {"ok": True, "action": "disabled", "reason": "disabled", "source": source}

    try:
        budget = float(timeout)
    except (TypeError, ValueError):
        budget = 20.0
    deadline = time.monotonic() + max(0.0, budget)

    health = check_mount_health()
    if health["healthy"] and not force:
        return {"ok": True, "action": "none", "reason": "ok",
                "health": health, "source": source}

    # 挂载点在、只是探测路径不可达（多为目录尚未创建/无权限）：重挂无益，交回上层
    # 既有 root_unavailable 逻辑处理，避免无意义的重挂风暴。
    need_remount = force or (not health["mounted"]) or health["reason"] == "timeout"
    if not need_remount:
        return {"ok": False, "action": "none", "reason": health["reason"],
                "error": "挂载点已存在但归档路径不可达", "health": health, "source": source}

    # 只在「真的掉载」时记 detected_down；挂载点在（force 手动重挂 / 探测路径待复验）
    # 记 manual_remount，避免用户点一次「一键重挂」就在事件列表留下「共享盘不可用」假象。
    if health["mounted"] and health["reason"] != "timeout":
        record_event("manual_remount", "手动触发重挂（挂载点在，归档路径待复验）",
                     source, health)
    else:
        record_event("detected_down", "检测到共享盘不可用，准备重挂", source, health)

    acquired = _MOUNT_LOCK.acquire(timeout=max(0.0, _remaining(deadline)))
    if not acquired:
        record_event("remount_timeout", "等待其他重挂操作超时", source, health)
        return {"ok": False, "action": "failed", "reason": "timeout",
                "error": "等待其他重挂操作超时", "health": health, "source": source}

    try:
        # 双检：可能在等待锁期间已被其他请求/巡检修好。
        health = check_mount_health()
        if health["healthy"] and not force:
            return {"ok": True, "action": "none", "reason": "ok",
                    "health": health, "source": source}

        ok, err, verify = _remount_and_verify(deadline)
        if ok:
            record_event("remounted", err or "重挂成功", source, verify)
            return {"ok": True, "action": "remounted", "reason": verify["reason"],
                    "error": err, "health": verify, "source": source}

        if _remaining(deadline) <= 0:
            record_event("remount_timeout", err or "重挂超时", source, verify)
            return {"ok": False, "action": "failed", "reason": "timeout",
                    "error": err or "重挂超时", "health": verify, "source": source}

        record_event("remount_failed", err or "重挂失败", source, verify)
        return {"ok": False, "action": "failed", "reason": verify["reason"],
                "error": err or "重挂失败", "health": verify, "source": source}
    finally:
        _MOUNT_LOCK.release()
