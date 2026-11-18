from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAMES_DIR = ROOT / "games"
ROLES = ("extract", "consolidate", "dialogue", "director", "audit", "vision")


class ConfigError(RuntimeError):
    pass


def load_env(path: Path | None = None) -> None:
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    models: dict[str, str]
    concurrency: int
    extra_body: dict = field(default_factory=dict)
    image_url: str = ""
    headers: dict = field(default_factory=dict)
    session_header: str = ""
    request_timeout: float = 0.0      # 0: scale with max_tokens
    read_timeout: float = 180.0
    progress_every: float = 30.0
    budget: float = 0.0               # USD per command run; 0: no cap
    price: tuple[float, float, float] | None = None  # USD per million (input, output, cached input)


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is not set; copy .env.example to .env and fill it in")
    return value


def get_settings() -> Settings:
    load_env()
    default_model = _require("N2G_MODEL_DEFAULT")
    extra = os.environ.get("N2G_LLM_EXTRA_BODY", "").strip()
    headers = os.environ.get("N2G_LLM_HEADERS", "").strip()
    return Settings(
        base_url=_require("N2G_LLM_BASE_URL").rstrip("/"),
        api_key=_require("N2G_LLM_API_KEY"),
        models={role: os.environ.get(f"N2G_MODEL_{role.upper()}", default_model) for role in ROLES},
        concurrency=int(os.environ.get("N2G_LLM_CONCURRENCY", "8")),
        extra_body=json.loads(extra) if extra else {},
        image_url=os.environ.get("N2G_IMAGE_URL", "").rstrip("/"),
        headers=json.loads(headers) if headers else {},
        session_header=os.environ.get("N2G_LLM_SESSION_HEADER", "").strip(),
        request_timeout=float(os.environ.get("N2G_LLM_REQUEST_TIMEOUT", "0") or 0),
        read_timeout=float(os.environ.get("N2G_LLM_READ_TIMEOUT", "180") or 180),
        progress_every=float(os.environ.get("N2G_PROGRESS_EVERY", "30") or 0),
        budget=float(os.environ.get("N2G_BUDGET_USD", "0") or 0),
        price=_price(os.environ.get("N2G_PRICE_PER_MTOK", "")),
    )


def _price(spec: str) -> tuple[float, float, float] | None:
    parts = [float(x) for x in spec.replace(" ", "").split(",") if x]
    if not parts:
        return None
    return (parts[0], parts[1] if len(parts) > 1 else parts[0], parts[2] if len(parts) > 2 else parts[0])


def book_dir(slug: str) -> Path:
    return GAMES_DIR / slug
