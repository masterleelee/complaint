"""百度云 OCR 客户端 —— 合同图片 OCR 主路。

规格：`.scratch/contract-ocr-baidu/spec.md` v3（§5 代码接入点）。
- 密钥优先级：环境变量 `BAIDU_OCR_API_KEY` / `BAIDU_OCR_SECRET_KEY`
  → `data/config.json` 的 `baidu_ocr` 段（`empty_env_fallback=True` 时回落）。
- `extract_pages_via_baidu` 返回 **dict**（printed / handwriting 两份独立文本）：
  两个接口的原始文本**严禁拼接**（实测拼接后 extract_contract_fees 全 0、模板无匹配），
  字段层合并由 `contract_service` 编排。
- access_token 进程内缓存（百度有效期 30 天），失效（error_code 110/111）自动刷新重试一次。
"""
import base64
import os
import threading
import time

import requests

from config import load_config
from utils.logger import system_logger

TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
OCR_URL_TMPL = "https://aip.baidubce.com/rest/2.0/ocr/v1/{api}"

# 百度 OCR 的 token 失效以 HTTP 200 + error_code 返回（110=invalid，111=expired）
_TOKEN_INVALID_CODES = frozenset({110, 111})
_TOKEN_TTL_MARGIN_SEC = 60
_DEFAULT_TIMEOUT_SEC = 15.0


class BaiduOcrError(RuntimeError):
    """百度 OCR 调用失败（配置/网络/接口错误）。code 为百度 error_code（可为 None）。"""

    def __init__(self, message: str, code=None):
        super().__init__(message)
        self.code = code


_token_cache = {"credentials": None, "token": "", "expires_at": 0.0}
_token_lock = threading.Lock()


def _credentials() -> tuple[str, str]:
    """密钥：环境变量优先；empty_env_fallback=True 时回落 config.baidu_ocr 段。"""
    cfg = load_config().get("baidu_ocr") or {}
    api_key = (os.getenv("BAIDU_OCR_API_KEY") or "").strip()
    secret_key = (os.getenv("BAIDU_OCR_SECRET_KEY") or "").strip()
    if cfg.get("empty_env_fallback", True):
        api_key = api_key or str(cfg.get("api_key") or "").strip()
        secret_key = secret_key or str(cfg.get("secret_key") or "").strip()
    return api_key, secret_key


def is_configured() -> bool:
    api_key, secret_key = _credentials()
    return bool(api_key and secret_key)


def invalidate_token() -> None:
    """清空进程内 token 缓存（测试与 401 刷新用）。"""
    with _token_lock:
        _token_cache.update(credentials=None, token="", expires_at=0.0)


def get_access_token(api_key: str, secret_key: str, *, force_refresh: bool = False) -> str:
    """获取 access_token；同凭证进程内缓存，过期/强制刷新时重新请求。"""
    if not api_key or not secret_key:
        raise BaiduOcrError("百度 OCR 未配置：缺少 API Key / Secret Key")
    cred = (api_key, secret_key)
    now = time.time()
    if not force_refresh:
        with _token_lock:
            if (_token_cache["credentials"] == cred
                    and _token_cache["token"]
                    and now < _token_cache["expires_at"]):
                return _token_cache["token"]
    try:
        resp = requests.get(TOKEN_URL, params={
            "grant_type": "client_credentials",
            "client_id": api_key,
            "client_secret": secret_key,
        }, timeout=_DEFAULT_TIMEOUT_SEC)
        resp.raise_for_status()
        body = resp.json()
    except BaiduOcrError:
        raise
    except Exception as exc:
        raise BaiduOcrError(f"获取百度 access_token 失败: {exc}")
    token = str(body.get("access_token") or "").strip()
    if not token:
        raise BaiduOcrError(f"百度 access_token 响应异常: {str(body)[:200]}")
    expires_in = float(body.get("expires_in") or 30 * 24 * 3600)
    with _token_lock:
        _token_cache.update(
            credentials=cred, token=token,
            expires_at=now + expires_in - _TOKEN_TTL_MARGIN_SEC,
        )
    return token


