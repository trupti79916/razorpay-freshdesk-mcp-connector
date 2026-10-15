"""Minimal Streamlit UI: Tickets, Triage Copilot (human approval), Connector health.

Run: uv run streamlit run app/streamlit_app.py
The UI is just another MCP client: it spawns the Freshdesk MCP server over stdio.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import triage_agent  # noqa: E402
from agent.llm import OllamaLLM  # noqa: E402
from agent.mcp_bridge import connect  # noqa: E402
from freshdesk_mcp.guardrails import AuditLog  # noqa: E402

st.set_page_config(page_title="Freshdesk Triage Copilot", layout="wide")
st.title("Support Triage Copilot")
st.caption("Freshdesk MCP connector + local Ollama model. Writes are gated: dry-run, then human approval.")

# ----------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Settings")
    model = st.text_input("Ollama model", value=OllamaLLM().model)
    llm = OllamaLLM(model=model)
    ok, msg = llm.health()
    st.success("Ollama ready") if ok else st.error(msg)
    if llm.is_cloud:
        st.warning("Cloud model: ticket text leaves this machine.")
    write_mode = st.toggle("Enable write mode (WRITE_ENABLED)", value=False,
                           help="Lets the server execute approved private notes / priority / tag updates.")
    limit = st.slider("Tickets per triage run", 1, 15, 6)


def run(fn):
    """Open an MCP session, run `fn(bridge)`, return its result."""
    async def _go():
        async with connect(write_enabled=write_mode) as bridge:
            return await fn(bridge)
    return asyncio.run(_go())


PRIORITY_ICON = {"Low": "🟢", "Medium": "🟡", "High": "🟠", "Urgent": "🔴"}


def clean(wrapped) -> str:
    """Unwrap {'content': '<untrusted_ticket_content>..'} to plain text for display."""
    text = wrapped.get("content", "") if isinstance(wrapped, dict) else (wrapped or "")
    return text.replace("<untrusted_ticket_content>", "").replace("</untrusted_ticket_content>", "").strip()


def fmt_dt(value: str | None) -> str:
    if not value:
        return "-"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%d %b %Y, %H:%M")
    except ValueError:
        return value


def render_ticket(detail: dict, llm, llm_ok: bool) -> None:
    if "error" in detail:
        st.error(f"{detail['error']['code']}: {detail['error']['message']}")
        return
    t = detail["ticket"]
    st.subheader(f"#{t['id']}  {clean(t['subject'])}")
    c1, c2, c3, c4 = st.columns(4)
    for col, label, value in (
        (c1, "Status", t["status_label"] or "-"),
        (c2, "Priority", f"{PRIORITY_ICON.get(t['priority_label'], '')} {t['priority_label'] or '-'}"),
        (c3, "Created", fmt_dt(t["created_at"])),
        (c4, "Updated", fmt_dt(t["updated_at"])),
    ):
        col.caption(label)
        col.markdown(f"**{value}**")  # body-sized, so it stays smaller than the ticket heading
    req = t.get("requester") or {}
    st.markdown(
        f"**Requester:** {req.get('name') or '-'} &nbsp;|&nbsp; {req.get('email') or '-'} &nbsp;|&nbsp; {req.get('phone') or '-'}"
        f"  \n**Tags:** {' '.join(f'`{x}`' for x in t['tags']) or '-'}"
        f"  \n**Due by:** {fmt_dt(t.get('due_by'))}" + ("  \n:red[**Escalated**]" if t.get("is_escalated") else "")
    )

    # --- agent summary
    st.markdown("#### Agent summary")
    if st.button("Summarize with agent", disabled=not llm_ok, key=f"sum{t['id']}"):
        with st.spinner("Summarizing with the local model..."):
            st.session_state.summary = (t["id"], triage_agent.summarize_ticket(llm, t))
    summary = st.session_state.get("summary")
    if summary and summary[0] == t["id"]:
        st.info(summary[1])
    elif not llm_ok:
        st.caption("Ollama is not available; see the sidebar.")

    # --- description + thread
    st.markdown("#### Description")
    desc = t.get("description") or {}
    st.text(clean(desc) or "(empty)")
    if desc.get("truncated"):
        st.caption("Description truncated by the connector.")
    convs = t.get("conversations") or []
    st.markdown(f"#### Conversation ({len(convs)})")
    if not convs:
        st.caption("No replies or notes yet.")
    for c in convs:
        who = "user" if c["from"] == "customer" else "assistant"
        with st.chat_message(who):
            label = "Customer" if c["from"] == "customer" else "Agent"
            st.caption(f"{label}{' (private note)' if c['private'] else ''} - {fmt_dt(c['created_at'])}")
            st.write(clean(c["body"]) or "(empty)")
    with st.expander("Raw JSON"):
        st.json(detail)


tab_tickets, tab_triage, tab_health = st.tabs(["Tickets", "Triage Copilot", "Connector health"])

# ----------------------------------------------------------------- tickets
with tab_tickets:
    c1, c2, c3 = st.columns(3)
    status = c1.selectbox("Status", [None, 2, 3, 4], format_func=lambda s: {None: "Any", 2: "Open", 3: "Pending", 4: "Resolved"}[s])
    priority = c2.selectbox("Priority", [None, 1, 2, 3, 4], format_func=lambda s: {None: "Any", 1: "Low", 2: "Medium", 3: "High", 4: "Urgent"}[s])
    query = c3.text_input("Search query (optional)", placeholder="status:2 AND tag:'refund'")
    if st.button("Load tickets"):
        async def _load(b):
            if query:
                return await b.call("search_tickets", {"query": query})
            return await b.call("list_tickets", {"status": status, "priority": priority, "per_page": 30})
        data = run(_load)
        if "error" in data:
            st.error(data["error"])
        else:
            st.session_state.tickets = data["tickets"]
    tickets = st.session_state.get("tickets", [])
    if tickets:
        df = pd.DataFrame([{"id": t["id"], "subject": t["subject"].replace("<untrusted_ticket_content>\n", "").replace("\n</untrusted_ticket_content>", ""),
                            "status": t["status_label"], "priority": t["priority_label"], "tags": ", ".join(t["tags"]),
                            "updated": t["updated_at"]} for t in tickets])
        st.dataframe(df, use_container_width=True, hide_index=True)
        pick = st.selectbox("Ticket detail", [t["id"] for t in tickets])
        if st.button("Show detail"):
            st.session_state.detail = run(lambda b: b.call("get_ticket", {"ticket_id": pick, "include_conversations": True}))
            st.session_state.pop("summary", None)
        detail = st.session_state.get("detail")
        if detail:
            render_ticket(detail, llm, ok)

# ----------------------------------------------------------------- triage
with tab_triage:
    st.markdown("Classifies open tickets with the local model, then **proposes** a private note, tags and a priority raise. "
                "Nothing is written until you click Approve.")
    if st.button("Run triage", type="primary", disabled=not ok):
        box = st.empty()
        run_log: list[str] = []

        def progress(m: str):
            run_log.append(m)
            box.info(m)

        st.session_state.triage = run(lambda b: triage_agent.run_triage(b, llm, limit=limit, progress=progress))
        box.empty()
        st.session_state.pop("done", None)

    items = st.session_state.get("triage", [])
    done = st.session_state.setdefault("done", {})
    if items:
        st.dataframe(pd.DataFrame([{
            "ticket": i["ticket_id"], "category": i["result"]["category"], "urgency": i["result"]["urgency"],
            "current": i["current_priority"], "source": i["source"],
            "injection?": i["result"]["injection_suspected"], "reason": i["result"]["reason"],
        } for i in items if "result" in i]), use_container_width=True, hide_index=True)
        for i in items:
            if "result" not in i:
                st.error(f"#{i['ticket_id']}: {i['error']}")
                continue
            p = i["proposal"]
            with st.expander(f"#{i['ticket_id']} - {i['result']['category']} / {i['result']['urgency']}"):
                if i["result"]["injection_suspected"]:
                    st.warning("Prompt-injection attempt suspected in this ticket.")
                st.text(p["note"])
                st.write(f"Priority change: {p['priority'] or 'none'} | Tags: {', '.join(p['add_tags'])}")
                if not write_mode:
                    st.info("Write mode is off (see sidebar). Enable it to preview and approve.")
                    continue
                if st.button("Preview (dry run)", key=f"pv{p['ticket_id']}"):
                    st.json(run(lambda b, p=p: triage_agent.preview_proposal(b, p)))
                if done.get(p["ticket_id"]):
                    st.success("Applied.")
                    st.json(done[p["ticket_id"]])
                else:
                    a, r = st.columns(2)
                    if a.button("Approve and apply", key=f"ap{p['ticket_id']}"):
                        done[p["ticket_id"]] = run(lambda b, p=p: triage_agent.apply_proposal(b, p))
                        st.rerun()
                    r.button("Reject", key=f"rj{p['ticket_id']}")

    with st.expander("Ask the assistant (experimental, read-only tools)"):
        q = st.text_input("Question", placeholder="Which open tickets mention refunds?")
        if st.button("Ask") and q:
            trace: list[str] = []
            answer = run(lambda b: triage_agent.chat(b, llm, q, trace=trace.append))
            if trace:
                st.caption("Tool calls: " + " -> ".join(trace))
            st.write(answer)

# ----------------------------------------------------------------- health
with tab_health:
    if st.button("Refresh status"):
        st.session_state.status = run(lambda b: b.call("get_connector_status", {}))
        async def _tools(b):
            return [{"tool": t.name, "read_only": bool(t.annotations and t.annotations.readOnlyHint)} for t in await b.load_tools()]
        st.session_state.tools = run(_tools)
    if "status" in st.session_state:
        st.json(st.session_state.status)
        st.dataframe(pd.DataFrame(st.session_state.tools), hide_index=True)
    st.subheader("Last 20 audit entries")
    entries = AuditLog(str(ROOT / "audit.log.jsonl")).tail(20)
    if entries:
        st.dataframe(pd.DataFrame(entries).iloc[::-1], use_container_width=True, hide_index=True)
    else:
        st.caption("No calls yet.")
