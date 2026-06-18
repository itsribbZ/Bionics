"""Bionics CLI — Command-line interface to the tool registry.

Exposes every registered Bionics tool as a CLI subcommand, auto-generated
from the tool schemas. Mirrors soft-ue-cli's CLI surface but with 192 tools
covering desktop automation + UE5.

Examples:
    bionics-cli list                           # list all tools
    bionics-cli list --category ue5_actor      # filter by category
    bionics-cli describe ue5_spawn_actor       # detail on one tool
    bionics-cli run capture_screen --monitor 0 --save_path shot.png
    bionics-cli run ue5_query_level --class_filter StaticMeshActor --limit 50
    bionics-cli run click --x 100 --y 200
    bionics-cli info                           # registry summary
    bionics-cli test                           # sanity-check registration

Also supports direct tool invocation:
    bionics-cli capture_screen --monitor 0
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Path setup
PROJECT_ROOT = Path(__file__).parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Suppress logging for clean CLI output unless verbose
logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

from bionics_tools import register_all
from core.bridge import SafetyTier, ToolGate, get_registry

_MSYS_PREFIXES = ("/Game/", "/Engine/", "/Script/", "/Temp/", "/Niagara/", "/Paper2D/")


def _unmangle_path(arg: str) -> str:
    """Reverse MSYS/Git Bash path mangling of UE5 asset paths.

    e.g. 'C:/Program Files/Git/Game/Test' → '/Game/Test'
    """
    for prefix in _MSYS_PREFIXES:
        idx = arg.find(prefix)
        if idx > 0:  # Don't match if already at start
            return arg[idx:]
    return arg


def _coerce_arg(value: str, schema_type: str):
    """Coerce a string arg from argparse into the proper JSON-schema type."""
    if value is None:
        return None
    if schema_type == "integer":
        return int(value)
    if schema_type == "number":
        return float(value)
    if schema_type == "boolean":
        return value.lower() in ("true", "1", "yes", "y")
    if schema_type == "array":
        try:
            return json.loads(value)
        except (ValueError, json.JSONDecodeError) as _e:
            # If it looks like JSON but didn't parse, fail clearly
            if value.lstrip().startswith(("[", "{")):
                raise SystemExit(
                    f"ERROR: array arg is malformed JSON: {value!r} ({_e})"
                )
            # Otherwise try comma-separated
            return [v.strip() for v in value.split(",") if v.strip()]
    if schema_type == "object":
        try:
            return json.loads(value)
        except (ValueError, json.JSONDecodeError) as _e:
            raise SystemExit(f"ERROR: argument is not valid JSON: {value!r} ({_e})")
    return value


def _add_tool_args(parser: argparse.ArgumentParser, input_schema: dict):
    """Add argparse args for each parameter in the tool's JSON schema."""
    properties = input_schema.get("properties", {})
    required = input_schema.get("required", [])
    for param_name, prop in properties.items():
        ptype = prop.get("type", "string")
        default = prop.get("default")
        help_text = prop.get("description", "")
        is_required = param_name in required and default is None

        flag = f"--{param_name.replace('_', '-')}"

        if ptype == "boolean":
            parser.add_argument(
                flag,
                type=lambda v: v.lower() not in ("false", "0", "no", "off"),
                default=default if default is not None else False,
                nargs="?",
                const=True,
                help=help_text,
            )
        else:
            parser.add_argument(
                flag,
                default=default,
                required=is_required,
                help=f"[{ptype}] {help_text}" + (f" (default: {default})" if default is not None else ""),
            )


def _print_result(result: dict, as_json: bool = False):
    """Print a ToolResult dict to stdout."""
    if as_json:
        print(json.dumps(result, indent=2, default=str))
        return

    ok = result.get("ok", False)
    content = result.get("content", "")
    error = result.get("error", "")
    data = result.get("data", {})
    meta = result.get("meta", {})

    if ok:
        if content:
            print(content)
        if data:
            print(f"\n--- data ---\n{json.dumps(data, indent=2, default=str)}")
        if meta.get("elapsed_ms"):
            print(f"\n[{meta['elapsed_ms']}ms, tier={meta.get('tier','?')}]")
    else:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)


