"""Probe a running gateway.

Start the gateway in one terminal:
    uvicorn gateway.main:app

Then run this in another:
    python scripts/probe_gateway.py "Which state was warmest?"

Hits /health, /tools, and streams /chat, printing each tool event as it
arrives. No browser, no Streamlit. If this works, the UI just needs to render
what this prints.
"""

import sys

import httpx

BASE = "http://localhost:8000"


def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else "What data do you have?"

    print("health:", httpx.get(f"{BASE}/health", timeout=10).json())

    tools = httpx.get(f"{BASE}/tools", timeout=10).json()
    print("tools :", [t["name"] for t in tools["tools"]])

    print(f"\nAsking: {question}\n")
    with httpx.stream(
        "POST", f"{BASE}/chat", json={"message": question}, timeout=120
    ) as r:
        event = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload = line.split(":", 1)[1].strip()
                if event == "tool":
                    import json

                    d = json.loads(payload)
                    print(f"  [tool] {d['tool']}({d['arguments']}) {d['duration_ms']}ms")
                elif event == "answer":
                    import json

                    print(f"\n{json.loads(payload)['text']}")
                elif event == "error":
                    print(f"\nERROR: {payload}")


if __name__ == "__main__":
    main()
