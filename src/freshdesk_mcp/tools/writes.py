from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from ..context import Context
from ..guardrails import (
    PRIORITY, STATUS, audited, require_write, validate_id, validate_note, validate_triage_update,
)

NOTE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
UPDATE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)

_APPROVAL = (
    "WRITE TOOL. Disabled unless the server sets WRITE_ENABLED=true. Defaults to dry_run=true "
    "(preview only). A real write needs dry_run=false AND confirm=true, where confirm must come "
    "from a human approval step - never set it yourself."
)


def register(mcp: FastMCP, ctx: Context) -> None:
    @mcp.tool(
        annotations=NOTE,
        description="Add an INTERNAL (private) note to a ticket. Never visible to the customer and never emails anyone. " + _APPROVAL,
    )
    @audited("add_private_note", ctx.audit, write=True)
    async def add_private_note(
        ticket_id: Annotated[int, Field(description="Freshdesk ticket id")],
        body: Annotated[str, Field(description="Internal note text (max 5000 chars)")],
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict:
        tid = validate_id(ticket_id, "ticket_id")
        text = validate_note(body)
        execute = require_write(ctx.config, dry_run, confirm)
        payload = {"body": text, "private": True}  # private is hard-coded: no customer-facing replies
        if not execute:
            return {"dry_run": True, "would_call": f"POST /tickets/{tid}/notes", "payload": payload}
        created = await ctx.client.post(f"/tickets/{tid}/notes", payload)
        return {"executed": True, "note_id": created.get("id"), "private": created.get("private")}

    @mcp.tool(
        annotations=UPDATE,
        description="Update ONLY priority, status and/or add tags on a ticket. Cannot assign, close, delete or reply. " + _APPROVAL,
    )
    @audited("update_ticket_triage", ctx.audit, write=True)
    async def update_ticket_triage(
        ticket_id: Annotated[int, Field(description="Freshdesk ticket id")],
        priority: Annotated[int | None, Field(description="1=Low, 2=Medium, 3=High, 4=Urgent")] = None,
        status: Annotated[int | None, Field(description="2=Open, 3=Pending, 4=Resolved (Closed not allowed)")] = None,
        add_tags: Annotated[list[str] | None, Field(description="Tags to add (existing tags are kept)")] = None,
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict:
        tid = validate_id(ticket_id, "ticket_id")
        changes = validate_triage_update(priority, status, add_tags)
        execute = require_write(ctx.config, dry_run, confirm)

        current = await ctx.client.get(f"/tickets/{tid}")
        before = {"priority": current.get("priority"), "status": current.get("status"),
                  "tags": current.get("tags", [])}
        payload = dict(changes)
        if "tags" in payload:  # Freshdesk replaces the tag list, so merge with existing
            payload["tags"] = sorted(set(before["tags"]) | set(payload["tags"]))
        after = {**before, **payload}
        diff = {
            "before": {**before, "priority_label": PRIORITY.get(before["priority"]),
                       "status_label": STATUS.get(before["status"])},
            "after": {**after, "priority_label": PRIORITY.get(after["priority"]),
                      "status_label": STATUS.get(after["status"])},
        }
        if not execute:
            return {"dry_run": True, "would_call": f"PUT /tickets/{tid}", "payload": payload, "diff": diff}
        await ctx.client.put(f"/tickets/{tid}", payload)
        return {"executed": True, "diff": diff}
