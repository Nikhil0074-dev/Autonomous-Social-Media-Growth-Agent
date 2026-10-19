"""Command line helper.  Examples:
    python -m app.cli check            # test the model connection
    python -m app.cli demo             # generate a demo campaign with the mock model
    python -m app.cli generate "Docker volumes explained" --platforms linkedin x
"""
from __future__ import annotations

import argparse
import os
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("demo")
    g = sub.add_parser("generate")
    g.add_argument("topic")
    g.add_argument("--platforms", nargs="+", default=["linkedin", "x"])
    g.add_argument("--audience", default="General professional audience")
    args = ap.parse_args(argv)

    if args.cmd == "demo":
        os.environ["SGA_LLM_PROVIDER"] = "mock"
    from . import jobs, llm, services
    from .schemas import CampaignCreate

    if args.cmd == "check":
        h = llm.get_provider().health()
        print(("OK: " if h["ok"] else "PROBLEM: ") + h["detail"])
        return 0 if h["ok"] else 1

    topic = "How S3-compatible object storage works with MinIO" if args.cmd == "demo" else args.topic
    platforms = ["linkedin", "x"] if args.cmd == "demo" else args.platforms
    cid = services.create_campaign(CampaignCreate(topic=topic, platforms=platforms,
                                                  audience=getattr(args, "audience", "General professional audience")))
    job = jobs.get_job(jobs.submit_generation(cid, run_async=False)["job_id"])
    print(f"Job {job['status']}: {job['message']} {job['error']}")
    if job["status"] != "DONE":
        return 1
    for p in services.get_campaign(cid)["posts"]:
        print(f"\n--- {p['platform']} ({p['id']}) ---\n{services.build_package(p)['text']}\nimage: {p['image_abs']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
