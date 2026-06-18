# Contributing to Bionics

Thanks for wanting to help. Bionics is a solo-maintained project; I value
small, well-scoped PRs with tests over sweeping rewrites.

## Quickstart (dev install)

```bash
# 1. Fork + clone
git clone https://github.com/jbro1/bionics.git
cd bionics

# 2. Editable install with dev extras (pytest, ruff, mypy)
pip install -e ".[dev]"

# 3. Optional: enable OTel + sqlite-vec for feature tests
pip install -e ".[dev,otel,vector]"

# 4. Set your Anthropic API key (needed for tests that exercise the real API
#    path; most tests use mocks and do not need it)
#    Windows PowerShell: $env:ANTHROPIC_API_KEY = "sk-ant-..."
#    Linux/macOS bash:   export ANTHROPIC_API_KEY=sk-ant-...

# 5. Verify
pytest tests/                 # should be 446 passed
ruff check .                  # lint
mypy core bionics_tools       # type check (warnings OK, errors not)
```

## Writing a new tool

Every Bionics tool is one function decorated with `@bionics_tool`. The
minimum-viable contribution is a new `.py` file under `bionics_tools/` plus a
matching import line in `bionics_tools/__init__.py::register_all()`.

**Template** (`bionics_tools/my_category.py`):

```python
from typing import Annotated
from core.bridge import bionics_tool, SafetyTier, ToolResult

@bionics_tool(
    name="my_tool_name",
    category="my_category",
    safety_tier=SafetyTier.SAFE,      # or MODERATE / DESTRUCTIVE
    read_only=True,                    # hint for client UIs
    idempotent=True,                   # same input → same output?
    title="Human-readable title",
    output_schema={                    # MCP 2025-11-25 structuredContent
        "type": "object",
        "properties": {"result": {"type": "string"}},
        "required": ["result"],
    },
)
def my_tool_name(
    arg1: Annotated[str, "A human-readable description shown in schemas"],
    count: int = 1,
) -> ToolResult:
    """One-line tool summary (first line of docstring becomes MCP description)."""
    try:
        result = f"{arg1} x {count}"
        return ToolResult.success(content=result, data={"result": result})
    except Exception as e:
        return ToolResult.failure(str(e))
```

Register it by adding a single import to `bionics_tools/__init__.py`:

```python
def register_all() -> int:
    ...
    from bionics_tools import my_category  # noqa: F401
    ...
```

Then run `pytest tests/` to make sure nothing regressed.

## Adding tests

Tests live under `tests/test_*.py`. Every feature merge must add or extend a
test file. We use pytest + its fixtures; no custom framework.

**Preferred pattern**:

```python
import pytest
from core.bridge import ToolGate, ToolResult

def test_my_tool_roundtrips_ok():
    gate = ToolGate()
    gate.set_bypass_safety(True)
    result = gate.execute("my_tool_name", {"arg1": "hi", "count": 3})
    assert result.ok
    assert result.data["result"] == "hi x 3"
```

For tools that call the Anthropic API or UE5, mock — don't hit the real
service. See `tests/test_agent_definitions.py` for the `mock_client` fixture
pattern.

## Code quality

- `ruff check .` must pass. Config in `pyproject.toml[tool.ruff]`.
- `mypy core bionics_tools` should produce zero new errors.
- Keep modules under ~700 lines; split if you're growing past.
- Docstrings on every public function. First line is the tool description
  shown to LLM callers — it's load-bearing.
- No emojis in code / docstrings / comments unless the user explicitly asks.

## Commit + PR style

- One logical change per PR. If you find tangential bugs, file a separate issue.
- Commit messages: imperative mood, <72 chars for the title.
  - Good: `Fix CORS wildcard defeating bearer-auth layer`
  - Bad: `various fixes`
- Reference the audit finding / issue number if applicable.
- If the PR bumps `pyproject.toml::version`, also update:
  - `config.yaml::bionics.version`
  - `config.yaml.example::bionics.version`
  - `main.py` banner string
  - `tests/test_integration.py` version assertion
  - `core/otel_hook.py` tracer instrument version
  - `mcp_server.py` INSTRUCTIONS (tool count if it changed)
  - `README.md` (tool count + version headline)
  - `CHANGELOG.md` — add a new entry with Added/Changed/Fixed/Security sections.

## Safety-tier classification

When adding a tool, pick the right tier:

| Tier | Use for | Example |
|------|---------|---------|
| `SAFE` | Read-only, no side effects | `list_tools`, `ue5_query_blueprint` |
| `MODERATE` | Reversible side effects | `click`, `type_text`, `ue5_save_asset` |
| `DESTRUCTIVE` | Cannot be undone | `ue5_delete_actor`, `ue5_run_python`, `ue5_batch_delete` |

Destructive tools are gated by `BIONICS_MCP_ALLOW_DESTRUCTIVE=true` at the
MCP layer. Don't misclassify — a missed DESTRUCTIVE tag is a footgun.

## Where to find things

- Core execution loop: `core/agent.py`
- Tool registry + decorator: `core/bridge.py`
- Safety tier enforcement: `core/safety.py`
- Session persistence + progress: `core/session.py`
- Sub-agent fan-out: `core/agent_definitions.py`
- Async task manager: `core/task_manager.py`
- Vector + lexical memory: `core/memory.py` + `core/embeddings.py`
- OTel opt-in: `core/otel_hook.py`
- C++ UE5 bridge: `plugins/BionicsBridge/Source/`
- BPDoctor static analysis: `plugins/BPDoctor/Source/`
- Python-side bridge client: `bionics_tools/ue5_native.py`

## Releasing

Only the maintainer cuts releases. Format is `MAJOR.MINOR.PATCH`:
- **MAJOR** for breaking API changes.
- **MINOR** for new features (new tools, new subsystems).
- **PATCH** for bug fixes + security patches (e.g. `0.7.2 → 0.7.3`).

Release checklist is in `CHANGELOG.md` — add your entry at the top, land,
tag, push.
