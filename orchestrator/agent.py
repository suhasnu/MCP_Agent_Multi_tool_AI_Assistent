"""Agent construction.

Builds an initialised, instrumented MCPAgent. The LLM provider is chosen by the
LLM_PROVIDER environment variable, so switching between Groq and Gemini is a
config change, not a code change. Everything downstream (tools, gateway,
tracing) is provider-agnostic.
"""

import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # must run before mcp_use is imported, it reads env at import time

from mcp_use import MCPAgent, MCPClient

from orchestrator.tracing import ToolEvent, Trace, instrument

CONFIG_PATH = Path(os.getenv("MCP_CONFIG", "browser_mcp.json"))
PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "12"))

# Sensible default model per provider; override with LLM_MODEL.
_DEFAULT_MODEL = {
    "gemini": "gemini-2.5-flash",
    "groq": "llama-3.1-8b-instant",
}


def _build_llm(model: str | None):
    """Construct the chat model for the configured provider.

    Both providers read their API key from the environment (GOOGLE_API_KEY /
    GROQ_API_KEY), so no key is passed here. temperature=0 because the task is
    SQL generation, not prose.
    """
    provider = PROVIDER
    chosen = model or os.getenv("LLM_MODEL") or _DEFAULT_MODEL.get(provider)
    temperature = 0
    max_retries = int(os.getenv("LLM_MAX_RETRIES", "2"))
    timeout = float(os.getenv("LLM_TIMEOUT_S", "60"))

    if provider == "gemini":
        if not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")):
            raise RuntimeError(
                "GOOGLE_API_KEY is not set. Get one at aistudio.google.com "
                "and add it to .env."
            )
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=chosen,
            temperature=temperature,
            max_retries=max_retries,
            timeout=timeout,
        )

    if provider == "groq":
        if not os.getenv("GROQ_API_KEY"):
            raise RuntimeError("GROQ_API_KEY is not set. Add it to .env.")
        from langchain_groq import ChatGroq

        extra: dict = {}
        if os.getenv("LLM_NO_PARALLEL_TOOLS", "").lower() == "true":
            extra["model_kwargs"] = {"parallel_tool_calls": False}
        return ChatGroq(
            model=chosen,
            temperature=temperature,
            request_timeout=timeout,
            max_retries=max_retries,
            **extra,
        )

    raise RuntimeError(
        f"Unknown LLM_PROVIDER '{provider}'. Use 'gemini' or 'groq'."
    )


async def noop_event(event: ToolEvent) -> None:
    """Default sink: record the event but emit it nowhere."""
    return


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

    trace = trace if trace is not None else Trace()
    client = MCPClient.from_config_file(str(CONFIG_PATH))
    llm = _build_llm(model)

    agent = MCPAgent(
        llm=llm,
        client=client,
        max_steps=MAX_STEPS,
        memory_enabled=True,
    )

    # initialize() populates agent._tools. It is normally called lazily inside
    # run(), but we need the tools now so we can wrap them.
    await agent.initialize()

    # instrument() patches each tool's class in place, so the executor built
    # during initialize() picks up the wrapping without a rebuild.
    for tool in agent._tools:
        instrument(tool, trace, on_event)

    return agent, client, trace
