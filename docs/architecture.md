# Architecture

```
Dashboard (Streamlit) --\
                         >-- services.py --- SQLite (data/studio.db) + files (data/campaigns/...)
HTTP API (FastAPI) -----/        |
                                 +-- jobs.py (1 worker thread) -> generator.py -> llm.py (Ollama | mock)
                                 +-- renderer.py (Pillow)      -> PNG/JPEG + preview
                                 +-- quality.py, similarity.py, content.py, research.py, publishing.py
```
Both front ends call the same `services.py`, so rules are enforced in one place.

## States
Campaign: CREATED -> RESEARCHING -> GENERATING -> RENDERING -> REVIEW_REQUIRED (or FAILED / CANCELLED)
Post: REVIEW_REQUIRED -> APPROVED -> READY_TO_PUBLISH -> PUBLISHED (or REJECTED).
PUBLISHED is only set by the user's confirmation. Copying text or opening a composer is not proof.

## Approval rule
`review_hash` = SHA-256 of title, hook, body, cta, hashtags, thread parts and image hash.
Approval stores that hash. Preparing or confirming re-checks it. Any edit or re-render changes the hash,
resets the post to REVIEW_REQUIRED and writes an `approval_invalidated` audit event.

## Pipeline
brief (1 model call) -> one draft per platform (resumable) -> banner text (1 call) -> render per post.
Each model reply is parsed as JSON, validated by Pydantic, and retried up to SGA_MAX_RETRIES times with the
error fed back. Each finished step is saved immediately. Render failures never delete a draft.

## Security
- Source text is wrapped in tags and the system prompt says to treat it as data (reduces, does not remove, prompt injection).
- URL fetching blocks private/loopback addresses (also on redirects). Files: type, size and binary checks.
- Stored asset paths are relative and resolved inside the data folder. Text is drawn as pixels, never inserted into HTML.
- API binds to 127.0.0.1 and allows CORS only from local dashboard origins.
