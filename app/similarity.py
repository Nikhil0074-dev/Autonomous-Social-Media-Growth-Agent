"""Simple text similarity for duplicate and repetition checks (no external data)."""
from __future__ import annotations

import hashlib
import re


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def fingerprint(text: str) -> str:
    return hashlib.sha256(" ".join(words(text)).encode("utf-8")).hexdigest()


def _shingles(ws: list[str], n: int = 3) -> set:
    if len(ws) < n:
        return set(ws)
    return {tuple(ws[i:i + n]) for i in range(len(ws) - n + 1)}


def similarity(a: str, b: str) -> float:
    sa, sb = _shingles(words(a)), _shingles(words(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def find_similar(text: str, others: list[tuple[str, str]], threshold: float = 0.7) -> list[tuple[str, float]]:
    out = []
    for oid, otext in others:
        score = similarity(text, otext)
        if score >= threshold:
            out.append((oid, round(score, 2)))
    return sorted(out, key=lambda x: -x[1])
