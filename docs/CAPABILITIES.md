# What an agent can and can't do with this connector

**Problem it solves:** Indian SMB merchants using Razorpay get many payment-related support tickets ("money deducted, order not confirmed", "refund not received", "settlement delayed"). Urgent money tickets get buried. This connector lets an agent safely read Freshdesk and make a few low-risk, human-approved changes so urgent tickets surface first.

## Can

| Area | Capability | Tool |
|---|---|---|
| Read | List tickets, filter by status/priority, newest first | `list_tickets` |
| Read | Full ticket with description and recent conversation thread | `get_ticket` |
| Read | Search with Freshdesk query syntax (`status:2 AND tag:'refund'`) | `search_tickets` |
| Read | Replies and notes on a ticket | `list_ticket_conversations` |
| Read | Look up a customer by id, exact email or phone | `get_contact`, `search_contacts` |
| Read | Valid status/priority codes and custom fields | `list_ticket_fields` |
| Read | Connector health, rate-limit budget, write mode | `get_connector_status` |
| Write (opt-in) | Add an **internal** private note | `add_private_note` |
| Write (opt-in) | Raise/change priority, change status (not Closed), add tags | `update_ticket_triage` |

The demo Triage Copilot builds on these to classify open tickets (payment failure, refund, settlement, shipping, invoice, spam, other), rate urgency and propose a note, tags and priority raise.

## Can't (by design)

- Reply to customers, send email, or create public notes (notes are hard-coded `private=true`).
- Delete, merge, close, spam-mark or reassign tickets; change agents, groups, SLAs or any other field.
- Create tickets or modify contacts, companies or account settings.
- Touch Razorpay data (payments, refunds, settlements). Order and payment IDs in tickets are text only.
- Self-approve a write. A real write needs `WRITE_ENABLED=true` on the server, `dry_run=false` and `confirm=true`. The UI sends `confirm=true` only after a human clicks Approve, and the LLM is never given the write tools.

## Limits

- **Rate limits:** a client-side token bucket (default 40 req/min), max 3 concurrent requests, `Retry-After` honoured on HTTP 429, exponential backoff with jitter on 5xx for GETs. After 4 retries the tool returns `rate_limited` with `retry_after_seconds`. POSTs are never auto-retried on failure.
- **Pagination:** 30 items per page; search is capped at 10 pages (Freshdesk limit). Search indexing may lag a few seconds behind new tickets.
- **Defaults:** `list_tickets` covers the last 90 days unless `updated_since` is set.
- **Output size:** bodies truncated to 4,000 chars; attachments are not downloaded.
- **PII:** emails and phones masked; card numbers, PAN, Aadhaar redacted from text unless the operator sets `REVEAL_PII=true`.
- **Search grammar:** only whitelisted fields (status, priority, type, tag, agent_id, group_id, requester_id, company_id, created_at, updated_at, due_by, source).

## Security notes

- **Auth:** Freshdesk API key (Basic auth) from environment variables only; never logged or returned by tools. Startup fails fast on bad credentials.
- **Prompt injection:** ticket text is attacker-controlled. It is wrapped in `<untrusted_ticket_content>` tags, and the model is told not to follow it. This is defence in depth only. The real protection is structural: the model gets read-only tools, write proposals are built by deterministic code from a validated schema, and the server enforces the write gate.
- **Audit:** every tool call is appended to `audit.log.jsonl` (timestamp, tool, redacted args, outcome, latency).
- **Local model:** with a local Ollama model, ticket data never leaves the machine. `:cloud` models do send data off-machine.
- **Scope of your key:** the connector acts with the permissions of the API key's agent. Use a dedicated least-privilege agent in production.
