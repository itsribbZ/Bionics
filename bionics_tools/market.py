"""Market Tools — Marketing content generation at scale.

Turns the Market Bot skill into a Bionics-native rapid generation pipeline:

    1. Parse marketbot master PDFs → structured knowledge base
    2. Load product specs (JSON) → context
    3. Generate N posts/ads/emails via Claude API (all marketbot rules enforced)
    4. Run guardrail checks (10 NEVER rules from marketbot skill)
    5. Save outputs + create Bionics plan with one step per post

Workflow:
    market_parse_pdf(pdf_path)         → extracts knowledge base text + sections
    market_save_product(name, ...)     → saves product JSON spec
    market_generate_post(product, ...) → ONE post generated
    market_batch_generate(product, count=10, content_type="social") → 10 posts in sequence
    market_build_plan(product, count, ...) → creates Bionics plan with N generation steps
    market_guardrails(content)         → validates content against 10 NEVER rules

Paths (configurable via config.yaml `paths.market_kb`):
    Knowledge base PDF:  <paths.market_kb>/MarketBot_KnowledgeBase.pdf
    Product specs:       <bionics_root>/market/products/*.json
    Generated output:    <bionics_root>/market/output/*.md
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from core.bridge import SafetyTier, ToolResult, bionics_tool

logger = logging.getLogger("bionics.tools.market")

PROJECT_ROOT = Path(__file__).parent.parent
MARKET_DIR = PROJECT_ROOT / "market"
PRODUCTS_DIR = MARKET_DIR / "products"
OUTPUT_DIR = MARKET_DIR / "output"
KB_CACHE = MARKET_DIR / "kb_cache.json"

# Marketbot master PDF locations (in priority order)
def _default_kb_paths() -> list[Path]:
    """Resolve KB paths from config, env, or well-known Desktop locations."""
    from core.paths import get_market_kb_paths
    configured = get_market_kb_paths()
    if configured:
        return configured
    # Fallback: check Desktop for known files
    desktop = Path.home() / "Desktop"
    known = [
        desktop / "MarketBot_KnowledgeBase.pdf",
        desktop / "market_bot_psychology_foundations.md",
        desktop / "market_bot_content_methodology.md",
        desktop / "market_bot_pitfalls_agent_e.md",
    ]
    return [p for p in known if p.exists()]


DEFAULT_KB_PATHS = _default_kb_paths()

FRAMEWORKS = {
    "AIDA": "Attention → Interest → Desire → Action (default for cold audiences)",
    "PAS": "Pain → Agitate → Solution (pain-aware audiences, short formats)",
    "BAB": "Before → After → Bridge (solution-aware, comparing options)",
    "StoryBrand": "Hero / Guide / Plan (brand-aware, needs a nudge)",
    "PASTOR": "Problem → Amplify → Story → Transform → Offer → Response (high-ticket)",
}

CONTENT_TYPES = {
    "social": "Short social media post (platform-optimized, pattern-interrupt hook)",
    "ad": "Ad copy (headline + body + CTA, 4 U's scored)",
    "email_subject": "Email subject line (4 U's test)",
    "email_body": "Email body copy (framework-based)",
    "headline": "Landing page headline (11 words, 4 U's, front-loaded value)",
    "landing": "Full landing page structure (section-by-section wireframe)",
    "tagline": "Short tagline / value prop (8-12 words)",
}

GUARDRAIL_RULES = [
    ("fake_urgency", r"(only \d+ (left|remaining)|countdown|hurry|last chance)"),
    ("fake_scarcity", r"(limited supply|exclusive access|while supplies last)(?![^\n]{0,60}(because|reason|due to))"),
    # NOTE: superlatives uses word boundaries + excludes 'bestselling', 'besides' via (?![a-z])
    ("superlatives", r"\b(best|#1|revolutionary|world.?class|groundbreaking|game.?changing|cutting.?edge|next.?generation)(?![a-z])"),
    ("vague_buzzwords", r"\b(industry.?leading|synergy|leverage|disrupt|paradigm|innovative solutions?)\b"),
    ("confirmshaming", r"(no thanks,? i )(hate|don.?t want|refuse)"),
]
# Headline-specific (only checked at document start, not MULTILINE):
HEADLINE_RULE = ("our_we_headline", r"^(our |we )")


# ============================================================================
# KNOWLEDGE BASE — Parse marketbot PDFs
# ============================================================================


@bionics_tool(
    name="market_parse_pdf",
    category="market",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Parse Marketbot PDF",
)
def market_parse_pdf(
    pdf_path: Annotated[str, "Path to marketbot master PDF"] = "",
    cache: Annotated[bool, "Save parsed output to kb_cache.json"] = True,
) -> ToolResult:
    """Parse a marketbot master PDF into structured sections + full text."""
    target = Path(pdf_path) if pdf_path else None
    if target is None:
        for candidate in DEFAULT_KB_PATHS:
            if candidate.exists():
                target = candidate
                break
    if target is None or not target.exists():
        return ToolResult.failure(
            f"PDF/MD not found. Tried: {pdf_path or DEFAULT_KB_PATHS[0]}"
        )
    # Size limit — 50MB max
    try:
        size_mb = target.stat().st_size / (1024 * 1024)
        if size_mb > 50:
            return ToolResult.failure(
                f"File too large ({size_mb:.1f}MB, max 50MB): {target.name}"
            )
    except OSError as _e:
        return ToolResult.failure(f"Cannot stat file: {_e}")
    try:
        if target.suffix.lower() == ".pdf":
            try:
                import fitz  # PyMuPDF
            except ImportError:
                return ToolResult.failure("PyMuPDF not installed (pip install PyMuPDF)")
            doc = fitz.open(str(target))
            pages = []
            for page in doc:
                pages.append(page.get_text())
            full_text = "\n\n".join(pages)
            doc.close()
            page_count = len(pages)
        else:
            full_text = target.read_text(encoding="utf-8", errors="replace")
            page_count = 1

        # Extract sections by markdown-style headings
        sections = {}
        current = "preamble"
        buffer: list[str] = []
        for line in full_text.splitlines():
            m = re.match(r"^(#{1,3})\s+(.+)$", line.strip())
            if m:
                if buffer:
                    sections[current] = "\n".join(buffer).strip()
                current = m.group(2).strip()
                buffer = []
            else:
                buffer.append(line)
        if buffer:
            sections[current] = "\n".join(buffer).strip()

        data = {
            "source": str(target),
            "page_count": page_count,
            "section_count": len(sections),
            "total_chars": len(full_text),
            "sections": list(sections.keys())[:30],
            "parsed_at": datetime.now().isoformat(),
        }
        if cache:
            MARKET_DIR.mkdir(exist_ok=True)
            KB_CACHE.write_text(
                json.dumps({
                    "meta": data,
                    "full_text": full_text,
                    "sections": sections,
                }, indent=2, default=str),
                encoding="utf-8",
            )
            data["cached_to"] = str(KB_CACHE)
        return ToolResult.success(
            content=f"Parsed {target.name}: {page_count} pages, {len(sections)} sections, {len(full_text)} chars",
            data=data,
        )
    except Exception as e:
        return ToolResult.failure(f"Parse failed: {e}")


@bionics_tool(
    name="market_kb_info",
    category="market",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Marketbot KB Info",
)
def market_kb_info() -> ToolResult:
    """Return info about the cached marketbot knowledge base."""
    if not KB_CACHE.exists():
        return ToolResult.failure(
            "No KB cached. Run market_parse_pdf first."
        )
    try:
        kb = json.loads(KB_CACHE.read_text(encoding="utf-8"))
        return ToolResult.success(
            content=f"KB cached from {kb['meta']['source']}",
            data=kb["meta"],
        )
    except Exception as e:
        return ToolResult.failure(f"KB read failed: {e}")


@bionics_tool(
    name="market_kb_section",
    category="market",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Get KB Section",
)
def market_kb_section(section_name: str) -> ToolResult:
    """Return the text of a specific section from the parsed marketbot KB."""
    if not KB_CACHE.exists():
        return ToolResult.failure("No KB cached. Run market_parse_pdf first.")
    try:
        kb = json.loads(KB_CACHE.read_text(encoding="utf-8"))
        sections = kb.get("sections", {})
        # Case-insensitive fuzzy match
        name_lower = section_name.lower()
        match = None
        for key in sections:
            if name_lower in key.lower():
                match = key
                break
        if match is None:
            available = list(sections.keys())[:15]
            return ToolResult.failure(
                f"Section not found. Try: {available}"
            )
        text = sections[match]
        return ToolResult.success(
            content=f"[{match}] ({len(text)} chars)",
            data={"section": match, "text": text},
        )
    except Exception as e:
        return ToolResult.failure(f"Section read failed: {e}")


# ============================================================================
# PRODUCTS — Save + load product specifications
# ============================================================================


@bionics_tool(
    name="market_save_product",
    category="market",
    safety_tier=SafetyTier.MODERATE,
    title="Save Product Spec",
)
def market_save_product(
    name: Annotated[str, "Product name (alphanumeric)"],
    what_it_does: Annotated[str, "1-2 sentences of TRUTH about what the product does"],
    target_audience: Annotated[str, "Who is it for"],
    features: Annotated[list[str] | None, "Key features (3-5 max)"] = None,
    competitors: Annotated[str, "Competitive context"] = "",
    tone: Annotated[Literal["professional", "friendly", "bold", "minimal"], "Brand tone"] = "professional",
    audience_state: Annotated[Literal["cold", "warm", "pain-aware", "solution-aware", "brand-aware"], "Awareness stage"] = "cold",
    unique_differentiator: Annotated[str, "The 'Only We' statement"] = "",
) -> ToolResult:
    """Save a product specification that marketbot generation can load."""
    # Sanitize name — reject traversal chars + enforce alphanumeric
    if "/" in name or "\\" in name or ".." in name or name.startswith("."):
        return ToolResult.failure(
            f"Product name contains invalid path characters: {name!r}"
        )
    safe_name = Path(name).name
    if safe_name != name or not safe_name.replace("_", "").replace("-", "").isalnum():
        return ToolResult.failure(
            f"Product name must be alphanumeric (plus _-): {name!r}"
        )
    PRODUCTS_DIR.mkdir(parents=True, exist_ok=True)
    spec = {
        "name": safe_name,
        "what_it_does": what_it_does,
        "target_audience": target_audience,
        "features": features or [],
        "competitors": competitors,
        "tone": tone,
        "audience_state": audience_state,
        "unique_differentiator": unique_differentiator,
        "saved_at": datetime.now().isoformat(),
    }
    spec_path = PRODUCTS_DIR / f"{safe_name}.json"
    try:
        spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
        return ToolResult.success(
            content=f"Saved product spec: {safe_name}",
            data={"path": str(spec_path), "spec": spec},
        )
    except Exception as e:
        return ToolResult.failure(f"Save failed: {e}")


@bionics_tool(
    name="market_load_product",
    category="market",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Load Product Spec",
)
def market_load_product(name: str) -> ToolResult:
    """Load a saved product specification by name."""
    safe_name = Path(name).name
    if "/" in name or "\\" in name or ".." in name:
        return ToolResult.failure(f"Invalid product name: {name!r}")
    spec_path = PRODUCTS_DIR / f"{safe_name.removesuffix('.json')}.json"
    if not spec_path.exists():
        return ToolResult.failure(f"Product not found: {safe_name}")
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        return ToolResult.success(
            content=f"Loaded: {spec.get('name')} — {spec.get('what_it_does', '')[:80]}",
            data=spec,
        )
    except Exception as e:
        return ToolResult.failure(f"Load failed: {e}")


@bionics_tool(
    name="market_list_products",
    category="market",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="List Product Specs",
)
def market_list_products() -> ToolResult:
    """List all saved product specifications."""
    if not PRODUCTS_DIR.exists():
        return ToolResult.success(content="No products saved", data={"products": []})
    products = []
    for p in sorted(PRODUCTS_DIR.glob("*.json")):
        try:
            spec = json.loads(p.read_text(encoding="utf-8"))
            products.append({
                "name": spec.get("name", p.stem),
                "audience": spec.get("target_audience", ""),
                "tone": spec.get("tone", ""),
                "path": str(p.relative_to(PROJECT_ROOT)),
            })
        except Exception as e:
            products.append({"name": p.stem, "error": str(e)})
    return ToolResult.success(
        content=f"{len(products)} products",
        data={"products": products, "count": len(products)},
    )


# ============================================================================
# FRAMEWORKS / META
# ============================================================================


@bionics_tool(
    name="market_list_frameworks",
    category="market",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=True,
    title="List Frameworks",
)
def market_list_frameworks() -> ToolResult:
    """List all available marketing frameworks with descriptions."""
    return ToolResult.success(
        content=f"{len(FRAMEWORKS)} frameworks available",
        data={
            "frameworks": FRAMEWORKS,
            "content_types": CONTENT_TYPES,
            "default": "AIDA",
        },
    )


@bionics_tool(
    name="market_recommend_framework",
    category="market",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=True,
    title="Recommend Framework",
)
def market_recommend_framework(
    audience_state: Annotated[
        Literal["cold", "warm", "pain-aware", "solution-aware", "brand-aware"],
        "Audience awareness stage",
    ] = "cold",
    content_type: Annotated[
        Literal["social", "ad", "email_subject", "email_body", "headline", "landing", "tagline"],
        "Content type",
    ] = "social",
) -> ToolResult:
    """Recommend the best framework for a given audience state + content type."""
    # Short formats prefer PAS / 4 U's
    if content_type in ("social", "ad", "email_subject", "headline", "tagline"):
        if audience_state == "pain-aware":
            rec = "PAS"
        elif audience_state in ("solution-aware", "brand-aware"):
            rec = "BAB"
        else:
            rec = "AIDA"  # default short for cold/warm
    elif content_type == "landing" or content_type == "email_body":
        if audience_state == "cold":
            rec = "AIDA"
        elif audience_state == "pain-aware":
            rec = "PAS"
        elif audience_state == "solution-aware":
            rec = "BAB"
        elif audience_state == "brand-aware":
            rec = "StoryBrand"
        else:
            rec = "AIDA"
    else:
        rec = "AIDA"
    return ToolResult.success(
        content=f"Recommended: {rec} — {FRAMEWORKS[rec]}",
        data={
            "framework": rec,
            "description": FRAMEWORKS[rec],
            "audience_state": audience_state,
            "content_type": content_type,
        },
    )


# ============================================================================
# GUARDRAILS
# ============================================================================


@bionics_tool(
    name="market_guardrails",
    category="market",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=True,
    title="Check Guardrails",
)
def market_guardrails(content: Annotated[str, "Marketing content to validate"]) -> ToolResult:
    """Validate content against the 10 NEVER rules from marketbot skill."""
    violations = []
    content_lower = content.lower()
    # Body-level rules use standard (non-MULTILINE) so patterns without ^ work correctly
    for rule_name, pattern in GUARDRAIL_RULES:
        matches = re.findall(pattern, content_lower, re.IGNORECASE)
        if matches:
            violations.append({
                "rule": rule_name,
                "matches": (matches[:3] if isinstance(matches[0], str)
                            else [str(m)[:50] for m in matches[:3]]),
                "count": len(matches),
            })
    # Headline front-loading check — only the FIRST line matters
    first_line = content.strip().split("\n")[0].lower() if content.strip() else ""
    # Only flag as a headline if it's short enough to be one
    if first_line.startswith(("our ", "we ", "the ")) and len(first_line) < 120:
        violations.append({
            "rule": "headline_front_loading",
            "matches": [first_line[:60]],
            "count": 1,
            "note": "headline starts with 'Our/We/The' — front-load benefit instead",
        })
    return ToolResult(
        ok=len(violations) == 0,
        content=(
            "PASS — no guardrail violations" if not violations
            else f"FAIL — {len(violations)} violations"
        ),
        data={
            "violations": violations,
            "violation_count": len(violations),
            "content_length": len(content),
        },
    )


# ============================================================================
# GENERATION — Single + Batch
# ============================================================================


def _build_generation_prompt(product: dict, content_type: str, framework: str, variant_num: int = 1) -> str:
    """Build the Claude prompt for generating marketing content."""
    features_str = "\n".join(f"- {f}" for f in product.get("features", []))
    return f"""You are Market Bot — an expert marketing content generator. Generate ONE piece of {content_type} content following the {framework} framework and ALL Market Bot rules.

