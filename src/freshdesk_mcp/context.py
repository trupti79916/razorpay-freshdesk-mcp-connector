from __future__ import annotations

from dataclasses import dataclass, field

from .client import FreshdeskClient
from .config import Config
from .guardrails import AuditLog


@dataclass
class Context:
    config: Config
    client: FreshdeskClient
    audit: AuditLog
    identity: dict = field(default_factory=dict)  # agent identity from /agents/me
