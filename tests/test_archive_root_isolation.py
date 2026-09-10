"""ISS-AP-00 回归：conftest 的「归档根隔离」必须对所有用例生效。

背景（2026-09-10 审计实测）：
`app.py:32` 与 `services/archive_service.py:24` 都是 `from config import load_config`
—— 导入时**直接绑定**函数对象。测试若只写 `monkeypatch.setattr(config_module,
"load_config", ...)`，对这两个模块完全无效，归档路由仍会读真实
`data/config.json` 的 `archive_root`（/Volumes/File/... SMB 共享盘）：

* 共享盘未挂载 → `PermissionError` / ValueError → 归档域 6 例批量失败；
* 共享盘已挂载 → 测试文件被真实写进生产归档目录（数据污染）。

本文件把「隔离已生效」固化为断言，防止该 bug 以任何形式复发。
"""
import json
import os

import app as app_module
import config as config_module
import services.archive_service as archive_service_module


def _real_archive_root() -> str:
    """绕开一切打桩，直接读盘取真实配置里的 archive_root。"""
    if not os.path.isfile(config_module.CONFIG_FILE):
        return ""
    with open(config_module.CONFIG_FILE, encoding="utf-8") as f:
        return str(json.load(f).get("archive_root") or "")


def test_both_modules_archive_root_isolated_to_tmp(tmp_path):
    """app / archive_service 两处 load_config 都必须指向本用例 tmp_path。"""
    expected = os.path.realpath(str(tmp_path))
    for mod in (app_module, archive_service_module):
        got = str(mod.load_config().get("archive_root") or "")
        assert got, f"{mod.__name__}.load_config 未返回 archive_root"
        assert os.path.isdir(got), f"{mod.__name__} 的归档根不存在: {got}"
        assert os.path.realpath(got) == expected, (
            f"{mod.__name__} 的归档根未隔离到 tmp_path: {got}")


def test_isolated_root_never_under_real_root(tmp_path):
    """隔离后的归档根不得落在真实归档根之下（否则仍会污染生产目录）。"""
    real = _real_archive_root()
    got = os.path.realpath(str(app_module.load_config()["archive_root"]))
    if not real:
        return  # 配置缺失时无从比较（load_config 会用默认值）
    real_abs = os.path.realpath(real)
    assert got != real_abs
    assert not got.startswith(real_abs + os.sep), f"{got} 落在真实归档根 {real_abs} 内"


def test_legacy_config_module_patch_no_longer_leaks(monkeypatch, tmp_path):
    """历史错误写法（只 patch config 模块）不再让 app 层读到真实归档根。

    这正是 6 例归档失败用例的成因；本断言把它变成"已修复"的可执行证据。
    """
    legacy_root = str(tmp_path / "legacy-root")
    monkeypatch.setattr(config_module, "load_config",
                        lambda: {"archive_root": legacy_root})
    got = str(app_module.load_config().get("archive_root") or "")
    assert os.path.realpath(got) != os.path.realpath(legacy_root)
    assert os.path.realpath(got) == os.path.realpath(str(tmp_path))


def test_other_config_keys_kept_real():
    """只覆盖 archive_root，其余键必须保留真实值（避免波及非归档用例）。"""
    cfg = app_module.load_config()
    assert isinstance(cfg.get("paths"), dict)
    for section in ("internal_system", "third_system", "driving_system", "llm"):
        assert section in cfg, f"配置段 {section} 被打桩丢失"
