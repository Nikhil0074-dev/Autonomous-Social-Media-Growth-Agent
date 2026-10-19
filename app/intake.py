"""Create a campaign together with its source material (files and URLs)."""
from __future__ import annotations

from typing import Optional

from . import research, services
from .config import get_settings
from .errors import ServiceError
from .schemas import CampaignCreate


def create_campaign_with_sources(data: CampaignCreate, files: Optional[list[tuple[str, bytes]]] = None,
                                 fetch_urls: bool = True) -> dict:
    """Returns {'campaign_id': str, 'warnings': [str]}. Bad files/URLs become warnings, not crashes."""
    warnings: list[str] = []
    prepared = []
    for name, raw in files or []:
        try:
            prepared.append((research.read_text_file(name, raw), name))
        except ServiceError as exc:
            warnings.append(exc.message)
    cid = services.create_campaign(data)
    for doc, name in prepared:
        services.add_source(cid, doc["title"], doc["content"], kind="file")
    if data.source_urls:
        if not (fetch_urls and get_settings().allow_url_fetch):
            warnings.append("URLs were saved but not downloaded (URL fetching is off).")
        else:
            for url in data.source_urls:
                try:
                    page = research.fetch_url(url)
                    services.add_source(cid, page["title"], page["content"], url=url, kind="url")
                except ServiceError as exc:
                    warnings.append(f"{url}: {exc.message}")
    return {"campaign_id": cid, "warnings": warnings}
