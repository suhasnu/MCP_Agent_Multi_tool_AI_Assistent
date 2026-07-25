"""Talk to the analytics server directly, no LLM.

Bisects the hang. The agent times out after a successful HTTP 200, which means
the stall is in the MCP tool call, not the model. This exercises the same
server over the same stdio transport with a timeout on every step.

If this hangs, the server is the problem.
If this passes, the problem is in the agent framework above it.

Run:  python scripts/probe_analytics.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

STEP_TIMEOUT = 20

PARAMS = StdioServerParameters(
    command=sys.executable,
    args=["-m", "servers.analytics.main"],
    env={"MCP_TRANSPORT": "stdio", "PATH": __import__("os").environ.get("PATH", "")},
)


async def step(label: str, coro):
    print(f"\n{label}")
    try:
        result = await asyncio.wait_for(coro, timeout=STEP_TIMEOUT)
        print("  ok")
        return result
    except asyncio.TimeoutError:
        print(f"  HUNG: no response within {STEP_TIMEOUT}s")
        raise
    except Exception as exc:
        print(f"  FAILED: {type(exc).__name__}: {str(exc)[:200]}")
        raise


async def main_async() -> None:
    print("Starting analytics server as a subprocess...")

    async with stdio_client(PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await step("1. Protocol handshake", session.initialize())

            tools = await step("2. List tools", session.list_tools())
            print(f"     {[t.name for t in tools.tools]}")

            r1 = await step(
                "3. Call list_tables", session.call_tool("list_tables", {})
            )
            print(f"     {r1.content[0].text[:120]}...")

            r2 = await step(
                "4. Call run_query (the step that hangs in the agent)",
                session.call_tool(
                    "run_query",
                    {
                        "sql": "SELECT bundesland, avg_temp_c FROM "
                        "gold_monthly_by_bundesland ORDER BY avg_temp_c DESC LIMIT 1"
                    },
                ),
            )
            print(f"     {r2.content[0].text[:200]}")

            r3 = await step(
                "5. Call run_query again (second call on the same session)",
                session.call_tool(
                    "run_query", {"sql": "SELECT count(*) AS n FROM gold_stations"}
                ),
            )
            print(f"     {r3.content[0].text[:120]}")

            r4 = await step(
                "6. Call describe_schema",
                session.call_tool(
                    "describe_schema", {"table": "gold_monthly_by_bundesland"}
                ),
            )
            print(f"     {r4.content[0].text[:120]}...")

    print("\nAll steps completed. The server is not the problem.")


def main() -> None:
    try:
        asyncio.run(main_async())
    except (asyncio.TimeoutError, Exception) as exc:
        print(f"\nStopped: {type(exc).__name__}")
        print("The step marked HUNG or FAILED above is where to look.")
        sys.exit(1)


if __name__ == "__main__":
    main()