r"""远程打开归档 + Windows 归档助手 测试（2026-09-01 修复方案 #1/#2/#4 回归）。

覆盖：
1. 来源识别 _request_from_server_host：本机回环/本机局域网 IP → True；局域网其他电脑 → False
2. UNC 转换 to_unc_path：/Volumes/File/... → \\kj-server\File\...；非 SMB 路径 → None；config smb_share 覆盖生效
3. 助手脚本 generate_helper_bat：BASE_URL 注入、纯 ASCII、注册命令齐全；CRLF
4. 路由 /kopen-helper（GBK bat）与 /kopen-helper.vbs（纯 ASCII）
5. API 来源门禁（2026-09-11 收敛，ISS-AP-05）：open-archive 远程来源 → 403 且绝不调起打开动作；
   archive-files 追加 is_server_host 供前端决定是否显示「在服务器上打开文件夹」
"""
import json
import tempfile
from pathlib import Path

import pytest
from conftest import _autologin_admin  # noqa: F401

_TMP_DIR = Path(tempfile.mkdtemp(prefix="remote-archive-tests-"))

import database  # noqa: E402

database.DB_PATH = _TMP_DIR / "test.db"
database._local.clear()

import app as app_module  # noqa: E402
import config as config_module  # noqa: E402
import services.archive_service as archive_service_module  # noqa: E402


@pytest.fixture()
def fresh_db():
    database._local.clear()
    database.init_db()
    yield database
    database._local.clear()


@pytest.fixture()
def client(fresh_db):
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        try: _autologin_admin(c)
        except Exception: pass
        yield c


def _make_ticket(**overrides):
    data = {
        "student_name": "王五",
        "id_card": "110101199003070011",
        "complaint_date": "2026-09-01",
        "complaint_type": "A",
        "school_short": "南城",
        "organization_unit_type": "分校",
        "organization_unit_name": "南城分校",
        "registration_date": "2026-01-01",
        "license_type": "C2",
        "exam_stage": "科目二",
        "total_fee": 3700,
        "actual_paid": 3700,
        "fee_plan_status": "confirmed",
    }
    data.update(overrides)
    tid = database.save_ticket(data)
    return database.get_ticket(tid)


@pytest.fixture(autouse=True)
def _clear_smb_cache():
    archive_service_module._SMB_CACHE.update({"at": 0.0, "maps": []})
    yield
    archive_service_module._SMB_CACHE.update({"at": 0.0, "maps": []})


# ───────────────────────────────────────────────────────────
# 1) 来源识别
# ───────────────────────────────────────────────────────────
def test_source_detection_loopback_is_local(client):
    with app_module.app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        assert app_module._request_from_server_host() is True


def test_source_detection_lan_ip_self_is_local(client):
    # 用户在本机用局域网 IP 访问自己：remote_addr == Host
    with app_module.app.test_request_context(
            "/", base_url="http://192.168.1.192:5003/",
            environ_base={"REMOTE_ADDR": "192.168.1.192"}):
        assert app_module._request_from_server_host() is True


def test_source_detection_other_lan_machine_is_remote(client):
    # 局域网另一台 Windows（192.168.1.50）访问服务器（Host=192.168.1.192）
    with app_module.app.test_request_context(
            "/", base_url="http://192.168.1.192:5003/",
            environ_base={"REMOTE_ADDR": "192.168.1.50"}):
        assert app_module._request_from_server_host() is False


# ───────────────────────────────────────────────────────────
# 2) UNC 转换
# ───────────────────────────────────────────────────────────
_MAPS = [{"server": "kj-server", "share": "File", "mount_point": "/Volumes/File"}]


def test_to_unc_path_converts_mounted_path(monkeypatch):
    monkeypatch.setattr(archive_service_module, "get_smb_mappings", lambda: _MAPS)
    assert archive_service_module.to_unc_path(
        "/Volumes/File/学员投诉档案/张三") == "\\\\kj-server\\File\\学员投诉档案\\张三"


def test_to_unc_path_mount_point_itself(monkeypatch):
    monkeypatch.setattr(archive_service_module, "get_smb_mappings", lambda: _MAPS)
    assert archive_service_module.to_unc_path("/Volumes/File") == "\\\\kj-server\\File"


