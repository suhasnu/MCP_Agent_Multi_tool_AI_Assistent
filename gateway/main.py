"""FastAPI gateway.

Splits the UI from the agent. Streamlit used to hold the MCPAgent in session
state, which coupled the interface to the agent's lifecycle and forced async
gymnastics inside a synchronous framework. Now the agent lives here, behind
HTTP, and the UI is a thin client.


Run:  uvicorn gateway.main:app --reload
      http://localhost:8000/docs
"""

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from orchestrator.agent import build_agent
from orchestrator.tracing import ToolEvent, Trace, TraceSink, instrument_to_sink

STATE: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    agent, client, _ = await build_agent()

    sink = TraceSink()
    for tool in agent._tools:
        instrument_to_sink(tool, sink)

    STATE.update(agent=agent, client=client, sink=sink, lock=asyncio.Lock())
    yield
    if client and client.get_all_active_sessions():
        await client.close_all_sessions()


app = FastAPI(title="MCP Agent Gateway", version="1.0", lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "agent_ready": "agent" in STATE}


@app.get("/tools")
async def tools() -> dict:
    agent = STATE.get("agent")
    if not agent:
        return {"tools": []}
    return {
        "tools": [
            {"name": t.name, "description": (t.description or "").split("\n")[0]}
            for t in agent._tools
        ]
    }


async def _run_and_stream(message: str):
    """Yield SSE events: each tool call as it happens, then the answer."""
    queue: asyncio.Queue = asyncio.Queue()
    trace = Trace()

    async def on_event(event: ToolEvent) -> None:
        await queue.put({"event": "tool", "data": json.dumps(event.as_dict())})

    async with STATE["lock"]:
        STATE["sink"].retarget(trace, on_event)
        agent = STATE["agent"]

        async def run() -> None:
            try:
                answer = await agent.run(message)
                await queue.put(
                    {"event": "answer", "data": json.dumps({"text": answer})}
                )
            except Exception as exc:
                raw = str(exc)
                # The 8B sometimes keeps calling tools after it has the answer
                # and trips the step cap. Translate the framework's raw graph
                # error into something a user can act on.
                if "recursion" in raw.lower() or "GRAPH_RECURSION" in raw:
                    err_message = (
                        "The agent took too many steps without settling on an "
                        "answer. This happens on the smaller model with "
                        "multi-part questions. Try rephrasing, or ask the two "
                        "parts separately."
                    )
                else:
                    err_message = raw[:500]
                await queue.put(
                    {"event": "error", "data": json.dumps({"message": err_message})}
                )
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            if not task.done():
                task.cancel()
        yield {"event": "done", "data": "{}"}


@app.post("/chat")
async def chat(req: ChatRequest) -> EventSourceResponse:
    return EventSourceResponse(_run_and_stream(req.message))
