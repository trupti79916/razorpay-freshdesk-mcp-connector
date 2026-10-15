import pytest

from freshdesk_mcp.client import FreshdeskClient
from freshdesk_mcp.config import Config
from freshdesk_mcp.context import Context
from freshdesk_mcp.guardrails import AuditLog
from freshdesk_mcp.server import build_server


def make_config(tmp_path, **kw) -> Config:
    base = dict(domain="test.freshdesk.com", api_key="secret-key", audit_log_path=str(tmp_path / "audit.jsonl"),
                rate_limit_per_min=6000, max_retries=3)
    base.update(kw)
    return Config(**base)


@pytest.fixture
def make_ctx(tmp_path, monkeypatch):
    async def _no_sleep(_):
        return None

    monkeypatch.setattr("freshdesk_mcp.client.asyncio.sleep", _no_sleep)

    def _make(**kw):
        cfg = make_config(tmp_path, **kw)
        ctx = Context(config=cfg, client=FreshdeskClient(cfg), audit=AuditLog(cfg.audit_log_path))
        return ctx, build_server(ctx)

    return _make


async def call(mcp, name, args):
    """Call a tool the way an MCP client would and return the parsed JSON payload."""
    import json
    result = await mcp.call_tool(name, args)
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)
