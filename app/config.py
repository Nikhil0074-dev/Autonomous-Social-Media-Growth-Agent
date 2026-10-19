"""Settings, read from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_file = PROJECT_ROOT / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split("#", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


_load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    llm_provider: str
    ollama_url: str
    ollama_model: str
    llm_timeout: int
    max_retries: int
    brand_name: str
    font_path: str
    allow_url_fetch: bool

    @property
    def db_path(self) -> Path:
        return self.data_dir / "studio.db"


def get_settings() -> Settings:
    """Read settings fresh each time so tests can change the environment."""
    data_dir = os.environ.get("SGA_DATA_DIR", "").strip()
    return Settings(
        data_dir=Path(data_dir).expanduser().resolve() if data_dir else (PROJECT_ROOT / "data").resolve(),
        llm_provider=os.environ.get("SGA_LLM_PROVIDER", "ollama").strip().lower() or "ollama",
        ollama_url=os.environ.get("SGA_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=os.environ.get("SGA_OLLAMA_MODEL", "llama3.2").strip() or "llama3.2",
        llm_timeout=_int("SGA_LLM_TIMEOUT", 300),
        max_retries=max(0, _int("SGA_MAX_RETRIES", 2)),
        brand_name=os.environ.get("SGA_BRAND_NAME", "My Brand").strip() or "My Brand",
        font_path=os.environ.get("SGA_FONT_PATH", "").strip(),
        allow_url_fetch=os.environ.get("SGA_ALLOW_URL_FETCH", "1").strip().lower() not in ("0", "false", "no"),
    )
