import streamlit as st
import asyncio
import os
import json
from datetime import datetime
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from mcp_use import MCPAgent, MCPClient
import nest_asyncio

# Apply nest_asyncio to allow async functions to run properly inside Streamlit
nest_asyncio.apply()

# Configure the Streamlit page
st.set_page_config(page_title="MCP AI Assistant", page_icon="🤖", layout="wide")

# Load environment variables
load_dotenv()
if not os.getenv("GROQ_API_KEY"):
    st.error("⚠️ GROQ_API_KEY is missing in your .env file!")
    st.stop()

os.environ["GROQ_API_KEY"] = os.getenv("GROQ_API_KEY")

# Initialize Agent in Session State
# We do this so the agent and server connections don't reset on every single chat message
async def init_agent():
    config_path = "browser_mcp.json"
    client = MCPClient.from_config_file(config_path)
    llm = ChatGroq(model="llama-3.1-8b-instant")
    
    agent = MCPAgent(
        llm=llm,
        client=client,
        max_steps=15,
        memory_enabled=True # Keeps conversation history
    )
    return agent, client

if "agent" not in st.session_state:
    with st.spinner("🔌 Initializing MCP Agent and connecting to tools..."):
        loop = asyncio.get_event_loop()
        agent, client = loop.run_until_complete(init_agent())
        st.session_state.agent = agent
        st.session_state.client = client
        st.session_state.messages = [] # For rendering the UI chat history

# 2. Sidebar Configuration
with st.sidebar:
    st.title("🛠️ Active MCP Tools")
    
    # Read the JSON to display available tools in the sidebar
    try:
        with open("browser_mcp.json", "r") as f:
            config = json.load(f)
            tools = list(config.get("mcpServers", {}).keys())
            for tool in tools:
                st.success(f"✅ {tool}")
    except Exception:
        st.warning("Could not load tool configuration.")
        
    st.divider()
    
   # Replaces the old terminal "clear" command
    if st.button("🗑️ Clear Chat Memory", use_container_width=True):
        # 1. Clear the UI chat history
        st.session_state.messages = []
        
        # 2. Delete the agent and client to force a fresh memory wipe
        if "agent" in st.session_state:
            del st.session_state.agent
        if "client" in st.session_state:
            del st.session_state.client
            
        # 3. Reload the page
        st.rerun()

# 3. Main Chat Interface
st.title("💬 Interactive AI Dashboard")
st.caption("Connected to local and Dockerized MCP tools")

# Render existing chat history in the UI
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Capture user input
user_input = st.chat_input("Ask me anything or ask me to use a tool...")

if user_input:
    # 1. Add user message to UI state and display it
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # 2. Get AI Response
    with st.chat_message("assistant"):
        response_placeholder = st.empty()
        
        with st.spinner("🤖 Thinking and executing tools..."):
            current_date = datetime.now().strftime("%Y-%m-%d")
            enhanced_input = f"[System Note: Today is {current_date}] {user_input}"
            
            try:
                # Run the async agent run command synchronously for Streamlit
                loop = asyncio.get_event_loop()
                response = loop.run_until_complete(st.session_state.agent.run(enhanced_input))
                
                # Display and save response
                response_placeholder.markdown(response)
                st.session_state.messages.append({"role": "assistant", "content": response})
                
            except RuntimeError as e:
                error_msg = f"**[Tool Error]** A tool scraper failed or was rate-limited: {e}"
                response_placeholder.error(error_msg)
                st.session_state.messages.append({"role": "assistant", "content": error_msg})
                
            except Exception as e:
                error_msg = f"**[System Error]** {str(e)}"
                response_placeholder.error(error_msg)
                st.session_state.messages.append({"role": "assistant", "content": error_msg})
