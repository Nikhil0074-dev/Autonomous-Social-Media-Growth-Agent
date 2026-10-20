# Evaluation guide

The app records timing for every generation job (`generation_jobs.metrics`: brief, each draft, each render, total)
and every action in `audit_events`. Use them for the study in the proposal.

## Suggested protocol
1. Pick 10+ topics. Same audience, platforms and rubric for both conditions.
2. Manual baseline: write posts and make a graphic with your normal tools. Time each task.
3. Assisted: use the app. Time from "Create" to "Approve", including your edits.
4. Score each result 1-5 on relevance, accuracy, readability, hook, platform fit, repetition, usefulness.
   Use at least 2 reviewers who do not know which condition produced which post.
5. Same participants in both conditions: use a paired test (Wilcoxon signed-rank if n is small)
   and alternate the order to control learning effects.

## Automated checks included
`pytest` covers: generation, persistence, approval invalidation, bad JSON, retries, model down, partial failure and
resume, cancel, duplicate jobs, renderer validation, text fitting, path escape, export, API, UI flows, and the
Ollama client (against a fake local server).
Not covered: real model quality, real LinkedIn/X pages, the Chrome extension.
