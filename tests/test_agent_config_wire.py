"""Tests for the config.yaml → AgentCore wiring (temperature, max_tokens, model).

Prior to 2026-04-23, `config.yaml::api.temperature` was defined but never
read — AgentCore hardcoded `max_tokens=4096` and had no temperature at all.
These tests pin the fix.
"""
from __future__ import annotations

import pytest

from core.agent import AgentCore
from core.capture import ScreenCapture
from core.executor import ActionExecutor
from core.safety import SafetyLayer
from core.state import StateMachine


@pytest.fixture(autouse=True)
def _ensure_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-dummy")


def _make_agent(**kwargs) -> AgentCore:
    return AgentCore(
        StateMachine(), SafetyLayer(), ScreenCapture(), ActionExecutor(),
        **kwargs,
    )


def test_defaults_pick_up_config_yaml(tmp_path, monkeypatch):
    """When no kwargs are passed, AgentCore reads model/temperature/max_tokens from config.yaml."""
    agent = _make_agent()
    # config.yaml ships api.temperature=0.0, api.max_tokens=4096, api.model=claude-sonnet-4-5-...
    assert agent._model.startswith("claude")
    assert agent._temperature == 0.0
    assert agent._max_tokens == 4096


def test_explicit_kwargs_override_config(monkeypatch):
    agent = _make_agent(model="claude-opus-4-7", temperature=0.7, max_tokens=8192)
    assert agent._model == "claude-opus-4-7"
    assert agent._temperature == 0.7
    assert agent._max_tokens == 8192


def test_none_kwargs_falls_back_to_config():
    """Passing explicit None should fall back to config.yaml, not crash."""
    agent = _make_agent(model=None, temperature=None, max_tokens=None)
    assert agent._temperature == 0.0
    assert agent._max_tokens == 4096


def test_missing_config_falls_back_to_hardcoded_defaults(monkeypatch, tmp_path):
    """If _load_api_config returns an empty dict, baked-in defaults apply."""
    monkeypatch.setattr(AgentCore, "_load_api_config", staticmethod(lambda: {}))
    agent = _make_agent()
    assert agent._model == "claude-sonnet-4-6"
    assert agent._temperature == 0.0
    assert agent._max_tokens == 4096
