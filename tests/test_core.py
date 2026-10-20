import json
import sqlite3

import pytest
from PIL import Image

from app import jobs, llm, services
from app.config import get_settings
from app.errors import ServiceError
from app.renderer import RenderError, RenderSpec, TextFitError, render_og
from tests.helpers import make_campaign


def test_generation_creates_posts_and_images():
    cid, jid = make_campaign()
    job = jobs.get_job(jid)
    assert job["status"] == "DONE", job
    camp = services.get_campaign(cid)
    assert camp["status"] == "REVIEW_REQUIRED"
    assert {p["platform"] for p in camp["posts"]} == {"linkedin", "x"}
    for p in camp["posts"]:
        assert p["image_exists"]
        with Image.open(p["image_abs"]) as im:
            assert im.size == (1200, 630)
    x = next(p for p in camp["posts"] if p["platform"] == "x")
    assert len(x["thread_parts"]) >= 2
    assert camp["brief"]["key_points"]
    assert job["metrics"]["total_s"] >= 0


def test_persistence_across_restart():
    cid, _ = make_campaign()
    from app import db
    db._ready.clear()  # simulate a fresh process
    assert services.get_campaign(cid)["posts"]


def test_approval_flow_and_invalidation():
    cid, _ = make_campaign()
    post = services.get_campaign(cid)["posts"][0]
    pid = post["id"]
    approved = services.approve_post(pid)
    assert approved["status"] == "APPROVED" and approved["approval_valid"]
    edited = services.update_post(pid, {"cta": "A different call to action?"})
    assert edited["status"] == "REVIEW_REQUIRED"
    assert edited["approval_status"] == "pending" and not edited["approval_valid"]
    with pytest.raises(ServiceError):
        services.prepare_post(pid, copy=False)


def test_image_change_invalidates_approval():
    cid, _ = make_campaign()
    pid = services.get_campaign(cid)["posts"][0]["id"]
    services.approve_post(pid)
    services.render_post(pid, template="quote_card")
    assert services.get_post(pid)["status"] == "REVIEW_REQUIRED"


def test_prepare_and_confirm_publish():
    cid, _ = make_campaign()
    pid = services.get_campaign(cid)["posts"][0]["id"]
    with pytest.raises(ServiceError):  # not approved yet
        services.prepare_post(pid, copy=False)
    services.approve_post(pid)
    pkg = services.prepare_post(pid, copy=False)
    assert pkg["text"] and pkg["composer_url"]
    assert services.get_post(pid)["status"] == "READY_TO_PUBLISH"
    assert services.get_package(pid)["text"] == pkg["text"]
    done = services.confirm_published(pid, "https://example.com/post/1")
    assert done["status"] == "PUBLISHED"
    with pytest.raises(ServiceError):
        services.update_post(pid, {"body": "x"})


def test_confirm_requires_prepare():
    cid, _ = make_campaign()
    pid = services.get_campaign(cid)["posts"][0]["id"]
    services.approve_post(pid)
    with pytest.raises(ServiceError):
        services.confirm_published(pid)


def test_versions_and_restore():
    cid, _ = make_campaign()
    p = services.get_campaign(cid)["posts"][0]
    original = p["body"] if p["platform"] != "x" else p["thread_parts"]
    pid = p["id"]
    if p["platform"] == "x":
        services.update_post(pid, {"thread_parts": [{"text": "one"}, {"text": "two"}]})
    else:
        services.update_post(pid, {"body": "Changed body."})
    vers = services.list_versions(pid)
    assert vers
    restored = services.restore_version(pid, vers[-1]["id"])
    assert (restored["body"] if p["platform"] != "x" else restored["thread_parts"]) == original


def test_x_thread_limit_blocks_approval():
    cid, _ = make_campaign()
    x = next(p for p in services.get_campaign(cid)["posts"] if p["platform"] == "x")
    services.update_post(x["id"], {"thread_parts": [{"text": "a" * 300}, {"text": "ok"}]})
    checks = services.run_checks(x["id"])
    assert any(c["check"] == "length" and c["status"] == "fail" for c in checks)
    with pytest.raises(ServiceError):
        services.approve_post(x["id"])


