"""SMB 共享盘挂载自愈机制测试（TDD）。

背景（2026-09 交付总监现场审计 + 受控复现）：
服务器把归档文件写到 macOS SMB 挂载点 `/Volumes/File`，该挂载点会**不定时自行消失**
（`mount` 输出里 smbfs 行消失、挂载点目录不存在），系统原先**没有任何自动重挂机制**，
于是 11 处依赖 `build_archive_dir` / `resolve_case_dir` 的归档功能同时失效。

关键实测结论（决定实现正确性）：
  * 主方案是 `open -g "smb://server/share"`（实测可用，约 5s 异步完成挂载）；
  * `osascript mount volume` 本机**不可用**（rc=1、`-5014` = TCC 自动化权限被拒），
    降级为备选；
  * `mount_smbfs -N` 为最后兜底；
  * 子进程退出码**不可信**，成败一律看「轮询复验挂载点真实状态」。

本文件覆盖：
  1. 健康检测四个分支（已挂载 / 未挂载 / 探测路径缺失 / 超时）
  2. 策略链顺序（open 优先于 osascript）
  3. **核心陷阱回归**：退出码非 0（-5014）但复验显示已挂载 → 必须判成功
  4. **轮询复验**：模拟异步挂载（前几次 False、第 N 次 True）→ 判成功、不过早跳方案
  5. 并发单飞、超时降级、幂等性
  6. 测试隔离：LOG_PATH 不污染生产日志；force 健康时不产生 detected_down
  7. 接入归档链路：未挂载 → 归档 API 自动恢复并返回 200

⚠️ 全部用例均以 monkeypatch 打桩，**绝不真的挂载/卸载生产共享盘**。
"""
import os
import threading
import time
from pathlib import Path

import pytest

import services.smb_mount_service as smb


# ═══════════════════════════════════════════════════════════════
#  可切换的假环境：一个 mounted 状态位 + 各方案是否生效
# ═══════════════════════════════════════════════════════════════
class _FakeMount:
    def __init__(self, mount_point: str):
        self.mount_point = mount_point
        self.mounted = False              # 当前是否挂载
        self.probe_ok = True              # 探测路径是否可达
        self.probe_timeout = False        # 探测是否超时
        self.calls: list[str] = []        # 策略调用顺序
        self.open_effective = True        # 主方案 open -g 是否生效
        self.osascript_effective = False  # 本机 osascript 不可用（TCC -5014）
        self.smbfs_effective = False      # mount_smbfs 兜底是否生效
        self.osascript_rc = 1             # osascript 恒返回非 0（-5014）


