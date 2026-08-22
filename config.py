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
        "max_tokens": int(os.getenv("LLM_CONTRACT_VISION_MAX_TOKENS", "2048")),
    },
    "llm_contract_text": {
        "api_url": os.getenv("LLM_CONTRACT_TEXT_API_URL", ""),
        "api_key": os.getenv("LLM_CONTRACT_TEXT_API_KEY", ""),
        "model": os.getenv("LLM_CONTRACT_TEXT_MODEL", ""),
        "max_tokens": int(os.getenv("LLM_CONTRACT_TEXT_MAX_TOKENS", "2048")),
    },
    "feishu": {
        "app_id": os.getenv("FEISHU_APP_ID", ""),
        "app_secret": os.getenv("FEISHU_APP_SECRET", ""),
        "bitable_app_token": os.getenv("FEISHU_BITABLE_TOKEN", ""),
        "bitable_table_id": os.getenv("FEISHU_TABLE_ID", ""),
    },
    "paths": {
        "reply_dir": os.getenv("REPLY_DIR", str(BASE_DIR / "回复函")),
        "contract_dir": os.getenv("CONTRACT_DIR", str(BASE_DIR / "合同文件")),
        "upload_dir": os.getenv("UPLOAD_DIR", str(BASE_DIR / "uploads")),
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
