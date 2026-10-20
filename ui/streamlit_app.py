"""Content Studio dashboard. Run: streamlit run ui/streamlit_app.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from app import content, generator, intake, jobs, llm, services  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.errors import ServiceError  # noqa: E402
from app.schemas import SUPPORTED_PLATFORMS, TEMPLATE_NAMES, CampaignCreate  # noqa: E402

st.set_page_config(page_title="Content Studio", page_icon="📝", layout="wide")


@st.cache_resource
def _startup() -> bool:
    services.ensure_ready()
    jobs.recover_interrupted()
    return True


_startup()

ICONS = {"pass": "✅", "warn": "⚠️", "fail": "❌"}
STATUS_LABEL = {"REVIEW_REQUIRED": "Review required", "APPROVED": "Approved", "READY_TO_PUBLISH": "Ready to publish",
                "PUBLISHED": "Published", "REJECTED": "Rejected"}


def guard(fn, *args, success=None, **kwargs):
    """Run a service call and show errors in the UI instead of crashing."""
    try:
        result = fn(*args, **kwargs)
        if success:
            st.success(success)
        return result
    except ServiceError as exc:
        st.error(exc.message)
    except llm.LLMError as exc:
        st.error(str(exc))
    except Exception as exc:  # noqa: BLE001
        st.error(f"Unexpected error: {exc}")
    return None


# ------------------------------------------------------------------ pages

def page_dashboard():
    st.header("Dashboard")
    camps = services.list_campaigns()
    posts = services.list_posts()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Campaigns", len(camps))
    c2.metric("Awaiting review", sum(p["status"] == "REVIEW_REQUIRED" for p in posts))
    c3.metric("Ready to publish", sum(p["status"] in ("APPROVED", "READY_TO_PUBLISH") for p in posts))
    c4.metric("Published (confirmed)", sum(p["status"] == "PUBLISHED" for p in posts))

    s = get_settings()
    with st.expander("Model status", expanded=False):
        st.write(f"Provider: **{s.llm_provider}**" + (f", model: **{s.ollama_model}**" if s.llm_provider == "ollama" else ""))
        if st.button("Check model connection"):
            h = guard(lambda: llm.get_provider().health())
            if h:
                (st.success if h["ok"] else st.error)(h["detail"] or "OK")
        if s.llm_provider == "mock":
            st.warning("Mock mode: text is generic filler for testing. Set SGA_LLM_PROVIDER=ollama in .env for real drafts.")

    active = jobs.active_jobs()
    for j in active:
        st.info(f"Job {j['id']}: {j['message']}")
        st.progress(min(1.0, float(j["progress"] or 0)))
        if st.button("Cancel", key=f"cancel_{j['id']}"):
            jobs.cancel_job(j["id"])
            st.rerun()

    st.subheader("Campaigns")
    if not camps:
        st.write("No campaigns yet. Create one in **New campaign**.")
    for c in camps:
        a, b, d = st.columns([6, 2, 2])
        a.write(f"**{c['topic']}**  \n{c['created_at']} · {', '.join(c['platforms'])}")
        b.write(f"{c['status']} · {c['post_count']} posts")
        if d.button("Open", key=f"open_{c['id']}"):
            st.session_state["campaign_id"] = c["id"]
            st.session_state["page"] = "Review"
            st.rerun()
    if active:
        time.sleep(2)
        st.rerun()


def page_new():
    st.header("New content campaign")
    with st.form("new_campaign"):
        topic = st.text_input("Topic", placeholder="How S3-compatible object storage works with MinIO and Docker Compose")
        c1, c2, c3 = st.columns(3)
        audience = c1.text_input("Audience", "Computer science students")
        goal = c2.selectbox("Goal", ["educate", "explain", "promote", "engage"])
        tone = c3.selectbox("Tone", ["professional", "conversational", "technical", "friendly"])
        angle = st.text_input("Preferred angle (optional)", placeholder="e.g. common mistakes for beginners")
        platforms = st.multiselect("Platforms", list(SUPPORTED_PLATFORMS), default=["linkedin", "x"])
        c4, c5 = st.columns(2)
        template = c4.selectbox("Graphic template", list(TEMPLATE_NAMES))
        graphic = c5.checkbox("Create a graphic", value=True)
        notes = st.text_area("Your notes / facts to use (the more you give, the less the model invents)", height=140)
        files = st.file_uploader("Markdown or text files", type=["md", "markdown", "txt"], accept_multiple_files=True)
        urls = st.text_area("Reference URLs (one per line, optional)", height=70)
        fetch = st.checkbox("Download the URLs for context", value=get_settings().allow_url_fetch,
                            disabled=not get_settings().allow_url_fetch)
        submitted = st.form_submit_button("Create and generate")
    if not submitted:
        return
    try:
        data = CampaignCreate(topic=topic, audience=audience, content_goal=goal, tone=tone, angle=angle,
                              source_notes=notes, source_urls=[u for u in urls.splitlines() if u.strip()],
                              platforms=platforms, template=template, include_graphic=graphic)
    except Exception as exc:  # noqa: BLE001 - pydantic validation message
        st.error(f"Please fix the form: {exc}")
        return
    res = guard(intake.create_campaign_with_sources, data, [(f.name, f.getvalue()) for f in files], fetch)
    if not res:
        return
    for w in res["warnings"]:
        st.warning(w)
    job = guard(jobs.submit_generation, res["campaign_id"])
    if job:
        st.session_state["campaign_id"] = res["campaign_id"]
        st.success("Generation started. Open the Dashboard to watch progress, or go to Review.")


def _post_editor(post: dict, camp: dict):
    pid, rev = post["id"], post["revision_number"]
    k = f"{pid}_{rev}"
    left, right = st.columns([3, 2])
    with left:
        st.caption(f"{STATUS_LABEL.get(post['status'], post['status'])} · revision {rev}"
                   + (" · approval valid" if post["approval_valid"] else ""))
        locked = post["status"] == "PUBLISHED"
        title = st.text_input("Title (internal)", post["title"], key=f"title_{k}", disabled=locked)
        changes = {"title": title}
        if post["platform"] == "x":
            parts = []
            for i, p in enumerate(post["thread_parts"]):
                txt = st.text_area(f"Post {i + 1} ({p.get('purpose') or 'part'})", p["text"], key=f"tp_{k}_{i}",
                                   height=90, disabled=locked)
                n = content.x_length(txt)
                st.caption(f"{n}/{content.X_LIMIT}" + ("  ⚠️ too long" if n > content.X_LIMIT else ""))
                parts.append({"purpose": p.get("purpose", ""), "text": txt})
            extra = st.text_area("Add another post (optional)", "", key=f"tp_new_{k}", height=70, disabled=locked)
            if extra.strip():
                parts.append({"purpose": "", "text": extra})
            changes["thread_parts"] = parts
        else:
            changes["hook"] = st.text_input("Hook", post["hook"], key=f"hook_{k}", disabled=locked)
            changes["body"] = st.text_area("Body", post["body"], key=f"body_{k}", height=260, disabled=locked)
            changes["cta"] = st.text_input("Call to action", post["cta"], key=f"cta_{k}", disabled=locked)
        tags = st.text_input("Hashtags (space separated)", " ".join(post["hashtags"]), key=f"tags_{k}", disabled=locked)
        changes["hashtags"] = tags.split()
        if st.button("Save changes", key=f"save_{k}", disabled=locked):
            if guard(services.update_post, pid, changes) is not None:
                st.rerun()

        with st.expander("Rewrite with the model"):
            instr = st.text_input("Instruction", placeholder="Make the hook more direct", key=f"instr_{k}")
            opts = sorted(generator.PARTS_BY_PLATFORM[post["platform"]])
            part = st.selectbox("Part to rewrite", opts, index=opts.index("all"), key=f"part_{k}")
            if st.button("Rewrite", key=f"regen_{k}", disabled=locked or len(instr.strip()) < 3):
                with st.spinner("The local model is writing..."):
                    r = guard(generator.regenerate_post, pid, instr.strip(), part)
                if r is not None:
                    st.rerun()
        with st.expander("Revision history"):
            for v in services.list_versions(pid):
                st.write(f"Revision {v['revision_number']} · {v['created_at']} · {v['reason']}")
                if st.button("Restore this text", key=f"rest_{v['id']}", disabled=locked):
                    if guard(services.restore_version, pid, v["id"]) is not None:
                        st.rerun()

    with right:
        if post["image_exists"]:
            st.image(post["image_abs"])
        elif post["image_path"]:
            st.error("Image file is missing. Render it again.")
        else:
            st.info("No image yet.")
        with st.expander("Graphic settings"):
            tpl = st.selectbox("Template", list(TEMPLATE_NAMES),
                               index=list(TEMPLATE_NAMES).index(camp["template"]) if camp["template"] in TEMPLATE_NAMES else 0,
                               key=f"tpl_{k}")
            hl = st.text_input("Headline (blank = keep)", "", key=f"hl_{k}")
            sub = st.text_input("Subtitle (blank = keep)", "", key=f"sub_{k}")
            if st.button("Render image", key=f"render_{k}", disabled=locked):
                ov = {}
                if hl.strip():
                    ov["headline"] = hl.strip()
                if sub.strip():
                    ov["subtitle"] = sub.strip()
                meta = guard(services.render_post, pid, tpl, ov)
                if meta is not None:
                    for w in meta["warnings"]:
                        st.warning(w)
                    if not meta["warnings"]:
                        st.rerun()

        st.subheader("Quality checks")
        checks = services.run_checks(pid)
        for c in checks:
            st.write(f"{ICONS[c['status']]} **{c['check'].replace('_', ' ')}**: {c['message']}")

        st.subheader("Approval")
        note = st.text_input("Note (optional)", key=f"note_{k}")
        waive = st.checkbox("Approve without an image", key=f"waive_{k}") if not post["image_exists"] else False
        a, b = st.columns(2)
        if a.button("Approve this revision", key=f"appr_{k}", disabled=post["status"] in ("PUBLISHED",)):
            if guard(services.approve_post, pid, note, waive) is not None:
                st.rerun()
        if b.button("Reject", key=f"rej_{k}", disabled=post["status"] in ("PUBLISHED",)):
            if guard(services.reject_post, pid, note) is not None:
                st.rerun()

        st.subheader("Publishing")
        ready = post["status"] in ("APPROVED", "READY_TO_PUBLISH") and post["approval_valid"]
        if not ready and post["status"] != "PUBLISHED":
            st.caption("Approve the current revision first.")
        if st.button("Prepare to publish", key=f"prep_{k}", disabled=not ready):
            pkg = guard(services.prepare_post, pid, True, False)
            if pkg:
                st.session_state[f"pkg_{pid}"] = pkg
                st.rerun()
        if post["status"] == "READY_TO_PUBLISH":
            pkg = st.session_state.get(f"pkg_{pid}") or services.get_package(pid)
            st.success("Text copied to the clipboard." if pkg.get("clipboard") else
                       "Copy the text below (clipboard is not available on this machine).")
            for i, part in enumerate(pkg["parts"]):
                st.code(part, language=None)
            if pkg["composer_url"]:
                st.link_button(f"Open {post['platform']} composer", pkg["composer_url"])
            if pkg["image_path"]:
                st.caption(f"Attach this image yourself: {pkg['image_path']}")
            for step in pkg["steps"]:
                st.write("• " + step)
            url = st.text_input("Link to the live post (optional)", key=f"url_{k}")
            if st.button("I published it", key=f"pub_{k}"):
                if guard(services.confirm_published, pid, url) is not None:
                    st.rerun()
        if post["status"] == "PUBLISHED":
            st.success("Marked as published (confirmed by you).")


def page_review():
    st.header("Review and approve")
    camps = services.list_campaigns()
    if not camps:
        st.write("No campaigns yet.")
        return
    ids = [c["id"] for c in camps]
    current = st.session_state.get("campaign_id")
    idx = ids.index(current) if current in ids else 0
    cid = st.selectbox("Campaign", ids, index=idx, format_func=lambda i: next(c["topic"][:70] for c in camps if c["id"] == i))
    st.session_state["campaign_id"] = cid
    camp = services.get_campaign(cid)
    st.write(f"Status: **{camp['status']}**")
    for j in camp["jobs"][:1]:
        if j["status"] in ("QUEUED", "RUNNING"):
            st.info(j["message"])
            st.progress(min(1.0, float(j["progress"] or 0)))
        elif j["status"] == "FAILED":
            st.error(f"Generation failed: {j['error']}")
        elif j["status"] == "CANCELLED":
            st.warning("Generation was cancelled. Finished drafts were kept.")
        elif j["error"]:
            st.warning(j["error"])
    if camp["status"] in ("FAILED", "CANCELLED") and st.button("Generate the missing drafts"):
        if guard(jobs.submit_generation, cid):
            st.rerun()
    if camp["brief"]:
        with st.expander("Content brief"):
            b = camp["brief"]
            st.write(b["main_message"])
            for p in b["key_points"]:
                st.write("• " + p)
            if b.get("claims_to_verify"):
                st.warning("Verify by hand: " + "; ".join(b["claims_to_verify"]))
    if not camp["posts"]:
        st.write("No drafts yet.")
    else:
        tabs = st.tabs([p["platform"].upper() for p in camp["posts"]])
        for tab, post in zip(tabs, camp["posts"]):
            with tab:
                _post_editor(post, camp)
    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        if camp["posts"] and st.button("Export campaign (zip)"):
            path = guard(services.export_campaign, cid)
            if path:
                st.download_button("Download zip", path.read_bytes(), file_name=path.name, mime="application/zip")
    with c2:
        if st.checkbox("I understand deleting is permanent", key="confirm_del") and st.button("Delete this campaign"):
            try:
                services.delete_campaign(cid)
            except ServiceError as exc:
                st.error(exc.message)
            else:
                st.session_state.pop("campaign_id", None)
                st.rerun()
    if camp["status"] in ("RESEARCHING", "GENERATING", "RENDERING"):
        time.sleep(2)
        st.rerun()


def page_settings():
    st.header("Settings")
    s = get_settings()
    st.write(f"Data folder: `{s.data_dir}`")
    st.write(f"Model provider: **{s.llm_provider}**, model: **{s.ollama_model}** (change in the `.env` file, then restart)")
    st.subheader("Brand")
    b = services.get_brand()
    with st.form("brand"):
        name = st.text_input("Brand name", b["brand_name"])
        c1, c2, c3 = st.columns(3)
        prim = c1.color_picker("Background colour", b["primary_color"])
        acc = c2.color_picker("Accent colour", b["accent_color"])
        txt = c3.color_picker("Text colour", b["text_color"])
        footer = st.text_input("Footer text", b["footer"] or "")
        font = st.text_input("Font file (.ttf, optional)", b["font_path"] or "")
        logo = st.text_input("Logo file (png, optional)", b["logo_path"] or "")
        if st.form_submit_button("Save brand"):
            guard(services.update_brand, {"brand_name": name, "primary_color": prim, "accent_color": acc,
                                          "text_color": txt, "footer": footer, "font_path": font, "logo_path": logo},
                  success="Brand saved. Re-render images to apply it.")


def page_history():
    st.header("History")
    st.caption("Everything the app did: generation, edits, approvals, publishing preparation.")
    rows = services.history(300)
    st.dataframe(rows, hide_index=True)


PAGES = {"Dashboard": page_dashboard, "New campaign": page_new, "Review": page_review,
         "History": page_history, "Settings": page_settings}

if "page" not in st.session_state:
    st.session_state["page"] = "Dashboard"
st.sidebar.title("Content Studio")
choice = st.sidebar.radio("Go to", list(PAGES), key="page")
st.sidebar.caption("Local-first. Nothing is posted for you.")
PAGES[choice]()
