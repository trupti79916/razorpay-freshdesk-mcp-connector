from __future__ import annotations

import re
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..context import Context
from ..guardrails import GuardrailError, audited, validate_id
from ..models import slim_contact
from .tickets import READ

_EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?\d[\d \-]{6,18}\d")


def register(mcp: FastMCP, ctx: Context) -> None:
    @mcp.tool(annotations=READ)
    @audited("get_contact", ctx.audit)
    async def get_contact(contact_id: Annotated[int, Field(description="Freshdesk contact id")]) -> dict:
        """Get a customer contact by id. Email and phone are masked unless the server reveals PII."""
        cid = validate_id(contact_id, "contact_id")
        return {"contact": slim_contact(await ctx.client.get(f"/contacts/{cid}"), ctx.config)}

    @mcp.tool(annotations=READ)
    @audited("search_contacts", ctx.audit)
    async def search_contacts(
        email: Annotated[str | None, Field(description="Exact email address")] = None,
        phone: Annotated[str | None, Field(description="Exact phone or mobile number")] = None,
    ) -> dict:
        """Find contacts by exact email or phone (provide exactly one)."""
        if bool(email) == bool(phone):
            raise GuardrailError("invalid_input", "provide exactly one of email or phone")
        if email:
            if not _EMAIL.fullmatch(email):
                raise GuardrailError("invalid_input", "invalid email")
            data = await ctx.client.get("/contacts", email=email)
        else:
            if not _PHONE.fullmatch(phone or ""):
                raise GuardrailError("invalid_input", "invalid phone")
            data = await ctx.client.get("/contacts", phone=phone)
        return {"contacts": [slim_contact(c, ctx.config) for c in data[:10]]}