def test_missing_image_detected_and_waivable():
    cid, _ = make_campaign()
    p = services.get_campaign(cid)["posts"][0]
    import os
    os.remove(p["image_abs"])
    checks = services.run_checks(p["id"])
    assert any(c["check"] == "image" and c["status"] == "fail" for c in checks)
    with pytest.raises(ServiceError):
        services.approve_post(p["id"])
    assert services.approve_post(p["id"], waive_image=True)["status"] == "APPROVED"


def test_duplicate_detection_warns():
    make_campaign(topic="Docker volumes explained simply", platforms=("linkedin",))
    cid2, _ = make_campaign(topic="Docker volumes explained simply", platforms=("linkedin",))
    pid = services.get_campaign(cid2)["posts"][0]["id"]
    dup = next(c for c in services.run_checks(pid) if c["check"] == "duplicates")
    assert dup["status"] == "warn"


# ---- LLM failure handling ----

class Garbage(llm.LLMProvider):
    name, model = "garbage", "g"

    def generate(self, task, system, prompt, meta=None):
        return "this is not json at all"


class FlakyThenGood(llm.MockProvider):
    def __init__(self):
        self.calls = 0

    def generate(self, task, system, prompt, meta=None):
        self.calls += 1
        if self.calls == 1:
            return '```json\n{"main_message": "x"'  # truncated
        return super().generate(task, system, prompt, meta)


class Down(llm.LLMProvider):
    name, model = "down", "d"

    def generate(self, task, system, prompt, meta=None):
        raise llm.LLMUnavailable("Cannot reach Ollama")


def test_invalid_json_fails_cleanly():
    llm.set_provider_override(Garbage())
    cid, jid = make_campaign()
    job = jobs.get_job(jid)
    assert job["status"] == "FAILED" and "invalid" in job["error"].lower()
    camp = services.get_campaign(cid)
    assert camp["status"] == "FAILED" and camp["posts"] == []
    with sqlite3.connect(get_settings().db_path) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_retry_recovers_from_bad_json():
    llm.set_provider_override(FlakyThenGood())
    _, jid = make_campaign()
    assert jobs.get_job(jid)["status"] == "DONE"


def test_model_down_reports_message():
    llm.set_provider_override(Down())
    _, jid = make_campaign()
    job = jobs.get_job(jid)
    assert job["status"] == "FAILED" and "Ollama" in job["error"]


def test_resume_after_partial_failure():
    class FailsOnX(llm.MockProvider):
        def generate(self, task, system, prompt, meta=None):
            if task == "draft" and (meta or {}).get("platform") == "x":
                return "nope"
            return super().generate(task, system, prompt, meta)

    llm.set_provider_override(FailsOnX())
    cid, jid = make_campaign()
    assert jobs.get_job(jid)["status"] == "FAILED"
    assert [p["platform"] for p in services.get_campaign(cid)["posts"]] == ["linkedin"]  # kept
    llm.set_provider_override(llm.MockProvider())
    job = jobs.submit_generation(cid, run_async=False)
    assert jobs.get_job(job["job_id"])["status"] == "DONE"
    plats = [p["platform"] for p in services.get_campaign(cid)["posts"]]
    assert sorted(plats) == ["linkedin", "x"]  # no duplicate linkedin


def test_duplicate_submission_prevented():
    from app.schemas import CampaignCreate
    cid = services.create_campaign(CampaignCreate(topic="Duplicate job test"))
    from app.db import connect, new_id, now
    with connect() as conn:
        conn.execute("INSERT INTO generation_jobs (id,campaign_id,status,created_at) VALUES ('job_x',?,'RUNNING',?)", (cid, now()))
    res = jobs.submit_generation(cid, run_async=False)
    assert res["duplicate"] and res["job_id"] == "job_x"


def test_cancel_queued_job_and_recover():
    from app.schemas import CampaignCreate
    from app.db import connect, now
    cid = services.create_campaign(CampaignCreate(topic="Cancel test topic"))
    with connect() as conn:
        conn.execute("INSERT INTO generation_jobs (id,campaign_id,status,created_at) VALUES ('job_q',?,'QUEUED',?)", (cid, now()))
    assert jobs.cancel_job("job_q")["status"] == "CANCELLED"
    with connect() as conn:
        conn.execute("INSERT INTO generation_jobs (id,campaign_id,status,created_at) VALUES ('job_r',?,'RUNNING',?)", (cid, now()))
    assert jobs.recover_interrupted() == 1
    assert jobs.get_job("job_r")["status"] == "FAILED"