## Product
**Name:** {product.get('name', 'Unnamed')}
**What it does:** {product.get('what_it_does', '')}
**Target audience:** {product.get('target_audience', '')}
**Audience state:** {product.get('audience_state', 'cold')}
**Tone:** {product.get('tone', 'professional')}
**Unique differentiator:** {product.get('unique_differentiator', '')}
**Competitors:** {product.get('competitors', '')}

**Features:**
{features_str}

## Framework: {framework}
{FRAMEWORKS.get(framework, '')}

## Content Type: {content_type}
{CONTENT_TYPES.get(content_type, '')}

## HARD RULES (NEVER violate):
1. NO fake urgency / fake scarcity
2. NO superlatives without evidence (best, #1, revolutionary, cutting-edge)
3. NO vague buzzwords (industry-leading, synergy, leverage, disrupt)
4. NO confirmshaming ("No thanks, I hate savings")
5. NO overclaiming beyond product reality
6. ALWAYS include specific outcomes (real numbers)
7. ALWAYS front-load value in headlines (never start with "Our" or "We")
8. ALWAYS pass the "So What?" test on every bullet
9. ALWAYS 8th-grade reading level
10. ALWAYS end on a high note (peak-end rule)

## Output Format
Return ONLY the content — no preamble, no explanation. Clean text ready to copy.

This is variant #{variant_num} — make it DIFFERENT from typical output. Unique angle, concrete hook.

Begin:"""


@bionics_tool(
    name="market_generate_post",
    category="market",
    safety_tier=SafetyTier.MODERATE,
    open_world=True,
    title="Generate Marketing Post",
)
def market_generate_post(
    product_name: Annotated[str, "Name of saved product spec"],
    content_type: Annotated[
        Literal["social", "ad", "email_subject", "email_body", "headline", "landing", "tagline"],
        "Content type",
    ] = "social",
    framework: Annotated[
        Literal["AIDA", "PAS", "BAB", "StoryBrand", "PASTOR", "AUTO"],
        "Framework (AUTO recommends best fit)",
    ] = "AUTO",
    variant: Annotated[int, "Variant number (for batch diversity)"] = 1,
    save_output: Annotated[bool, "Save output to market/output/ directory"] = True,
    model: Annotated[str, "Claude model ID"] = "claude-sonnet-4-6",
) -> ToolResult:
    """Generate one marketing post via Claude API (enforces all marketbot rules)."""
    # Load product
    load_result = market_load_product(product_name)
    if not load_result.ok:
        return load_result
    product = load_result.data

    # Auto-select framework
    if framework == "AUTO":
        rec = market_recommend_framework(
            audience_state=product.get("audience_state", "cold"),
            content_type=content_type,
        )
        framework = rec.data["framework"]

    # Build prompt
    prompt = _build_generation_prompt(product, content_type, framework, variant)

    # Check API key
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ToolResult.failure(
            "ANTHROPIC_API_KEY not set. Use: setx ANTHROPIC_API_KEY \"sk-ant-...\""
        )

    # Call Claude
    try:
        from core.anthropic_client import get_shared_client
        client = get_shared_client(api_key)
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            temperature=0.9,  # High creativity for variant diversity
            messages=[{"role": "user", "content": prompt}],
        )
        # Defensively extract text from response (handles empty content, ToolUseBlock, etc.)
        generated = ""
        if response.content:
            for block in response.content:
                if hasattr(block, "text"):
                    generated += block.text
        if not generated.strip():
            return ToolResult.failure(
                f"Claude returned empty content (stop_reason={getattr(response, 'stop_reason', '?')})"
            )
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
    except Exception as e:
        return ToolResult.failure(f"Claude API call failed: {type(e).__name__}: {e}")

    # Run guardrails
    guard = market_guardrails(generated)
    guardrail_pass = guard.ok

    result_data = {
        "product": product.get("name"),
        "content_type": content_type,
        "framework": framework,
        "variant": variant,
        "generated": generated,
        "guardrail_pass": guardrail_pass,
        "guardrail_violations": guard.data.get("violations", []),
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }

    # Save output
    if save_output:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_name = f"{product.get('name')}_{content_type}_{framework}_v{variant}_{timestamp}.md"
        out_path = OUTPUT_DIR / out_name
        body = f"""# {product.get('name')} — {content_type} ({framework}) variant #{variant}

_{datetime.now().isoformat()}_

**Tone:** {product.get('tone')} | **Audience:** {product.get('target_audience')}
**Guardrails:** {'PASS' if guardrail_pass else 'FAIL — ' + str(len(guard.data.get('violations', []))) + ' violations'}

---

{generated}

---

## Metadata
- Model: {model}
- Framework: {framework}
- Tokens: {input_tokens} in / {output_tokens} out
"""
        try:
            out_path.write_text(body, encoding="utf-8")
            result_data["saved_to"] = str(out_path)
        except Exception as e:
            result_data["save_error"] = str(e)

    return ToolResult.success(
        content=generated[:500],
        data=result_data,
    )


@bionics_tool(
    name="market_batch_generate",
    category="market",
    safety_tier=SafetyTier.MODERATE,
    open_world=True,
    title="Batch Generate Posts",
)
def market_batch_generate(
    product_name: str,
    count: Annotated[int, "Number of variants to generate (1-50)"] = 5,
    content_type: Annotated[
        Literal["social", "ad", "email_subject", "email_body", "headline", "landing", "tagline"],
        "Content type",
    ] = "social",
    framework: Annotated[
        Literal["AIDA", "PAS", "BAB", "StoryBrand", "PASTOR", "AUTO"],
        "Framework (AUTO recommends best fit)",
    ] = "AUTO",
    model: str = "claude-sonnet-4-6",
) -> ToolResult:
    """Generate N marketing post variants in sequence (saves each + returns summary)."""
    if count < 1 or count > 50:
        return ToolResult.failure("count must be 1-50")
    results = []
    passed = 0
    for i in range(1, count + 1):
        r = market_generate_post(
            product_name=product_name,
            content_type=content_type,
            framework=framework,
            variant=i,
            save_output=True,
            model=model,
        )
        if r.ok:
            passed += 1
            results.append({
                "variant": i,
                "ok": True,
                "preview": r.data.get("generated", "")[:150],
                "guardrail_pass": r.data.get("guardrail_pass"),
                "saved_to": r.data.get("saved_to", ""),
            })
        else:
            results.append({"variant": i, "ok": False, "error": r.error})
    return ToolResult(
        ok=passed == count,
        content=f"Generated {passed}/{count} variants for {product_name}",
        data={
            "product": product_name,
            "content_type": content_type,
            "framework": framework,
            "count_requested": count,
            "count_generated": passed,
            "results": results,
        },
    )


# ============================================================================
# PLAN BUILDER — Create Bionics plan from product + PDF
# ============================================================================


@bionics_tool(
    name="market_build_plan",
    category="market",
    safety_tier=SafetyTier.MODERATE,
    title="Build Marketing Plan",
)
def market_build_plan(
    product_name: str,
    count: Annotated[int, "How many posts to generate"] = 10,
    content_types: Annotated[list[str] | None, "Content types to generate (one plan step per type)"] = None,
    frameworks: Annotated[list[str] | None, "Frameworks to use (cycles through them)"] = None,
    plan_name: Annotated[str, "Plan filename (alphanumeric)"] = "",
    model: str = "claude-sonnet-4-6",
) -> ToolResult:
    """Build a Bionics plan that generates N posts for a product.

    The resulting plan can be executed via `execute_plan` — each step generates
    one piece of content following marketbot rules.
    """
    # Bound count
    if count < 1 or count > 500:
        return ToolResult.failure("count must be 1-500")
    # Validate product exists
    load_result = market_load_product(product_name)
    if not load_result.ok:
        return load_result

    types = content_types or ["social"]
    fws = frameworks or ["AUTO"]
    # Validate
    valid_types = set(CONTENT_TYPES.keys())
    valid_fws = set(FRAMEWORKS.keys()) | {"AUTO"}
    for t in types:
        if t not in valid_types:
            return ToolResult.failure(f"Invalid content_type: {t!r}. Valid: {sorted(valid_types)}")
    for f in fws:
        if f not in valid_fws:
            return ToolResult.failure(f"Invalid framework: {f!r}. Valid: {sorted(valid_fws)}")

    # Build steps — one per post, cycling through types + frameworks
    steps = []
    for i in range(count):
        t = types[i % len(types)]
        f = fws[i % len(fws)]
        steps.append({
            "action": "market_generate_post",
            "params": {
                "product_name": product_name,
                "content_type": t,
                "framework": f,
                "variant": i + 1,
                "save_output": True,
                "model": model,
            },
        })

    plan_slug = plan_name or f"market_{product_name}_{count}posts"
    # Sanitize plan name
    safe_plan = Path(plan_slug).name
    if not safe_plan.replace("_", "").replace("-", "").isalnum():
        return ToolResult.failure(f"Invalid plan name: {plan_slug!r}")

    # Save via save_plan tool
    from bionics_tools.bionics_core import save_plan
    save_result = save_plan(
        name=safe_plan,
        steps=steps,
        title=f"Market Bot: {count} posts for {product_name}",
        description=(
            f"Generates {count} marketing content variants for '{product_name}' "
            f"using types={types}, frameworks={fws}. Executes via execute_plan."
        ),
    )
    if not save_result.ok:
        return save_result

    return ToolResult.success(
        content=f"Built plan '{safe_plan}' with {count} generation steps",
        data={
            "plan_name": safe_plan,
            "steps": len(steps),
            "product": product_name,
            "content_types": types,
            "frameworks": fws,
            "next": f"Run: bionics-cli run execute_plan --name {safe_plan} --allow-destructive false",
        },
    )


# ============================================================================
# LIST OUTPUTS
# ============================================================================


@bionics_tool(
    name="market_list_outputs",
    category="market",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="List Generated Outputs",
)
def market_list_outputs(
    product_filter: str = "",
    limit: int = 50,
) -> ToolResult:
    """List generated marketing content files."""
    if not OUTPUT_DIR.exists():
        return ToolResult.success(content="No outputs yet", data={"outputs": []})
    outputs = []
    all_files = sorted(OUTPUT_DIR.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    if product_filter:
        all_files = [f for f in all_files if product_filter.lower() in f.name.lower()]
    for p in all_files[:limit]:
        stat = p.stat()
        outputs.append({
            "name": p.name,
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "path": str(p.relative_to(PROJECT_ROOT)),
        })
    return ToolResult.success(
        content=f"{len(outputs)} outputs",
        data={"outputs": outputs, "count": len(outputs)},
    )


@bionics_tool(
    name="market_read_output",
    category="market",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Read Generated Output",
)
def market_read_output(filename: str) -> ToolResult:
    """Read a generated marketing content file."""
    # Path traversal guard
    safe = Path(filename).name
    if "/" in filename or "\\" in filename or ".." in filename:
        return ToolResult.failure(f"Invalid filename: {filename!r}")
    out_path = OUTPUT_DIR / safe
    if not out_path.exists():
        return ToolResult.failure(f"Output not found: {safe}")
    try:
        content = out_path.read_text(encoding="utf-8")
        return ToolResult.success(
            content=content[:2000],
            data={"filename": safe, "full_text": content},
        )
    except Exception as e:
        return ToolResult.failure(f"Read failed: {e}")
