"""视觉识别页级容错测试（ISS-VC-01 · P0-1）。

背景：2026-09-14 工单「郑智林」上传 2 页纸质合同，第 2 页因 provider 返回
「HTTP 200 + choices=null」/「HTTP 200 + 空 body」而整页丢失，导致仅印在该页的
「第八条 退学退费」未被提取，合同被误判为「关键字段识别不完整」。

本测试锁定三类契约：
1. `choices` 为 null / 非列表 → 不抛异常（原实现 `len(None)` TypeError）；
2. body 非 JSON（空 body）→ 不抛异常，并返回可排障的诊断串；
3. 单次失败会自动重试，重试上限生效后以空串收口；成功场景只请求一次。
"""

from __future__ import annotations

import json
import types

import pytest

from services import contract_service as cs


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, raw_text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = raw_text

    def json(self):
        if self._payload is None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


def _install_fake_post(monkeypatch, responses):
    """按顺序返回预设响应；耗尽后重复最后一个。"""
    calls = {"count": 0}

    def fake_post(api_url, headers=None, json=None, timeout=None, **kwargs):
        calls["count"] += 1
        idx = min(calls["count"] - 1, len(responses) - 1)
        return responses[idx]

    monkeypatch.setattr(cs.requests, "post", fake_post)
    monkeypatch.setattr(cs.time, "sleep", lambda *_: None)  # 测试不等待退避
    return calls


def _make_tmp_image(tmp_path):
    img = tmp_path / "page.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    return str(img)


def _ok_payload(text: str = "东莞市机动车驾驶员培训合同"):
    return {
        "choices": [
            {"finish_reason": "stop", "message": {"role": "assistant", "content": text}}
        ]
    }


def test_choices_null_is_tolerated_and_retried(monkeypatch, tmp_path):
    """choices=null：不得抛 TypeError，且重试到上限后返回空串。"""
    monkeypatch.setattr(cs, "_VISION_MAX_ATTEMPTS", 3, raising=False)
    calls = _install_fake_post(monkeypatch, [_FakeResponse(200, {"choices": None})])

    idx, text = cs._recognize_single_image(
        (1, _make_tmp_image(tmp_path), "http://x/v1", "k", "m", 2048)
    )

    assert idx == 1
    assert text == ""
    assert calls["count"] == 3


def test_empty_body_is_tolerated_and_logged(monkeypatch, tmp_path):
    """HTTP 200 + 空 body（JSONDecodeError 场景）：不得向上抛异常。"""
    monkeypatch.setattr(cs, "_VISION_MAX_ATTEMPTS", 2, raising=False)
    calls = _install_fake_post(monkeypatch, [_FakeResponse(200, None, raw_text="   ")])

    idx, text = cs._recognize_single_image(
        (2, _make_tmp_image(tmp_path), "http://x/v1", "k", "m", 2048)
    )

    assert (idx, text) == (2, "")
    assert calls["count"] == 2


def test_retry_then_success(monkeypatch, tmp_path):
    """首两次异常、第三次成功 → 返回正文，共请求 3 次。"""
    monkeypatch.setattr(cs, "_VISION_MAX_ATTEMPTS", 3, raising=False)
    calls = _install_fake_post(
        monkeypatch,
        [
            _FakeResponse(200, {"choices": None}),
            _FakeResponse(200, None, raw_text=""),
            _FakeResponse(200, _ok_payload("第八条 退学退费")),
        ],
    )

    idx, text = cs._recognize_single_image(
        (2, _make_tmp_image(tmp_path), "http://x/v1", "k", "m", 2048)
    )

    assert idx == 2
    assert text == "第八条 退学退费"
    assert calls["count"] == 3


def test_success_first_try_only_once(monkeypatch, tmp_path):
    """正常响应：只请求一次，不再重试（避免无谓计费与耗时）。"""
    monkeypatch.setattr(cs, "_VISION_MAX_ATTEMPTS", 3, raising=False)
    calls = _install_fake_post(monkeypatch, [_FakeResponse(200, _ok_payload("ok"))])

    idx, text = cs._recognize_single_image(
        (1, _make_tmp_image(tmp_path), "http://x/v1", "k", "m", 2048)
    )

    assert (idx, text) == (1, "ok")
    assert calls["count"] == 1


def test_missing_file_short_circuits(monkeypatch, tmp_path):
    """文件不存在：直接返回空串，不发请求。"""
    calls = _install_fake_post(monkeypatch, [_FakeResponse(200, _ok_payload())])

    idx, text = cs._recognize_single_image((1, str(tmp_path / "nope.png"), "http://x", "k", "m", 1))

    assert (idx, text) == (1, "")
    assert calls["count"] == 0


def test_content_as_parts_list(monkeypatch, tmp_path):
    """content 为分片列表时也能拼回正文（不同 provider 形态兼容）。"""
    monkeypatch.setattr(cs, "_VISION_MAX_ATTEMPTS", 1, raising=False)
    _install_fake_post(
        monkeypatch,
        [
            _FakeResponse(
                200,
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": [
                                    {"type": "text", "text": "第一条 订立合同的条件"},
                                    {"type": "text", "text": "第二条 培训服务的基本内容"},
                                ]
                            },
                        }
                    ]
                },
            )
        ],
    )

    idx, text = cs._recognize_single_image(
        (1, _make_tmp_image(tmp_path), "http://x/v1", "k", "m", 2048)
    )

    assert idx == 1
    assert "第一条" in text and "第二条" in text
