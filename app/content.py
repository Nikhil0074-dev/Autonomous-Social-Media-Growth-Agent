"""Platform rules, hashtag handling and text composition."""
from __future__ import annotations

import re

X_LIMIT = 280
LINKEDIN_LIMIT = 3000
SHORT_SOFT_LIMIT = 500
URL_RE = re.compile(r"https?://\S+")

COMPOSER_URLS = {
    # These can change at any time. The clipboard text always works even if a link breaks.
    "linkedin": "https://www.linkedin.com/feed/?shareActive=true",
    "x": "https://x.com/intent/post",
    "short": None,
}


def x_length(text: str) -> int:
    """Approximate X length: every URL counts as 23 characters. Emoji are not weighted."""
    return len(URL_RE.sub("x" * 23, text))


def normalize_hashtags(tags, fallback=None, limit: int = 5) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        t = re.sub(r"[^\w]", "", str(raw).strip().lstrip("#"), flags=re.UNICODE)
        if not t or t.isdigit() or t.lower() in seen or len(out) >= limit:
            return
        seen.add(t.lower())
        out.append("#" + t)

    for raw in tags or []:
        add(raw)
    if len(out) < 3:
        for kw in fallback or []:
            add("".join(w.capitalize() for w in str(kw).split()))
            if len(out) >= 3:
                break
    return out


def normalize_thread_parts(parts) -> list[dict]:
    out = []
    for p in parts or []:
        if isinstance(p, str):
            p = {"text": p}
        if not isinstance(p, dict):
            raise ValueError("each thread part must be an object with a 'text' field")
        text = str(p.get("text", "")).strip()
        if text:
            out.append({"position": len(out) + 1, "purpose": str(p.get("purpose", "") or ""), "text": text})
    return out


def compose(post: dict) -> dict:
    """Build the exact text the user will paste. Returns {'text': str, 'parts': [str]}."""
    tags = " ".join(post.get("hashtags") or [])
    if post["platform"] == "x":
        parts = [p["text"] for p in post.get("thread_parts") or []]
        if parts and tags and x_length(parts[-1] + "\n\n" + tags) <= X_LIMIT:
            parts[-1] = parts[-1] + "\n\n" + tags
        return {"text": "\n\n---\n\n".join(parts), "parts": parts}
    blocks = [post.get("hook", ""), post.get("body", ""), post.get("cta", ""), tags]
    text = "\n\n".join(b.strip() for b in blocks if b and b.strip())
    return {"text": text, "parts": [text]}


def post_plain_text(post: dict) -> str:
    """All text of a post, used for similarity and hashing."""
    if post["platform"] == "x":
        return "\n".join(p["text"] for p in post.get("thread_parts") or [])
    return "\n".join(x for x in (post.get("hook"), post.get("body"), post.get("cta")) if x)
