"""Agent construction.

This is the piece the snippets referenced but never defined. It is a refactor
of the setup code that used to live at the top of app.py, with one addition:
every tool is wrapped by instrument() before the agent sees it.
"""

import os
from pathlib import Path
from typing import Awaitable, Callable

from dotenv import load_dotenv

load_dotenv()  # must run before mcp_use is imported, it reads env at import time

from langchain_groq import ChatGroq  # noqa: E402
from mcp_use import MCPAgent, MCPClient  # noqa: E402

from orchestrator.tracing import Trace, ToolEvent, instrument

CONFIG_PATH = Path(os.getenv("MCP_CONFIG", "browser_mcp.json"))
DEFAULT_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "12"))


async def noop_event(event: ToolEvent) -> None:
    """Default sink: record the event but emit it nowhere."""
    return None


async def build_agent(
    trace: Trace | None = None,
    on_event: Callable[[ToolEvent], Awaitable[None]] = noop_event,
    model: str | None = None,
) -> tuple[MCPAgent, MCPClient, Trace]:
    """Create an initialised, instrumented MCPAgent.

    Returns the agent, the client (so the caller can close sessions), and the
    trace that will accumulate tool events.
    """
    load_dotenv()
    if not os.getenv("GROQ_API_KEY"):
        raise RuntimeError("GROQ_API_KEY is not set. Copy .env.example to .env.")

    trace = trace if trace is not None else Trace()

    client = MCPClient.from_config_file(str(CONFIG_PATH))

    # No model_kwargs by default. Passing parallel_tool_calls through
    # model_kwargs can make Groq reject the request, and with retries enabled
    # that surfaces as a silent stall rather than an error. The schema now
    # lives in the run_query tool description, so the model no longer needs a
    # tool result before writing SQL and serialisation is not required.
    #
    # Set LLM_NO_PARALLEL_TOOLS=true to opt in once you have confirmed the
    # provider accepts it.
    #
    # request_timeout is the important line: without it a stalled call hangs
    # the terminal with no message, which is what happened on 2026-07-24.
    extra: dict = {}
    if os.getenv("LLM_NO_PARALLEL_TOOLS", "").lower() == "true":
        extra["model_kwargs"] = {"parallel_tool_calls": False}

    llm = ChatGroq(
        model=model or DEFAULT_MODEL,
        temperature=0,
        request_timeout=float(os.getenv("LLM_TIMEOUT_S", "60")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
        **extra,
    )

    agent = MCPAgent(
        llm=llm,
        client=client,
        max_steps=MAX_STEPS,
        memory_enabled=True,
    )

    # initialize() is what populates agent._tools. It is normally called
    # lazily inside run(), but we need the tools now so we can wrap them.
    await agent.initialize()

    # instrument() patches each tool's class in place, so the executor built
    # during initialize() picks up the wrapping without needing a rebuild.
    for tool in agent._tools:
        instrument(tool, trace, on_event)

    return agent, client, trace