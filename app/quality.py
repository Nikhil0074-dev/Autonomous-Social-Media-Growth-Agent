"""Automated checks. They find structural problems; they cannot prove a post is true."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from PIL import Image

from . import content, similarity

PASS, WARN, FAIL = "pass", "warn", "fail"
NUM_RE = re.compile(r"\b\d[\d,.]*\s?%?")
AI_PHRASES = ("as an ai", "language model", "i cannot browse", "here is your", "here's your post")


def _c(name: str, status: str, message: str) -> dict:
    return {"check": name, "status": status, "message": message}


def run_checks(post: dict, campaign: dict, source_text: str, claims: list[str],
               others: list[tuple[str, str]], data_dir: Path, image_required: bool = True) -> list[dict]:
    res: list[dict] = []
    platform = post["platform"]
    text = content.post_plain_text(post)

    # 1. required fields
    if platform == "x":
        n = len(post.get("thread_parts") or [])
        res.append(_c("required_fields", PASS if n >= 2 else FAIL,
                      f"Thread has {n} posts." if n >= 2 else "A thread needs at least 2 posts."))
    else:
        ok = bool((post.get("body") or "").strip())
        res.append(_c("required_fields", PASS if ok else FAIL, "Body present." if ok else "The body is empty."))
    if not (post.get("title") or "").strip():
        res.append(_c("title", WARN, "No title. It is only used for your own organisation."))

    # 2. length limits (computed on the exact text that will be pasted)
    composed = content.compose(post)
    if platform == "x":
        too_long = [i + 1 for i, p in enumerate(composed["parts"]) if content.x_length(p) > content.X_LIMIT]
        res.append(_c("length", FAIL if too_long else PASS,
                      f"Posts {too_long} are over {content.X_LIMIT} characters." if too_long
                      else f"Every post is within {content.X_LIMIT} characters."))
        if len(composed["parts"]) > 12:
            res.append(_c("thread_size", WARN, "Very long thread (over 12 posts)."))
    elif platform == "linkedin":
        n = len(composed["text"])
        res.append(_c("length", FAIL if n > content.LINKEDIN_LIMIT else PASS,
                      f"{n} characters (limit {content.LINKEDIN_LIMIT})."))
    else:
        n = len(composed["text"])
        res.append(_c("length", WARN if n > content.SHORT_SOFT_LIMIT else PASS,
                      f"{n} characters (aim for under {content.SHORT_SOFT_LIMIT})."))

    # 3. hashtags
    tags = post.get("hashtags") or []
    res.append(_c("hashtags", WARN if (len(tags) > 5 or not tags) else PASS,
                  "Use 1 to 5 hashtags." if (len(tags) > 5 or not tags) else f"{len(tags)} hashtags."))

    # 4. image
    asset_ok = False
    if post.get("image_path"):
        p = (data_dir / post["image_path"])
        if not p.is_file():
            res.append(_c("image", FAIL, "The image file is missing from disk. Render it again."))
        else:
            try:
                with Image.open(p) as im:
                    w, h = im.size
                asset_ok = True
                res.append(_c("image", PASS, f"Image OK ({w}x{h})."))
            except Exception:  # noqa: BLE001
                res.append(_c("image", FAIL, "The image file is damaged. Render it again."))
    elif image_required:
        res.append(_c("image", FAIL, "No image yet. Render one, or approve with the 'no image' option."))
    else:
        res.append(_c("image", PASS, "No image required."))
    _ = asset_ok

    # 5. duplicates and repetition
    exact = [oid for oid, otext in others if similarity.fingerprint(otext) == similarity.fingerprint(text)]
    if exact:
        res.append(_c("duplicates", WARN, f"Identical text already exists in {exact[:3]}."))
    else:
        sim = similarity.find_similar(text, others, 0.6)
        res.append(_c("duplicates", WARN if sim else PASS,
                      f"Very similar to {sim[:3]} (id, score)." if sim else "No near-duplicates found."))
    if platform == "x":
        parts = [p["text"] for p in post.get("thread_parts") or []]
        reps = [(i + 1, j + 1) for i in range(len(parts)) for j in range(i + 1, len(parts))
                if similarity.similarity(parts[i], parts[j]) > 0.6]
        if reps:
            res.append(_c("repetition", WARN, f"Thread posts {reps[:3]} repeat each other."))

    # 6. numbers that do not appear in the supplied material
    nums = {n.strip().rstrip(".,") for n in NUM_RE.findall(text) if len(n.strip()) > 1 or "%" in n}
    src_norm = (source_text or "") + " " + (campaign.get("topic") or "")
    unsupported = sorted(n for n in nums if n not in src_norm)
    if unsupported:
        res.append(_c("unsupported_numbers", WARN,
                      f"Numbers not found in your sources: {', '.join(unsupported[:6])}. Verify them or remove them."))
    else:
        res.append(_c("unsupported_numbers", PASS, "No unsourced numbers detected."))

    # 7. sources
    if not (source_text or "").strip():
        res.append(_c("sources", WARN, "No source material was supplied. All facts come from the model's memory."))
    else:
        res.append(_c("sources", PASS, "Source material was supplied."))

    # 8. model leftovers
    low = text.lower()
    leak = [p for p in AI_PHRASES if p in low]
    if leak:
        res.append(_c("model_artifacts", WARN, f"Text contains assistant-style phrases: {leak}."))

    # 9. always a human task
    res.append(_c("manual_fact_check", WARN,
                  "Claims to verify by hand: " + ("; ".join(claims[:5]) if claims else "none listed by the model")
                  + ". Automated checks cannot confirm facts."))
    return res


def has_failures(checks: list[dict]) -> list[dict]:
    return [c for c in checks if c["status"] == FAIL]