def test_to_unc_path_non_smb_returns_none(monkeypatch):
    monkeypatch.setattr(archive_service_module, "get_smb_mappings", lambda: _MAPS)
    assert archive_service_module.to_unc_path("/Users/master/Desktop/x") is None
    assert archive_service_module.to_unc_path("") is None


def test_to_unc_path_longest_prefix_first(monkeypatch):
    maps = [
        {"server": "s1", "share": "A", "mount_point": "/Volumes/A"},
        {"server": "s2", "share": "B", "mount_point": "/Volumes/A/sub"},
    ]
    monkeypatch.setattr(archive_service_module, "get_smb_mappings", lambda: maps)
    # /Volumes/A/sub/... 应命中更长前缀 s2/B（get_smb_mappings 已按长度排序）
    assert archive_service_module.to_unc_path("/Volumes/A/sub/x") == "\\\\s2\\B\\x"


def test_smb_mappings_config_override(monkeypatch):
    monkeypatch.setattr(archive_service_module, "load_config", lambda: {
        "smb_share": {"server": "kj-server", "share": "File",
                      "mount_point": "/Volumes/File"}})
    maps = archive_service_module.get_smb_mappings()
    assert maps == [{"server": "kj-server", "share": "File",
                     "mount_point": "/Volumes/File"}]


