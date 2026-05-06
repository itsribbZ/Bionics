"""Bionics Plan Parser - Reads PDFs/text and extracts structured execution steps via Claude."""

import base64
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF
from anthropic import Anthropic

logger = logging.getLogger("bionics.planner")

PLAN_EXTRACTION_PROMPT = """You are Bionics, an AI-powered PC automation agent. The user has provided a blueprint document that describes a task to be performed on their computer.

Your job is to read this document and extract a structured list of execution steps that can be performed by an automation agent controlling the user's mouse, keyboard, and windows.

For each step, provide:
1. A clear description of what needs to happen
2. The type of verification needed (what should the screen look like after this step)
3. Whether the step involves any destructive actions (deletion, overwriting, closing unsaved work)

Return your response as a JSON object with this exact structure:
{
    "plan_name": "Short name for this plan",
    "plan_description": "One paragraph describing what this plan accomplishes",
    "estimated_steps": <number>,
    "steps": [
        {
            "index": 1,
            "description": "Human-readable description of what to do",
            "detailed_instructions": "Specific instructions for the automation agent",
            "verification": "What the screen should look like after this step completes",
            "is_destructive": false,
            "requires_app": "Name of application needed (e.g., 'Unreal Engine 5')",
            "category": "navigation|input|configuration|creation|deletion|verification"
        }
    ],
    "prerequisites": ["List of things that must be true before starting"],
    "warnings": ["Any potential risks or things to watch out for"]
}

Be thorough but practical. Break complex operations into atomic steps that can each be verified independently. If the document references UI elements, describe them precisely enough that a vision model can locate them on screen."""


@dataclass
class PlanStep:
    """A single step in an execution plan."""
    index: int
    description: str
    detailed_instructions: str
    verification: str
    is_destructive: bool = False
    requires_app: str = ""
    category: str = "navigation"
    status: str = "pending"  # pending, in_progress, completed, failed, skipped


@dataclass
class ExecutionPlan:
    """A complete execution plan parsed from a blueprint document."""
    name: str
    description: str
    steps: list[PlanStep] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_file: str = ""
    raw_text: str = ""

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    @property
    def completed_steps(self) -> int:
        return sum(1 for s in self.steps if s.status == "completed")

    @property
    def current_step(self) -> PlanStep | None:
        for s in self.steps:
            if s.status in ("pending", "in_progress"):
                return s
        return None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "steps": [
                {
                    "index": s.index,
                    "description": s.description,
                    "detailed_instructions": s.detailed_instructions,
                    "verification": s.verification,
                    "is_destructive": s.is_destructive,
                    "requires_app": s.requires_app,
                    "category": s.category,
                    "status": s.status,
                }
                for s in self.steps
            ],
            "prerequisites": self.prerequisites,
            "warnings": self.warnings,
        }


