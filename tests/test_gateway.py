"""Gateway tests.

A fake agent stands in for the real one, so these need no LLM and no MCP
servers. They assert the streaming contract: tool events arrive during the run,
the answer arrives after, and the stream terminates.
"""

import json

import httpx
import pytest
from httpx import ASGITransport

import gateway.main as gw

pytestmark = pytest.mark.anyio
from orchestrator.tracing import Trace


def _make_fake_tool_class():
    """A fresh class per test. instrument_to_sink patches the class and sets a
    flag on it, so reusing one class across tests would leak that state."""

    class _FakeTool:
        name = "run_query"
        description = "Run SQL.\nsecond line ignored"

        async def _arun(self, **kwargs):
            return f"result for {kwargs.get('sql', '?')}"

    return _FakeTool


class _FakeAgent:
    def __init__(self, tools):
        self._tools = tools

    async def run(self, message):
        for tool in self._tools:
            await tool._arun(sql="SELECT 1")
            await tool._arun(sql="SELECT 2")
        return f"Answered: {message}"


class _FailingAgent:
    def __init__(self, tools):
        self._tools = tools

    async def run(self, message):
        raise RuntimeError("model exploded")


@pytest.fixture
async def client(monkeypatch, request):
    agent_cls = getattr(request, "param", _FakeAgent)

    async def fake_build_agent(*a, **k):
        return agent_cls([_make_fake_tool_class()()]), None, Trace()

    monkeypatch.setattr(gw, "build_agent", fake_build_agent)

    async with gw.app.router.lifespan_context(gw.app):
        transport = ASGITransport(app=gw.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t"
        ) as c:
            yield c


async def _collect(client, message):
    events = []
    async with client.stream("POST", "/chat", json={"message": message}) as r:
        current = None
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                current = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                events.append((current, line.split(":", 1)[1].strip()))
    return events


async def test_health(client):
    r = await client.get("/health")
    assert r.json() == {"status": "ok", "agent_ready": True}


async def test_tools_returns_first_description_line_only(client):
    r = await client.get("/tools")
    tools = r.json()["tools"]
    assert tools[0]["name"] == "run_query"
    assert tools[0]["description"] == "Run SQL."


async def test_chat_streams_tools_then_answer_then_done(client):
    events = await _collect(client, "warmest state?")
    names = [e[0] for e in events]
    assert names == ["tool", "tool", "answer", "done"]


async def test_tool_events_arrive_before_the_answer(client):
    events = await _collect(client, "hi")
    answer_index = next(i for i, (n, _) in enumerate(events) if n == "answer")
    tool_indices = [i for i, (n, _) in enumerate(events) if n == "tool"]
    assert all(i < answer_index for i in tool_indices)


async def test_answer_carries_the_message(client):
    events = await _collect(client, "ping")
    answer = next(json.loads(d) for n, d in events if n == "answer")
    assert answer["text"] == "Answered: ping"


async def test_tool_event_has_expected_shape(client):
    events = await _collect(client, "hi")
    tool = next(json.loads(d) for n, d in events if n == "tool")
    assert set(tool) >= {"tool", "arguments", "duration_ms", "ok"}
    assert tool["tool"] == "run_query"


@pytest.mark.parametrize("client", [_FailingAgent], indirect=True)
async def test_agent_error_becomes_an_error_event(client):
    events = await _collect(client, "boom")
    names = [e[0] for e in events]
    assert "error" in names
    assert names[-1] == "done"
    err = next(json.loads(d) for n, d in events if n == "error")
    assert "exploded" in err["message"]