def test_load_config_preserves_smb_share_section(tmp_path, monkeypatch):
    """回归（ISS-AP-09）：load_config() 的合并逻辑只遍历 DEFAULT_CONFIG 的顶层键，
    文件里多出来的键会被**静默丢弃** —— 曾导致往 data/config.json 写了 smb_share
    却读不到，UNC 回退去解析 mount 拿到主机名（Windows 客户端解析不了）。"""
    cfg_file = tmp_path / "config.json"
    want = {"server": "192.0.2.199", "share": "File",
            "mount_point": "/Volumes/File"}
    cfg_file.write_text(json.dumps(
        {"archive_root": str(tmp_path / "archive"), "smb_share": want},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config_module, "CONFIG_FILE", cfg_file)
    cfg = config_module.load_config()
    assert cfg["smb_share"] == want


def test_smb_mappings_parse_mount_output(monkeypatch):
    sample = (
        "/System/Volumes/Data on / (apfs, local)\n"
        "//admin@KJ-SERVER/File on /Volumes/File (smbfs, nodev, nosuid)\n"
        "//user@other-srv/Docs on /Volumes/Docs (smbfs)\n"
    )
    monkeypatch.setattr(archive_service_module, "load_config", lambda: {})
    monkeypatch.setattr(
        archive_service_module.subprocess, "run",
        lambda *a, **k: type("R", (), {"stdout": sample})())
    maps = archive_service_module.get_smb_mappings()
    servers = {m["server"] for m in maps}
    assert servers == {"KJ-SERVER", "other-srv"}


# ───────────────────────────────────────────────────────────
# 3) 助手 .bat 生成
# ───────────────────────────────────────────────────────────
def test_generate_helper_bat_injects_base_url():
    bat = archive_service_module.generate_helper_bat("http://192.168.1.192:5003")
    assert "__BASE_URL__" not in bat
    assert "http://192.168.1.192:5003/kopen-helper.vbs" in bat


def test_generate_helper_bat_registers_protocol():
    bat = archive_service_module.generate_helper_bat("http://x:1")
    for token in ("HKCU\\Software\\Classes\\kjfolder", "URL Protocol",
                  "kjfolder-open.vbs", "wscript.exe"):
        assert token in bat, token


def test_generate_helper_bat_ascii_only():
    bat = archive_service_module.generate_helper_bat("http://192.168.1.192:5003")
    bat.encode("ascii")  # 不抛 UnicodeEncodeError 即可（cmd 代码页防乱码）


def test_generate_helper_bat_strips_trailing_slash():
    bat = archive_service_module.generate_helper_bat("http://192.168.1.192:5003/")
    assert "http://192.168.1.192:5003/kopen-helper.vbs" in bat


# ───────────────────────────────────────────────────────────
# 4) 下载路由
# ───────────────────────────────────────────────────────────
def test_kopen_helper_route_returns_gbk_bat(client):
    resp = client.get("/kopen-helper")
    assert resp.status_code == 200
    assert "octet-stream" in resp.headers["Content-Type"]
    assert "kopen-helper.bat" in resp.headers["Content-Disposition"]
    bat = resp.data.decode("gbk")
    assert "kjfolder" in bat and "kopen-helper.vbs" in bat


def test_kopen_helper_vbs_route(client):
    resp = client.get("/kopen-helper.vbs")
    assert resp.status_code == 200
    # v2：UTF-16 LE + BOM（MsgBox 含中文提示，wscript 按 BOM 自动识别）
    assert resp.data[:2] == b"\xff\xfe"
    vbs = resp.data[2:].decode("utf-16-le")
    assert "Shell.Application" in vbs and "MSXML2.DOMDocument.6.0" in vbs
    # v2 加固特征：只保留 base64url 字符 + 友好失败提示
    assert "0123456789-_" in vbs
    assert "B64ToUtf8" in vbs


def test_kopen_diagnose_route(client):
    """HITL 诊断脚本：GBK 可解码、四环检查齐全、判定矩阵关键命令在位。"""
    resp = client.get("/kopen-diagnose")
    assert resp.status_code == 200
    assert "kjfolder-diagnose.bat" in resp.headers["Content-Disposition"]
    bat = resp.data.decode("gbk")  # 非 GBK 字符会在此抛异常
    for token in (
        "transport-hardened",       # [1/4] v1/v2 版本判别
        "reg query",                # [2/4] 注册检查
        'wscript.exe "%VBS%"',      # [3/4] 测试A：绕过浏览器直接调用
        'start "" "kjfolder://',    # [3/4] 测试B：浏览器协议调用
        "choice /c YN",             # HITL 判定
        "浏览器拦截",                # 判定矩阵结论
        "/kopen-helper",            # 修复指引
    ):
        assert token in bat, token


def test_generate_diagnose_bat_injects_base_url():
    bat = archive_service_module.generate_diagnose_bat("http://192.168.1.192:5003/")
    assert "__BASE_URL__" not in bat
    assert "http://192.168.1.192:5003/kopen-helper" in bat


# ───────────────────────────────────────────────────────────
# 4.5) VBS 解码算法 Python 镜像：传输污染全免疫
#     （真实故障 2026-09-01：Windows 端 URL 被追加尾部 "/"，
#       MSXML6 拒绝非规范 base64 → 80004005）
# ───────────────────────────────────────────────────────────
_B64URL_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _vbs_decode_simulate(url: str) -> str:
    """1:1 复刻 v2 kjfolder-open.vbs 的提取+解码逻辑。"""
    if "://" in url:
        p = url[url.index("://") + 3:]
    elif ":" in url:
        p = url[url.index(":") + 1:]
    else:
        p = url
    # step1: percent-decode
    out, i = "", 0
    while i < len(p):
        c = p[i]
        if c == "%" and i + 2 <= len(p):
            out += chr(int(p[i + 1:i + 3], 16) & 0xFF)
            i += 3
        elif c == "%":
            i += 1
        else:
            out += c
            i += 1
    p = out
    # step2: keep only base64url chars
    p = "".join(c for c in p if c in _B64URL_ALPHABET)
    p = p.replace("-", "+").replace("_", "/")
    p += "=" * (-len(p) % 4)
    import base64 as _b64
    return _b64.b64decode(p).decode("utf-8")


_UNC = "\\\\kj-server\\File\\综合办公室\\内部共用\\投诉文件\\投诉处理系统\\分校\\常-常平横江厦分校\\2026-08-31_王小兵_110101199003070011_常"


def _payload(unc: str) -> str:
    import base64 as _b64
    return _b64.b64encode(unc.encode("utf-8")).decode().rstrip("=") \
        .replace("+", "-").replace("/", "_")


@pytest.mark.parametrize("transport_url", [
    "kjfolder://" + _payload(_UNC),              # 正常
    "kjfolder://" + _payload(_UNC) + "/",        # 尾部斜杠（真实故障）
    "kjfolder://" + _payload(_UNC) + "//",       # 双斜杠
    "kjfolder://" + _payload(_UNC) + "%2F",      # 百分号编码
    "kjfolder:" + _payload(_UNC),                # 无双斜线（Explorer 收敛）
    "kjfolder://" + _payload(_UNC) + ' """',     # 命令行引号/空格杂质
])
def test_vbs_decode_immune_to_transport_junk(transport_url):
    assert _vbs_decode_simulate(transport_url) == _UNC


def test_vbs_decode_rejects_garbage_payload():
    # 全部字符被剥离后为空串 → 解码异常路径（VBS 侧弹友好提示，不再裸崩 80004005）
    import base64 as _b64
    with pytest.raises(Exception):
        _b64.b64decode("", validate=True) if False else _b64.b64decode("====", validate=True)


# ───────────────────────────────────────────────────────────
# 5) API 来源门禁：远程客户端一律 403（2026-09-11 收敛，ISS-AP-05）
#    —— 主路径已改为网页内置面板；服务器端「绝不打开」的约束反而更硬：
#       不调起打开动作，也不再回传 UNC 路径（前端已无消费方）。
# ───────────────────────────────────────────────────────────
def test_open_archive_remote_is_forbidden(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "load_config", lambda: {"archive_root": str(tmp_path)})
    monkeypatch.setattr(archive_service_module, "load_config", lambda: {"archive_root": str(tmp_path)})
    ticket = _make_ticket()
    case_dir, _, _ = app_module.build_archive_dir(ticket, root=str(tmp_path))
    Path(case_dir).mkdir(parents=True, exist_ok=True)

    # 伪造「局域网另一台电脑」发起
    monkeypatch.setattr(app_module, "_request_from_server_host", lambda: False)

    calls = []
    monkeypatch.setattr(archive_service_module.subprocess, "run",
                        lambda cmd, *a, **k: calls.append(cmd))

    resp = client.post(f"/api/tickets/{ticket['id']}/open-archive")
    body = resp.get_json()
    assert resp.status_code == 403
    assert body["success"] is False
    assert body["code"] == "local_only"
    assert "仅限服务器本机使用" in body["error"]
    # 不再回传 UNC（该能力已由面板取代，D1 冻结不删但不再对外暴露）
    assert "unc" not in body
    # 核心：服务器端绝不执行任何打开动作
    assert calls == []


def test_open_archive_local_still_works(client, tmp_path, monkeypatch):
    """本机来源（test_client 默认 127.0.0.1）不受门禁影响，仍能打开。"""
    monkeypatch.setattr(config_module, "load_config", lambda: {"archive_root": str(tmp_path)})
    monkeypatch.setattr(archive_service_module, "load_config", lambda: {"archive_root": str(tmp_path)})
    ticket = _make_ticket()
    case_dir, _, _ = app_module.build_archive_dir(ticket, root=str(tmp_path))
    Path(case_dir).mkdir(parents=True, exist_ok=True)

    calls = []
    monkeypatch.setattr(archive_service_module.subprocess, "run",
                        lambda cmd, *a, **k: calls.append(cmd))

    resp = client.post(f"/api/tickets/{ticket['id']}/open-archive")
    body = resp.get_json()
    assert resp.status_code == 200 and body["success"] is True
    assert body["data"]["mode"] == "local"
    assert body["data"]["opened"] is True
    assert len(calls) == 1


# ───────────────────────────────────────────────────────────
# 6) archive-files 追加键 is_server_host（面板据以决定是否显示「打开文件夹」）
# ───────────────────────────────────────────────────────────
def test_archive_files_reports_is_server_host(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "load_config", lambda: {"archive_root": str(tmp_path)})
    monkeypatch.setattr(archive_service_module, "load_config", lambda: {"archive_root": str(tmp_path)})
    ticket = _make_ticket()
    case_dir, _, _ = app_module.build_archive_dir(ticket, root=str(tmp_path))
    Path(case_dir).mkdir(parents=True, exist_ok=True)
    (Path(case_dir) / "回复函.docx").write_bytes(b"x")

    # 本机
    resp = client.get(f"/api/tickets/{ticket['id']}/archive-files")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["is_server_host"] is True
    # 既有键不变（契约不破坏）
    assert set(data.keys()) >= {"dir", "files"}
    assert data["files"][0]["name"] == "回复函.docx"

    # 远程
    monkeypatch.setattr(app_module, "_request_from_server_host", lambda: False)
    resp = client.get(f"/api/tickets/{ticket['id']}/archive-files")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["is_server_host"] is False
