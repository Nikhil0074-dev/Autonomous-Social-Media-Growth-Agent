import time

import pytest
from fastapi.testclient import TestClient

from app.api import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def wait(client, job_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("DONE", "FAILED", "CANCELLED"):
            return job
        time.sleep(0.1)
    raise AssertionError("job timeout")


def test_full_api_flow(client):
    assert client.get("/api/health").json()["status"] == "ok"
    r = client.post("/api/generation/drafts", json={
        "topic": "Building a local AI content agent", "audience": "Computer science students",
        "tone": "professional", "platforms": ["linkedin", "x"], "include_graphic": True, "include_source_notes": True})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued" and body["job_id"].startswith("job_")
    assert wait(client, body["job_id"])["status"] == "DONE"
    camp = client.get(f"/api/campaigns/{body['campaign_id']}").json()
    assert len(camp["posts"]) == 2
    pid = camp["posts"][0]["id"]
    assert client.patch(f"/api/posts/{pid}", json={"title": "New title"}).json()["title"] == "New title"
    assert client.get(f"/api/posts/{pid}/checks").status_code == 200
    assert client.post(f"/api/posts/{pid}/prepare").status_code == 409  # not approved
    assert client.post(f"/api/posts/{pid}/approve", json={}).json()["status"] == "APPROVED"
    assert client.post(f"/api/posts/{pid}/prepare").json()["text"]
    assert client.get(f"/api/posts/{pid}/package").status_code == 200
    assert client.post(f"/api/posts/{pid}/confirm-published", json={"url": "https://x.com/a/1"}).json()["status"] == "PUBLISHED"
    assert client.get("/api/history").json()
    assert client.get(f"/api/campaigns/{body['campaign_id']}/export").status_code == 200


def test_asset_file_endpoint_and_regenerate_and_render(client):
    r = client.post("/api/generation/drafts", json={"topic": "Docker volumes explained", "platforms": ["linkedin"]})
    cid = r.json()["campaign_id"]
    wait(client, r.json()["job_id"])
    post = client.get(f"/api/campaigns/{cid}").json()["posts"][0]
    rr = client.post(f"/api/posts/{post['id']}/render", json={"template": "professional_insight", "headline": "Docker Volumes"})
    assert rr.status_code == 200
    f = client.get(f"/api/assets/{rr.json()['asset_id']}/file")
    assert f.status_code == 200 and f.headers["content-type"] == "image/png"
    assert client.get(f"/api/assets/{rr.json()['asset_id']}/file?preview=true").status_code == 200
    rg = client.post(f"/api/posts/{post['id']}/regenerate", json={"instruction": "Make the hook more direct", "part": "hook"})
    assert rg.status_code == 200 and "more direct" in rg.json()["hook"]
    assert client.post(f"/api/posts/{post['id']}/regenerate", json={"instruction": "Make it shorter", "part": "thread"}).status_code == 422


def test_validation_errors(client):
    assert client.post("/api/campaigns", json={"topic": "ab"}).status_code == 422
    assert client.post("/api/campaigns", json={"topic": "Valid topic", "platforms": ["myspace"]}).status_code == 422
    assert client.post("/api/generation/drafts", json={}).status_code == 422
    assert client.get("/api/campaigns/nope").status_code == 404
    assert client.get("/api/assets/nope/file").status_code == 404
    assert client.patch("/api/posts/nope", json={"title": "x"}).status_code == 404
    assert client.patch("/api/posts/nope", json={"bogus": "x"}).status_code == 422
    assert client.put("/api/brand", json={"accent_color": "red"}).status_code == 422


def test_duplicate_submission_via_api(client):
    c = client.post("/api/campaigns", json={"topic": "Same job twice"}).json()["campaign_id"]
    from app.db import connect, now
    with connect() as conn:
        conn.execute("INSERT INTO generation_jobs (id,campaign_id,status,created_at) VALUES ('job_dup',?,'RUNNING',?)", (c, now()))
    r = client.post("/api/generation/drafts", json={"campaign_id": c})
    assert r.json()["duplicate"] is True


def test_brief_and_brand_endpoints(client):
    c = client.post("/api/campaigns", json={"topic": "Brief endpoint test"}).json()["campaign_id"]
    assert client.post("/api/research/brief", json={"campaign_id": c}).json()["key_points"]
    assert client.put("/api/brand", json={"brand_name": "Acme", "accent_color": "#FF8800"}).json()["brand_name"] == "Acme"
    assert client.post("/api/campaigns/%s/sources" % c, json={"title": "n", "content": "text"}).status_code == 201
