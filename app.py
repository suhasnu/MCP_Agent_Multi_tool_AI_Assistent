import asyncio
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from mcp_use import MCPAgent, MCPClient
import os

async def run_memory_chat():
    # Run a chat using MCP-Agent's built-in conversation memory
    load_dotenv()
    os.environ["GROQ_API_KEY"] = os.getenv("GROQ_API_KEY")

    # Config file path
    config_path = "browser_mcp.json"

    print("Initializing chat")

    # Initialize MCP client and LLM
    client = MCPClient.from_config_file(config_path)
    llm = ChatGroq(model="llama-3.1-8b-instant")

    # Create MCP-Agent with memory
    agent = MCPAgent(
        llm=llm,
        client=client,
        max_steps=15,
        memory_enabled=True, # Enable conversation memory
    )

    print("\n=== Interactive MCP Chat ===")
    print("Type 'exit' or 'quit' to end the chat") 
    print("Type 'clear' to clear the conversation memory")
    print("--------------------------------")

    try:
        # Main chat loop
        while True:
            # Get user input
            user_input = input("\nYou: ")

            # Check for exit commands
            if user_input.lower() in ["exit", "quit"]:
                print("Ending Chat!")
                break

            # Check for clear command
            if user_input.lower() == "clear":
                agent.memory.clear()
                print("Conversation memory cleared")
                continue

            # Get agent response
            print("\nAssistant: ", end="", flush=True)

            try:
                # Run the agent with user input
                response = await agent.run(user_input)
                print(response)

            except Exception as e:
                print(f"\nError: {e}")

    finally:
        # Clean up
        if client and client.sessions:
            await client.close_allsessions()

if __name__ == "__main__":
    asyncio.run(run_memory_chat())            