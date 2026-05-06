"""Tests for Phase 4 SOTA modules: persistent memory, Voyager cache, memory tools.

Covers the three modules added 2026-04-16 that shipped without tests:
- core/memory.py        — BionicsMemory SQLite store
- core/tool_cache.py    — ToolUseCache Voyager-pattern sequence cache
- bionics_tools/memory_tools.py — 7 MCP-exposed wrappers
- core/verification.py::ActionVerifier.verify_semantic — Claude-vision confirm

All tests use temp DB files (isolated per test) — no ~/.claude pollution.
"""

from unittest.mock import MagicMock, patch

# ============================================================================
# BionicsMemory
# ============================================================================


class TestBionicsMemory:
    def _make(self, tmp_path) -> "BionicsMemory":
        from core.memory import BionicsMemory
        return BionicsMemory(db_path=tmp_path / "memory.db")

    def test_schema_creates_on_init(self, tmp_path):
        mem = self._make(tmp_path)
        # Empty DB → count == 0, but table exists
        assert mem.count() == 0
        assert mem.count("task_outcome") == 0

    def test_remember_and_recall_roundtrip(self, tmp_path):
        mem = self._make(tmp_path)
        ok = mem.remember("task_outcome", "ANIMATION", "mm_setup_v1",
                           {"demo_ready": True, "duration_ms": 1500})
        assert ok is True

        entries = mem.recall("task_outcome", "ANIMATION")
        assert len(entries) == 1
        assert entries[0]["key"] == "mm_setup_v1"
        assert entries[0]["value"]["demo_ready"] is True

    def test_recall_bumps_hit_counter_across_calls(self, tmp_path):
        """The hit bump in recall() happens AFTER the SELECT, so the first call
        shows hits=0 and the second shows the bumped count from the prior call."""
        mem = self._make(tmp_path)
        mem.remember("task_outcome", "ANIMATION", "k", "v")
        first = mem.recall("task_outcome", "ANIMATION")
        assert first[0]["hits"] == 0
        second = mem.recall("task_outcome", "ANIMATION")
        assert second[0]["hits"] >= 1

    def test_remember_is_idempotent_on_conflict(self, tmp_path):
        mem = self._make(tmp_path)
        mem.remember("task_outcome", "COMBAT", "hit_chain", "v1")
        mem.remember("task_outcome", "COMBAT", "hit_chain", "v2")
        entries = mem.recall("task_outcome", "COMBAT")
        assert len(entries) == 1
        assert entries[0]["value"] == "v2"

    def test_recall_filters_by_topic(self, tmp_path):
        mem = self._make(tmp_path)
        mem.remember("task_outcome", "ANIMATION", "a", "1")
        mem.remember("task_outcome", "COMBAT", "b", "2")
        anim = mem.recall("task_outcome", "ANIMATION")
        combat = mem.recall("task_outcome", "COMBAT")
        assert len(anim) == 1 and anim[0]["topic"] == "ANIMATION"
        assert len(combat) == 1 and combat[0]["topic"] == "COMBAT"

    def test_recall_no_topic_returns_all_scope_entries(self, tmp_path):
        mem = self._make(tmp_path)
        mem.remember("task_outcome", "ANIMATION", "a", "1")
        mem.remember("task_outcome", "COMBAT", "b", "2")
        all_entries = mem.recall("task_outcome")
        assert len(all_entries) == 2

    def test_search_matches_topic_key_and_value(self, tmp_path):
        mem = self._make(tmp_path)
        mem.remember("learned_fix", "AnimBP", "slot_missing",
                     "Add DefaultSlot between BlendSpaceGraph and Root")
        # Match in value
        assert any("DefaultSlot" in (h["value"] if isinstance(h["value"], str) else "")
                   for h in mem.search("defaultslot"))
        # Match in key
        assert len(mem.search("slot_missing")) == 1
        # Match in topic
        assert len(mem.search("animbp")) == 1

    def test_search_case_insensitive(self, tmp_path):
        mem = self._make(tmp_path)
        mem.remember("failure", "UE5", "python_re", "Python Remote Exec cannot wire AnimGraph")
        assert len(mem.search("PYTHON REMOTE EXEC")) == 1
        assert len(mem.search("python remote exec")) == 1

    def test_forget_deletes_entry(self, tmp_path):
        mem = self._make(tmp_path)
        mem.remember("failure", "UE5", "k1", "v1")
        assert mem.count("failure") == 1
        deleted = mem.forget("failure", "k1")
        assert deleted is True
        assert mem.count("failure") == 0

    def test_forget_with_topic(self, tmp_path):
        mem = self._make(tmp_path)
        mem.remember("failure", "UE5", "dup", "v1")
        mem.remember("failure", "BP", "dup", "v2")
        # Topic-scoped forget should only affect one
        mem.forget("failure", "dup", topic="UE5")
        remaining = mem.recall("failure")
        assert len(remaining) == 1
        assert remaining[0]["topic"] == "BP"

    def test_forget_nonexistent_returns_false(self, tmp_path):
        mem = self._make(tmp_path)
        assert mem.forget("failure", "nope") is False

    def test_count_by_scope(self, tmp_path):
        mem = self._make(tmp_path)
        mem.remember("task_outcome", "A", "k1", "1")
        mem.remember("task_outcome", "B", "k2", "2")
        mem.remember("failure", "C", "k3", "3")
        assert mem.count("task_outcome") == 2
        assert mem.count("failure") == 1
        assert mem.count() == 3

    def test_value_deserialization_handles_json_and_plain_strings(self, tmp_path):
        mem = self._make(tmp_path)
        mem.remember("task_outcome", "T", "dict", {"a": 1})
        mem.remember("task_outcome", "T", "plain", "just a string")
        entries = {e["key"]: e["value"] for e in mem.recall("task_outcome", "T")}
        assert entries["dict"] == {"a": 1}
        assert entries["plain"] == "just a string"

    def test_singleton_returns_same_instance(self):
        from core.memory import get_memory
        a = get_memory()
        b = get_memory()
        assert a is b


