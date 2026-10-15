"""Environment-driven configuration. Secrets only come from env vars."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    domain: str
    api_key: str
    write_enabled: bool = False
    reveal_pii: bool = False
    max_body_chars: int = 4000
    max_pages: int = 3
    rate_limit_per_min: int = 40
    max_concurrency: int = 3
    max_retries: int = 4
    audit_log_path: str = "audit.log.jsonl"

    @property
    def base_url(self) -> str:
        return f"https://{self.domain}/api/v2"


class ConfigError(RuntimeError):
    pass


def load_config() -> Config:
    load_dotenv()
    domain = os.getenv("FRESHDESK_DOMAIN", "").strip()
    domain = domain.removeprefix("https://").removeprefix("http://").rstrip("/")
    api_key = os.getenv("FRESHDESK_API_KEY", "").strip()
    if not domain or not api_key:
        raise ConfigError("FRESHDESK_DOMAIN and FRESHDESK_API_KEY must be set (see .env.example)")
    if not domain.endswith(".freshdesk.com") and not _bool("ALLOW_CUSTOM_DOMAIN"):
        raise ConfigError("FRESHDESK_DOMAIN must end with .freshdesk.com")
    return Config(
        domain=domain,
        api_key=api_key,
        write_enabled=_bool("WRITE_ENABLED"),
        reveal_pii=_bool("REVEAL_PII"),
        max_body_chars=int(os.getenv("MAX_BODY_CHARS", "4000")),
        max_pages=int(os.getenv("MAX_PAGES", "3")),
        rate_limit_per_min=int(os.getenv("RATE_LIMIT_PER_MIN", "40")),
        max_concurrency=int(os.getenv("MAX_CONCURRENCY", "3")),
        max_retries=int(os.getenv("MAX_RETRIES", "4")),
        audit_log_path=os.getenv("AUDIT_LOG_PATH", "audit.log.jsonl"),
    )