def cmd_list(args):
    reg = get_registry()
    specs = reg.list_all()
    if args.category:
        specs = [s for s in specs if s.category == args.category]
    if args.search:
        q = args.search.lower()
        specs = [s for s in specs if q in s.name.lower() or q in s.description.lower()]

    by_cat: dict[str, list] = {}
    for spec in specs:
        by_cat.setdefault(spec.category, []).append(spec)

    if args.json:
        print(json.dumps(
            [s.to_dict() for s in specs], indent=2, default=str,
        ))
        return

    total = len(specs)
    print(f"\nBionics Tool Registry — {total} tools")
    print("=" * 70)
    for cat in sorted(by_cat.keys()):
        tools = sorted(by_cat[cat], key=lambda t: t.name)
        print(f"\n[{cat}]  ({len(tools)} tools)")
        for t in tools:
            # ASCII tier markers — emoji break Windows cp1252 terminals (UnicodeEncodeError mid-list).
            tier_mark = {
                SafetyTier.SAFE: " ",
                SafetyTier.MODERATE: "!",
                SafetyTier.DESTRUCTIVE: "X",
            }[t.safety_tier]
            desc = t.description.split("\n")[0][:55]
            print(f"  {tier_mark} {t.name:30s} {desc}")


def cmd_describe(args):
    reg = get_registry()
    spec = reg.get(args.tool_name)
    if spec is None:
        print(f"Unknown tool: {args.tool_name}", file=sys.stderr)
        print("Use 'bionics-cli list' to see all tools.", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(spec.to_dict(), indent=2, default=str))
        return

    print(f"\n{spec.name}")
    print("=" * 70)
    print(f"Category:    {spec.category}")
    print(f"Safety Tier: {spec.safety_tier.value}")
    print(f"Read-only:   {spec.annotations.read_only}")
    print(f"Destructive: {spec.annotations.destructive}")
    print(f"Idempotent:  {spec.annotations.idempotent}")
    print(f"\n{spec.description}")
    print("\nArguments:")
    props = spec.input_schema.get("properties", {})
    required = spec.input_schema.get("required", [])
    if not props:
        print("  (none)")
    for name, prop in props.items():
        req = " (REQUIRED)" if name in required else ""
        ptype = prop.get("type", "string")
        default = prop.get("default")
        desc = prop.get("description", "")
        print(f"  --{name.replace('_', '-')} [{ptype}]{req}")
        if desc:
            print(f"       {desc}")
        if default is not None:
            print(f"       default: {default}")
    if spec.aliases:
        print(f"\nAliases: {', '.join(spec.aliases)}")


def cmd_run(args, extra_argv: list[str]):
    reg = get_registry()
    spec = reg.get(args.tool_name)
    if spec is None:
        print(f"Unknown tool: {args.tool_name}", file=sys.stderr)
        sys.exit(1)

    # Parse tool-specific args
    sub_parser = argparse.ArgumentParser(prog=f"bionics-cli run {args.tool_name}")
    _add_tool_args(sub_parser, spec.input_schema)
    sub_args = sub_parser.parse_args(extra_argv)
    sub_dict = vars(sub_args)

    # Coerce types
    props = spec.input_schema.get("properties", {})
    required = spec.input_schema.get("required", [])
    arguments: dict = {}
    for key, val in sub_dict.items():
        if val is None and key not in required:
            continue
        schema_type = props.get(key, {}).get("type", "string")
        arguments[key] = _coerce_arg(val, schema_type) if isinstance(val, str) else val

    gate = ToolGate()
    gate.set_bypass_safety(args.no_safety)
    result = gate.execute(
        args.tool_name,
        arguments,
        dry_run=args.dry_run,
        confirm_override=args.yes,
    )
    _print_result(result.to_dict(), as_json=args.json)