class PlanParser:
    """Parses blueprint documents into structured execution plans."""

    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-4-6"):
        import os
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._model = model
        self._client: Anthropic | None = None

    def _get_client(self) -> Anthropic:
        if self._client is None:
            from core.anthropic_client import get_shared_client
            self._client = get_shared_client(self._api_key)
        return self._client

    def read_pdf(self, filepath: str | Path) -> tuple[str, list[str]]:
        """Read a PDF file. Returns (extracted_text, list_of_page_image_base64)."""
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"PDF not found: {filepath}")

        MAX_IMAGE_PAGES = 10  # Claude has a 20-image limit; cap for safety

        try:
            doc = fitz.open(str(filepath))
        except Exception as e:
            raise ValueError(f"Cannot open PDF '{filepath}': {e}") from e

        full_text = ""
        page_images: list[str] = []

        for page_num, page in enumerate(doc):
            full_text += f"\n--- Page {page_num + 1} ---\n"
            full_text += page.get_text()

            # Only render first N pages as images (saves tokens)
            if page_num < MAX_IMAGE_PAGES:
                mat = fitz.Matrix(1.0, 1.0)
                pix = page.get_pixmap(matrix=mat)
                img_bytes = pix.tobytes("jpeg")
                page_images.append(base64.standard_b64encode(img_bytes).decode("utf-8"))

        if len(doc) > MAX_IMAGE_PAGES:
            logger.warning(f"PDF has {len(doc)} pages; only first {MAX_IMAGE_PAGES} rendered as images")

        doc.close()
        return full_text, page_images

    def read_text_file(self, filepath: str | Path) -> str:
        """Read a plain text or markdown file."""
        filepath = Path(filepath)
        return filepath.read_text(encoding="utf-8")

    def parse_blueprint(self, filepath: str | Path) -> ExecutionPlan:
        """Parse a blueprint file (PDF, MD, TXT) into an ExecutionPlan."""
        filepath = Path(filepath)
        suffix = filepath.suffix.lower()
        logger.info(f"Parsing blueprint: {filepath}")

        # Build the message content
        content = []

        if suffix == ".pdf":
            text, page_images = self.read_pdf(filepath)

            # Send page images for vision analysis
            for i, img_b64 in enumerate(page_images):
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": img_b64,
                    },
                })
                content.append({
                    "type": "text",
                    "text": f"[Page {i + 1} of {len(page_images)}]",
                })

            # Also include extracted text as backup
            content.append({
                "type": "text",
                "text": f"\n\nExtracted text from PDF:\n{text}",
            })
        elif suffix in (".md", ".txt", ".yaml", ".yml", ".json"):
            text = self.read_text_file(filepath)
            content.append({
                "type": "text",
                "text": f"Blueprint document content:\n\n{text}",
            })
        else:
            raise ValueError(f"Unsupported file format: {suffix}")

        # Call Claude API
        client = self._get_client()
        logger.info("Sending blueprint to Claude for step extraction...")

        response = client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=PLAN_EXTRACTION_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": content,
                }
            ],
        )

        # Parse the response
        response_text = response.content[0].text
        logger.info(f"Received plan from Claude ({len(response_text)} chars)")

        # Extract JSON from response (handle markdown code blocks)
        json_str = response_text
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0]
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0]

        try:
            plan_data = json.loads(json_str.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse plan JSON: {e}\nRaw response:\n{response_text[:2000]}")
            raise ValueError(f"Claude returned invalid JSON for plan extraction: {e}") from e

        # Build ExecutionPlan
        steps = [
            PlanStep(
                index=s.get("index", i + 1),
                description=s["description"],
                detailed_instructions=s.get("detailed_instructions", s["description"]),
                verification=s.get("verification", ""),
                is_destructive=s.get("is_destructive", False),
                requires_app=s.get("requires_app", ""),
                category=s.get("category", "navigation"),
            )
            for i, s in enumerate(plan_data.get("steps", []))
        ]

        plan = ExecutionPlan(
            name=plan_data.get("plan_name", filepath.stem),
            description=plan_data.get("plan_description", ""),
            steps=steps,
            prerequisites=plan_data.get("prerequisites", []),
            warnings=plan_data.get("warnings", []),
            source_file=str(filepath),
            raw_text=text,  # Already read above for both PDF and text formats
        )

        logger.info(f"Plan parsed: '{plan.name}' with {plan.total_steps} steps")
        return plan

    def save_plan(self, plan: ExecutionPlan, filepath: str | Path):
        """Save a parsed plan to JSON for review/editing."""
        filepath = Path(filepath)
        filepath.write_text(
            json.dumps(plan.to_dict(), indent=2),
            encoding="utf-8",
        )

    def load_plan(self, filepath: str | Path) -> ExecutionPlan:
        """Load a previously saved plan from JSON."""
        filepath = Path(filepath)
        data = json.loads(filepath.read_text(encoding="utf-8"))

        steps = [
            PlanStep(
                index=s["index"],
                description=s["description"],
                detailed_instructions=s["detailed_instructions"],
                verification=s["verification"],
                is_destructive=s.get("is_destructive", False),
                requires_app=s.get("requires_app", ""),
                category=s.get("category", "navigation"),
                status=s.get("status", "pending"),
            )
            for s in data.get("steps", [])
        ]

        return ExecutionPlan(
            name=data["name"],
            description=data["description"],
            steps=steps,
            prerequisites=data.get("prerequisites", []),
            warnings=data.get("warnings", []),
            source_file=str(filepath),
        )
