"""Tests for UE5 Native Bridge wrappers.

Covers Python wrappers in bionics_tools/ue5_native.py that hit the C++
BionicsBridge plugin via JSON-RPC. Without a live UE5 these tests verify:

- Tool registration (name, category, safety tier, aliases)
- Each Python wrapper delegates to _call_tool with the right tool name + args
- Failure-path returns ToolResult.failure when bridge call raises

Initial scope (added with v0.7.8 live coding native lift): live_coding_compile.
Future native-tool wrappers should grow this file rather than start a new one.
"""

from unittest.mock import patch


def _mock_success(data: dict | None = None):
    from core.bridge import ToolResult
    return ToolResult.success(content="mocked", data=data or {"ok": True, "triggered": True})


def _mock_failure(msg: str = "mocked failure"):
    from core.bridge import ToolResult
    return ToolResult.failure(msg)


# ============================================================================
# live_coding_compile
# ============================================================================


class TestLiveCodingCompile:
    def test_tool_registered(self):
        from bionics_tools import ue5_native  # noqa: F401 — import registers
        from core.bridge import get_registry

        spec = get_registry().get("ue5_native_live_coding_compile")
        assert spec is not None, "ue5_native_live_coding_compile not registered"

    def test_category_and_safety_tier(self):
        from bionics_tools import ue5_native  # noqa: F401
        from core.bridge import SafetyTier, get_registry

        spec = get_registry().get("ue5_native_live_coding_compile")
        assert spec.category == "ue5_build"
        assert spec.safety_tier == SafetyTier.MODERATE

    def test_aliases_registered(self):
        from bionics_tools import ue5_native  # noqa: F401
        from core.bridge import get_registry

        spec = get_registry().get("ue5_native_live_coding_compile")
        assert "live-coding-compile" in spec.aliases
        assert "ue5-live-coding-native" in spec.aliases

    def test_calls_bridge_with_correct_method(self):
        from bionics_tools import ue5_native

        with patch("bionics_tools.ue5_native._call_tool", return_value=_mock_success()) as mock:
            result = ue5_native.ue5_native_live_coding_compile()

        assert result.ok is True
        mock.assert_called_once_with("live_coding_compile", {})

    def test_returns_bridge_failure_unchanged(self):
        from bionics_tools import ue5_native

        with patch(
            "bionics_tools.ue5_native._call_tool",
            return_value=_mock_failure("LiveCoding module not loaded"),
        ):
            result = ue5_native.ue5_native_live_coding_compile()

        assert result.ok is False
        assert "LiveCoding module not loaded" in result.error


# ============================================================================
# log_tail
# ============================================================================


class TestLogTail:
    def test_tool_registered(self):
        from bionics_tools import ue5_native  # noqa: F401
        from core.bridge import get_registry

        spec = get_registry().get("ue5_native_log_tail")
        assert spec is not None, "ue5_native_log_tail not registered"

    def test_is_safe_and_read_only(self):
        from bionics_tools import ue5_native  # noqa: F401
        from core.bridge import SafetyTier, get_registry

        spec = get_registry().get("ue5_native_log_tail")
        assert spec.safety_tier == SafetyTier.SAFE
        assert spec.annotations.read_only is True
        assert spec.category == "ue5_debug"

    def test_aliases_registered(self):
        from bionics_tools import ue5_native  # noqa: F401
        from core.bridge import get_registry

        spec = get_registry().get("ue5_native_log_tail")
        assert "log-tail" in spec.aliases
        assert "ue5-log-tail" in spec.aliases

    def test_default_args(self):
        """Initial poll call (cursor=0, no filter) — verify defaults round-trip."""
        from bionics_tools import ue5_native

        mock_result_data = {
            "lines": ["[WEAPON DIAG] equip start", "LogTemp: hello"],
            "cursor": 1234,
            "file_size": 1234,
            "line_count": 2,
            "truncated": False,
            "rotated": False,
            "log_path": "C:/proj/Saved/Logs/MyProject.log",
        }
        with patch(
            "bionics_tools.ue5_native._call_tool",
            return_value=_mock_success(data=mock_result_data),
        ) as mock:
            result = ue5_native.ue5_native_log_tail()

        assert result.ok is True
        mock.assert_called_once_with("log_tail", {
            "since_cursor": 0,
            "filter_regex": "",
            "max_lines": 500,
            "max_bytes": 1048576,
            "log_path": "",
        })

    def test_filter_and_cursor_passthrough(self):
        from bionics_tools import ue5_native

        with patch(
            "bionics_tools.ue5_native._call_tool",
            return_value=_mock_success(data={"lines": [], "cursor": 5000}),
        ) as mock:
            ue5_native.ue5_native_log_tail(
                since_cursor=4096,
                filter_regex=r"\[WEAPON DIAG\]|\[AltitudeTransition\]",
                max_lines=200,
                max_bytes=2048,
            )

        mock.assert_called_once_with("log_tail", {
            "since_cursor": 4096,
            "filter_regex": r"\[WEAPON DIAG\]|\[AltitudeTransition\]",
            "max_lines": 200,
            "max_bytes": 2048,
            "log_path": "",
        })

    def test_clamps_max_bytes_floor(self):
        """max_bytes < 1024 should clamp to 1024."""
        from bionics_tools import ue5_native

        with patch("bionics_tools.ue5_native._call_tool", return_value=_mock_success()) as mock:
            ue5_native.ue5_native_log_tail(max_bytes=128)

        called_args = mock.call_args[0][1]
        assert called_args["max_bytes"] == 1024

    def test_clamps_max_lines_ceiling(self):
        """max_lines > 10000 should clamp to 10000."""
        from bionics_tools import ue5_native

        with patch("bionics_tools.ue5_native._call_tool", return_value=_mock_success()) as mock:
            ue5_native.ue5_native_log_tail(max_lines=99999)

        called_args = mock.call_args[0][1]
        assert called_args["max_lines"] == 10000

    def test_negative_cursor_clamps_to_zero(self):
        """A negative since_cursor (e.g. uninitialized) clamps to 0 — safe initial call."""
        from bionics_tools import ue5_native

        with patch("bionics_tools.ue5_native._call_tool", return_value=_mock_success()) as mock:
            ue5_native.ue5_native_log_tail(since_cursor=-1)

        called_args = mock.call_args[0][1]
        assert called_args["since_cursor"] == 0

    def test_returns_bridge_failure_unchanged(self):
        from bionics_tools import ue5_native

        with patch(
            "bionics_tools.ue5_native._call_tool",
            return_value=_mock_failure("Log file not found"),
        ):
            result = ue5_native.ue5_native_log_tail()

        assert result.ok is False
        assert "Log file not found" in result.error