# ============================================================================
# ToolUseCache (Voyager pattern)
# ============================================================================


class TestToolUseCache:
    def _make(self, tmp_path) -> "ToolUseCache":
        from core.tool_cache import ToolUseCache
        return ToolUseCache(db_path=tmp_path / "cache.db")

    def test_prompt_signature_strips_stopwords(self):
        from core.tool_cache import ToolUseCache
        sig = ToolUseCache.prompt_signature("Please fix the AnimBP T-pose")
        # Stopwords ("please", "fix", "the") removed; content words sorted unique
        assert "please" not in sig
        assert "fix" not in sig
        assert "the" not in sig
        assert "animbp" in sig
        assert "pose" in sig

    def test_prompt_signature_is_order_invariant(self):
        from core.tool_cache import ToolUseCache
        a = ToolUseCache.prompt_signature("wire the combat damage pipeline")
        b = ToolUseCache.prompt_signature("pipeline damage combat wire")
        assert a == b

    def test_prompt_signature_deduplicates(self):
        from core.tool_cache import ToolUseCache
        sig = ToolUseCache.prompt_signature("combat combat combat")
        # Content set collapses duplicates → "combat" once
        assert sig == "combat"

    def test_record_and_find_proven_returns_matches(self, tmp_path):
        cache = self._make(tmp_path)
        # Record two successful runs with the same signature
        prompt = "wire combat damage"
        cache.record("COMBAT", prompt, [{"tool": "ue5_compile"}], success=True)
        cache.record("COMBAT", prompt, [{"tool": "ue5_compile"}], success=True)
        proven = cache.find_proven("COMBAT", prompt, min_success_count=2)
        assert len(proven) == 2
        assert proven[0]["success"] is True

    def test_find_proven_respects_min_success_count(self, tmp_path):
        cache = self._make(tmp_path)
        cache.record("ANIMATION", "mm setup", [{"tool": "a"}], success=True)
        # Only 1 success → min_success_count=2 returns empty
        assert cache.find_proven("ANIMATION", "mm setup", min_success_count=2) == []

    def test_find_proven_ignores_failed_runs(self, tmp_path):
        cache = self._make(tmp_path)
        cache.record("COMBAT", "hit chain", [{"tool": "x"}], success=False)
        cache.record("COMBAT", "hit chain", [{"tool": "x"}], success=False)
        # Both failed → no proven match
        assert cache.find_proven("COMBAT", "hit chain", min_success_count=1) == []

    def test_find_proven_bumps_reuse_counter(self, tmp_path):
        """The reuse bump happens AFTER the SELECT, so the second call reflects
        the first call's bump."""
        cache = self._make(tmp_path)
        cache.record("A", "prompt", [{"t": "x"}], success=True)
        cache.record("A", "prompt", [{"t": "x"}], success=True)
        cache.find_proven("A", "prompt", min_success_count=2)  # first call bumps DB
        hits = cache.find_proven("A", "prompt", min_success_count=2)
        assert hits[0]["times_reused"] >= 1

    def test_find_similar_via_jaccard(self, tmp_path):
        cache = self._make(tmp_path)
        cache.record("ANIMATION", "fix animbp t-pose slot", [{"t": "x"}], success=True)
        # Different but overlapping prompt
        similar = cache.find_similar("animbp slot missing")
        assert len(similar) >= 1
        assert similar[0]["score"] > 0

    def test_find_similar_empty_on_no_overlap(self, tmp_path):
        cache = self._make(tmp_path)
        cache.record("ANIMATION", "foo bar baz", [{"t": "x"}], success=True)
        similar = cache.find_similar("completely different unrelated content")
        assert similar == []

    def test_find_similar_weights_success_higher(self, tmp_path):
        cache = self._make(tmp_path)
        cache.record("X", "locomotion setup", [{"t": "a"}], success=True)
        cache.record("X", "locomotion setup", [{"t": "b"}], success=False)
        similar = cache.find_similar("locomotion setup")
        # Both match by tokens, but the successful one ranks higher
        assert similar[0]["success"] is True

    def test_stats_shape(self, tmp_path):
        cache = self._make(tmp_path)
        cache.record("A", "p", [{"t": "x"}], success=True)
        cache.record("A", "p2", [{"t": "y"}], success=False)
        stats = cache.stats()
        assert stats["total_sequences"] == 2
        assert stats["successful"] == 1

    def test_singleton_returns_same_instance(self):
        from core.tool_cache import get_tool_cache
        a = get_tool_cache()
        b = get_tool_cache()
        assert a is b


