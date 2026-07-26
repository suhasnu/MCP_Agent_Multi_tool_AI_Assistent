"""Live weather MCP server.

Current conditions for any city worldwide via Open-Meteo, which needs no API
key and serves the German weather service's own DWD ICON model for European
locations. That is the same national source as the historical warehouse, so
"how does today compare to the June average" draws both answers from DWD.

Two upstream calls: geocode the city name to coordinates, then fetch current
conditions. Both are plain HTTP GET returning JSON.

Attribution: weather data by Open-Meteo.com, CC BY 4.0.

Run:  python -m servers.live_weather.main
      MCP_TRANSPORT=sse python -m servers.live_weather.main
"""

import os

import httpx
from mcp.server.fastmcp import FastMCP

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
UA = {"User-Agent": "mcp-agent-portfolio/1.0 (github.com/suhasnu)"}
TIMEOUT = 15

# WMO weather interpretation codes, condensed to plain descriptions.
WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow", 77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "slight snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}

mcp = FastMCP(
    name="live_weather",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", "8061")),
)


def _get(url: str, params: dict) -> dict:
    resp = httpx.get(url, params=params, headers=UA, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _geocode(city: str) -> dict | None:
    """Resolve a city name to a coordinate and canonical name."""
    data = _get(GEOCODE_URL, {"name": city, "count": 1, "language": "en"})
    results = data.get("results")
    return results[0] if results else None


@mcp.tool()
def get_current_weather(city: str) -> str:
    """Get current live weather conditions for a city, anywhere in the world.

    Use this for present-moment questions ("what is the weather in Berlin right
    now", "is it raining in Tokyo"). For historical German averages, use the
    analytics tools instead: this returns only the current observation.

    Args:
        city: A city name, optionally with country, e.g. "Fulda" or "Paris, France".
    """
    try:
        place = _geocode(city)
    except Exception as exc:
        return f"Could not reach the geocoding service: {str(exc).splitlines()[0]}"

    if not place:
        return f"No location found for '{city}'. Check the spelling."

    lat, lon = place["latitude"], place["longitude"]
    label = place["name"]
    country = place.get("country", "")
    region = place.get("admin1", "")
    where = ", ".join(p for p in (label, region, country) if p)

    try:
        data = _get(
            FORECAST_URL,
            {
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,precipitation,"
                "weather_code,wind_speed_10m",
                "timezone": "auto",
            },
        )
    except Exception as exc:
        return f"Could not reach the weather service: {str(exc).splitlines()[0]}"

    current = data.get("current", {})
    units = data.get("current_units", {})
    if not current:
        return f"No current data available for {where}."

    code = current.get("weather_code")
    condition = WEATHER_CODES.get(code, f"code {code}")

    def field(key: str) -> str:
        value = current.get(key)
        unit = units.get(key, "")
        return f"{value}{unit}" if value is not None else "n/a"

    return (
        f"Current weather in {where} (observed {current.get('time', 'now')}):\n"
        f"  Condition: {condition}\n"
        f"  Temperature: {field('temperature_2m')}\n"
        f"  Humidity: {field('relative_humidity_2m')}\n"
        f"  Precipitation: {field('precipitation')}\n"
        f"  Wind: {field('wind_speed_10m')}\n"
        f"Source: Open-Meteo (DWD ICON for Europe)."
    )


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)
