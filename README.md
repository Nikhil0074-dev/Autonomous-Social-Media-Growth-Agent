# Autonomous Social Media Growth Agent (Zero-API)

A local app that turns a topic (plus your notes) into LinkedIn / X / short posts and a branded graphic,
lets you edit and approve them, and prepares publishing. **You press Post yourself.** No paid APIs.

## What works (tested)
- Topic + notes + .md/.txt files + optional URLs -> content brief -> drafts -> graphic
- Drafts and graphics via a local model (Ollama) or a built-in fake model for testing
- Editable drafts, revision history and restore, rewrite-with-instruction
- 3 graphic templates, text fitting, JPEG/PNG, any size 200-4000 px, brand colours/font/logo
- Quality checks (limits, missing image, duplicates, unsourced numbers) and human approval
- Approval is tied to the exact text AND image. Any change cancels the approval
- Clipboard + composer link + "I published it" confirmation, audit history, zip export
- Background jobs with progress, cancel, resume after failure, duplicate-job protection
- Optional local HTTP API and Chrome extension

## Quick start
1. Install Python 3.10+ and [Ollama](https://ollama.com). Then: `ollama pull llama3.2`
2. Run `./run_ui.sh` (Mac/Linux) or `run_ui.bat` (Windows). It installs everything and opens the dashboard.
3. Try without a model first: in `.env` set `SGA_LLM_PROVIDER=mock` (output is generic filler).

Manual setup:
```
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python -m app.cli check                  # tests the model connection
streamlit run ui/streamlit_app.py        # dashboard
python run_api.py                        # optional API on 127.0.0.1:8000 (docs at /docs)
pytest                                   # run the tests
```
Run **either** the dashboard or the API alone if you can. If you run both, restarting one marks the other's
running jobs as failed (it cannot tell them apart). Finished work is never lost.

## How to use
1. **New campaign**: enter a topic, audience, your notes (important!), pick platforms, click Create.
2. **Review**: wait for the progress bar. Edit text, check the quality list, render another template if wanted.
3. **Approve this revision**, then **Prepare to publish**. Text is copied. Open the composer, paste, attach the image, press Post.
4. Click **I published it**. Only then is the post marked PUBLISHED.

## Honest limits
- Small local models write generic text and can invent facts. Give real notes. Check every claim yourself.
  The checker only flags numbers that are not in your sources. It cannot verify truth.
- Composer links and the extension's selectors can break when LinkedIn/X change. Clipboard always works.
- "Duplicate" checks compare only against your own saved posts.
- X length counting is approximate (links = 23 characters, emoji not weighted).
- The model call cannot be interrupted in the middle. Cancel takes effect after the current model step.
- Fetching URLs: many sites block bots or need JavaScript. Paste the text as notes instead.
- Possible rule changes: check each platform's current automation rules before using the extension.
- No login, no multi-user. Keep the API on 127.0.0.1.

