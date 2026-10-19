"""SQLite storage (standard library only)."""
from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
  id TEXT PRIMARY KEY, topic TEXT NOT NULL, audience TEXT, content_goal TEXT, tone TEXT,
  angle TEXT DEFAULT '', source_notes TEXT DEFAULT '', source_urls TEXT DEFAULT '[]',
  platforms TEXT NOT NULL, brand_profile_id TEXT, template TEXT DEFAULT 'technical_explainer',
  include_graphic INTEGER DEFAULT 1, status TEXT NOT NULL, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS source_documents (
  id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  kind TEXT, title TEXT, url TEXT, content TEXT, retrieved_at TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS content_briefs (
  id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  brief_json TEXT NOT NULL, model TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS posts (
  id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  platform TEXT NOT NULL, title TEXT DEFAULT '', hook TEXT DEFAULT '', body TEXT DEFAULT '',
  cta TEXT DEFAULT '', hashtags TEXT DEFAULT '[]', thread_parts TEXT DEFAULT '[]',
  revision_number INTEGER DEFAULT 1, status TEXT NOT NULL, approval_status TEXT DEFAULT 'pending',
  approved_hash TEXT, image_path TEXT, image_sha TEXT, content_hash TEXT,
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS post_versions (
  id TEXT PRIMARY KEY, post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  revision_number INTEGER, snapshot TEXT NOT NULL, reason TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS brand_profiles (
  id TEXT PRIMARY KEY, name TEXT, brand_name TEXT, primary_color TEXT, accent_color TEXT,
  text_color TEXT, footer TEXT, font_path TEXT DEFAULT '', logo_path TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS generated_assets (
  id TEXT PRIMARY KEY, post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  path TEXT, preview_path TEXT, width INTEGER, height INTEGER, format TEXT, sha256 TEXT,
  template TEXT, metadata TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  revision_number INTEGER, content_hash TEXT, decision TEXT, note TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS publishing_events (
  id TEXT PRIMARY KEY, post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  platform TEXT, event_type TEXT, url TEXT, note TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS generation_jobs (
  id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  kind TEXT DEFAULT 'generate', status TEXT NOT NULL, progress REAL DEFAULT 0, message TEXT DEFAULT '',
  error TEXT DEFAULT '', model TEXT DEFAULT '', cancel_requested INTEGER DEFAULT 0,
  metrics TEXT DEFAULT '{}', created_at TEXT, started_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, action TEXT, entity_type TEXT, entity_id TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_posts_campaign ON posts(campaign_id);
CREATE INDEX IF NOT EXISTS idx_jobs_campaign ON generation_jobs(campaign_id);
"""

_ready: set[str] = set()
_lock = threading.Lock()

DEFAULT_BRAND_ID = "brand_default"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def ensure_ready() -> None:
    s = get_settings()
    key = str(s.db_path)
    if key in _ready and s.db_path.exists():
        return
    with _lock:
        s.data_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("campaigns", "exports"):
            (s.data_dir / sub).mkdir(exist_ok=True)
        conn = sqlite3.connect(s.db_path, timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            if conn.execute("SELECT 1 FROM brand_profiles LIMIT 1").fetchone() is None:
                conn.execute(
                    "INSERT INTO brand_profiles (id,name,brand_name,primary_color,accent_color,text_color,footer)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (DEFAULT_BRAND_ID, "Default", s.brand_name, "#101827", "#38BDF8", "#FFFFFF", "TECH NOTES"),
                )
            conn.commit()
        finally:
            conn.close()
        _ready.add(key)


@contextmanager
def connect():
    """Open a connection; commit on success, roll back on error."""
    ensure_ready()
    conn = sqlite3.connect(get_settings().db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def audit(conn: sqlite3.Connection, action: str, entity_type: str, entity_id: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_events (ts,action,entity_type,entity_id,detail) VALUES (?,?,?,?,?)",
        (now(), action, entity_type, entity_id, detail[:500]),
    )