@pytest.fixture()
def smb_env(monkeypatch, tmp_path):
    """假共享盘环境：mount_point 为 tmp_path 下的真实目录，避免碰 /Volumes。"""
    mp = str(tmp_path / "mnt")
    Path(mp).mkdir()
    env = _FakeMount(mp)

    monkeypatch.setattr(smb, "get_mount_config", lambda: {
        "server": "192.0.2.199", "share": "File", "mount_point": mp})
    monkeypatch.setattr(smb, "_mount_point_mounted", lambda point: env.mounted)
    monkeypatch.setattr(smb, "_probe_reachable",
                        lambda path, timeout: (env.probe_ok, env.probe_timeout))
    # 轮询参数调小，令测试快速
    monkeypatch.setattr(smb, "_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(smb, "_STRATEGY_WAIT", 0.05)

    def _open(server, share, deadline):
        env.calls.append("open")
        if env.open_effective:
            env.mounted = True
        return ""

    def _osascript(server, share, deadline):
        env.calls.append("osascript")
        if env.osascript_effective:
            env.mounted = True
        return f"rc={env.osascript_rc} -5014" if env.osascript_rc else ""

    def _smbfs(server, share, mount_point, deadline):
        env.calls.append("mount_smbfs")
        if env.smbfs_effective:
            env.mounted = True
        return ""

    monkeypatch.setattr(smb, "_run_open", _open)
    monkeypatch.setattr(smb, "_run_osascript", _osascript)
    monkeypatch.setattr(smb, "_run_mount_smbfs", _smbfs)

    smb.set_enabled(True)
    yield env
    smb.reset_enabled()


# ═══════════════════════════════════════════════════════════════
#  1) 健康检测四分支
# ═══════════════════════════════════════════════════════════════
class TestCheckMountHealth:
    def test_not_mounted(self, smb_env):
        smb_env.mounted = False
        assert smb.check_mount_health() == {
            "healthy": False, "mounted": False, "probe_ok": False, "reason": "not_mounted"}

    def test_mounted_and_probe_ok_is_healthy(self, smb_env):
        smb_env.mounted = True
        h = smb.check_mount_health()
        assert h["healthy"] is True and h["mounted"] is True and h["reason"] == "ok"

    def test_mounted_but_probe_missing(self, smb_env):
        smb_env.mounted = True
        smb_env.probe_ok = False
        assert smb.check_mount_health() == {
            "healthy": False, "mounted": True, "probe_ok": False, "reason": "probe_missing"}

    def test_mounted_but_probe_timeout(self, smb_env):
        smb_env.mounted = True
        smb_env.probe_ok = False
        smb_env.probe_timeout = True
        h = smb.check_mount_health()
        assert h["healthy"] is False and h["mounted"] is True and h["reason"] == "timeout"

    def test_no_config_is_neutral(self, monkeypatch):
        monkeypatch.setattr(smb, "get_mount_config", lambda: None)
        h = smb.check_mount_health()
        assert h["healthy"] is True and h["mounted"] is False


# ═══════════════════════════════════════════════════════════════
#  2) 策略链顺序：open(主) → osascript(备) → mount_smbfs(兜底)
# ═══════════════════════════════════════════════════════════════
class TestStrategyChain:
    def test_open_is_primary_and_osascript_not_called(self, smb_env):
        smb_env.mounted = False
        r = smb.ensure_mount()
        assert r["ok"] is True and r["action"] == "remounted"
        assert smb_env.calls == ["open"], "open 必须作主方案且成功后不再调用备选"

    def test_core_trap_osascript_rc_nonzero_but_remounted_is_success(self, smb_env):
        """核心回归：open 不生效 → osascript 报 -5014（rc≠0）但复验已挂载 → 必须判成功。"""
        smb_env.mounted = False
        smb_env.open_effective = False
        smb_env.osascript_effective = True
        smb_env.osascript_rc = 1
        r = smb.ensure_mount()
        assert r["ok"] is True, r
        assert r["action"] == "remounted"
        assert smb_env.calls == ["open", "osascript"]

    def test_full_chain_falls_through_to_smbfs(self, smb_env):
        smb_env.mounted = False
        smb_env.open_effective = False
        smb_env.osascript_effective = False
        smb_env.smbfs_effective = True
        r = smb.ensure_mount()
        assert r["ok"] is True and r["action"] == "remounted"
        assert smb_env.calls == ["open", "osascript", "mount_smbfs"]

    def test_all_strategies_fail(self, smb_env):
        smb_env.mounted = False
        smb_env.open_effective = False
        smb_env.osascript_effective = False
        smb_env.smbfs_effective = False
        r = smb.ensure_mount()
        assert r["ok"] is False and r["action"] == "failed"
        assert smb_env.calls == ["open", "osascript", "mount_smbfs"]


# ═══════════════════════════════════════════════════════════════
#  3) 轮询复验：异步挂载不得过早判失败
# ═══════════════════════════════════════════════════════════════
class TestPollingVerification:
    def test_polling_waits_for_async_mount(self, monkeypatch, tmp_path):
        """模拟 open 触发的异步挂载：open 后连续 2 次复验 False、第 3 次 True。"""
        mp = str(tmp_path / "mnt")
        Path(mp).mkdir()
        state = {"open_called": False, "polls": 0, "osascript_called": False}

        monkeypatch.setattr(smb, "get_mount_config", lambda: {
            "server": "192.0.2.199", "share": "File", "mount_point": mp})

        def _mounted(point):
            if not state["open_called"]:
                return False
            state["polls"] += 1
            return state["polls"] >= 3  # 异步：open 后第 3 次复验才挂上

        monkeypatch.setattr(smb, "_mount_point_mounted", _mounted)
        monkeypatch.setattr(smb, "_probe_reachable", lambda path, timeout: (True, False))
        monkeypatch.setattr(smb, "_POLL_INTERVAL", 0.01)
        monkeypatch.setattr(smb, "_STRATEGY_WAIT", 1.0)

        def _open(server, share, deadline):
            state["open_called"] = True
            return ""

        def _osascript(server, share, deadline):
            state["osascript_called"] = True  # 不应被调用
            return ""

        monkeypatch.setattr(smb, "_run_open", _open)
        monkeypatch.setattr(smb, "_run_osascript", _osascript)

        smb.set_enabled(True)
        try:
            r = smb.ensure_mount(timeout=5.0)
        finally:
            smb.reset_enabled()

        assert r["ok"] is True and r["action"] == "remounted", r
        assert state["osascript_called"] is False, "复验过早放弃，误跳到下一方案"


# ═══════════════════════════════════════════════════════════════
#  4) 幂等 / 超时 / probe_missing / force
# ═══════════════════════════════════════════════════════════════
class TestEnsureMount:
    def test_idempotent_when_healthy_no_subprocess(self, smb_env):
        smb_env.mounted = True
        r = smb.ensure_mount()
        assert r["ok"] is True and r["action"] == "none"
        assert smb_env.calls == []

    def test_zero_timeout_returns_timeout(self, smb_env):
        smb_env.mounted = False
        r = smb.ensure_mount(timeout=0.0)
        assert r["ok"] is False and r["reason"] == "timeout"

    def test_probe_missing_does_not_remount(self, smb_env):
        """挂载点在、仅探测路径不可达（多为目录未创建）→ 重挂无益，不动手。"""
        smb_env.mounted = True
        smb_env.probe_ok = False
        r = smb.ensure_mount()
        assert r["ok"] is False and r["action"] == "none"
        assert r["reason"] == "probe_missing"
        assert smb_env.calls == []

    def test_force_remounts_even_when_probe_missing(self, smb_env):
        """用户手点「一键重挂」= force：即使探测路径不可达也强制尝试重挂。"""
        smb_env.mounted = True
        smb_env.probe_ok = False
        smb.ensure_mount(force=True)
        assert smb_env.calls[:1] == ["open"]

    def test_force_when_healthy_no_detected_down_event(self, smb_env):
        """force 且健康时不得留下「检测到共享盘不可用」假事件（QA 发现）。"""
        smb_env.mounted = True
        before = len(smb.get_recent_events(10000))
        smb.ensure_mount(force=True)
        events = smb.get_recent_events(10000)
        new = events[: len(events) - before]
        kinds = [e["kind"] for e in new]
        assert "detected_down" not in kinds, kinds
        assert "manual_remount" in kinds
        assert smb_env.calls[:1] == ["open"]


# ═══════════════════════════════════════════════════════════════
#  5) 并发单飞
# ═══════════════════════════════════════════════════════════════
class TestSingleFlight:
    def test_concurrent_ensure_mount_remounts_once(self, smb_env, monkeypatch):
        smb_env.mounted = False
        barrier = threading.Barrier(4)

        def _slow_open(server, share, deadline):
            smb_env.calls.append("open")
            time.sleep(0.2)
            smb_env.mounted = True
            return ""

        monkeypatch.setattr(smb, "_run_open", _slow_open)
        monkeypatch.setattr(smb, "_STRATEGY_WAIT", 5.0)

        results = []

        def _worker():
            barrier.wait(timeout=5)
            results.append(smb.ensure_mount(timeout=5.0))

        threads = [threading.Thread(target=_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert smb_env.calls.count("open") == 1, "并发单飞失败：发生了重复重挂"
        assert len(results) == 4 and all(r["ok"] for r in results)


# ═══════════════════════════════════════════════════════════════
#  6) 工具函数 / 测试隔离
# ═══════════════════════════════════════════════════════════════
class TestHelpers:
    def test_get_mount_config_none_when_unset(self, monkeypatch):
        monkeypatch.setattr(smb, "load_config", lambda: {
            "smb_share": {"server": "", "share": "", "mount_point": ""}})
        assert smb.get_mount_config() is None

    def test_get_mount_config_returns_normalized_mapping(self, monkeypatch):
        monkeypatch.setattr(smb, "load_config", lambda: {
            "smb_share": {"server": "192.0.2.199", "share": "File",
                          "mount_point": "/Volumes/File"}})
        assert smb.get_mount_config() == {
            "server": "192.0.2.199", "share": "File", "mount_point": "/Volumes/File"}

    def test_parse_smb_mounts_reuses_archive_parser(self, monkeypatch):
        out = ("//all@192.0.2.199/File on /Volumes/File (smbfs, nodev, nosuid, "
               "mounted by all)\n")
        monkeypatch.setattr(smb, "_run_command",
                            lambda cmd, timeout: (0, out, "", False))
        assert smb.parse_smb_mounts() == [
            {"server": "192.0.2.199", "share": "File", "mount_point": "/Volumes/File"}]

    def test_parse_smb_mounts_empty_on_timeout(self, monkeypatch):
        monkeypatch.setattr(smb, "_run_command",
                            lambda cmd, timeout: (-1, "", "timeout", True))
        assert smb.parse_smb_mounts() == []

    def test_disabled_switch_never_runs_subprocess(self, monkeypatch):
        called = []
        monkeypatch.setattr(smb, "_run_command",
                            lambda *a, **k: called.append("run") or (0, "", "", False))
        monkeypatch.setattr(smb, "_run_open",
                            lambda *a, **k: called.append("open") or "")
        smb.set_enabled(False)
        try:
            assert smb.is_enabled() is False
            r = smb.ensure_mount()
            assert r["ok"] is True and r["action"] == "disabled"
            assert called == []
        finally:
            smb.reset_enabled()

    def test_log_path_isolated_to_tmp(self, tmp_path):
        """conftest 必须把 LOG_PATH 隔离到 tmp_path，避免污染生产日志（QA 发现）。"""
        assert os.path.dirname(smb.LOG_PATH) == str(tmp_path)
        assert smb.LOG_PATH != "/tmp/complaint_smbmount.log"

    def test_record_event_writes_isolated_log(self, tmp_path):
        smb.record_event("detected_down", "unit-test-marker", "test")
        assert smb.LOG_PATH != "/tmp/complaint_smbmount.log"
        with open(smb.LOG_PATH, encoding="utf-8") as f:
            assert "unit-test-marker" in f.read()


# ═══════════════════════════════════════════════════════════════
#  7) 集成：未挂载 → 归档 API 自动恢复并返回 200
# ═══════════════════════════════════════════════════════════════
@pytest.fixture()
def _api_client(tmp_path):
    """隔离 SQLite + 已登录 admin 的 Flask 测试客户端（自建自清，不污染其他用例）。"""
    import database
    import app as app_module
    from conftest import _autologin_admin

    prev_path = getattr(database, "DB_PATH", None)
    database.DB_PATH = tmp_path / "smb-test.db"
    database._local.clear()
    database.init_db()
    app_module.app.config["TESTING"] = True
    try:
        with app_module.app.test_client() as c:
            try:
                _autologin_admin(c)
            except Exception:
                pass
            yield c
    finally:
        database._local.clear()
        if prev_path is not None:
            database.DB_PATH = prev_path


def _new_ticket_api():
    import database
    ticket_id = database.save_ticket({
        "student_name": "孙七",
        "id_card": "110101199003070011",
        "complaint_date": "2026-09-01",
        "complaint_type": "A",
        "school_short": "南城",
        "organization_unit_type": "分校",
        "organization_unit_name": "南城分校",
        "fee_plan_status": "confirmed",
    })
    return database.get_ticket(ticket_id)


class TestArchiveApiSelfHeal:
    def test_archive_api_recovers_after_remount(self, _api_client, smb_env, monkeypatch):
        import app as app_module
        import services.archive_service as archive_service_module

        root = str(Path(smb_env.mount_point) / "综合办公室" / "投诉处理系统")
        monkeypatch.setattr(archive_service_module, "load_config",
                            lambda: {"archive_root": root})
        monkeypatch.setattr(app_module, "load_config", lambda: {"archive_root": root})

        ticket = _new_ticket_api()
        case_dir = Path(app_module.build_archive_dir(ticket, root=root, heal=False)[0])
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "投诉登记表.docx").write_bytes(b"%PK")

        smb_env.mounted = False  # 模拟挂载点已消失
        resp = _api_client.get(f"/api/tickets/{ticket['id']}/archive-files")

        assert resp.status_code == 200, resp.get_json()
        assert smb_env.mounted is True, "归档链路未触发自愈"
        assert smb_env.calls.count("open") == 1

    def test_build_archive_dir_triggers_heal(self, smb_env, tmp_path, monkeypatch):
        """build_archive_dir 前置自愈：未挂载时被自动重挂。"""
        import services.archive_service as archive_service_module
        root = str(Path(smb_env.mount_point) / "归档")
        monkeypatch.setattr(archive_service_module, "load_config",
                            lambda: {"archive_root": root})
        ticket = {
            "complaint_date": "2026-09-01", "student_name": "张三",
            "id_card": "110101199003070011", "school_short": "南城",
            "organization_unit_type": "分校", "organization_unit_name": "南城分校",
        }
        smb_env.mounted = False
        archive_service_module.build_archive_dir(ticket, root=root)
        assert smb_env.mounted is True
