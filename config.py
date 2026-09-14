"""配置管理模块 - 读写 data/config.json + .env 环境变量覆盖"""
import os
import json
from pathlib import Path

# 尝试加载 .env 文件
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 项目根目录
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
CONFIG_FILE = DATA_DIR / "config.json"


def normalize_llm_api_url(api_url: str) -> str:
    """兼容 OpenAI SDK base_url 和直接 HTTP endpoint 两种填写方式。"""
    url = (api_url or "").strip().rstrip("/")
    if not url:
        return ""
    if url.endswith("/chat/completions") or url.endswith("/responses"):
        return url
    if url.endswith("/compatible-mode/v1") or url.endswith("/v1"):
        return f"{url}/chat/completions"
    return url


def _normalize_runtime_config(config: dict) -> dict:
    for section in ("llm", "llm_intake", "llm_contract_vision", "llm_contract_text"):
        llm = config.get(section)
        if isinstance(llm, dict):
            llm["api_url"] = normalize_llm_api_url(llm.get("api_url", ""))
    return config

# 默认配置（空哨兵值，实际值由 .env 或 data/config.json 提供）
DEFAULT_CONFIG = {
    "internal_system": {
        "base_url": os.getenv("INTERNAL_BASE_URL", "http://jxywxt.dgcheshang.cn:10003"),
        "username": os.getenv("INTERNAL_USERNAME", ""),
        "password": os.getenv("INTERNAL_PASSWORD", ""),
    },
    "third_system": {
        "base_url": os.getenv("THIRD_BASE_URL", "http://jppt.dgcheshang.cn:8899"),
        "username": os.getenv("THIRD_USERNAME", ""),
        "password": os.getenv("THIRD_PASSWORD", ""),
    },
    "driving_system": {
        "base_url": os.getenv("DRIVING_BASE_URL", "https://www.guanjiaxie.com:8089"),
        "username": os.getenv("DRIVING_USERNAME", ""),
        "password": os.getenv("DRIVING_PASSWORD", ""),
    },
    "llm": {
        "api_url": os.getenv("LLM_API_URL", ""),
        "api_key": os.getenv("LLM_API_KEY", ""),
        "model": os.getenv("LLM_MODEL", "qwen-plus"),
        "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "4096")),
    },
    "llm_intake": {
        "api_url": os.getenv("LLM_INTAKE_API_URL", ""),
        "api_key": os.getenv("LLM_INTAKE_API_KEY", ""),
        "model": os.getenv("LLM_INTAKE_MODEL", ""),
        "max_tokens": int(os.getenv("LLM_INTAKE_MAX_TOKENS", "1024")),
    },
    "llm_contract_vision": {
        "api_url": os.getenv("LLM_CONTRACT_VISION_API_URL", ""),
        "api_key": os.getenv("LLM_CONTRACT_VISION_API_KEY", ""),
        "model": os.getenv("LLM_CONTRACT_VISION_MODEL", ""),
        "max_tokens": int(os.getenv("LLM_CONTRACT_VISION_MAX_TOKENS", "8192")),
        # 推理强度。默认 "none" 是必需的，不是优化：免费档多是纯推理模型，
        # thinking 会先吃满 max_tokens，导致 content 恒为 0 字（正文全空）；
        # 给大 max_tokens 又会因耗时超网关 ~300s 被掐断返回空 body。
        # 实测 nex-n2.5-pro / ling-3.0-flash-vl 在 effort=none 下 13~49s 正常出正文。
        # 置空字符串 = 不下发该参数（兼容不认识 reasoning 字段的 provider）。
        "reasoning_effort": os.getenv("LLM_CONTRACT_VISION_REASONING_EFFORT", "none"),
    },
    "llm_contract_text": {
        "api_url": os.getenv("LLM_CONTRACT_TEXT_API_URL", ""),
        "api_key": os.getenv("LLM_CONTRACT_TEXT_API_KEY", ""),
        "model": os.getenv("LLM_CONTRACT_TEXT_MODEL", ""),
        "max_tokens": int(os.getenv("LLM_CONTRACT_TEXT_MAX_TOKENS", "2048")),
    },
    "paths": {
        "reply_dir": os.getenv("REPLY_DIR", str(BASE_DIR / "回复函")),
        "contract_dir": os.getenv("CONTRACT_DIR", str(BASE_DIR / "合同文件")),
        "upload_dir": os.getenv("UPLOAD_DIR", str(BASE_DIR / "uploads")),
    },
    # 绝对路径默认值：避免相对路径按服务进程 CWD 解析导致落盘位置漂移
    "archive_root": os.getenv("ARCHIVE_ROOT", str(BASE_DIR / "案件归档")),
    # ⚠️ smb_share 必须出现在 DEFAULT_CONFIG 里：load_config() 的合并逻辑只遍历
    #    DEFAULT_CONFIG 的顶层键，文件里多出来的键会被**静默丢弃**（踩过一次：
    #    往 data/config.json 写了 smb_share，load_config() 却读不到）。
    #    留空 = 不配置，get_smb_mappings() 会回退去解析 macOS `mount` 输出。
    #    server 建议**填固定 IP 而不是主机名**——实测挂载点给出的主机名（kj-server）
    #    在部分 Windows 客户端上解析不了，UNC 路径贴过去打不开。
    "smb_share": {
        "server": os.getenv("SMB_SERVER", ""),
        "share": os.getenv("SMB_SHARE", ""),
        "mount_point": os.getenv("SMB_MOUNT_POINT", ""),
    },
}


def _ensure_dirs():
    """确保必要目录存在"""
    DATA_DIR.mkdir(exist_ok=True)
    for key in ("reply_dir", "contract_dir", "upload_dir"):
        path = Path(load_config()["paths"][key])
        path.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """读取配置，不存在则创建默认配置"""
    DATA_DIR.mkdir(exist_ok=True)
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return _normalize_runtime_config(DEFAULT_CONFIG.copy())
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # 合并缺失的默认值（深度合并一层）
    merged = DEFAULT_CONFIG.copy()
    for section, defaults in DEFAULT_CONFIG.items():
        if section in cfg:
            if isinstance(defaults, dict):
                merged[section] = {**defaults, **cfg[section]}
            else:
                merged[section] = cfg[section]
    return _normalize_runtime_config(merged)


def save_config(config: dict):
    """保存配置到文件"""
    DATA_DIR.mkdir(exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def update_config(section: str, updates: dict):
    """更新配置的某个部分"""
    cfg = load_config()
    if section in cfg and isinstance(cfg[section], dict):
        cfg[section].update(updates)
    else:
        cfg[section] = updates
    save_config(cfg)


# 模块加载时确保目录存在
_ensure_dirs()
