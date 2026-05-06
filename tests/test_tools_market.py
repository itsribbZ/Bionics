"""Unit tests for bionics_tools/market.py — marketbot integration.

Tests product spec CRUD, framework recommendation, guardrails, plan building.
Does NOT call the Claude API (no network).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bionics_tools import register_all

register_all()
from core.bridge import ToolGate

gate = ToolGate()
gate.set_bypass_safety(True)


# ============================================================================
# Product CRUD
# ============================================================================


class TestProductCRUD:
    def test_save_product_valid(self):
        r = gate.execute("market_save_product", {
            "name": "pytest_product",
            "what_it_does": "Test product for unit tests",
            "target_audience": "Pytest users",
            "tone": "professional",
            "audience_state": "cold",
        })
        assert r.ok is True

    def test_save_product_invalid_name(self):
        r = gate.execute("market_save_product", {
            "name": "../evil/../path",
            "what_it_does": "x",
            "target_audience": "y",
        })
        assert r.ok is False
        err_lower = r.error.lower()
        assert "path" in err_lower or "alphanumeric" in err_lower

    def test_load_product(self):
        gate.execute("market_save_product", {
            "name": "pytest_load_test",
            "what_it_does": "load test",
            "target_audience": "devs",
        })
        r = gate.execute("market_load_product", {"name": "pytest_load_test"})
        assert r.ok is True
        assert r.data["name"] == "pytest_load_test"

    def test_load_nonexistent(self):
        r = gate.execute("market_load_product", {"name": "doesnt_exist_zzz"})
        assert r.ok is False
        assert "not found" in r.error.lower()

    def test_load_path_traversal(self):
        r = gate.execute("market_load_product", {"name": "../../../etc/passwd"})
        assert r.ok is False


# ============================================================================
# Frameworks
# ============================================================================


class TestFrameworks:
    def test_list_frameworks(self):
        r = gate.execute("market_list_frameworks", {})
        assert r.ok is True
        assert "AIDA" in r.data["frameworks"]
        assert "PAS" in r.data["frameworks"]
        assert "BAB" in r.data["frameworks"]
        assert "StoryBrand" in r.data["frameworks"]
        assert "PASTOR" in r.data["frameworks"]

    def test_recommend_pain_aware(self):
        r = gate.execute("market_recommend_framework", {
            "audience_state": "pain-aware",
            "content_type": "ad",
        })
        assert r.ok is True
        assert r.data["framework"] == "PAS"

    def test_recommend_cold_landing(self):
        r = gate.execute("market_recommend_framework", {
            "audience_state": "cold",
            "content_type": "landing",
        })
        assert r.ok is True
        assert r.data["framework"] == "AIDA"

    def test_recommend_solution_aware(self):
        r = gate.execute("market_recommend_framework", {
            "audience_state": "solution-aware",
            "content_type": "email_body",
        })
        assert r.ok is True
        assert r.data["framework"] == "BAB"


# ============================================================================
# Guardrails
# ============================================================================


class TestGuardrails:
    def test_clean_content_passes(self):
        r = gate.execute("market_guardrails", {
            "content": "Save 4.2 hours per commission with automated delivery tracking.",
        })
        assert r.ok is True
        assert r.data["violation_count"] == 0

    def test_superlatives_caught(self):
        r = gate.execute("market_guardrails", {
            "content": "The #1 best solution for your needs.",
        })
        assert r.ok is False
        rules = [v["rule"] for v in r.data["violations"]]
        assert "superlatives" in rules

    def test_vague_buzzwords_caught(self):
        r = gate.execute("market_guardrails", {
            "content": "Our industry-leading platform leverages synergy.",
        })
        assert r.ok is False
        rules = [v["rule"] for v in r.data["violations"]]
        assert "vague_buzzwords" in rules

    def test_fake_urgency_caught(self):
        r = gate.execute("market_guardrails", {
            "content": "Only 3 left! Last chance to buy now!",
        })
        assert r.ok is False
        rules = [v["rule"] for v in r.data["violations"]]
        assert "fake_urgency" in rules

    def test_headline_our_caught(self):
        r = gate.execute("market_guardrails", {
            "content": "Our amazing product helps you save time.",
        })
        assert r.ok is False
        rules = [v["rule"] for v in r.data["violations"]]
        assert "headline_front_loading" in rules

    def test_superlatives_no_false_positive(self):
        """'bestselling' shouldn't trigger superlatives rule."""
        r = gate.execute("market_guardrails", {
            "content": "Save 4 hours per commission (our bestselling feature).",
        })
        # The word "bestselling" with word boundary (?![a-z]) should NOT trigger
        rules = [v["rule"] for v in r.data.get("violations", [])]
        # "our" IS in "Save...our..." — headline_front_loading is on first line only
        # First line: "Save 4 hours per commission (our bestselling feature)." — does NOT start with Our
        # So no headline violation
        assert "superlatives" not in rules


# ============================================================================
# Plan Building
# ============================================================================


class TestPlanBuilder:
    def test_build_plan_valid(self):
        # Save a product first
        gate.execute("market_save_product", {
            "name": "pytest_plan_test",
            "what_it_does": "plan test product",
            "target_audience": "testers",
        })
        r = gate.execute("market_build_plan", {
            "product_name": "pytest_plan_test",
            "count": 5,
            "content_types": ["social"],
            "frameworks": ["PAS"],
            "plan_name": "pytest_plan",
        })
        assert r.ok is True
        assert r.data["steps"] == 5
        # Verify the plan file was saved
        plan_path = PROJECT_ROOT / "plans" / "pytest_plan.json"
        assert plan_path.exists()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        assert len(plan["steps"]) == 5
        assert plan["steps"][0]["action"] == "market_generate_post"

    def test_build_plan_count_too_high(self):
        gate.execute("market_save_product", {
            "name": "pytest_count_test",
            "what_it_does": "x",
            "target_audience": "y",
        })
        r = gate.execute("market_build_plan", {
            "product_name": "pytest_count_test",
            "count": 99999,
        })
        assert r.ok is False
        assert "1-500" in r.error

    def test_build_plan_count_zero(self):
        gate.execute("market_save_product", {
            "name": "pytest_zero_test",
            "what_it_does": "x",
            "target_audience": "y",
        })
        r = gate.execute("market_build_plan", {
            "product_name": "pytest_zero_test",
            "count": 0,
        })
        assert r.ok is False

    def test_build_plan_invalid_content_type(self):
        gate.execute("market_save_product", {
            "name": "pytest_type_test",
            "what_it_does": "x",
            "target_audience": "y",
        })
        r = gate.execute("market_build_plan", {
            "product_name": "pytest_type_test",
            "count": 3,
            "content_types": ["invalid_type_xyz"],
        })
        assert r.ok is False

    def test_build_plan_missing_product(self):
        r = gate.execute("market_build_plan", {
            "product_name": "nonexistent_zzz",
            "count": 3,
        })
        assert r.ok is False


# ============================================================================
# Output management
# ============================================================================


class TestOutputs:
    def test_list_outputs_empty_or_populated(self):
        r = gate.execute("market_list_outputs", {})
        assert r.ok is True
        assert "outputs" in r.data

    def test_read_output_path_traversal(self):
        r = gate.execute("market_read_output", {"filename": "../../../etc/passwd"})
        assert r.ok is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
