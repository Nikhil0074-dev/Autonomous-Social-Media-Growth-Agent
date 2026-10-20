def make_campaign(topic="How S3-compatible object storage works with MinIO", platforms=("linkedin", "x"), **kw):
    from app import jobs
    from app.schemas import CampaignCreate
    from app import services
    cid = services.create_campaign(CampaignCreate(topic=topic, platforms=list(platforms), **kw))
    job = jobs.submit_generation(cid, run_async=False)
    return cid, job["job_id"]
