# Security Policy

Bionics is a **local desktop-automation agent** with a Python MCP server and a
C++ Unreal Engine 5 editor plugin (`BionicsBridge`). This document describes
its intended threat model, attack surface, and disclosure process.

## Threat Model

Bionics is designed for **a single developer on their own machine**, driving
their own Unreal Engine project. It is NOT a multi-tenant service, NOT a
public endpoint, and NOT hardened against untrusted code running on the
same machine.

If an attacker can execute arbitrary code on the user's workstation, they
can already do anything a Bionics tool can do — Bionics does not add attack
surface in that case.

Bionics **does** provide defense-in-depth against:

1. **Cross-origin attacks on the local HTTP bridge** — the BionicsBridge C++
   plugin binds to `127.0.0.1` only and gates `POST /bridge` behind a per-instance
   **256-bit bearer token** (two concatenated UUID v4s, ~244 bits of real
   randomness). CORS headers are locked to `http://127.0.0.1` so a malicious
   page served from another origin cannot use a stolen browser context to
   invoke destructive tools.

2. **Prompt injection via OCR'd screen text** — `core/guardrails.py` registers
   a pre-tool-use hook that scans every string argument for known injection
   patterns (`ignore previous instructions`, `pretend you are`, `system: ignore`,
   etc.) and trips a `GuardrailTripwire` on match. OCR content is the live
   attack surface; attackers can place adversarial text on the screen.

3. **Destructive tool abuse** — 17 destructive UE5 tools (`ue5_delete_*`,
   `ue5_batch_delete`, `ue5_run_python`, etc.) are gated by `SafetyTier.DESTRUCTIVE`
   which the MCP server blocks by default. Set `BIONICS_MCP_ALLOW_DESTRUCTIVE=true`
   to enable them.

4. **Oversized request DoS** — the C++ bridge rejects request bodies over
   1 MB before parsing. The Python MCP server relies on FastMCP's upstream
   limits.

## What Bionics does NOT protect against

- An attacker with filesystem access reading `.bionics-bridge/instance.json`
  (contains the bearer token in plaintext). Protect this file with OS-level
  ACLs — don't sync it to cloud drives, don't commit it to git (already in
  `.gitignore`).
- A compromised UE5 project: the BionicsBridge plugin runs inside the UE5
  editor process and has full editor privileges. Only install Bionics in
  projects you control.
- A malicious MCP client: if you point Bionics at a hostile MCP client, the
  client can issue any tool call you've allowed. Use `BIONICS_MCP_ALLOW_DESTRUCTIVE=true`
  sparingly.
- Supply-chain attacks on Python dependencies: pin your deps, review the lock
  file before `pip install`. Bionics's `requirements.txt` has upper-bound
  version caps but does not ship a lock file.

## Configuration

### Bearer token auth (C++ bridge)

The plugin auto-generates a fresh token on every UE5 startup and writes it to
`<ProjectDir>/.bionics-bridge/instance.json`:

```json
{ "url": "http://127.0.0.1:8090", "port": 8090, "token": "9a1c…fe2b" }
```

Override the generated token with `BIONICS_BRIDGE_TOKEN=<token>` before UE5
launches (useful for CI scenarios where Bionics is expected to use a known
token).

The Python side auto-discovers the token by reading `instance.json` and
attaches `Authorization: Bearer <token>` to every HTTP request.

### Destructive tool gating

```bash
# Default: destructive tools (delete_actor, delete_asset, run_python, ...) are blocked
python mcp_server.py

# Opt-in when you explicitly want the agent to do destructive work
BIONICS_MCP_ALLOW_DESTRUCTIVE=true python mcp_server.py
```

### Localhost-only bind

The C++ `FHttpServerModule` binds to `127.0.0.1` by default. To expose the
bridge to another machine you would have to modify the plugin source — this
is intentional friction. **Do not do that without adding TLS + a real auth
layer on top.**

## Reporting a vulnerability

If you find a security issue, please do NOT open a public GitHub issue.
Instead, contact the maintainer directly:

- GitHub: open a private security advisory via the repository's **Security**
  tab (GitHub will notify the maintainer).

Please include:
- Affected version (`bionics --version`).
- Reproduction steps.
- Observed vs. expected behavior.
- If applicable, a proposed patch.

I'll acknowledge within 72 hours and coordinate a fix + disclosure timeline.

## Hardening checklist for production-ish deployments

If you're running Bionics in a more exposed setting (shared workstation, CI
runner, VM):

- [ ] Confirm `.bionics-bridge/` is in `.gitignore` (it is by default).
- [ ] Confirm the UE5 editor is bound to `127.0.0.1` only (default) — not
      `0.0.0.0`.
- [ ] Set file ACLs on `.bionics-bridge/instance.json` to the running user only.
- [ ] Never set `BIONICS_MCP_ALLOW_DESTRUCTIVE=true` in a persistent env profile.
- [ ] Audit your `plans/*.json` before running — plans can invoke destructive
      tools even when the default gate is closed if the client explicitly
      approves.
- [ ] If you enable `BIONICS_OTEL_ENABLE=1`, make sure your OTel collector
      endpoint is over TLS and not leaking tool arguments to a third party.
