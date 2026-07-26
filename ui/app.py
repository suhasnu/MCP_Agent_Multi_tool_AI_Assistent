"""Streamlit UI: a thin client over the gateway.

The signature element is the trace panel: every tool call the agent makes is
shown live, with its arguments and latency, as it happens. That is what
separates this from a chat box wired to an API.

Run:  uvicorn gateway.main:app          (terminal 1)
      streamlit run ui/app.py           (terminal 2)
"""

import json
import os

import httpx
import streamlit as st

GATEWAY = os.getenv("GATEWAY_URL", "http://localhost:8000")

st.set_page_config(page_title="Weather Agent", page_icon="🌡️", layout="wide")


def gateway_up() -> tuple[bool, list[dict]]:
    try:
        health = httpx.get(f"{GATEWAY}/health", timeout=5).json()
        if health.get("status") != "ok":
            return False, []
        tools = httpx.get(f"{GATEWAY}/tools", timeout=5).json()["tools"]
        return True, tools
    except Exception:
        return False, []


def stream_chat(message: str):
    """Yield (kind, payload) tuples from the gateway SSE stream.

    kind is one of: tool, answer, error.
    """
    with httpx.stream(
        "POST", f"{GATEWAY}/chat", json={"message": message}, timeout=180
    ) as r:
        event = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                raw = line.split(":", 1)[1].strip()
                if event in ("tool", "answer", "error"):
                    yield event, json.loads(raw)


def render_tool_call(container, event: dict) -> None:
    """One row in the trace panel."""
    ok = event.get("ok", True)
    mark = "✓" if ok else "✕"
    args = ", ".join(f"{k}={v}" for k, v in event.get("arguments", {}).items())
    if len(args) > 80:
        args = args[:80] + "…"
    with container:
        st.markdown(
            f"`{mark}` **{event['tool']}**({args}) · {event['duration_ms']} ms"
        )
        if event.get("preview"):
            st.caption(event["preview"][:200])


if "history" not in st.session_state:
    st.session_state.history = []  # list of {role, content, tools}

up, tools = gateway_up()

with st.sidebar:
    st.title("Weather Agent")
    if up:
        st.success("Gateway connected")
        st.caption(f"{len(tools)} tools available")
        for t in tools:
            st.markdown(f"**{t['name']}**")
            st.caption(t["description"])
    else:
        st.error("Gateway not reachable")
        st.caption("Start it with:\n\n`uvicorn gateway.main:app`")
        st.caption(f"Expected at {GATEWAY}")

    st.divider()
    if st.button("Clear conversation", use_container_width=True):
        st.session_state.history = []
        st.rerun()

st.title("German Weather Agent")
st.caption("Ask about temperature and humidity across German weather stations. "
           "Every tool call the agent makes is shown live.")

for turn in st.session_state.history:
    with st.chat_message(turn["role"]):
        if turn.get("tools"):
            with st.expander(f"{len(turn['tools'])} tool call(s)", expanded=False):
                for ev in turn["tools"]:
                    render_tool_call(st.container(), ev)
        st.markdown(turn["content"])

prompt = st.chat_input(
    "Ask about German weather..." if up else "Start the gateway first",
    disabled=not up,
)

if prompt:
    st.session_state.history.append({"role": "user", "content": prompt, "tools": []})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        trace_box = st.expander("Tool calls", expanded=True)
        answer_box = st.empty()

        collected_tools = []
        answer = ""
        error = ""

        for kind, payload in stream_chat(prompt):
            if kind == "tool":
                collected_tools.append(payload)
                render_tool_call(trace_box, payload)
            elif kind == "answer":
                answer = payload["text"]
            elif kind == "error":
                error = payload["message"]

        if error:
            answer_box.error(error)
            answer = f"_Error: {error}_"
        else:
            answer_box.markdown(answer)

    st.session_state.history.append(
        {"role": "assistant", "content": answer, "tools": collected_tools}
    )
