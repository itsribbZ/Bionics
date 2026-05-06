import base64
import io
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

import mss
from PIL import Image

logger = logging.getLogger("bionics.quiz")

QUIZ_SYSTEM_PROMPT = """You are a quiz-solving assistant. You will be shown a screenshot of a quiz, test, or assignment. Your job is to:

1. Identify every question visible on screen
2. Provide the correct answer for each question

Return ONLY valid JSON in this exact format:
{
    "questions": [
        {
            "number": "1",
            "question": "The full question text",
            "answer": "The correct answer",
            "explanation": "Brief explanation of why this is correct",
            "type": "multiple_choice|true_false|short_answer|essay|fill_blank",
            "options": ["A) ...", "B) ...", "C) ...", "D) ..."]
        }
    ]
}

Rules:
- For multiple choice, specify which letter/option is correct in the answer field
- For true/false, answer with "True" or "False"
- For short answer or essay, provide a concise but complete answer
- If options are visible, include them in the options array
- If you cannot read a question clearly, still include it with your best interpretation
- Return ALL visible questions, not just some of them"""


@dataclass
class QuizQuestion:
    number: str
    question: str
    answer: str
    explanation: str
    q_type: str
    options: list[str] = field(default_factory=list)


@dataclass
class QuizResult:
    questions: list[QuizQuestion]
    raw_response: str
    tokens_used: int


class QuizEngine:
    def __init__(self, model: str = "claude-sonnet-4-6"):
        self._model = model
        from core.anthropic_client import get_shared_client
        self._client = get_shared_client()
        self._lock = threading.Lock()
        self._on_log: Callable[[str], None] | None = None
        self._on_result: Callable[[QuizResult], None] | None = None
        self._on_error: Callable[[str], None] | None = None
        self._last_result: QuizResult | None = None

    def set_callbacks(
        self,
        on_log: Callable[[str], None] | None = None,
        on_result: Callable[[QuizResult], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ):
        self._on_log = on_log
        self._on_result = on_result
        self._on_error = on_error

    def _log(self, msg: str):
        logger.info(msg)
        if self._on_log:
            self._on_log(msg)

    def _error(self, msg: str):
        logger.error(msg)
        if self._on_error:
            self._on_error(msg)

    @staticmethod
    def _extract_json(text: str) -> str:
        import re
        code_block = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if code_block:
            return code_block.group(1).strip()
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
            return text[brace_start:brace_end + 1]
        return text.strip()

    def scan(self):
        thread = threading.Thread(target=self._scan_worker, daemon=True)
        thread.start()

    def _scan_worker(self):
        with self._lock:
            try:
                self._log("[QUIZ] Capturing screen...")
                with mss.mss() as sct:
                    raw = sct.grab(sct.monitors[0])
                    img = Image.frombytes("RGB", (raw.width, raw.height), raw.rgb)
                    if img.width > 1920:
                        ratio = 1920 / img.width
                        img = img.resize((1920, int(img.height * ratio)), Image.LANCZOS)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=75)
                    b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")

                self._log("[QUIZ] Sending to Claude for analysis...")
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=4096,
                    temperature=0.0,
                    system=QUIZ_SYSTEM_PROMPT,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/jpeg",
                                        "data": b64,
                                    },
                                },
                                {
                                    "type": "text",
                                    "text": "Read all questions on this screen and provide the correct answers.",
                                },
                            ],
                        }
                    ],
                )

                raw_text = response.content[0].text if response.content else ""
                tokens = response.usage.input_tokens + response.usage.output_tokens

                self._log(f"[QUIZ] Got response ({len(raw_text)} chars, {tokens} tokens)")
                if not raw_text.strip():
                    self._error("[QUIZ] Empty response from Claude")
                    return

                json_str = self._extract_json(raw_text)
                data = json.loads(json_str)
                questions = []
                for q in data.get("questions", []):
                    questions.append(
                        QuizQuestion(
                            number=str(q.get("number", "?")),
                            question=q.get("question", ""),
                            answer=q.get("answer", ""),
                            explanation=q.get("explanation", ""),
                            q_type=q.get("type", "unknown"),
                            options=q.get("options", []),
                        )
                    )

                result = QuizResult(questions=questions, raw_response=raw_text, tokens_used=tokens)
                self._last_result = result
                self._log(f"[QUIZ] Found {len(questions)} questions ({tokens} tokens)")

                if self._on_result:
                    self._on_result(result)

            except json.JSONDecodeError:
                preview = raw_text[:200] if raw_text else "(empty)"
                self._error(f"[QUIZ] Failed to parse JSON. Preview: {preview}")
            except Exception as e:
                self._error(f"[QUIZ] Error: {e}")

    @property
    def last_result(self) -> QuizResult | None:
        return self._last_result
