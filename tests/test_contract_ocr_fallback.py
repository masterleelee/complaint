"""合同 OCR 降级链护栏（spec: .scratch/contract-ocr-baidu/spec.md v3 §10）。

覆盖：
- 百度主路 → macOS Vision 本地兜底（source 标记 / text_confidence_of 映射）；
- 省调用策略：仅 total_fee 缺失时追加 handwriting，且只跑 handwriting 不重跑主接口；
- baidu_rescue 金额字段注入（upload_pipeline 只填空位合并）；
- payment_plan 欠款本地推算 + 「尾款 OCR 错值」一致性闸门；
- 模板匹配 source 白名单包含 baidu_ocr；
- AI 视觉 OCR / PaddleOCR / EasyOCR 删除护栏（防回归引用）。

全部 mock，不消耗百度免费额度、不触发真实 OCR。
"""
from types import SimpleNamespace

import pytest

import services.baidu_ocr as baidu_ocr
import services.contract_service as cs
import config

LONG = "这是为了通过二十字符有效性阈值而准备的填充文本，"  # ≥20 字符


@pytest.fixture(autouse=True)
def _no_real_baidu(monkeypatch):
    """切断真实百度调用：env 清空 + extract_pages_via_baidu 打桩点。"""
    monkeypatch.delenv("BAIDU_OCR_API_KEY", raising=False)
    monkeypatch.delenv("BAIDU_OCR_SECRET_KEY", raising=False)
    yield


def _enable_baidu(monkeypatch, enabled=True):
    monkeypatch.setattr(cs, "load_config", lambda: {"baidu_ocr": {"enabled": enabled}})


def _fake_pages(printed, handwriting=None, calls=None):
    def fake(image_paths, need_handwriting=False, timeout=None):
        if calls is not None:
            calls.append(need_handwriting)
        return {"printed": printed, "handwriting": handwriting}
    return fake


def _contract_file(tmp_path):
    p = tmp_path / "contract.jpg"
    p.write_bytes(b"fake-image-bytes")
    return str(p)


# ── 主路 / 降级 ──────────────────────────────────────────────────────

def test_baidu_main_path_source(monkeypatch, tmp_path):
    _enable_baidu(monkeypatch)
    monkeypatch.setattr(baidu_ocr, "extract_pages_via_baidu",
                        _fake_pages(printed=LONG + "培训费用总额合计人民币3580元"))
    out = cs.extract_contract_text_from_file(_contract_file(tmp_path))
    assert out.get("source") == "baidu_ocr"
    assert "3580" in out.get("text", "")


def test_baidu_error_falls_back_to_local_vision(monkeypatch, tmp_path):
    _enable_baidu(monkeypatch)

    def boom(*a, **kw):
        raise baidu_ocr.BaiduOcrError("网络不可用")

    monkeypatch.setattr(baidu_ocr, "extract_pages_via_baidu", boom)
    monkeypatch.setattr(cs, "_file_parser_extract_text", lambda path: LONG + "本地文本")
    out = cs.extract_contract_text_from_file(_contract_file(tmp_path))
    assert out.get("source") == "local_ocr"


def test_baidu_disabled_uses_local_directly(monkeypatch, tmp_path):
    _enable_baidu(monkeypatch, enabled=False)

    def must_not_call(*a, **kw):
        raise AssertionError("baidu disabled 时不应调用百度 OCR")

    monkeypatch.setattr(baidu_ocr, "extract_pages_via_baidu", must_not_call)
    monkeypatch.setattr(cs, "_file_parser_extract_text", lambda path: LONG + "本地文本")
    out = cs.extract_contract_text_from_file(_contract_file(tmp_path))
    assert out.get("source") == "local_ocr"


# ── 省调用策略：handwriting 按需追加 ────────────────────────────────

def test_handwriting_rescue_triggered_only_when_total_missing(monkeypatch, tmp_path):
    _enable_baidu(monkeypatch)
    calls = []
    monkeypatch.setattr(baidu_ocr, "extract_pages_via_baidu", _fake_pages(
        printed=LONG + "本页无任何金额信息，纯条款文本。",
        handwriting=LONG + "培训费用总额合计人民币3580元",
        calls=calls,
    ))
    out = cs.extract_contract_text_from_file(_contract_file(tmp_path))
    assert calls == [False, True]          # 主路一次（无 hw）+ 补提一次（只跑 hw）
    assert out["baidu_rescue"]["fees"]["total_fee"] == 3580.0


