"""Pydantic models: API requests and the structured output we expect from the LLM."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

SUPPORTED_PLATFORMS = ("linkedin", "x", "short")
TEMPLATE_NAMES = ("technical_explainer", "professional_insight", "quote_card")


def _as_str_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)):
        raise ValueError("must be a list")
    return [str(x).strip() for x in v if x is not None and str(x).strip()]


class CampaignCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    topic: str = Field(min_length=3, max_length=500)
    audience: str = "General professional audience"
    content_goal: str = "educate"
    tone: str = "professional"
    angle: str = ""
    source_notes: str = ""
    source_urls: list[str] = []
    platforms: list[str] = ["linkedin", "x"]
    template: str = "technical_explainer"
    include_graphic: bool = True

    @field_validator("topic", "audience", "content_goal", "tone", "angle")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("topic")
    @classmethod
    def _topic_not_blank(cls, v: str) -> str:
        if len(v) < 3:
            raise ValueError("topic is too short")
        return v

    @field_validator("platforms")
    @classmethod
    def _platforms(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for p in v:
            p = p.strip().lower()
            if p not in SUPPORTED_PLATFORMS:
                raise ValueError(f"unsupported platform '{p}'. Use: {', '.join(SUPPORTED_PLATFORMS)}")
            if p not in out:
                out.append(p)
        if not out:
            raise ValueError("choose at least one platform")
        return out

    @field_validator("template")
    @classmethod
    def _template(cls, v: str) -> str:
        if v not in TEMPLATE_NAMES:
            raise ValueError(f"unknown template. Use: {', '.join(TEMPLATE_NAMES)}")
        return v

    @field_validator("source_urls")
    @classmethod
    def _urls(cls, v: list[str]) -> list[str]:
        out = []
        for u in v:
            u = u.strip()
            if not u:
                continue
            if not u.lower().startswith(("http://", "https://")):
                raise ValueError(f"URL must start with http:// or https://: {u}")
            out.append(u)
        return out


# ---- LLM output models -------------------------------------------------

class BriefModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    main_message: str = Field(min_length=1)
    audience: str = ""
    key_points: list[str]
    example: str = ""
    angles: list[str] = []
    keywords: list[str] = []
    claims_to_verify: list[str] = []

    @field_validator("key_points", "angles", "keywords", "claims_to_verify", mode="before")
    @classmethod
    def _lists(cls, v):
        return _as_str_list(v)

    @field_validator("audience", "example", mode="before")
    @classmethod
    def _text(cls, v):
        return "" if v is None else str(v)

    @field_validator("key_points")
    @classmethod
    def _need_points(cls, v):
        if not v:
            raise ValueError("key_points must not be empty")
        return v


class ThreadPart(BaseModel):
    model_config = ConfigDict(extra="ignore")
    position: int = 0
    purpose: str = ""
    text: str = Field(min_length=1)

    @field_validator("position", mode="before")
    @classmethod
    def _pos(cls, v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    @field_validator("purpose", mode="before")
    @classmethod
    def _purpose(cls, v):
        return "" if v is None else str(v)


class DraftModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = ""
    hook: str = ""
    body: str = ""
    cta: str = ""
    hashtags: list[str] = []
    thread_parts: list[ThreadPart] = []

    @field_validator("title", "hook", "body", "cta", mode="before")
    @classmethod
    def _text(cls, v):
        if v is None:
            return ""
        if isinstance(v, list):
            return "\n\n".join(str(x) for x in v)
        return str(v)

    @field_validator("hashtags", mode="before")
    @classmethod
    def _tags(cls, v):
        if isinstance(v, str):
            v = v.replace(",", " ").split()
        return _as_str_list(v)

    @field_validator("thread_parts", mode="before")
    @classmethod
    def _parts(cls, v):
        if v is None:
            return []
        if not isinstance(v, list):
            raise ValueError("thread_parts must be a list")
        return [{"text": x} if isinstance(x, str) else x for x in v]


class VisualCopy(BaseModel):
    model_config = ConfigDict(extra="ignore")
    category: str = ""
    headline: str = Field(min_length=1)
    subtitle: str = ""

    @field_validator("category", "subtitle", mode="before")
    @classmethod
    def _text(cls, v):
        return "" if v is None else str(v)


# ---- API request models ----------------------------------------------------

class SourceCreate(BaseModel):
    title: str = "Pasted notes"
    content: str = Field(min_length=1, max_length=500_000)
    url: Optional[str] = None


class PostUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Optional[str] = None
    hook: Optional[str] = None
    body: Optional[str] = None
    cta: Optional[str] = None
    hashtags: Optional[list[str]] = None
    thread_parts: Optional[list[dict]] = None


class RegenerateRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=500)
    part: str = "all"


class RenderRequest(BaseModel):
    template: Optional[str] = None
    category: Optional[str] = None
    headline: Optional[str] = None
    subtitle: Optional[str] = None
    footer: Optional[str] = None
    width: int = 1200
    height: int = 630
    format: str = "png"


class DecisionRequest(BaseModel):
    note: str = ""
    waive_image: bool = False


class PublishConfirm(BaseModel):
    url: str = ""
    note: str = ""


class DraftRequest(BaseModel):
    """Either an existing campaign_id, or the fields for a new campaign."""
    model_config = ConfigDict(extra="ignore")
    campaign_id: Optional[str] = None
    topic: Optional[str] = None
    audience: Optional[str] = None
    content_goal: Optional[str] = None
    tone: Optional[str] = None
    angle: Optional[str] = None
    source_notes: Optional[str] = None
    source_urls: Optional[list[str]] = None
    platforms: Optional[list[str]] = None
    template: Optional[str] = None
    include_graphic: Optional[bool] = None


class BrandUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brand_name: Optional[str] = None
    primary_color: Optional[str] = None
    accent_color: Optional[str] = None
    text_color: Optional[str] = None
    footer: Optional[str] = None
    font_path: Optional[str] = None
    logo_path: Optional[str] = None
