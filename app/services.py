"""Business logic: campaigns, posts, revisions, rendering, approval, publishing preparation."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import zipfile
from pathlib import Path
from typing import Optional

from . import content, quality, similarity
from .config import get_settings
from .db import DEFAULT_BRAND_ID, audit, connect, ensure_ready, new_id, now
from .errors import ServiceError
from .renderer import HEX_RE, RenderError, RenderSpec, TextFitError, render_og, shorten
from .schemas import SUPPORTED_PLATFORMS, TEMPLATE_NAMES, CampaignCreate

__all__ = ["ensure_ready"]

EDITABLE = {"title", "hook", "body", "cta", "hashtags", "thread_parts"}
LOCKED_STATUSES = ("PUBLISHED",)


# ---------------------------------------------------------------- helpers

def data_dir() -> Path:
    return get_settings().data_dir


def abs_path(rel: str) -> Path:
    """Resolve a stored relative path and make sure it stays inside the data directory."""
    root = data_dir().resolve()
    p = (root / rel).resolve()
    if root != p and root not in p.parents:
        raise ServiceError("Path is outside the data directory", 400)
    return p


def _decode_post(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["hashtags"] = json.loads(d.get("hashtags") or "[]")
    d["thread_parts"] = json.loads(d.get("thread_parts") or "[]")
    return d


def _decode_campaign(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["source_urls"] = json.loads(d.get("source_urls") or "[]")
    d["platforms"] = json.loads(d.get("platforms") or "[]")
    d["include_graphic"] = bool(d.get("include_graphic"))
    return d


def _post(conn, post_id: str) -> dict:
    row = conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if row is None:
        raise ServiceError(f"Post '{post_id}' not found", 404)
    return _decode_post(row)


def _campaign(conn, campaign_id: str) -> dict:
    row = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    if row is None:
        raise ServiceError(f"Campaign '{campaign_id}' not found", 404)
    return _decode_campaign(row)


def review_hash(post: dict) -> str:
    """Fingerprint of everything the user approves: text AND image."""
    payload = {k: post.get(k) for k in ("title", "hook", "body", "cta", "hashtags", "thread_parts")}
    payload["image_sha"] = post.get("image_sha") or ""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _with_image_info(post: dict) -> dict:
    post["review_hash"] = review_hash(post)
    post["approval_valid"] = bool(post.get("approved_hash")) and post["approved_hash"] == post["review_hash"]
    post["image_exists"] = False
    post["image_abs"] = None
    if post.get("image_path"):
        try:
            p = abs_path(post["image_path"])
            post["image_exists"] = p.is_file()
            post["image_abs"] = str(p)
        except ServiceError:
            pass
    return post


# ---------------------------------------------------------------- brand

def get_brand(conn=None) -> dict:
    def _get(c):
        row = c.execute("SELECT * FROM brand_profiles WHERE id=?", (DEFAULT_BRAND_ID,)).fetchone()
        return dict(row)
    if conn is not None:
        return _get(conn)
    with connect() as c:
        return _get(c)


def update_brand(changes: dict) -> dict:
    changes = {k: v for k, v in changes.items() if v is not None}
    for k in ("primary_color", "accent_color", "text_color"):
        if k in changes and not HEX_RE.match(changes[k]):
            raise ServiceError(f"{k} must look like #RRGGBB", 422)
    for k in ("font_path", "logo_path"):
        if changes.get(k) and not Path(changes[k]).expanduser().is_file():
            raise ServiceError(f"{k}: file not found: {changes[k]}", 422)
    if "brand_name" in changes and not changes["brand_name"].strip():
        raise ServiceError("brand_name cannot be empty", 422)
    with connect() as conn:
        for k, v in changes.items():
            if k not in ("brand_name", "primary_color", "accent_color", "text_color", "footer", "font_path", "logo_path"):
                raise ServiceError(f"Unknown brand field '{k}'", 422)
            conn.execute(f"UPDATE brand_profiles SET {k}=? WHERE id=?", (v, DEFAULT_BRAND_ID))
        audit(conn, "brand_updated", "brand", DEFAULT_BRAND_ID, ",".join(changes))
        return get_brand(conn)


# ---------------------------------------------------------------- campaigns

def create_campaign(data: CampaignCreate) -> str:
    cid = new_id("campaign")
    with connect() as conn:
        conn.execute(
            "INSERT INTO campaigns (id,topic,audience,content_goal,tone,angle,source_notes,source_urls,platforms,"
            "brand_profile_id,template,include_graphic,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, data.topic, data.audience, data.content_goal, data.tone, data.angle, data.source_notes,
             json.dumps(data.source_urls), json.dumps(data.platforms), DEFAULT_BRAND_ID, data.template,
             int(data.include_graphic), "CREATED", now(), now()),
        )
        audit(conn, "campaign_created", "campaign", cid, data.topic[:100])
    return cid


def set_campaign_status(campaign_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE campaigns SET status=?, updated_at=? WHERE id=?", (status, now(), campaign_id))


def add_source(campaign_id: str, title: str, content_text: str, url: Optional[str] = None, kind: str = "notes") -> str:
    if not content_text.strip():
        raise ServiceError("Source content is empty", 422)
    sid = new_id("source")
    with connect() as conn:
        _campaign(conn, campaign_id)
        conn.execute(
            "INSERT INTO source_documents (id,campaign_id,kind,title,url,content,retrieved_at,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (sid, campaign_id, kind, title[:200], url, content_text, now() if url else None, now()),
        )
        audit(conn, "source_added", "campaign", campaign_id, f"{kind}: {title[:80]}")
    return sid


def get_sources(conn, campaign_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM source_documents WHERE campaign_id=? ORDER BY created_at, id", (campaign_id,))]


def save_brief(campaign_id: str, brief: dict, model: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO content_briefs (id,campaign_id,brief_json,model,created_at) VALUES (?,?,?,?,?)",
                     (new_id("brief"), campaign_id, json.dumps(brief, ensure_ascii=False), model, now()))


def get_brief(conn, campaign_id: str) -> Optional[dict]:
    row = conn.execute("SELECT brief_json FROM content_briefs WHERE campaign_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                       (campaign_id,)).fetchone()
    return json.loads(row["brief_json"]) if row else None


def get_campaign(campaign_id: str) -> dict:
    with connect() as conn:
        c = _campaign(conn, campaign_id)
        c["sources"] = [{k: v for k, v in s.items() if k != "content"} | {"chars": len(s["content"] or "")}
                        for s in get_sources(conn, campaign_id)]
        c["brief"] = get_brief(conn, campaign_id)
        c["posts"] = [_with_image_info(_decode_post(r)) for r in conn.execute(
            "SELECT * FROM posts WHERE campaign_id=? ORDER BY created_at, id", (campaign_id,))]
        c["jobs"] = [dict(r) for r in conn.execute(
            "SELECT * FROM generation_jobs WHERE campaign_id=? ORDER BY created_at DESC, rowid DESC LIMIT 5", (campaign_id,))]
        return c


def list_campaigns() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM posts p WHERE p.campaign_id=c.id) AS post_count "
            "FROM campaigns c ORDER BY c.created_at DESC, c.rowid DESC").fetchall()
        return [_decode_campaign(r) | {"post_count": r["post_count"]} for r in rows]


def delete_campaign(campaign_id: str) -> None:
    with connect() as conn:
        _campaign(conn, campaign_id)
        active = conn.execute("SELECT 1 FROM generation_jobs WHERE campaign_id=? AND status IN ('QUEUED','RUNNING')",
                              (campaign_id,)).fetchone()
        if active:
            raise ServiceError("Cancel the running generation job first", 409)
        conn.execute("DELETE FROM campaigns WHERE id=?", (campaign_id,))
        audit(conn, "campaign_deleted", "campaign", campaign_id)
    shutil.rmtree(data_dir() / "campaigns" / campaign_id, ignore_errors=True)
    shutil.rmtree(data_dir() / "exports" / campaign_id, ignore_errors=True)


# ---------------------------------------------------------------- posts

def create_post(campaign_id: str, platform: str, fields: dict) -> str:
    if platform not in SUPPORTED_PLATFORMS:
        raise ServiceError(f"Unsupported platform '{platform}'", 422)
    pid = new_id("post")
    parts = content.normalize_thread_parts(fields.get("thread_parts"))
    post = {"platform": platform, "title": fields.get("title", ""), "hook": fields.get("hook", ""),
            "body": fields.get("body", ""), "cta": fields.get("cta", ""),
            "hashtags": fields.get("hashtags", []), "thread_parts": parts}
    with connect() as conn:
        _campaign(conn, campaign_id)
        conn.execute(
            "INSERT INTO posts (id,campaign_id,platform,title,hook,body,cta,hashtags,thread_parts,revision_number,status,"
            "approval_status,content_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, campaign_id, platform, post["title"], post["hook"], post["body"], post["cta"],
             json.dumps(post["hashtags"], ensure_ascii=False), json.dumps(parts, ensure_ascii=False), 1,
             "REVIEW_REQUIRED", "pending", similarity.fingerprint(content.post_plain_text(post)), now(), now()))
        audit(conn, "post_created", "post", pid, platform)
    return pid


def get_post(post_id: str) -> dict:
    with connect() as conn:
        p = _with_image_info(_post(conn, post_id))
        p["versions_count"] = conn.execute("SELECT COUNT(*) FROM post_versions WHERE post_id=?", (post_id,)).fetchone()[0]
        return p


def list_posts(status: Optional[str] = None, campaign_id: Optional[str] = None) -> list[dict]:
    sql, args = "SELECT * FROM posts WHERE 1=1", []
    if status:
        sql += " AND status=?"
        args.append(status)
    if campaign_id:
        sql += " AND campaign_id=?"
        args.append(campaign_id)
    sql += " ORDER BY created_at DESC, rowid DESC"
    with connect() as conn:
        return [_with_image_info(_decode_post(r)) for r in conn.execute(sql, args)]


def _commit_change(conn, old: dict, new: dict, reason: str) -> dict:
    """Save `new` over `old`. If the reviewable content changed: snapshot, bump revision, drop approval."""
    if review_hash(old) == review_hash(new):
        return old
    snap = {k: old.get(k) for k in ("title", "hook", "body", "cta", "hashtags", "thread_parts", "image_path", "image_sha")}
    conn.execute("INSERT INTO post_versions (id,post_id,revision_number,snapshot,reason,created_at) VALUES (?,?,?,?,?,?)",
                 (new_id("ver"), old["id"], old["revision_number"], json.dumps(snap, ensure_ascii=False), reason[:200], now()))
    status = old["status"]
    invalidated = status in ("APPROVED", "READY_TO_PUBLISH", "REJECTED") or old.get("approved_hash")
    if invalidated:
        status = "REVIEW_REQUIRED"
        audit(conn, "approval_invalidated", "post", old["id"], reason[:200])
    conn.execute(
        "UPDATE posts SET title=?,hook=?,body=?,cta=?,hashtags=?,thread_parts=?,image_path=?,image_sha=?,"
        "revision_number=?,status=?,approval_status='pending',approved_hash=NULL,content_hash=?,updated_at=? WHERE id=?",
        (new["title"], new["hook"], new["body"], new["cta"], json.dumps(new["hashtags"], ensure_ascii=False),
         json.dumps(new["thread_parts"], ensure_ascii=False), new.get("image_path"), new.get("image_sha"),
         old["revision_number"] + 1, status, similarity.fingerprint(content.post_plain_text(new)), now(), old["id"]))
    audit(conn, "post_revised", "post", old["id"], reason[:200])
    return _post(conn, old["id"])


def update_post(post_id: str, changes: dict, reason: str = "manual edit") -> dict:
    changes = {k: v for k, v in changes.items() if v is not None}
    unknown = set(changes) - EDITABLE
    if unknown:
        raise ServiceError(f"Cannot edit: {sorted(unknown)}", 422)
    with connect() as conn:
        old = _post(conn, post_id)
        if old["status"] in LOCKED_STATUSES:
            raise ServiceError("This post is already published and cannot be edited.", 409)
        new = dict(old)
        for k, v in changes.items():
            if k in ("title", "hook", "body", "cta"):
                new[k] = str(v)
            elif k == "hashtags":
                if not isinstance(v, list):
                    raise ServiceError("hashtags must be a list", 422)
                new[k] = content.normalize_hashtags(v, limit=30)
            elif k == "thread_parts":
                try:
                    new[k] = content.normalize_thread_parts(v)
                except ValueError as exc:
                    raise ServiceError(str(exc), 422) from exc
        if new["platform"] == "x" and "thread_parts" in changes:
            parts = new["thread_parts"]
            new["hook"] = parts[0]["text"] if parts else ""
            new["body"] = "\n\n".join(p["text"] for p in parts)
        _commit_change(conn, old, new, reason)
        return _with_image_info(_post(conn, post_id))


def list_versions(post_id: str) -> list[dict]:
    with connect() as conn:
        _post(conn, post_id)
        rows = conn.execute("SELECT * FROM post_versions WHERE post_id=? ORDER BY revision_number DESC, rowid DESC", (post_id,))
        return [{"id": r["id"], "revision_number": r["revision_number"], "reason": r["reason"],
                 "created_at": r["created_at"], "snapshot": json.loads(r["snapshot"])} for r in rows]


def restore_version(post_id: str, version_id: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM post_versions WHERE id=? AND post_id=?", (version_id, post_id)).fetchone()
        if row is None:
            raise ServiceError("Version not found", 404)
        snap = json.loads(row["snapshot"])
    return update_post(post_id, {k: snap[k] for k in ("title", "hook", "body", "cta", "hashtags", "thread_parts")},
                       reason=f"restored revision {row['revision_number']}")


# ---------------------------------------------------------------- rendering

def _spec_for(post: dict, campaign: dict, brand: dict, template: Optional[str], overrides: dict) -> RenderSpec:
    prev = overrides.get("_previous") or {}
    topic_words = campaign["topic"].upper()
    return RenderSpec(
        template=template or campaign.get("template") or "technical_explainer",
        width=int(overrides.get("width") or 1200), height=int(overrides.get("height") or 630),
        brand_name=brand["brand_name"], primary_color=brand["primary_color"], accent_color=brand["accent_color"],
        text_color=brand["text_color"],
        category=overrides.get("category") if overrides.get("category") is not None else prev.get("category", shorten(topic_words, 24)),
        headline=overrides.get("headline") or prev.get("headline") or post.get("title") or campaign["topic"],
        subtitle=overrides.get("subtitle") if overrides.get("subtitle") is not None else prev.get("subtitle", shorten(post.get("hook", ""), 100)),
        footer=overrides.get("footer") if overrides.get("footer") is not None else (prev.get("footer") or brand.get("footer") or ""),
        format=overrides.get("format") or "png",
        font_path=brand.get("font_path") or get_settings().font_path, logo_path=brand.get("logo_path") or "",
    )


def render_post(post_id: str, template: Optional[str] = None, overrides: Optional[dict] = None) -> dict:
    overrides = dict(overrides or {})
    if template and template not in TEMPLATE_NAMES:
        raise ServiceError(f"Unknown template. Use: {', '.join(TEMPLATE_NAMES)}", 422)
    with connect() as conn:
        post = _post(conn, post_id)
        if post["status"] in LOCKED_STATUSES:
            raise ServiceError("This post is already published and cannot be changed.", 409)
        campaign = _campaign(conn, post["campaign_id"])
        brand = get_brand(conn)
        prev_row = conn.execute("SELECT metadata, template FROM generated_assets WHERE post_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                                (post_id,)).fetchone()
    if prev_row:
        overrides["_previous"] = json.loads(prev_row["metadata"] or "{}").get("content", {})
        if template is None:
            template = prev_row["template"]
    spec = _spec_for(post, campaign, brand, template, overrides)

    asset_id = new_id("asset")
    ext = "jpg" if spec.format == "jpeg" else "png"
    rel_dir = Path("campaigns") / post["campaign_id"] / "assets"
    rel = rel_dir / f"{post_id}_{asset_id}.{ext}"
    rel_prev = rel_dir / f"{post_id}_{asset_id}_preview.png"
    notes = []
    try:
        try:
            meta = render_og(spec, abs_path(str(rel)), abs_path(str(rel_prev)))
        except TextFitError as exc:  # shorten the offending text once, and say so
            limit = {"headline": 45, "subtitle": 70, "category": 18}.get(exc.field_name)
            if limit is None or not getattr(spec, exc.field_name, ""):
                raise
            setattr(spec, exc.field_name, shorten(getattr(spec, exc.field_name), limit))
            notes.append(f"The {exc.field_name} was shortened automatically to fit.")
            meta = render_og(spec, abs_path(str(rel)), abs_path(str(rel_prev)))
    except TextFitError as exc:
        raise ServiceError(str(exc), 422) from exc
    except RenderError as exc:
        raise ServiceError(f"Rendering failed: {exc}", 422) from exc
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(f"Rendering failed unexpectedly: {exc}", 500) from exc
    meta["warnings"] = list(meta.get("warnings", [])) + notes
    meta["content"] = {"category": spec.category, "headline": spec.headline, "subtitle": spec.subtitle, "footer": spec.footer}

    with connect() as conn:
        old = _post(conn, post_id)
        conn.execute(
            "INSERT INTO generated_assets (id,post_id,path,preview_path,width,height,format,sha256,template,metadata,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (asset_id, post_id, rel.as_posix(), rel_prev.as_posix(), meta["width"], meta["height"], meta["format"],
             meta["sha256"], spec.template, json.dumps(meta), now()))
        new = dict(old, image_path=rel.as_posix(), image_sha=meta["sha256"])
        _commit_change(conn, old, new, f"image rendered ({spec.template})")
        audit(conn, "image_rendered", "post", post_id, spec.template)
    meta["asset_id"], meta["path"] = asset_id, rel.as_posix()
    return meta


def get_asset(asset_id: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM generated_assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise ServiceError("Asset not found", 404)
        d = dict(row)
        d["metadata"] = json.loads(d["metadata"] or "{}")
        d["exists"] = abs_path(d["path"]).is_file()
        return d


# ---------------------------------------------------------------- checks & approval

def run_checks(post_id: str, image_required: Optional[bool] = None) -> list[dict]:
    with connect() as conn:
        post = _post(conn, post_id)
        campaign = _campaign(conn, post["campaign_id"])
        sources = get_sources(conn, post["campaign_id"])
        brief = get_brief(conn, post["campaign_id"]) or {}
        others_rows = conn.execute(
            "SELECT * FROM posts WHERE platform=? AND id<>? AND status<>'REJECTED' ORDER BY created_at DESC LIMIT 200",
            (post["platform"], post_id)).fetchall()
        others = [(r["id"], content.post_plain_text(_decode_post(r))) for r in others_rows]
    source_text = (campaign.get("source_notes") or "") + " " + " ".join(s["content"] or "" for s in sources)
    req = campaign["include_graphic"] if image_required is None else image_required
    return quality.run_checks(post, campaign, source_text, brief.get("claims_to_verify", []), others, data_dir(), req)


def approve_post(post_id: str, note: str = "", waive_image: bool = False) -> dict:
    if waive_image:  # drop a stale image reference so the approved package really has no image
        with connect() as conn:
            old = _post(conn, post_id)
            if old.get("image_path") and not abs_path(old["image_path"]).is_file():
                _commit_change(conn, old, dict(old, image_path=None, image_sha=None), "missing image removed")
    checks = run_checks(post_id, image_required=False if waive_image else None)
    fails = quality.has_failures(checks)
    if fails:
        raise ServiceError("Cannot approve yet: " + " ".join(f["message"] for f in fails), 422)
    with connect() as conn:
        post = _post(conn, post_id)
        if post["status"] == "PUBLISHED":
            raise ServiceError("Already published", 409)
        h = review_hash(post)
        if post["status"] in ("APPROVED", "READY_TO_PUBLISH") and post.get("approved_hash") == h:
            return _with_image_info(post)
        conn.execute("UPDATE posts SET status='APPROVED', approval_status='approved', approved_hash=?, updated_at=? WHERE id=?",
                     (h, now(), post_id))
        conn.execute("INSERT INTO approvals (id,post_id,revision_number,content_hash,decision,note,created_at) VALUES (?,?,?,?,?,?,?)",
                     (new_id("appr"), post_id, post["revision_number"], h, "approved",
                      (note + (" [approved without image]" if waive_image else "")).strip(), now()))
        audit(conn, "post_approved", "post", post_id, f"revision {post['revision_number']}")
        return _with_image_info(_post(conn, post_id))


def reject_post(post_id: str, note: str = "") -> dict:
    with connect() as conn:
        post = _post(conn, post_id)
        if post["status"] == "PUBLISHED":
            raise ServiceError("Already published", 409)
        conn.execute("UPDATE posts SET status='REJECTED', approval_status='rejected', approved_hash=NULL, updated_at=? WHERE id=?",
                     (now(), post_id))
        conn.execute("INSERT INTO approvals (id,post_id,revision_number,content_hash,decision,note,created_at) VALUES (?,?,?,?,?,?,?)",
                     (new_id("appr"), post_id, post["revision_number"], review_hash(post), "rejected", note, now()))
        audit(conn, "post_rejected", "post", post_id, note[:200])
        return _with_image_info(_post(conn, post_id))


# ---------------------------------------------------------------- publishing preparation

def _ensure_approved(post: dict) -> None:
    if post["status"] not in ("APPROVED", "READY_TO_PUBLISH"):
        raise ServiceError("Only approved posts can be prepared for publishing.", 409)
    if not post.get("approved_hash") or post["approved_hash"] != review_hash(post):
        raise ServiceError("The approval no longer matches the current text/image. Review and approve again.", 409)


def build_package(post: dict) -> dict:
    composed = content.compose(post)
    steps = ["Open the composer (link below) and sign in yourself if needed.",
             "Paste the copied text." if post["platform"] != "x" else "Paste post 1, then add the next posts to the thread one by one.",
             "Attach the image file from the path shown." if post.get("image_path") else "No image for this post.",
             "Check everything, then press the platform's own Post/Schedule button.",
             "Come back here and click 'I published it' so the history is correct."]
    return {"post_id": post["id"], "platform": post["platform"], "text": composed["text"], "parts": composed["parts"],
            "composer_url": content.COMPOSER_URLS.get(post["platform"]), "image_path": post.get("image_abs"),
            "image_exists": post.get("image_exists", False), "steps": steps}


def get_package(post_id: str) -> dict:
    """Read-only. Used by the browser extension. Only for posts that were prepared."""
    with connect() as conn:
        post = _with_image_info(_post(conn, post_id))
    if post["status"] != "READY_TO_PUBLISH":
        raise ServiceError("Post is not in READY_TO_PUBLISH state", 409)
    _ensure_approved(post)
    return build_package(post)


def prepare_post(post_id: str, copy: bool = True, open_browser: bool = False) -> dict:
    from . import publishing
    with connect() as conn:
        post = _with_image_info(_post(conn, post_id))
        _ensure_approved(post)
        if post.get("image_path") and not post["image_exists"]:
            raise ServiceError("The image file is missing from disk. Render it again and re-approve.", 409)
        conn.execute("UPDATE posts SET status='READY_TO_PUBLISH', updated_at=? WHERE id=?", (now(), post_id))
        conn.execute("INSERT INTO publishing_events (id,post_id,platform,event_type,url,note,created_at) VALUES (?,?,?,?,?,?,?)",
                     (new_id("pub"), post_id, post["platform"], "prepared", "", "", now()))
        audit(conn, "publish_prepared", "post", post_id, post["platform"])
    pkg = build_package(post)
    pkg["clipboard"] = publishing.copy_to_clipboard(pkg["text"]) if copy else False
    pkg["browser_opened"] = publishing.open_url(pkg["composer_url"]) if (open_browser and pkg["composer_url"]) else False
    return pkg


def confirm_published(post_id: str, url: str = "", note: str = "") -> dict:
    with connect() as conn:
        post = _with_image_info(_post(conn, post_id))
        if post["status"] != "READY_TO_PUBLISH":
            raise ServiceError("Prepare the post for publishing first.", 409)
        _ensure_approved(post)
        conn.execute("UPDATE posts SET status='PUBLISHED', updated_at=? WHERE id=?", (now(), post_id))
        conn.execute("INSERT INTO publishing_events (id,post_id,platform,event_type,url,note,created_at) VALUES (?,?,?,?,?,?,?)",
                     (new_id("pub"), post_id, post["platform"], "published", url[:500], note[:500], now()))
        audit(conn, "publish_confirmed_by_user", "post", post_id, url[:200])
        return _with_image_info(_post(conn, post_id))


# ---------------------------------------------------------------- export, history

def export_campaign(campaign_id: str) -> Path:
    camp = get_campaign(campaign_id)
    out_dir = data_dir() / "exports" / campaign_id
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    brief = camp.get("brief") or {}
    lines = [f"# {camp['topic']}", "", f"Audience: {camp['audience']}  ", f"Tone: {camp['tone']}", ""]
    if brief:
        lines += ["## Brief", "", brief.get("main_message", ""), ""] + [f"- {p}" for p in brief.get("key_points", [])]
        if brief.get("claims_to_verify"):
            lines += ["", "### Claims to verify"] + [f"- {c}" for c in brief["claims_to_verify"]]
    (out_dir / "brief.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for p in camp["posts"]:
        pkg = content.compose(p)
        (out_dir / f"{p['platform']}_{p['id']}.md").write_text(
            f"# {p['title']}\n\nStatus: {p['status']} (revision {p['revision_number']})\n\n{pkg['text']}\n", encoding="utf-8")
        data = {k: p[k] for k in ("id", "platform", "title", "hook", "body", "cta", "hashtags", "thread_parts",
                                  "status", "revision_number", "image_path")}
        (out_dir / f"{p['platform']}_{p['id']}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        if p.get("image_exists"):
            shutil.copy2(p["image_abs"], out_dir / Path(p["image_abs"]).name)
    zip_path = data_dir() / "exports" / f"{campaign_id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(out_dir.iterdir()):
            z.write(f, f.name)
    return zip_path


def history(limit: int = 200) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (max(1, min(limit, 1000)),))]