def test_cancel_running_job_keeps_finished_work():
    class CancelAfterBrief(llm.MockProvider):
        def generate(self, task, system, prompt, meta=None):
            out = super().generate(task, system, prompt, meta)
            if task == "draft":
                from app.db import connect
                with connect() as c:
                    c.execute("UPDATE generation_jobs SET cancel_requested=1")
            return out

    llm.set_provider_override(CancelAfterBrief())
    cid, jid = make_campaign()
    assert jobs.get_job(jid)["status"] == "CANCELLED"
    camp = services.get_campaign(cid)
    assert camp["brief"] and len(camp["posts"]) == 1  # first draft was kept


# ---- renderer ----

def test_renderer_validation(tmp_path):
    with pytest.raises(RenderError):
        render_og(RenderSpec(headline=""), tmp_path / "a.png")
    with pytest.raises(RenderError):
        render_og(RenderSpec(headline="Hi", width=10), tmp_path / "a.png")
    with pytest.raises(RenderError):
        render_og(RenderSpec(headline="Hi", accent_color="red"), tmp_path / "a.png")
    with pytest.raises(TextFitError):
        render_og(RenderSpec(headline="word " * 100), tmp_path / "a.png")


def test_renderer_emoji_and_jpeg_and_sizes(tmp_path):
    meta = render_og(RenderSpec(headline="Rocket \U0001F680 launch", format="jpeg", width=1080, height=1080),
                     tmp_path / "a.jpg", tmp_path / "p.png")
    assert meta["warnings"] and Image.open(tmp_path / "a.jpg").size == (1080, 1080)


def test_long_headline_is_shortened_not_cropped():
    cid, _ = make_campaign()
    pid = services.get_campaign(cid)["posts"][0]["id"]
    meta = services.render_post(pid, overrides={"headline": "Extraordinarily " * 25})
    assert any("shortened" in w for w in meta["warnings"])


def test_html_in_text_is_drawn_literally(tmp_path):
    render_og(RenderSpec(headline="<script>alert(1)</script> & more"), tmp_path / "a.png")


def test_path_escape_blocked():
    with pytest.raises(ServiceError):
        services.abs_path("../../etc/passwd")


def test_delete_campaign_removes_files():
    cid, _ = make_campaign()
    p = services.get_campaign(cid)["posts"][0]
    services.delete_campaign(cid)
    import os
    assert not os.path.exists(p["image_abs"])
    with pytest.raises(ServiceError):
        services.get_campaign(cid)


def test_export_zip():
    import zipfile
    cid, _ = make_campaign()
    z = services.export_campaign(cid)
    names = zipfile.ZipFile(z).namelist()
    assert "brief.md" in names and any(n.endswith(".png") for n in names)


def test_research_helpers():
    from app import research
    doc = research.read_text_file("n.md", b"---\ntitle: x\n---\n# Docker\n\nVolumes persist data.")
    assert doc["title"] == "Docker" and "Volumes" in doc["content"]
    with pytest.raises(ServiceError):
        research.read_text_file("a.exe", b"abc")
    with pytest.raises(ServiceError):
        research.read_text_file("a.txt", b"a\x00b")
    with pytest.raises(ServiceError):
        research.fetch_url("http://127.0.0.1:1/")
    with pytest.raises(ServiceError):
        research.fetch_url("file:///etc/passwd")
    assert "docker" in research.extract_keywords("Docker docker volumes volumes volumes the and")


def test_hashtags_and_extract_json():
    from app import content
    assert content.normalize_hashtags(["#AI", "ai", "bad tag!", ""], fallback=["local models"]) == ["#AI", "#badtag", "#LocalModels"]
    assert llm.extract_json('Sure! {"a": 1} thanks') == {"a": 1}
    with pytest.raises(llm.InvalidModelOutput):
        llm.extract_json("[1,2]")
