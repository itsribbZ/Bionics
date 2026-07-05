"""Security tests — verify UE5 tools resist Python code injection.

These tests do NOT require a running UE5 editor. They verify that
the tools REJECT malicious inputs before even attempting to connect,
via the validation layer and escape_path/safe_json_literal functions.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bionics_tools._ue5_common import escape_path, safe_json_literal


class TestEscapePath:
    def test_basic_string(self):
        assert escape_path("hello") == "hello"

    def test_backslash_converted(self):
        assert "\\" not in escape_path(r"C:\path\file")
        assert escape_path(r"C:\path\file") == "C:/path/file"

    def test_single_quote_escaped(self):
        result = escape_path("foo'bar")
        assert "\\'" in result

    def test_double_quote_escaped(self):
        result = escape_path('foo"bar')
        assert '\\"' in result

    def test_newline_escaped(self):
        result = escape_path("foo\nbar")
        assert "\n" not in result  # literal newline gone
        assert "\\n" in result     # escaped newline present

    def test_carriage_return_escaped(self):
        result = escape_path("foo\rbar")
        assert "\r" not in result

    def test_null_byte_stripped(self):
        result = escape_path("foo\x00bar")
        assert "\x00" not in result
        assert result == "foobar"

    def test_triple_quote_broken(self):
        result = escape_path("'''injection")
        assert "'''" not in result

    def test_non_string_coerced(self):
        assert escape_path(12345) == "12345"
        assert escape_path(None) == "None"

    def test_injection_payload_neutralized(self):
        payload = "x')\nimport os; os.system('calc')\ngetattr(unreal, 'x"
        result = escape_path(payload)
        # Must NOT contain unescaped single quote followed by code
        assert "\\'" in result
        assert "\\n" in result
        # Literal newline must be gone
        assert "\n" not in result


class TestSafeJsonLiteral:
    def test_round_trip(self):
        import base64
        import json
        obj = {"a": 1, "b": [1, 2, 3], "c": "hello"}
        b64 = safe_json_literal(obj)
        decoded = json.loads(base64.b64decode(b64).decode("utf-8"))
        assert decoded == obj

    def test_with_malicious_strings(self):
        import base64
        import json
        obj = {"injection": "'''print('INJECTED')#"}
        b64 = safe_json_literal(obj)
        # b64 output has no quotes in it
        assert "'" not in b64
        assert '"' not in b64
        # Decoded correctly
        decoded = json.loads(base64.b64decode(b64).decode("utf-8"))
        assert decoded == obj

    def test_with_unicode(self):
        import base64
        import json
        obj = {"text": "café ñ 中文"}
        b64 = safe_json_literal(obj)
        decoded = json.loads(base64.b64decode(b64).decode("utf-8"))
        assert decoded == obj


class TestUE5ValidationBeforeExec:
    """Verify UE5 tools REJECT malicious input at validation level,
    before connecting to UE5 (or failing due to no UE5)."""

    def setup_method(self):
        from bionics_tools import register_all
        register_all()
        from core.bridge import ToolGate
        self.gate = ToolGate()
        self.gate.set_bypass_safety(True)

    def test_ue5_spawn_rejects_non_numeric_location(self):
        r = self.gate.execute("ue5_spawn_actor", {
            "actor_class": "StaticMeshActor",
            "location": ["bad", "string", "input"],
        })
        assert r.ok is False
        # Should fail at coerce, not at UE5 call
        assert "numeric" in r.error.lower() or "invalid" in r.error.lower() or "float" in r.error.lower()

    def test_ue5_spawn_rejects_short_location(self):
        r = self.gate.execute("ue5_spawn_actor", {
            "actor_class": "StaticMeshActor",
            "location": [1.0, 2.0],  # only 2 elements
        })
        assert r.ok is False

    def test_ue5_set_transform_rejects_bad_vectors(self):
        r = self.gate.execute("ue5_set_transform", {
            "actor_name": "test",
            "location": ["inject", "me", "now"],
        })
        assert r.ok is False

    def test_ue5_material_vector_rejects_short_rgba(self):
        r = self.gate.execute("ue5_set_material_vector", {
            "asset_path": "/Game/M",
            "parameter_name": "Color",
            "rgba": [0.5],  # only 1 element
        })
        assert r.ok is False

    def test_ue5_material_vector_rejects_non_numeric(self):
        r = self.gate.execute("ue5_set_material_vector", {
            "asset_path": "/Game/M",
            "parameter_name": "Color",
            "rgba": ["r", "g", "b"],
        })
        assert r.ok is False

    def test_ue5_batch_spawn_rejects_empty(self):
        r = self.gate.execute("ue5_batch_spawn", {"actors": []})
        assert r.ok is False

    def test_ue5_add_variable_rejects_bad_type(self):
        r = self.gate.execute("ue5_add_variable", {
            "asset_path": "/Game/BP",
            "variable_name": "x",
            "variable_type": "RANDOM_GARBAGE",
        })
        assert r.ok is False

    def test_ue5_run_python_file_rejects_non_py(self):
        r = self.gate.execute("ue5_run_python_file", {
            "file_path": "C:/Users/jbro1/.ssh/id_rsa",
        })
        assert r.ok is False
        assert ".py" in r.error.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
