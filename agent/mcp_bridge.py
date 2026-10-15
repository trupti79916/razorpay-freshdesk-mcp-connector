"""MCP client session over stdio + conversion of MCP tools to Ollama tool definitions."""
from __future__ import annotations

import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


class McpBridge:
    def __init__(self, session: ClientSession):
        self.session = session
        self._tools: list[Any] = []

    async def load_tools(self) -> list[Any]:
        self._tools = (await self.session.list_tools()).tools
        return self._tools

    async def call(self, name: str, arguments: dict[str, Any]) -> dict:
        result = await self.session.call_tool(name, arguments)
        text = "".join(getattr(c, "text", "") for c in result.content)
        try:
            return json.loads(text)
        except ValueError:
            return {"error": {"code": "bad_response", "message": text[:300]}}

    def ollama_tools(self, read_only: bool = True) -> list[dict]:
        """Tool definitions for the LLM. Write tools are NEVER exposed to the model:
        writes happen only via explicit human approval in the UI."""
        out = []
        for t in self._tools:
            ann = getattr(t, "annotations", None)
            is_read = bool(ann and ann.readOnlyHint)
            if read_only and not is_read:
                continue
            props = {k: {x: v for x, v in p.items() if x in ("type", "description", "enum", "anyOf", "items")}
                     for k, p in (t.inputSchema.get("properties") or {}).items()}
            out.append({"type": "function", "function": {
                "name": t.name,
                "description": (t.description or "").split("\n")[0][:300],
                "parameters": {"type": "object", "properties": props,
                               "required": t.inputSchema.get("required", [])},
            }})
        return out


@asynccontextmanager
async def connect(write_enabled: bool = False) -> AsyncIterator[McpBridge]:
    """Spawn the Freshdesk MCP server as a stdio subprocess and open a client session."""
    env = {**os.environ, "WRITE_ENABLED": "true" if write_enabled else "false"}
    env.setdefault("AUDIT_LOG_PATH", str(ROOT / "audit.log.jsonl"))
    params = StdioServerParameters(command=sys.executable, args=["-m", "freshdesk_mcp.server"],
                                   env=env, cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            bridge = McpBridge(session)
            await bridge.load_tools()
            yield bridge
