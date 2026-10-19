"""The generation pipeline: brief -> drafts -> visual copy -> graphics. Each step is saved as it finishes."""
from __future__ import annotations

import json
import time
from typing import Optional

from . import content, llm, prompts, research, services
from .config import get_settings
from .db import audit, connect, now
from .errors import ServiceError
from .renderer import shorten
from .schemas import BriefModel, DraftModel, VisualCopy


class Cancelled(Exception):
    pass


# ------------------------------------------------------------ job bookkeeping

def _job_update(job_id: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE generation_jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def _progress(job_id: str, frac: float, msg: str) -> None:
    with connect() as conn:
        row = conn.execute("SELECT cancel_requested FROM generation_jobs WHERE id=?", (job_id,)).fetchone()
        if row and row["cancel_requested"]:
            raise Cancelled()
        conn.execute("UPDATE generation_jobs SET progress=?, message=? WHERE id=?", (round(frac, 2), msg, job_id))


# ------------------------------------------------------------ steps

def _validate_draft(platform: str, d: DraftModel) -> None:
    if platform == "x":
        parts = [p for p in d.thread_parts if p.text.strip()]
        if len(parts) < 2:
            raise ValueError("thread_parts must contain at least 2 posts, each with a 'text' field")
    elif not d.body.strip():
        raise ValueError("'body' must not be empty")


def build_brief(campaign: dict, sources: list[dict], provider: llm.LLMProvider, retries: int) -> BriefModel:
    ctx = research.build_context(campaign.get("source_notes", ""), sources)
    return llm.call_validated(provider, "brief", prompts.SYSTEM_PROMPT, prompts.brief_prompt(campaign, ctx),
                              BriefModel, retries, meta={"topic": campaign["topic"], "audience": campaign["audience"]})


def generate_draft(platform: str, campaign: dict, brief: dict, provider: llm.LLMProvider, retries: int,
                   instruction: str = "", current: Optional[dict] = None) -> DraftModel:
    meta = {"topic": campaign["topic"], "platform": platform, "instruction": instruction}
    return llm.call_validated(provider, "draft", prompts.SYSTEM_PROMPT,
                              prompts.draft_prompt(platform, campaign, brief, instruction, current),
                              DraftModel, retries, meta=meta, check=lambda d: _validate_draft(platform, d))


def draft_to_fields(platform: str, draft: DraftModel, brief: dict, campaign: dict) -> dict:
    parts = content.normalize_thread_parts([p.model_dump() for p in draft.thread_parts]) if platform == "x" else []
    fields = {
        "title": (draft.title or campaign["topic"])[:120],
        "hook": draft.hook.strip(), "body": draft.body.strip(), "cta": draft.cta.strip(),
        "hashtags": content.normalize_hashtags(draft.hashtags, fallback=brief.get("keywords", [])),
        "thread_parts": parts,
    }
    if platform == "x":
        fields["hook"] = parts[0]["text"]
        fields["body"] = "\n\n".join(p["text"] for p in parts)
        fields["cta"] = ""
    return fields


def make_visual(campaign: dict, brief: dict, provider: llm.LLMProvider, retries: int) -> dict:
    v = llm.call_validated(provider, "visual", prompts.SYSTEM_PROMPT, prompts.visual_prompt(campaign, brief),
                           VisualCopy, retries, meta={"topic": campaign["topic"]})
    return {"category": shorten(v.category.upper(), 24), "headline": shorten(v.headline, 70),
            "subtitle": shorten(v.subtitle, 110)}


# ------------------------------------------------------------ main job

def run_generation(job_id: str) -> None:
    """Runs one generation job. Never raises: failures are stored on the job."""
    s = get_settings()
    metrics: dict = {}
    campaign_id = ""
    try:
        with connect() as conn:
            row = conn.execute("SELECT * FROM generation_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return
            campaign_id = row["campaign_id"]
        provider = llm.get_provider()
        _job_update(job_id, model=f"{provider.name}:{provider.model}")
        campaign = services.get_campaign(campaign_id)
        with connect() as conn:
            sources = services.get_sources(conn, campaign_id)
        t_all = time.time()

        services.set_campaign_status(campaign_id, "RESEARCHING")
        _progress(job_id, 0.05, "Building content brief")
        brief = campaign.get("brief")
        if brief is None:
            t = time.time()
            brief = build_brief(campaign, sources, provider, s.max_retries).model_dump()
            services.save_brief(campaign_id, brief, provider.model)
            metrics["brief_s"] = round(time.time() - t, 2)

        services.set_campaign_status(campaign_id, "GENERATING")
        done = {p["platform"] for p in campaign["posts"]}  # resume: skip platforms that already have a draft
        todo = [p for p in campaign["platforms"] if p not in done]
        total = max(1, len(todo))
        post_ids = [p["id"] for p in campaign["posts"] if not p["image_path"]]
        for i, platform in enumerate(todo):
            _progress(job_id, 0.15 + 0.5 * i / total, f"Writing {platform} draft")
            t = time.time()
            draft = generate_draft(platform, campaign, brief, provider, s.max_retries)
            metrics[f"draft_{platform}_s"] = round(time.time() - t, 2)
            pid = services.create_post(campaign_id, platform, draft_to_fields(platform, draft, brief, campaign))
            post_ids.append(pid)

        errors: list[str] = []
        if campaign["include_graphic"] and post_ids:
            services.set_campaign_status(campaign_id, "RENDERING")
            _progress(job_id, 0.7, "Writing banner text")
            visual = None
            try:
                visual = make_visual(campaign, brief, provider, s.max_retries)
            except llm.InvalidModelOutput:
                visual = {"category": shorten(campaign["topic"].upper(), 24), "headline": shorten(campaign["topic"], 60),
                          "subtitle": ""}
                errors.append("Banner text from the model was invalid; used the topic instead.")
            for j, pid in enumerate(post_ids):
                _progress(job_id, 0.75 + 0.2 * j / len(post_ids), "Rendering graphics")
                t = time.time()
                try:
                    services.render_post(pid, overrides=dict(visual))
                except ServiceError as exc:  # a render failure never loses the draft
                    errors.append(f"Graphic for {pid} failed: {exc.message}")
                metrics[f"render_{j + 1}_s"] = round(time.time() - t, 2)
        metrics["total_s"] = round(time.time() - t_all, 2)

        services.set_campaign_status(campaign_id, "REVIEW_REQUIRED")
        with connect() as conn:
            audit(conn, "generation_finished", "campaign", campaign_id, f"job {job_id}")
        _job_update(job_id, status="DONE", progress=1.0, message="Done" + (" (with warnings)" if errors else ""),
                    error="; ".join(errors), metrics=json.dumps(metrics), finished_at=now())
    except Cancelled:
        if campaign_id:
            services.set_campaign_status(campaign_id, "CANCELLED")
        _job_update(job_id, status="CANCELLED", message="Cancelled. Finished drafts were kept.",
                    metrics=json.dumps(metrics), finished_at=now())
    except Exception as exc:  # noqa: BLE001 - everything is reported on the job
        msg = exc.message if isinstance(exc, ServiceError) else str(exc)
        if campaign_id:
            try:
                services.set_campaign_status(campaign_id, "FAILED")
            except Exception:  # noqa: BLE001
                pass
        _job_update(job_id, status="FAILED", message="Failed", error=msg[:1000],
                    metrics=json.dumps(metrics), finished_at=now())


# ------------------------------------------------------------ regenerate one post

PARTS_BY_PLATFORM = {"linkedin": {"all", "hook", "body", "cta", "hashtags"},
                     "short": {"all", "hook", "body", "cta", "hashtags"},
                     "x": {"all", "thread", "hashtags"}}


def regenerate_post(post_id: str, instruction: str, part: str = "all") -> dict:
    post = services.get_post(post_id)
    if part not in PARTS_BY_PLATFORM[post["platform"]]:
        raise ServiceError(f"For {post['platform']} posts, part must be one of {sorted(PARTS_BY_PLATFORM[post['platform']])}", 422)
    campaign = services.get_campaign(post["campaign_id"])
    brief = campaign.get("brief") or {"main_message": campaign["topic"], "key_points": [], "keywords": []}
    provider = llm.get_provider()
    current = {k: post[k] for k in ("title", "hook", "body", "cta", "hashtags", "thread_parts")}
    try:
        draft = generate_draft(post["platform"], campaign, brief, provider, get_settings().max_retries,
                               instruction=instruction, current=current)
    except llm.LLMUnavailable as exc:
        raise ServiceError(str(exc), 503) from exc
    except llm.InvalidModelOutput as exc:
        raise ServiceError(f"The model did not return a usable draft. Your text was not changed. ({exc})", 502) from exc
    new = draft_to_fields(post["platform"], draft, brief, campaign)
    wanted = ({"title", "hook", "body", "cta", "hashtags", "thread_parts"} if part == "all"
              else {"thread": {"thread_parts"}}.get(part, {part}))
    changes = {k: new[k] for k in wanted if k in new}
    return services.update_post(post_id, changes, reason=f"regenerated {part}: {instruction[:80]}")


def create_brief_now(campaign_id: str) -> dict:
    """Synchronous brief creation (used by POST /api/research/brief)."""
    campaign = services.get_campaign(campaign_id)
    with connect() as conn:
        sources = services.get_sources(conn, campaign_id)
    provider = llm.get_provider()
    try:
        brief = build_brief(campaign, sources, provider, get_settings().max_retries).model_dump()
    except llm.LLMUnavailable as exc:
        raise ServiceError(str(exc), 503) from exc
    except llm.InvalidModelOutput as exc:
        raise ServiceError(str(exc), 502) from exc
    services.save_brief(campaign_id, brief, provider.model)
    return brief
