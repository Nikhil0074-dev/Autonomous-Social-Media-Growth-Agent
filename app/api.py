"""Local HTTP API. Run: uvicorn app.api:app --host 127.0.0.1 --port 8000"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import generator, intake, jobs, llm, services
from .config import get_settings
from .errors import ServiceError
from .schemas import (BrandUpdate, CampaignCreate, DecisionRequest, DraftRequest, PostUpdate, PublishConfirm,
                      RegenerateRequest, RenderRequest, SourceCreate)


@asynccontextmanager
async def lifespan(_: FastAPI):
    services.ensure_ready()
    jobs.recover_interrupted()
    yield


app = FastAPI(title="Social Growth Agent (local)", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501", "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"], allow_headers=["*"],
)


@app.exception_handler(ServiceError)
async def _service_error(_, exc: ServiceError):
    return JSONResponse(status_code=exc.status, content={"detail": exc.message})


@app.exception_handler(llm.LLMError)
async def _llm_error(_, exc: llm.LLMError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.get("/api/health")
def health(check_llm: bool = False):
    s = get_settings()
    out = {"status": "ok", "provider": s.llm_provider, "model": s.ollama_model if s.llm_provider == "ollama" else "mock-1"}
    if check_llm:
        out["llm"] = llm.get_provider().health()
    return out


# ---- campaigns ----
@app.post("/api/campaigns", status_code=201)
def create_campaign(body: CampaignCreate):
    return intake.create_campaign_with_sources(body)


@app.get("/api/campaigns")
def list_campaigns():
    return services.list_campaigns()


@app.get("/api/campaigns/{campaign_id}")
def get_campaign(campaign_id: str):
    return services.get_campaign(campaign_id)


@app.delete("/api/campaigns/{campaign_id}")
def delete_campaign(campaign_id: str):
    services.delete_campaign(campaign_id)
    return {"deleted": campaign_id}


@app.post("/api/campaigns/{campaign_id}/sources", status_code=201)
def add_source(campaign_id: str, body: SourceCreate):
    return {"source_id": services.add_source(campaign_id, body.title, body.content, body.url)}


@app.get("/api/campaigns/{campaign_id}/export")
def export_campaign(campaign_id: str):
    path = services.export_campaign(campaign_id)
    return FileResponse(path, media_type="application/zip", filename=path.name)


# ---- research / generation ----
@app.post("/api/research/brief")
def research_brief(body: dict):
    cid = body.get("campaign_id")
    if not cid:
        raise ServiceError("campaign_id is required", 422)
    return generator.create_brief_now(cid)


@app.post("/api/generation/drafts", status_code=202)
def generate_drafts(body: DraftRequest):
    if body.campaign_id:
        return jobs.submit_generation(body.campaign_id)
    if not body.topic:
        raise ServiceError("Provide campaign_id or topic", 422)
    fields = {k: v for k, v in body.model_dump().items() if v is not None and k != "campaign_id"}
    created = intake.create_campaign_with_sources(CampaignCreate(**fields))
    result = jobs.submit_generation(created["campaign_id"])
    result["warnings"] = created["warnings"]
    return result


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return jobs.get_job(job_id)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    return jobs.cancel_job(job_id)


# ---- posts ----
@app.get("/api/posts")
def list_posts(status: Optional[str] = None, campaign_id: Optional[str] = None):
    return services.list_posts(status, campaign_id)


@app.get("/api/posts/{post_id}")
def get_post(post_id: str):
    return services.get_post(post_id)


@app.patch("/api/posts/{post_id}")
def patch_post(post_id: str, body: PostUpdate):
    return services.update_post(post_id, body.model_dump(exclude_none=True))


@app.post("/api/posts/{post_id}/regenerate")
def regenerate(post_id: str, body: RegenerateRequest):
    return generator.regenerate_post(post_id, body.instruction, body.part)


@app.post("/api/posts/{post_id}/render")
def render(post_id: str, body: Optional[RenderRequest] = None):
    body = body or RenderRequest()
    overrides = {k: v for k, v in body.model_dump().items() if k != "template" and v is not None}
    return services.render_post(post_id, body.template, overrides)


@app.get("/api/posts/{post_id}/checks")
def checks(post_id: str):
    return services.run_checks(post_id)


@app.get("/api/posts/{post_id}/versions")
def versions(post_id: str):
    return services.list_versions(post_id)


@app.post("/api/posts/{post_id}/versions/{version_id}/restore")
def restore(post_id: str, version_id: str):
    return services.restore_version(post_id, version_id)


@app.post("/api/posts/{post_id}/approve")
def approve(post_id: str, body: Optional[DecisionRequest] = None):
    body = body or DecisionRequest()
    return services.approve_post(post_id, body.note, body.waive_image)


@app.post("/api/posts/{post_id}/reject")
def reject(post_id: str, body: Optional[DecisionRequest] = None):
    return services.reject_post(post_id, (body or DecisionRequest()).note)


@app.post("/api/posts/{post_id}/prepare")
def prepare(post_id: str, open_browser: bool = Query(False)):
    return services.prepare_post(post_id, copy=True, open_browser=open_browser)


@app.get("/api/posts/{post_id}/package")
def package(post_id: str):
    return services.get_package(post_id)


@app.post("/api/posts/{post_id}/confirm-published")
def confirm_published(post_id: str, body: Optional[PublishConfirm] = None):
    body = body or PublishConfirm()
    return services.confirm_published(post_id, body.url, body.note)


# ---- assets, brand, history ----
@app.get("/api/assets/{asset_id}")
def asset_meta(asset_id: str):
    return services.get_asset(asset_id)


@app.get("/api/assets/{asset_id}/file")
def asset_file(asset_id: str, preview: bool = False):
    a = services.get_asset(asset_id)
    path = services.abs_path(a["preview_path"] if preview else a["path"])
    if not path.is_file():
        raise ServiceError("The asset file is missing from disk", 404)
    return FileResponse(path)


@app.get("/api/brand")
def get_brand():
    return services.get_brand()


@app.put("/api/brand")
def put_brand(body: BrandUpdate):
    return services.update_brand(body.model_dump(exclude_none=True))


@app.get("/api/history")
def history(limit: int = 200):
    return services.history(limit)
