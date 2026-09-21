"""百度云 OCR 客户端单测（spec: .scratch/contract-ocr-baidu/spec.md v3 §10）。

覆盖：token 缓存复用 + 401/失效刷新、接口异常抛错、dict 返回结构
（printed / handwriting 独立文本、不拼接整段文本）、密钥优先级与优雅降级。
全部 mock HTTP，不消耗真实免费额度。
"""
import pytest
import requests

import services.baidu_ocr as baidu_ocr


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _clean_env_and_cache(monkeypatch):
    baidu_ocr.invalidate_token()
    monkeypatch.delenv("BAIDU_OCR_API_KEY", raising=False)
    monkeypatch.delenv("BAIDU_OCR_SECRET_KEY", raising=False)
    # 默认切断配置文件里的密钥，用例按需注入
    monkeypatch.setattr(baidu_ocr, "load_config", lambda: {"baidu_ocr": {"enabled": True}})
    yield
    baidu_ocr.invalidate_token()


def _token_resp():
    return _FakeResp({"access_token": "tok-1", "expires_in": 2592000})


def _ocr_resp(words):
    return _FakeResp({"words_result": [{"words": w} for w in words]})


def _err_resp(code, msg="err"):
    return _FakeResp({"error_code": code, "error_msg": msg})


# ── token 缓存复用 + 刷新 ────────────────────────────────────────────