# ============================================================================
# Memory Tools (MCP wrappers)
# ============================================================================


class TestMemoryTools:
    """Verify the 7 bionics_tool wrappers call through to BionicsMemory/ToolUseCache
    correctly and return ToolResult shapes expected by the bridge."""

    def test_memory_remember_returns_success(self, tmp_path, monkeypatch):
        from bionics_tools import memory_tools
        from core.memory import BionicsMemory
        # Inject fresh memory into the singleton slot
        fake = BionicsMemory(db_path=tmp_path / "m.db")
        monkeypatch.setattr("bionics_tools.memory_tools.get_memory", lambda: fake)

        result = memory_tools.bionics_memory_remember(
            "task_outcome", "ANIMATION", "k1", "v1",
        )
        assert result.ok is True
        assert result.data["stored"] is True

    def test_memory_recall_returns_entries(self, tmp_path, monkeypatch):
        from bionics_tools import memory_tools
        from core.memory import BionicsMemory
        fake = BionicsMemory(db_path=tmp_path / "m.db")
        fake.remember("task_outcome", "X", "kk", "vv")
        monkeypatch.setattr("bionics_tools.memory_tools.get_memory", lambda: fake)

        result = memory_tools.bionics_memory_recall("task_outcome", "X")
        assert result.ok is True
        assert result.data["count"] == 1

    def test_memory_search_returns_hits(self, tmp_path, monkeypatch):
        from bionics_tools import memory_tools
        from core.memory import BionicsMemory
        fake = BionicsMemory(db_path=tmp_path / "m.db")
        fake.remember("failure", "UE5", "python_re", "cannot wire AnimGraph")
        monkeypatch.setattr("bionics_tools.memory_tools.get_memory", lambda: fake)

        result = memory_tools.bionics_memory_search("animgraph")
        assert result.ok is True
        assert result.data["count"] >= 1

    def test_memory_forget_returns_deleted_flag(self, tmp_path, monkeypatch):
        from bionics_tools import memory_tools
        from core.memory import BionicsMemory
        fake = BionicsMemory(db_path=tmp_path / "m.db")
        fake.remember("failure", "X", "k", "v")
        monkeypatch.setattr("bionics_tools.memory_tools.get_memory", lambda: fake)

        result = memory_tools.bionics_memory_forget("failure", "k")
        assert result.ok is True
        assert result.data["deleted"] is True

    def test_memory_stats_returns_scope_breakdown(self, tmp_path, monkeypatch):
        from bionics_tools import memory_tools
        from core.memory import BionicsMemory
        fake = BionicsMemory(db_path=tmp_path / "m.db")
        fake.remember("task_outcome", "A", "k", "v")
        fake.remember("failure", "B", "k", "v")
        monkeypatch.setattr("bionics_tools.memory_tools.get_memory", lambda: fake)

        result = memory_tools.bionics_memory_stats()
        assert result.ok is True
        assert result.data["total"] == 2
        assert result.data["task_outcome"] == 1
        assert result.data["failure"] == 1

    def test_tool_cache_stats(self, tmp_path, monkeypatch):
        from bionics_tools import memory_tools
        from core.tool_cache import ToolUseCache
        fake = ToolUseCache(db_path=tmp_path / "c.db")
        fake.record("COMBAT", "hit chain", [{"t": "x"}], success=True)
        monkeypatch.setattr("bionics_tools.memory_tools.get_tool_cache", lambda: fake)

        result = memory_tools.bionics_tool_cache_stats()
        assert result.ok is True
        assert result.data["total_sequences"] == 1
        assert result.data["successful"] == 1

    def test_tool_cache_find_returns_proven_when_available(self, tmp_path, monkeypatch):
        from bionics_tools import memory_tools
        from core.tool_cache import ToolUseCache
        fake = ToolUseCache(db_path=tmp_path / "c.db")
        fake.record("COMBAT", "hit chain damage", [{"t": "x"}], success=True)
        fake.record("COMBAT", "hit chain damage", [{"t": "x"}], success=True)
        monkeypatch.setattr("bionics_tools.memory_tools.get_tool_cache", lambda: fake)

        result = memory_tools.bionics_tool_cache_find("COMBAT", "hit chain damage", min_success_count=2)
        assert result.ok is True
        assert len(result.data["proven"]) == 2

    def test_tool_cache_find_falls_back_to_similar(self, tmp_path, monkeypatch):
        from bionics_tools import memory_tools
        from core.tool_cache import ToolUseCache
        fake = ToolUseCache(db_path=tmp_path / "c.db")
        # Only 1 success → not "proven" at min_success_count=2
        fake.record("ANIMATION", "mm locomotion setup trooper", [{"t": "x"}], success=True)
        monkeypatch.setattr("bionics_tools.memory_tools.get_tool_cache", lambda: fake)

        result = memory_tools.bionics_tool_cache_find(
            "ANIMATION", "mm locomotion trooper", min_success_count=2,
        )
        assert result.ok is True
        # No proven → similar fallback kicks in
        assert result.data["proven"] == []
        assert len(result.data["similar"]) >= 1


