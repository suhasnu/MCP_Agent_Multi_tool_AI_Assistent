import asyncio
from datetime import datetime

from orchestrator.agent import build_agent
from orchestrator.tracing import ToolEvent


async def print_event(event: ToolEvent) -> None:
    """Terminal version of the trace panel."""
    status = "ok" if event.ok else f"FAILED: {event.error}"
    print(f"  [tool] {event.tool}({event.arguments}) {event.duration_ms}ms {status}")


async def run_memory_chat() -> None:
    print("Initializing chat...")
    agent, client, trace = await build_agent(on_event=print_event)

    current_date = datetime.now().strftime("%Y-%m-%d")
    print("\n=== Interactive MCP Chat ===")
    print("Type 'exit' or 'quit' to end the chat")
    print("Type 'clear' to clear the conversation memory")
    print("--------------------------------")

    try:
        while True:
            user_input = input("\nYou: ")

            if user_input.lower() in ("exit", "quit"):
                print("Ending Chat!")
                break

            if user_input.lower() == "clear":
                agent.clear_conversation_history()   # was: agent.memory.clear()
                print("Conversation memory cleared")
                continue

            print("\nAssistant: ", end="", flush=True)
            enhanced_input = f"[System Note: Today is {current_date}] {user_input}"

            try:
                response = await agent.run(enhanced_input)
                print(response)
            except Exception as e:
                print(f"\nError: {e}")
    finally:
        if client and client.get_all_active_sessions():
            await client.close_all_sessions()        # was: close_allsessions()


if __name__ == "__main__":
    asyncio.run(run_memory_chat())
