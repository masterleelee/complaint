#!/usr/bin/env python3
"""SMB 共享盘挂载巡检（launchd 定时任务，ISS-SMB-01）。

由 `com.complaint.smbmount` 每 60s 调用一次：检测共享盘挂载健康，不健康则尝试重挂
（复用 services.smb_mount_service.ensure_mount 的「检测→重挂→复验→单飞→限时」闭环）。

行为约定：
  * 健康态（action=none）静默退出 0，**不写日志**，避免每 60s 刷屏；
  * 重挂/失败/超时才落一行日志到 /tmp/complaint_smbmount.log（便于观察掉载规律）；
  * 退出码：0=健康或已恢复；1=重挂失败/超时（供 launchd 与人工观察）。

注意：本脚本以绝对路径工作，不依赖 PYTHONPATH；生产由 venv 解释器运行。
"""
import os
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from services.smb_mount_service import (  # noqa: E402
    ensure_mount, is_enabled, LOG_PATH,
)


def _log(line: str) -> None:
    """写巡检日志（同时落文件与 stdout，供 launchd StandardOutPath 收集）。"""
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line.rstrip("\n") + "\n")
    except OSError:
        pass
    print(line, flush=True)


def main() -> int:
    if not is_enabled():
        # 自愈被显式禁用（环境变量 SMB_AUTOMOUNT_DISABLED）→ 不动作
        return 0

    result = ensure_mount(timeout=25.0, source="watchdog")
    action = result.get("action")

    if action == "disabled":
        return 0
    if action in ("remounted", "failed"):
        _log(f"[watchdog] action={action} ok={result.get('ok')} "
             f"reason={result.get('reason')} error={result.get('error')}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