def cmd_info(args):
    reg = get_registry()
    summary = reg.summary()
    if args.json:
        print(json.dumps(summary, indent=2))
        return
    print("\nBionics Tool Registry Summary")
    print("=" * 50)
    print(f"Total tools: {summary['total_tools']}")
    print("\nBy category:")
    for cat, count in sorted(summary["categories"].items()):
        print(f"  {cat:20s} {count:4d}")
    print("\nBy safety tier:")
    for tier, count in sorted(summary["safety_tiers"].items()):
        print(f"  {tier:15s} {count:4d}")


def cmd_test(args):
    """Self-test: verify registry populated and gate works."""
    reg = get_registry()
    total = reg.count()
    print(f"Registered tools: {total}")
    if total == 0:
        print("FAIL: no tools registered", file=sys.stderr)
        sys.exit(1)

    # Test a few safe tools
    gate = ToolGate()
    gate.set_bypass_safety(True)
    tests = ["version", "list_tools", "list_categories", "get_screen_size"]
    passed = 0
    for name in tests:
        result = gate.execute(name, {})
        status = "OK " if result.ok else "FAIL"
        print(f"  [{status}] {name}: {result.content[:60]}")
        if result.ok:
            passed += 1
    print(f"\n{passed}/{len(tests)} self-tests passed")
    if passed < len(tests):
        sys.exit(1)


def main():
    # Register all tools before parsing args
    try:
        register_all()
    except Exception as e:
        print(f"WARNING: Some tools failed to register: {e}", file=sys.stderr)

    # Opt-in observability: BIONICS_OTEL_ENABLE=1 wires OTLP spans for every tool call.
    try:
        from core.otel_hook import install_from_env
        install_from_env()
    except Exception:
        pass  # OTel is best-effort; never block CLI startup

    parser = argparse.ArgumentParser(
        prog="bionics-cli",
        description="Bionics CLI — Desktop automation + UE5 game dev toolkit",
    )
    parser.add_argument(
        "--json", action="store_true", help="Output results as JSON",
    )
    parser.add_argument(
        "--no-safety", action="store_true",
        help="Bypass safety layer (trusted caller, scripts only)",
    )
    parser.add_argument(
        "--yes", "-y", action="store_true", help="Pre-approve destructive actions",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate only; do not execute",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    lp = subparsers.add_parser("list", help="List all registered tools")
    lp.add_argument("--category", "-c", default="", help="Filter by category")
    lp.add_argument("--search", "-s", default="", help="Name/description substring")

    # describe
    dp = subparsers.add_parser("describe", help="Show full spec for a tool")
    dp.add_argument("tool_name", help="Tool to describe")

    # run
    rp = subparsers.add_parser("run", help="Execute a tool")
    rp.add_argument("tool_name", help="Tool to run")

    # info
    subparsers.add_parser("info", help="Show registry summary")

    # test
    subparsers.add_parser("test", help="Run self-tests")

    # Parse, splitting after the tool_name for 'run'
    # We look for 'run' anywhere in argv (after optional global flags)
    argv = sys.argv[1:]
    extra: list[str] = []
    # Find 'run' as a subcommand — it must come after flags/values that don't consume it
    # We walk forward, skipping recognized global flags and their arguments
    global_flags_with_value: set[str] = set()  # our global flags are all boolean
    i = 0
    run_idx = -1
    while i < len(argv):
        arg = argv[i]
        if arg == "run":
            run_idx = i
            break
        if arg.startswith("--") and arg in global_flags_with_value:
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        # First non-flag is the subcommand — if it's not 'run', break
        break

    if run_idx >= 0 and run_idx + 1 < len(argv):
        pre_argv = argv[: run_idx + 2]
        extra = argv[run_idx + 2 :]
        extra = [_unmangle_path(a) for a in extra]
        args = parser.parse_args(pre_argv)
    else:
        args = parser.parse_args(argv)

    if args.command == "list":
        cmd_list(args)
    elif args.command == "describe":
        cmd_describe(args)
    elif args.command == "run":
        cmd_run(args, extra)
    elif args.command == "info":
        cmd_info(args)
    elif args.command == "test":
        cmd_test(args)


if __name__ == "__main__":
    main()
