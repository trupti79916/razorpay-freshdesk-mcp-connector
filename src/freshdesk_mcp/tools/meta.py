from __future__ import annotations

import time

from mcp.server.fastmcp import FastMCP

from .. import __version__
from ..context import Context
from ..guardrails import PRIORITY, STATUS, audited
from .tickets import READ

_FIELDS_TTL = 600.0


def register(mcp: FastMCP, ctx: Context) -> None:
    cache: dict = {"at": 0.0, "value": None}

    @mcp.tool(annotations=READ)
    @audited("list_ticket_fields", ctx.audit)
    async def list_ticket_fields() -> dict:
        """List valid status/priority codes and any custom ticket fields, so you only use valid values."""
        if cache["value"] is None or time.monotonic() - cache["at"] > _FIELDS_TTL:
            fields = await ctx.client.get("/ticket_fields")
            cache["value"] = [
                {"name": f.get("name"), "label": f.get("label"), "type": f.get("type"),
                 "required": f.get("required_for_agents"), "choices": f.get("choices") if isinstance(f.get("choices"), (list, dict)) and f.get("type") != "default_agent" else None}
                for f in fields
            ]
            cache["at"] = time.monotonic()
        return {"status_codes": STATUS, "priority_codes": PRIORITY, "fields": cache["value"]}

    @mcp.tool(annotations=READ)
    @audited("get_connector_status", ctx.audit)
    async def get_connector_status() -> dict:
        """Report connector health: auth identity, write mode, PII mode and remaining rate-limit budget."""
        return {
            "version": __version__,
            "freshdesk_domain": ctx.config.domain,
            "authenticated_as": {k: ctx.identity.get(k) for k in ("id", "available")} | {
                "email_masked": (ctx.identity.get("contact") or {}).get("email", "")[:1] + "***"
            },
            "write_enabled": ctx.config.write_enabled,
            "pii_revealed": ctx.config.reveal_pii,
            "rate_limit": ctx.client.limiter.snapshot(),
            "retries_performed": ctx.client.retries_performed,
        }
