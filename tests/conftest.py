import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """测试不触达真实大模型：登记表 AI 整理一律模拟为不可用，走数据层兜底链。"""
    import services.intake_service as intake_svc
    monkeypatch.setattr(intake_svc, "ai_polish_registration", lambda ticket: {}, raising=False)
