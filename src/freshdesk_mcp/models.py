"""Slim, LLM-friendly projections of Freshdesk objects (+ PII scrubbing)."""
from __future__ import annotations

from typing import Any

from .config import Config
from .guardrails import PRIORITY, STATUS, mask_email, mask_phone, wrap_untrusted


def slim_requester(r: dict | None, cfg: Config) -> dict | None:
    if not r:
        return None
    reveal = cfg.reveal_pii
    return {
        "id": r.get("id"),
        "name": r.get("name"),
        "email": r.get("email") if reveal else mask_email(r.get("email")),
        "phone": (r.get("mobile") or r.get("phone")) if reveal else mask_phone(r.get("mobile") or r.get("phone")),
    }


def slim_conversation(c: dict, cfg: Config) -> dict:
    return {
        "id": c.get("id"),
        "from": "customer" if c.get("incoming") else "agent",
        "private": bool(c.get("private")),
        "created_at": c.get("created_at"),
        "body": wrap_untrusted(c.get("body_text") or c.get("body"), cfg.max_body_chars, cfg.reveal_pii),
    }


def slim_ticket(t: dict, cfg: Config, *, with_body: bool = False) -> dict:
    out: dict[str, Any] = {
        "id": t.get("id"),
        "subject": wrap_untrusted(t.get("subject"), 300, cfg.reveal_pii)["content"] if t.get("subject") else None,
        "status": t.get("status"),
        "status_label": STATUS.get(t.get("status")),
        "priority": t.get("priority"),
        "priority_label": PRIORITY.get(t.get("priority")),
        "tags": t.get("tags", []),
        "type": t.get("type"),
        "requester_id": t.get("requester_id"),
        "responder_id": t.get("responder_id"),
        "created_at": t.get("created_at"),
        "updated_at": t.get("updated_at"),
        "due_by": t.get("due_by"),
        "first_response_due_by": t.get("fr_due_by"),
        "is_escalated": t.get("is_escalated"),
    }
    if t.get("requester"):
        out["requester"] = slim_requester(t["requester"], cfg)
    if with_body:
        out["description"] = wrap_untrusted(
            t.get("description_text") or t.get("description"), cfg.max_body_chars, cfg.reveal_pii
        )
    if "conversations" in t:
        out["conversations"] = [slim_conversation(c, cfg) for c in t["conversations"]]
        out["attachments_count"] = sum(len(c.get("attachments") or []) for c in t["conversations"])
    return out


def slim_contact(c: dict, cfg: Config) -> dict:
    return {
        "id": c.get("id"),
        "name": c.get("name"),
        "email": c.get("email") if cfg.reveal_pii else mask_email(c.get("email")),
        "phone": (c.get("mobile") or c.get("phone")) if cfg.reveal_pii else mask_phone(c.get("mobile") or c.get("phone")),
        "company_id": c.get("company_id"),
        "tags": c.get("tags", []),
        "created_at": c.get("created_at"),
    }
