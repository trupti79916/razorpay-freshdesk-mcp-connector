"""Exercise the server through a real MCP client session (in-memory transport)."""
import json

import httpx
import respx
from mcp.shared.memory import create_connected_server_and_client_session

from agent.mcp_bridge import McpBridge

BASE = "https://test.freshdesk.com/api/v2"


@respx.mock
async def test_client_session_roundtrip_and_llm_tool_filtering(make_ctx):
    _, mcp = make_ctx(write_enabled=True)
    respx.get(f"{BASE}/tickets/1").mock(return_value=httpx.Response(200, json={
        "id": 1, "subject": "s", "status": 2, "priority": 1, "tags": [], "description_text": "hello"}))
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        bridge = McpBridge(session)
        tools = await bridge.load_tools()
        assert len(tools) == 10
        exposed = {t["function"]["name"] for t in bridge.ollama_tools(read_only=True)}
        assert exposed == {t.name for t in tools if t.annotations.readOnlyHint}
        assert "add_private_note" not in exposed and "update_ticket_triage" not in exposed
        out = await bridge.call("get_ticket", {"ticket_id": 1})
        assert out["ticket"]["id"] == 1
        bad = await bridge.call("get_ticket", {"ticket_id": "abc"})
        assert "error" in bad or "Error" in json.dumps(bad)