# ============================================================================
# verify_semantic (Claude vision confirm)
# ============================================================================


class TestVerifySemantic:
    """Verify the new semantic-vision confirm method returns correct VerifyResults
    based on Claude's YES/NO/UNCERTAIN first-line response."""

    def _mock_response(self, first_line: str, explanation: str = "test"):
        """Build a fake Anthropic response with a single text block."""
        block = MagicMock()
        block.type = "text"
        block.text = f"{first_line}\n{explanation}"
        resp = MagicMock()
        resp.content = [block]
        return resp

    def _mock_client(self, response_text: str):
        client = MagicMock()
        client.messages.create.return_value = self._mock_response(response_text.split("\n", 1)[0],
                                                                   response_text.split("\n", 1)[1] if "\n" in response_text else "")
        return client

    def _fake_image(self):
        from PIL import Image
        return Image.new("RGB", (100, 100), color=(128, 128, 128))

    def test_yes_returns_pass(self):
        from core.verification import ActionVerifier, VerifyResult
        verifier = ActionVerifier()
        client = self._mock_client("YES\nThe dialog is closed as expected")
        report = verifier.verify_semantic(
            self._fake_image(),
            expected_description="Dialog should be closed",
            anthropic_client=client,
        )
        assert report.result == VerifyResult.PASS
        assert report.confidence >= 0.5
        assert "YES" in report.details

    def test_no_returns_fail(self):
        from core.verification import ActionVerifier, VerifyResult
        verifier = ActionVerifier()
        client = self._mock_client("NO\nThe dialog is still open")
        report = verifier.verify_semantic(
            self._fake_image(),
            expected_description="Dialog should be closed",
            anthropic_client=client,
        )
        assert report.result == VerifyResult.FAIL
        assert "NO" in report.details

    def test_uncertain_returns_uncertain(self):
        from core.verification import ActionVerifier, VerifyResult
        verifier = ActionVerifier()
        client = self._mock_client("UNCERTAIN\nThe state is ambiguous")
        report = verifier.verify_semantic(
            self._fake_image(),
            expected_description="Some expected state",
            anthropic_client=client,
        )
        assert report.result == VerifyResult.UNCERTAIN

    def test_api_error_returns_uncertain_not_raises(self):
        from core.verification import ActionVerifier, VerifyResult
        verifier = ActionVerifier()
        broken_client = MagicMock()
        broken_client.messages.create.side_effect = Exception("API down")
        report = verifier.verify_semantic(
            self._fake_image(),
            expected_description="anything",
            anthropic_client=broken_client,
        )
        assert report.result == VerifyResult.UNCERTAIN
        assert report.confidence == 0.0

    def test_no_client_attempts_lazy_init_and_reports_failure(self):
        """If anthropic client is None and lazy-init can't return a client,
        return UNCERTAIN gracefully rather than raising.

        2026-05-02 fix: was patching `anthropic.Anthropic` directly, but
        `verify_semantic` calls `core.anthropic_client.get_shared_client`
        (a cached singleton). The original patch never fired — the test
        was passing because the prior model ID was 404'd by the API,
        which the verifier's outer try/except caught as UNCERTAIN.
        With the model ID corrected (claude-sonnet-4-6), the API call
        succeeds and returns PASS, exposing the test design flaw.
        """
        from core.verification import ActionVerifier, VerifyResult
        verifier = ActionVerifier()
        # Patch the actual lazy-init entry point used by verify_semantic.
        with patch("core.anthropic_client.get_shared_client",
                   side_effect=RuntimeError("no api key")):
            report = verifier.verify_semantic(
                self._fake_image(),
                expected_description="anything",
                anthropic_client=None,
            )
        assert report.result == VerifyResult.UNCERTAIN

    def test_history_appends_on_each_call(self):
        from core.verification import ActionVerifier
        verifier = ActionVerifier()
        client = self._mock_client("YES\nok")
        verifier.verify_semantic(self._fake_image(), "state a", anthropic_client=client)
        verifier.verify_semantic(self._fake_image(), "state b", anthropic_client=client)
        assert len(verifier.history) == 2
