"""Background job runner. One worker thread, because local models run one request at a time."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from . import generator
from .db import audit, connect, new_id, now
from .errors import ServiceError

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sga-gen")
ACTIVE = ("QUEUED", "RUNNING")
FINAL = ("DONE", "FAILED", "CANCELLED")


def _run(job_id: str) -> None:
    with connect() as conn:
        cur = conn.execute("UPDATE generation_jobs SET status='RUNNING', started_at=? WHERE id=? AND status='QUEUED'",
                           (now(), job_id))
        started = cur.rowcount == 1
    if started:
        generator.run_generation(job_id)


def submit_generation(campaign_id: str, run_async: bool = True) -> dict:
    """Queue a job. If one is already queued/running for this campaign, return it instead (no duplicates)."""
    with connect() as conn:
        if conn.execute("SELECT 1 FROM campaigns WHERE id=?", (campaign_id,)).fetchone() is None:
            raise ServiceError(f"Campaign '{campaign_id}' not found", 404)
        existing = conn.execute(
            "SELECT id, status FROM generation_jobs WHERE campaign_id=? AND status IN ('QUEUED','RUNNING') LIMIT 1",
            (campaign_id,)).fetchone()
        if existing:
            return {"job_id": existing["id"], "campaign_id": campaign_id, "status": existing["status"].lower(),
                    "duplicate": True, "message": "A generation job is already running for this campaign."}
        job_id = new_id("job")
        conn.execute("INSERT INTO generation_jobs (id,campaign_id,kind,status,message,created_at) VALUES (?,?,?,?,?,?)",
                     (job_id, campaign_id, "generate", "QUEUED", "Queued", now()))
        audit(conn, "generation_queued", "campaign", campaign_id, job_id)
    if run_async:
        _executor.submit(_run, job_id)
    else:
        _run(job_id)
    return {"job_id": job_id, "campaign_id": campaign_id, "status": "queued", "duplicate": False,
            "message": "Content generation has been queued."}


def get_job(job_id: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM generation_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ServiceError(f"Job '{job_id}' not found", 404)
        d = dict(row)
        d["metrics"] = json.loads(d.get("metrics") or "{}")
        return d


def cancel_job(job_id: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT status, campaign_id FROM generation_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ServiceError(f"Job '{job_id}' not found", 404)
        if row["status"] in FINAL:
            return get_job_inner(conn, job_id)
        conn.execute("UPDATE generation_jobs SET cancel_requested=1 WHERE id=?", (job_id,))
        if row["status"] == "QUEUED":
            conn.execute("UPDATE generation_jobs SET status='CANCELLED', message='Cancelled before start', finished_at=? WHERE id=?",
                         (now(), job_id))
            conn.execute("UPDATE campaigns SET status='CANCELLED', updated_at=? WHERE id=?", (now(), row["campaign_id"]))
        audit(conn, "generation_cancel_requested", "job", job_id)
    return get_job(job_id)


def get_job_inner(conn, job_id: str) -> dict:
    d = dict(conn.execute("SELECT * FROM generation_jobs WHERE id=?", (job_id,)).fetchone())
    d["metrics"] = json.loads(d.get("metrics") or "{}")
    return d


def wait_for_job(job_id: str, timeout: float = 60.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        job = get_job(job_id)
        if job["status"] in FINAL:
            return job
        time.sleep(0.05)
    raise TimeoutError(f"Job {job_id} did not finish within {timeout}s")


def recover_interrupted() -> int:
    """After a crash/restart, jobs stuck in QUEUED/RUNNING can never finish. Mark them failed.
    Only call this when starting the only running copy of the app."""
    with connect() as conn:
        rows = conn.execute("SELECT id, campaign_id FROM generation_jobs WHERE status IN ('QUEUED','RUNNING')").fetchall()
        for r in rows:
            conn.execute("UPDATE generation_jobs SET status='FAILED', error='Interrupted by an application restart', finished_at=? WHERE id=?",
                         (now(), r["id"]))
            conn.execute("UPDATE campaigns SET status='FAILED', updated_at=? WHERE id=?", (now(), r["campaign_id"]))
        return len(rows)


def active_jobs() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM generation_jobs WHERE status IN ('QUEUED','RUNNING') ORDER BY created_at")]
