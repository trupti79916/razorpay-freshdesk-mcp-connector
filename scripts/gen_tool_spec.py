"""Generate docs/tool_spec.json from the registered MCP tools so the spec never drifts from code."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from freshdesk_mcp import __version__  # noqa: E402
from freshdesk_mcp.config import Config  # noqa: E402
from freshdesk_mcp.server import build_context, build_server  # noqa: E402


async def main() -> None:
    cfg = Config(domain="example.freshdesk.com", api_key="unused", audit_log_path="/dev/null")
    mcp = build_server(build_context(cfg))
    tools = []
    for t in await mcp.list_tools():
        ann = t.annotations
        tools.append({
            "name": t.name,
            "description": t.description,
            "class": "read" if ann and ann.readOnlyHint else "write",
            "inputSchema": t.inputSchema,
            "annotations": ann.model_dump(exclude_none=True) if ann else {},
        })
    spec = {"server": "freshdesk", "version": __version__, "protocol": "MCP (stdio / streamable-http)",
            "tools": tools}
    out = ROOT / "docs" / "tool_spec.json"
    out.write_text(json.dumps(spec, indent=2) + "\n")
    print(f"wrote {out} ({len(tools)} tools)")


asyncio.run(main())