def test_token_cached_reuse_same_credentials(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return _token_resp()

    monkeypatch.setattr(baidu_ocr.requests, "get", fake_get)
    t1 = baidu_ocr.get_access_token("ak", "sk")
    t2 = baidu_ocr.get_access_token("ak", "sk")
    assert t1 == t2 == "tok-1"
    assert len(calls) == 1  # 第二次命中进程内缓存


def test_token_force_refresh_and_credential_change(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return _token_resp()

    monkeypatch.setattr(baidu_ocr.requests, "get", fake_get)
    baidu_ocr.get_access_token("ak", "sk")
    baidu_ocr.get_access_token("ak", "sk", force_refresh=True)
    baidu_ocr.get_access_token("ak2", "sk2")
    assert len(calls) == 3


def test_token_missing_credentials_raises():
    with pytest.raises(baidu_ocr.BaiduOcrError):
        baidu_ocr.get_access_token("", "sk")
    with pytest.raises(baidu_ocr.BaiduOcrError):
        baidu_ocr.get_access_token("ak", "")


# ── token 失效自动刷新 ───────────────────────────────────────────────

def test_recognize_refreshes_token_on_invalid_code(monkeypatch, tmp_path):
    img = tmp_path / "p1.jpg"
    img.write_bytes(b"fake")
    monkeypatch.setattr(baidu_ocr, "_credentials", lambda: ("ak", "sk"))
    token_calls = []
    post_bodies = []

    def fake_get(url, **kw):
        token_calls.append(url)
        return _token_resp()

    def fake_post(url, **kw):
        post_bodies.append(kw)
        # 第一次带失效 token → error_code 110；刷新后的调用成功
        if len(post_bodies) == 1:
            return _err_resp(110, "Access token invalid")
        return _ocr_resp(["培训费用总额合计人民币3580元"])

    monkeypatch.setattr(baidu_ocr.requests, "get", fake_get)
    monkeypatch.setattr(baidu_ocr.requests, "post", fake_post)

    out = baidu_ocr._recognize_with_refresh(str(img), "accurate_basic", 5.0)
    assert "3580" in out
    assert len(token_calls) == 2   # 首次 + 刷新
    assert len(post_bodies) == 2


def test_recognize_non_token_error_no_refresh(monkeypatch, tmp_path):
    img = tmp_path / "p1.jpg"
    img.write_bytes(b"fake")
    monkeypatch.setattr(baidu_ocr, "_credentials", lambda: ("ak", "sk"))
    token_calls = []

    def fake_get(url, **kw):
        token_calls.append(url)
        return _token_resp()

    def fake_post(url, **kw):
        return _err_resp(17, "Daily limit reached")  # 非 token 类错误

    monkeypatch.setattr(baidu_ocr.requests, "get", fake_get)
    monkeypatch.setattr(baidu_ocr.requests, "post", fake_post)

    with pytest.raises(baidu_ocr.BaiduOcrError) as ei:
        baidu_ocr._recognize_with_refresh(str(img), "accurate_basic", 5.0)
    assert ei.value.code == 17
    assert len(token_calls) == 1  # 未刷新


def test_recognize_timeout_wrapped(monkeypatch, tmp_path):
    img = tmp_path / "p1.jpg"
    img.write_bytes(b"fake")
    monkeypatch.setattr(baidu_ocr, "_credentials", lambda: ("ak", "sk"))

    monkeypatch.setattr(baidu_ocr.requests, "get", lambda url, **kw: _token_resp())

    def fake_post(url, **kw):
        raise requests.exceptions.Timeout()

    monkeypatch.setattr(baidu_ocr.requests, "post", fake_post)
    with pytest.raises(baidu_ocr.BaiduOcrError, match="超时"):
        baidu_ocr._recognize_with_refresh(str(img), "accurate_basic", 5.0)


# ── extract_pages_via_baidu：dict 结构 / 省调用 / 未配置 ─────────────

def _setup_ocr(monkeypatch, printed_words, hand_words=None):
    """返回 post 调用记录；(api, opts) 列表。"""
    monkeypatch.setattr(baidu_ocr, "_credentials", lambda: ("ak", "sk"))
    posts = []

    def fake_get(url, **kw):
        return _token_resp()

    def fake_post(url, **kw):
        api = url.rsplit("/", 1)[-1].split("?")[0]
        posts.append(api)
        if api == "handwriting":
            return _ocr_resp(hand_words or [])
        return _ocr_resp(printed_words)

    monkeypatch.setattr(baidu_ocr.requests, "get", fake_get)
    monkeypatch.setattr(baidu_ocr.requests, "post", fake_post)
    return posts


def test_extract_pages_default_only_printed(monkeypatch, tmp_path):
    p1 = tmp_path / "1.jpg"; p1.write_bytes(b"a")
    p2 = tmp_path / "2.jpg"; p2.write_bytes(b"b")
    posts = _setup_ocr(monkeypatch, ["合计人民币3580元"])

    out = baidu_ocr.extract_pages_via_baidu([str(p1), str(p2)])

    assert set(out.keys()) == {"printed", "handwriting"}
    assert out["handwriting"] is None          # 省调用：默认不跑 handwriting
    assert "--- 第1页 ---" in out["printed"] and "--- 第2页 ---" in out["printed"]
    assert "3580" in out["printed"]
    assert posts == ["accurate_basic", "accurate_basic"]


def test_extract_pages_handwriting_appends_not_reruns(monkeypatch, tmp_path):
    p1 = tmp_path / "1.jpg"; p1.write_bytes(b"a")
    posts = _setup_ocr(monkeypatch, ["印刷条款"], hand_words=["尾款1580"])

    out = baidu_ocr.extract_pages_via_baidu([str(p1)], need_handwriting=True)

    # 只追加 handwriting，不重跑 accurate_basic（2 次而非 3 次）
    assert posts == ["accurate_basic", "handwriting"]
    assert "1580" in out["handwriting"]
    assert "印刷条款" not in out["handwriting"]  # 两份文本独立，未拼接
    assert "1580" not in out["printed"]


def test_extract_pages_not_configured_raises(monkeypatch, tmp_path):
    p1 = tmp_path / "1.jpg"; p1.write_bytes(b"a")
    monkeypatch.setattr(baidu_ocr, "load_config",
                        lambda: {"baidu_ocr": {"enabled": True}})
    with pytest.raises(baidu_ocr.BaiduOcrError, match="未配置"):
        baidu_ocr.extract_pages_via_baidu([str(p1)])


def test_extract_pages_no_valid_images_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(
        baidu_ocr, "_credentials", lambda: ("ak", "sk"))
    with pytest.raises(baidu_ocr.BaiduOcrError, match="无可识别图片"):
        baidu_ocr.extract_pages_via_baidu([str(tmp_path / "nope.jpg")])


# ── 密钥优先级（环境变量 > config，empty_env_fallback 控制） ─────────

def test_credentials_env_overrides_config(monkeypatch):
    monkeypatch.setenv("BAIDU_OCR_API_KEY", "env-ak")
    monkeypatch.setenv("BAIDU_OCR_SECRET_KEY", "env-sk")
    monkeypatch.setattr(baidu_ocr, "load_config", lambda: {
        "baidu_ocr": {"enabled": True, "api_key": "cfg-ak", "secret_key": "cfg-sk"},
    })
    assert baidu_ocr._credentials() == ("env-ak", "env-sk")


def test_credentials_fallback_to_config(monkeypatch):
    monkeypatch.setattr(baidu_ocr, "load_config", lambda: {
        "baidu_ocr": {"enabled": True, "api_key": "cfg-ak", "secret_key": "cfg-sk"},
    })
    assert baidu_ocr._credentials() == ("cfg-ak", "cfg-sk")


def test_credentials_empty_env_fallback_false(monkeypatch):
    monkeypatch.setattr(baidu_ocr, "load_config", lambda: {
        "baidu_ocr": {"enabled": True, "api_key": "cfg-ak",
                      "secret_key": "cfg-sk", "empty_env_fallback": False},
    })
    assert baidu_ocr._credentials() == ("", "")


def test_is_configured(monkeypatch):
    assert not baidu_ocr.is_configured()
    monkeypatch.setenv("BAIDU_OCR_API_KEY", "ak")
    monkeypatch.setenv("BAIDU_OCR_SECRET_KEY", "sk")
    assert baidu_ocr.is_configured()