def recognize(image_path: str, api: str, token: str, timeout: float = _DEFAULT_TIMEOUT_SEC, **opts) -> str:
    """单图单接口识别，返回该接口原始多行文本；失败抛 BaiduOcrError。"""
    try:
        with open(image_path, "rb") as f:
            img = base64.b64encode(f.read()).decode()
    except Exception as exc:
        raise BaiduOcrError(f"读取图片失败 {image_path}: {exc}")

    payload = {"image": img, "language_type": "CHN_ENG"}
    payload.update(opts)
    try:
        resp = requests.post(
            OCR_URL_TMPL.format(api=api),
            params={"access_token": token},
            data=payload,  # dict → x-www-form-urlencoded（百度要求）
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
    except BaiduOcrError:
        raise
    except requests.exceptions.Timeout:
        raise BaiduOcrError(f"百度 OCR 超时（>{timeout:g}s）")
    except ValueError:
        raw = (getattr(resp, "text", "") or "")[:200]
        raise BaiduOcrError(f"百度 OCR 响应非 JSON: {raw!r}")
    except Exception as exc:
        raise BaiduOcrError(f"百度 OCR 请求失败: {exc}")

    err_code = body.get("error_code")
    if err_code:
        code = int(err_code) if str(err_code).isdigit() else None
        raise BaiduOcrError(
            f"百度 OCR 错误 {err_code}: {body.get('error_msg', '')}", code=code,
        )
    words = [str(w.get("words") or "") for w in (body.get("words_result") or [])]
    return "\n".join(w for w in words if w)


def _prepare_image(path: str) -> str:
    """百度限制 base64≤4MB、长边≤8192px；超限才压缩，否则发原图（保手写小字精度）。"""
    try:
        if os.path.getsize(path) <= 3_500_000:
            return path
    except OSError:
        return path
    try:
        from services.image_compressor import compress_for_vision_api
        out = compress_for_vision_api([path], max_size=(2400, 3200),
                                      jpeg_quality=88, target_max_mb=3.0)
        return out[0] if out else path
    except Exception as exc:
        system_logger.warning("[BAIDU-OCR] 压缩失败，使用原图: %s", exc)
        return path


def _recognize_with_refresh(image_path: str, api: str, timeout: float, **opts) -> str:
    """识别 + token 失效自动刷新后重试一次。"""
    api_key, secret_key = _credentials()
    token = get_access_token(api_key, secret_key)
    try:
        return recognize(image_path, api, token, timeout=timeout, **opts)
    except BaiduOcrError as exc:
        if exc.code in _TOKEN_INVALID_CODES:
            system_logger.warning("[BAIDU-OCR] token 失效（%s），刷新后重试", exc.code)
            token = get_access_token(api_key, secret_key, force_refresh=True)
            return recognize(image_path, api, token, timeout=timeout, **opts)
        raise


def extract_pages_via_baidu(image_paths: list[str], need_handwriting: bool = False,
                            timeout: float = None) -> dict:
    """主入口（spec v3 §5）。

    返回 `{"printed": <accurate_basic 各页拼接文本>, "handwriting": <str|None>}`：
    - printed: 各页 `accurate_basic` 结果按「--- 第N页 ---」拼接（**同接口**跨页允许）；
    - handwriting: 仅 `need_handwriting=True` 时才调用（**只追加，不重跑 accurate_basic**，
      保住省调用策略），否则为 None。
    未配置 / 无有效图片 / 接口失败 → 抛 `BaiduOcrError`，由上层降级本地兜底。
    """
    api_key, secret_key = _credentials()
    if not (api_key and secret_key):
        raise BaiduOcrError(
            "百度 OCR 未配置：缺少 API Key / Secret Key"
            "（环境变量 BAIDU_OCR_API_KEY / BAIDU_OCR_SECRET_KEY 或 data/config.json baidu_ocr 段）"
        )
    cfg = load_config().get("baidu_ocr") or {}
    timeout = float(timeout or cfg.get("timeout") or _DEFAULT_TIMEOUT_SEC)

    pages = [p for p in (image_paths or []) if p and os.path.exists(p)]
    if not pages:
        raise BaiduOcrError("百度 OCR 无可识别图片")

    printed_parts = []
    for idx, path in enumerate(pages, 1):
        text = _recognize_with_refresh(_prepare_image(path), "accurate_basic", timeout)
        if text:
            printed_parts.append(f"--- 第{idx}页 ---\n{text}")
    printed = "\n\n".join(printed_parts)

    handwriting = None
    if need_handwriting:
        hand_parts = []
        for idx, path in enumerate(pages, 1):
            text = _recognize_with_refresh(
                _prepare_image(path), "handwriting", timeout, recognize_granularity="big",
            )
            if text:
                hand_parts.append(f"--- 第{idx}页 ---\n{text}")
        handwriting = "\n\n".join(hand_parts)

    return {"printed": printed, "handwriting": handwriting}
