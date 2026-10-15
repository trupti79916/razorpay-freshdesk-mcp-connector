# MCP tool spec

Machine-readable version: [`tool_spec.json`](tool_spec.json), generated from the code with `uv run python scripts/gen_tool_spec.py`.

Transport: stdio (default) or streamable HTTP (`--transport streamable-http`). Every tool returns JSON. Errors are returned (not thrown) as:

```json
{"error": {"code": "rate_limited", "message": "...", "retry_after_seconds": 7}}
```

Error codes: `invalid_input`, `invalid_query`, `writes_disabled`, `confirmation_required`, `forbidden`, `rate_limited`, `freshdesk_<http status>`, `internal_error`.

All free text from tickets is returned as `{"content": "<untrusted_ticket_content>...</untrusted_ticket_content>", "truncated": bool}`.

## Read tools (`readOnlyHint=true`)

| Tool | Inputs | Output | Freshdesk call |
|---|---|---|---|
| `list_tickets` | `status?` (2-5), `priority?` (1-4), `updated_since?` (YYYY-MM-DD), `page=1`, `per_page<=30` | `{tickets[], page, has_more}` | `GET /tickets`, or `GET /search/tickets` when filtered |
| `get_ticket` | `ticket_id`, `include_conversations=false` | `{ticket}` with description (and conversations) | `GET /tickets/{id}?include=requester[,conversations]` |
| `search_tickets` | `query`, `page=1` | `{tickets[], total, page, has_more}` | `GET /search/tickets` |
| `list_ticket_conversations` | `ticket_id`, `page=1` | `{conversations[]}` | `GET /tickets/{id}/conversations` |
| `get_contact` | `contact_id` | `{contact}` (masked) | `GET /contacts/{id}` |
| `search_contacts` | exactly one of `email`, `phone` | `{contacts[]}` (max 10) | `GET /contacts?email=` / `?phone=` |
| `list_ticket_fields` | none | `{status_codes, priority_codes, fields[]}` (cached 10 min) | `GET /ticket_fields` |
| `get_connector_status` | none | version, auth identity, write mode, rate-limit budget | none (local state) |

## Write tools (`readOnlyHint=false`, `destructiveHint=false`)

Disabled unless the server runs with `WRITE_ENABLED=true`. Both default to `dry_run=true`; a real write needs `dry_run=false` **and** `confirm=true` (human approval).

| Tool | Inputs | Behaviour | Freshdesk call |
|---|---|---|---|
| `add_private_note` | `ticket_id`, `body` (<=5000), `dry_run=true`, `confirm=false` | Adds an internal note; `private=true` is hard-coded | `POST /tickets/{id}/notes` |
| `update_ticket_triage` | `ticket_id`, `priority?` (1-4), `status?` (2-4), `add_tags?` (<=10), `dry_run=true`, `confirm=false` | Updates only whitelisted fields; tags are merged with existing; dry run returns a before/after diff | `PUT /tickets/{id}` |

## Rate-limit behaviour (all tools)

Token bucket (`RATE_LIMIT_PER_MIN`), concurrency cap (`MAX_CONCURRENCY`), `X-Ratelimit-Remaining` tracking, `Retry-After` on 429, backoff + jitter on 5xx (GET only), max `MAX_RETRIES` retries.
