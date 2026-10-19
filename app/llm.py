"""LLM providers. The rest of the app only uses the small LLMProvider interface."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Callable, Optional, Type, TypeVar

from pydantic import BaseModel

from .config import get_settings


class LLMError(Exception):
    pass


class LLMUnavailable(LLMError):
    """The model server cannot be reached or the model is missing."""


class InvalidModelOutput(LLMError):
    """The model answered, but not with usable JSON."""


class LLMProvider:
    name = "base"
    model = ""

    def generate(self, task: str, system: str, prompt: str, meta: Optional[dict] = None) -> str:
        raise NotImplementedError

    def health(self) -> dict:
        return {"ok": True, "detail": ""}


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, url: str, model: str, timeout: int):
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout

    def generate(self, task: str, system: str, prompt: str, meta: Optional[dict] = None) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.6, "num_ctx": 4096},
        }
        req = urllib.request.Request(
            self.url + "/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            if exc.code == 404:
                raise LLMUnavailable(
                    f"Model '{self.model}' was not found in Ollama. Run: ollama pull {self.model}"
                ) from exc
            raise LLMUnavailable(f"Ollama returned HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise LLMUnavailable(
                f"Cannot reach Ollama at {self.url} ({exc}). Is it running? Start it with: ollama serve"
            ) from exc
        except json.JSONDecodeError as exc:
            raise InvalidModelOutput("Ollama sent a response that is not JSON") from exc
        try:
            return data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise InvalidModelOutput("Unexpected response shape from Ollama") from exc

    def health(self) -> dict:
        try:
            with urllib.request.urlopen(self.url + "/api/tags", timeout=5) as resp:
                tags = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - report any failure to the user
            return {"ok": False, "detail": f"Cannot reach Ollama at {self.url}: {exc}"}
        names = [m.get("name", "") for m in tags.get("models", [])]
        if not any(n == self.model or n.split(":")[0] == self.model.split(":")[0] for n in names):
            return {"ok": False, "detail": f"Ollama is running but model '{self.model}' is not installed. "
                                           f"Run: ollama pull {self.model}. Installed: {names or 'none'}"}
        return {"ok": True, "detail": f"Ollama OK, model {self.model} available"}


class MockProvider(LLMProvider):
    """Returns fake but valid JSON. For tests and demos only: the text is generic filler."""
    name = "mock"
    model = "mock-1"

    def generate(self, task: str, system: str, prompt: str, meta: Optional[dict] = None) -> str:
        meta = meta or {}
        topic = meta.get("topic", "this topic")
        short_topic = topic[:60]
        words = [w for w in re.findall(r"[A-Za-z]{4,}", topic)][:4] or ["content"]
        if task == "brief":
            return json.dumps({
                "main_message": f"{short_topic} is easier to understand when you break it into small, practical steps.",
                "audience": meta.get("audience", "learners"),
                "key_points": [f"Start with the core idea behind {short_topic}.",
                               "Separate the moving parts so each one can be understood alone.",
                               "Try a small example before building anything large.",
                               "Review the result and note what to improve."],
                "example": f"A small hands-on example that shows {short_topic} in action.",
                "angles": [f"Beginner-friendly explainer of {short_topic}",
                           f"Common mistakes with {short_topic}", f"A practical walkthrough of {short_topic}"],
                "keywords": [w.lower() for w in words],
                "claims_to_verify": ["Check any version numbers or performance claims against official docs."],
            })
        if task == "draft":
            platform = meta.get("platform", "linkedin")
            instr = meta.get("instruction", "")
            tag = f" ({instr[:40]})" if instr else ""
            tags = ["#" + w.capitalize() for w in words[:3]]
            if platform == "x":
                return json.dumps({
                    "title": f"{short_topic} thread",
                    "hashtags": tags,
                    "thread_parts": [
                        {"position": 1, "purpose": "Hook", "text": f"Want to understand {short_topic}? A short thread{tag}."},
                        {"position": 2, "purpose": "Core idea", "text": "Start with the core idea, then split the system into small parts."},
                        {"position": 3, "purpose": "Example", "text": "Try one small example before you build anything big."},
                        {"position": 4, "purpose": "Conclusion", "text": "Review the result before sharing it. Automation should save time, not remove judgment."},
                    ],
                })
            if platform == "short":
                return json.dumps({
                    "title": short_topic, "hook": f"One idea about {short_topic}{tag}.",
                    "body": "Break the problem into small parts and test each part alone.",
                    "cta": "What would you try first?", "hashtags": tags,
                })
            return json.dumps({
                "title": f"A practical look at {short_topic}",
                "hook": f"What if {short_topic} was simpler than it looks?{tag}",
                "body": ("Most people struggle because they try to learn everything at once.\n\n"
                         "Start with the core idea, split the system into small parts, and test each part alone.\n\n"
                         "A small hands-on example teaches more than a long article."),
                "cta": "What would you try first?",
                "hashtags": tags,
            })
        if task == "visual":
            return json.dumps({
                "category": (words[0].upper() if words else "INSIGHT")[:24],
                "headline": short_topic[:60],
                "subtitle": "A practical, step-by-step explanation",
            })
        return "{}"


_override: Optional[LLMProvider] = None


def set_provider_override(provider: Optional[LLMProvider]) -> None:
    global _override
    _override = provider


def get_provider() -> LLMProvider:
    if _override is not None:
        return _override
    s = get_settings()
    if s.llm_provider == "mock":
        return MockProvider()
    if s.llm_provider == "ollama":
        return OllamaProvider(s.ollama_url, s.ollama_model, s.llm_timeout)
    raise LLMError(f"Unknown LLM provider '{s.llm_provider}'. Use 'ollama' or 'mock'.")


def extract_json(text: str) -> dict:
    if not text or not text.strip():
        raise InvalidModelOutput("the model returned an empty reply")
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start == -1 or end <= start:
            raise InvalidModelOutput("no JSON object found in the reply")
        try:
            obj = json.loads(t[start:end + 1])
        except json.JSONDecodeError as exc:
            raise InvalidModelOutput(f"invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise InvalidModelOutput("the reply was JSON but not an object")
    return obj


M = TypeVar("M", bound=BaseModel)


def call_validated(provider: LLMProvider, task: str, system: str, prompt: str, model_cls: Type[M],
                   retries: int, meta: Optional[dict] = None,
                   check: Optional[Callable[[M], None]] = None) -> M:
    """Call the model, parse JSON, validate. Retry a limited number of times on bad output."""
    last: Optional[Exception] = None
    current = prompt
    for _ in range(retries + 1):
        raw = provider.generate(task, system, current, meta)  # LLMUnavailable is not retried
        try:
            parsed = model_cls.model_validate(extract_json(raw))
            if check:
                check(parsed)
            return parsed
        except (InvalidModelOutput, ValueError) as exc:  # ValidationError is a ValueError
            last = exc
            current = (prompt + "\n\nYour previous reply was rejected: " + str(exc)[:300].replace("\n", " ")
                       + "\nReply again with ONLY one valid JSON object using the requested keys.")
    raise InvalidModelOutput(f"The model's output was still invalid after {retries + 1} attempts: {str(last)[:300]}")
