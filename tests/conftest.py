import sys
from pathlib import Path

import pytest



from app import llm  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SGA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SGA_LLM_PROVIDER", "mock")
    monkeypatch.setenv("SGA_MAX_RETRIES", "2")
    llm.set_provider_override(None)
    yield
    llm.set_provider_override(None)
