"""Hit the real Open-Meteo API, no MCP, no LLM.

    python scripts/probe_live_weather.py Berlin
    python scripts/probe_live_weather.py "Tokyo"

Confirms the live API works from your machine and the response shape matches
what the server expects. My sandbox cannot reach Open-Meteo, so this is where
the real call gets verified.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from servers.live_weather.main import get_current_weather

city = " ".join(sys.argv[1:]) or "Berlin"
print(get_current_weather(city))
