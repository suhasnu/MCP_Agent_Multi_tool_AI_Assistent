
import os
from pathlib import Path
from typing import Awaitable, Callable

from dotenv import load_dotenv

load_dotenv()

from langchain_groq import ChatGroq 
from mcp_use import MCPAgent, MCPClient  

from orchestrator.tracing import Trace, ToolEvent, instrument

CONFIG_PATH = Path(os.getenv("MCP_CONFIG", "browser_mcp.json"))
DEFAULT_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "15"))


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
    llm = ChatGroq(model=model or DEFAULT_MODEL)

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