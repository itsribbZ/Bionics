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


# ============================================================================
# _discover_bridge — token resolution (env > cwd-walk > config fallback)
# ============================================================================


class TestDiscoverBridge:
    """Bridge URL/token resolution for native :8090 calls.

    The config fallback (paths.ue5_project) exists because the MCP server runs with
    cwd=Bionics repo, so the cwd-walk can't see <ue5_project>/.bionics-bridge/
    instance.json — without it, native tools 401 against the live bridge. Env vars
    and the cwd-walk keep priority; the config branch is a last resort before the
    auth-disabled default.
    """

    @staticmethod
    def _write_instance(project_dir, url, token):
        import json

        bridge_dir = project_dir / ".bionics-bridge"
        bridge_dir.mkdir(parents=True, exist_ok=True)
        (bridge_dir / "instance.json").write_text(
            json.dumps({"url": url, "token": token}), encoding="utf-8"
        )

    def test_env_vars_take_priority(self, monkeypatch):
        from bionics_tools import ue5_native

        monkeypatch.setenv("BIONICS_BRIDGE_URL", "http://127.0.0.1:9999/bridge")
        monkeypatch.setenv("BIONICS_BRIDGE_TOKEN", "env-token-abc")
        # Config must NOT be consulted when env fully resolves.
        monkeypatch.setattr(
            ue5_native,
            "_configured_ue5_project_dir",
            lambda: (_ for _ in ()).throw(AssertionError("config consulted despite env")),
        )

        url, token = ue5_native._discover_bridge()
        assert url == "http://127.0.0.1:9999/bridge"
        assert token == "env-token-abc"

    def test_config_fallback_resolves_token(self, monkeypatch, tmp_path):
        from bionics_tools import ue5_native

        monkeypatch.delenv("BIONICS_BRIDGE_URL", raising=False)
        monkeypatch.delenv("BIONICS_BRIDGE_TOKEN", raising=False)
        # cwd with no .bionics-bridge ancestor → the cwd-walk yields nothing.
        empty_cwd = tmp_path / "empty_cwd"
        empty_cwd.mkdir()
        monkeypatch.chdir(empty_cwd)

        proj = tmp_path / "MyProject"
        proj.mkdir()
        self._write_instance(proj, "http://127.0.0.1:8090/bridge", "live-token-xyz")
        monkeypatch.setattr(ue5_native, "_configured_ue5_project_dir", lambda: str(proj))

        url, token = ue5_native._discover_bridge()
        assert url == "http://127.0.0.1:8090/bridge"
        assert token == "live-token-xyz"

    def test_config_fallback_does_not_override_env_url(self, monkeypatch, tmp_path):
        """Partial env (url only) keeps the env url but fills the token from config."""
        from bionics_tools import ue5_native

        monkeypatch.setenv("BIONICS_BRIDGE_URL", "http://127.0.0.1:7777/bridge")
        monkeypatch.delenv("BIONICS_BRIDGE_TOKEN", raising=False)
        empty_cwd = tmp_path / "empty_cwd"
        empty_cwd.mkdir()
        monkeypatch.chdir(empty_cwd)

        proj = tmp_path / "MyProject"
        proj.mkdir()
        self._write_instance(proj, "http://127.0.0.1:8090/bridge", "cfg-token")
        monkeypatch.setattr(ue5_native, "_configured_ue5_project_dir", lambda: str(proj))

        url, token = ue5_native._discover_bridge()
        assert url == "http://127.0.0.1:7777/bridge"  # env url preserved
        assert token == "cfg-token"  # token filled from config

    def test_config_fallback_missing_file_returns_default(self, monkeypatch, tmp_path):
        from bionics_tools import ue5_native

        monkeypatch.delenv("BIONICS_BRIDGE_URL", raising=False)
        monkeypatch.delenv("BIONICS_BRIDGE_TOKEN", raising=False)
        empty_cwd = tmp_path / "empty_cwd"
        empty_cwd.mkdir()
        monkeypatch.chdir(empty_cwd)

        proj = tmp_path / "MyProject"  # exists but no .bionics-bridge/instance.json
        proj.mkdir()
        monkeypatch.setattr(ue5_native, "_configured_ue5_project_dir", lambda: str(proj))

        url, token = ue5_native._discover_bridge()
        assert url == ue5_native.DEFAULT_BRIDGE_URL
        assert token == ""

    def test_no_config_no_env_returns_default(self, monkeypatch, tmp_path):
        from bionics_tools import ue5_native

        monkeypatch.delenv("BIONICS_BRIDGE_URL", raising=False)
        monkeypatch.delenv("BIONICS_BRIDGE_TOKEN", raising=False)
        empty_cwd = tmp_path / "empty_cwd"
        empty_cwd.mkdir()
        monkeypatch.chdir(empty_cwd)
        monkeypatch.setattr(ue5_native, "_configured_ue5_project_dir", lambda: "")

        url, token = ue5_native._discover_bridge()
        assert url == ue5_native.DEFAULT_BRIDGE_URL
        assert token == ""

    def test_configured_ue5_project_dir_reads_real_config(self):
        from bionics_tools import ue5_native

        proj = ue5_native._configured_ue5_project_dir()
        assert proj, "paths.ue5_project should be set in config.yaml"
        assert "MyProject" in proj
