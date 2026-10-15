# Freshdesk MCP Connector + Support Triage Copilot

A private [MCP](https://modelcontextprotocol.io) connector for Freshdesk, built for an Agent Studio style agent: API-key auth, list/get/search primitives, guarded writes, rate-limit handling, a generated tool spec and a capabilities doc. A Streamlit UI and a local Ollama-powered **Support Triage Copilot** demonstrate it on a real problem: surfacing urgent payment tickets for Razorpay merchants.

- What the agent can and can't do: [docs/CAPABILITIES.md](docs/CAPABILITIES.md)
- Tool spec: [docs/TOOL_SPEC.md](docs/TOOL_SPEC.md), [docs/tool_spec.json](docs/tool_spec.json)
- Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Setup

1. Install [uv](https://docs.astral.sh/uv/) and Python 3.11+, then `uv sync --extra dev`.
2. `cp .env.example .env` and set `FRESHDESK_DOMAIN` (e.g. `acme.freshdesk.com`) and `FRESHDESK_API_KEY` (Freshdesk: Profile Settings -> API Key).
3. Pull a tool-capable local model: `ollama pull gemma4` (or `qwen3`, `llama3.1`) and set `OLLAMA_MODEL`.
4. Seed demo data: `uv run python scripts/seed_tickets.py` (15 Razorpay-themed tickets; `--bulk 40` adds filler, `--dry-run` previews).
5. Run the UI: `uv run streamlit run app/streamlit_app.py`.

## Run the MCP server on its own

```bash
uv run freshdesk-mcp                              # stdio
uv run freshdesk-mcp --transport streamable-http  # HTTP
npx @modelcontextprotocol/inspector uv run freshdesk-mcp   # explore tools interactively
```

Register in Claude Desktop / Cursor (`mcp.json`):

```json
{"mcpServers": {"freshdesk": {
  "command": "uv", "args": ["run", "--directory", "/path/to/razorpay-freshdesk-mcp-connector", "freshdesk-mcp"],
  "env": {"FRESHDESK_DOMAIN": "acme.freshdesk.com", "FRESHDESK_API_KEY": "..."}}}}
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `FRESHDESK_DOMAIN`, `FRESHDESK_API_KEY` | required | Auth (Basic, key as username) |
| `WRITE_ENABLED` | `false` | Allow private notes / priority / status / tag updates |
| `REVEAL_PII` | `false` | Disable masking of emails, phones, card/PAN text |
| `RATE_LIMIT_PER_MIN` | 40 | Client-side token bucket (keep below your plan limit) |
| `MAX_CONCURRENCY`, `MAX_RETRIES` | 3, 4 | Concurrency cap and retry budget |
| `MAX_BODY_CHARS` | 4000 | Truncate ticket bodies |
| `OLLAMA_HOST`, `OLLAMA_MODEL` | localhost:11434, gemma4:latest | Agent layer only |

## Tests

```bash
uv run pytest                                 # unit tests with mocked Freshdesk
FRESHDESK_LIVE_TEST=1 uv run pytest -m live   # opt-in smoke test against your trial account
```

## Productionising on Agent Studio

- Register the streamable-HTTP endpoint (or stdio command) as a custom tool server; the tool spec is in `docs/tool_spec.json`.
- Store one API key per merchant tenant in Agent Studio's secret store and inject it as env/headers per session; never in prompts.
- For tools with OAuth (e.g. Zoho Inventory), replace `client.py`'s auth with an authorization-code flow plus refresh-token rotation; the guardrails, rate limiter and tools layer stay unchanged.
- Next steps: Freshdesk webhooks for real-time ingestion, per-tenant rate budgets, response caching, triage accuracy evals, and a Razorpay payment-lookup tool to cross-check order and payment IDs.