def test_no_rescue_when_total_fee_present(monkeypatch, tmp_path):
    _enable_baidu(monkeypatch)
    calls = []
    monkeypatch.setattr(baidu_ocr, "extract_pages_via_baidu", _fake_pages(
        printed=LONG + "培训费用总额合计人民币3580元",
        calls=calls,
    ))
    out = cs.extract_contract_text_from_file(_contract_file(tmp_path))
    assert calls == [False]                # total_fee 正确 → 不烧 handwriting 额度
    assert "baidu_rescue" not in out


# ── payment_plan：欠款本地推算 + 尾款错值闸门 ───────────────────────

def test_payment_plan_derived_and_gate_overrides_ocr_tail(monkeypatch, tmp_path):
    _enable_baidu(monkeypatch)
    monkeypatch.setattr(baidu_ocr, "extract_pages_via_baidu", _fake_pages(
        printed=LONG + "培训费用总额合计人民币3580元。付款方式：首付2000元，尾款580元。",
    ))
    out = cs.extract_contract_text_from_file(_contract_file(tmp_path))
    pp = out["payment_plan"]
    assert pp["down_payment"] == 2000.0
    assert pp["balance"] == 1580.0          # 3580 − 2000，不信 OCR 的「尾580」
    assert pp["balance_source"] == "derived"
    assert any("580" in w and "1580" in w for w in pp["warnings"])


def test_payment_plan_falls_back_to_ocr_balance_when_underivable(monkeypatch, tmp_path):
    pp = cs._extract_payment_plan(LONG + "约定尾款1580元。", total_fee=0.0)
    assert pp["down_payment"] is None
    assert pp["balance"] == 1580.0
    assert pp["balance_source"] == "ocr"
    assert pp["warnings"] == []


def test_payment_plan_downpayment_exceeds_total(monkeypatch, tmp_path):
    pp = cs._extract_payment_plan("首付5000元", total_fee=3580.0)
    assert pp["balance"] is None and pp["balance_source"] == ""
    assert pp["warnings"]


# ── 置信度 / 模板白名单 ──────────────────────────────────────────────

def test_text_confidence_baidu_is_high():
    assert cs.text_confidence_of("baidu_ocr") == "high"
    assert cs.text_confidence_of("local_ocr") == "low"


def test_template_match_runs_for_baidu_source(monkeypatch, tmp_path):
    _enable_baidu(monkeypatch)
    monkeypatch.setattr(baidu_ocr, "extract_pages_via_baidu", _fake_pages(
        printed=LONG + "培训费用总额合计人民币3580元",
    ))

    class _FakeTemplateService:
        def match_template(self, text):
            return [SimpleNamespace(template_id=19, template_name="东莞驾培 2023 版分校",
                                    provider="builtin", confidence=0.6, is_confident=True)]

        def extract_fields(self, template_id, text):
            return {}

    monkeypatch.setattr(cs, "get_template_service", _FakeTemplateService)
    out = cs.extract_contract_text_from_file(_contract_file(tmp_path))
    assert out.get("template_match", {}).get("template_id") == 19


# ── 删除护栏：AI 视觉 OCR / Paddle / Easy 不得回归 ──────────────────

def test_vision_ocr_entries_removed():
    for name in ("extract_contract_text_vision", "_vision_amount_rescue",
                 "_vision_rescue_enabled", "_vision_model_candidates",
                 "_recognize_single_image", "extract_contract_text_ocr",
                 "_extract_contract_text_easyocr"):
        assert not hasattr(cs, name), f"已删除入口回归出现：{name}"


def test_vision_config_section_removed():
    assert "llm_contract_vision" not in config.DEFAULT_CONFIG
    import inspect
    import services.upload_pipeline as up
    assert "vision_rescue" not in inspect.getsource(up)


def test_file_parser_easyocr_paddle_removed():
    import services.file_parser as fp
    for name in ("_get_easyocr_reader", "_extract_image_easyocr",
                 "_create_paddle_ocr", "_run_paddle_ocr", "_flatten_paddleocr_result"):
        assert not hasattr(fp, name), f"死代码回归出现：{name}"


def test_baidu_rescue_merged_fill_empty_only():
    """upload_pipeline._merge_rescued_fees：只填空位、total_amount 覆盖。"""
    from services.upload_pipeline import _merge_rescued_fees
    fees = {"total_fee": 0, "theory_fee": 100.0, "total_amount": 0}
    rescued = {"total_fee": 3580.0, "theory_fee": 999.0, "total_amount": 3790.0}
    merged = _merge_rescued_fees(fees, rescued)
    assert merged["total_fee"] == 3580.0        # 空位 → 填
    assert merged["theory_fee"] == 100.0        # 已有 → 不覆盖
    assert merged["total_amount"] == 3790.0     # 派生值 → 覆盖
    assert any("手写 OCR" in w for w in merged["warnings"])
