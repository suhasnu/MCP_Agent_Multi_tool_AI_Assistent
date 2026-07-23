from typing import Any
import httpx
from mcp.server.fastmcp import FastMCP
import os


# Create an MCP server
mcp = FastMCP(
    name="weather",
    host="0.0.0.0",  # only used for SSE transport (localhost)
    port=8050,  # only used for SSE transport (set this to any port)
)

# Constants
NWS_API_BASE = "https://api.weather.gov"
USER_AGENT = "weather-app/1.0"


async def make_nws_request(url: str) -> dict[str, Any] | None:
    # Make a request to the NWS API with proper error handling.
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/geo+json"
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, timeout=30.0)
            response.raise_for_status()
            return response.json()
        except Exception:
            return None

MAX_ALERTS = 5
MAX_DESC_CHARS = 200


def format_alert(feature: dict) -> str:
    """Compact summary. Full text would flood the model's context."""
    props = feature["properties"]
    desc = (props.get("description") or "").strip().replace("\n", " ")
    if len(desc) > MAX_DESC_CHARS:
        desc = desc[:MAX_DESC_CHARS] + "..."
    return (
        f"{props.get('event', 'Unknown')} | "
        f"{props.get('severity', 'Unknown')} | "
        f"{(props.get('areaDesc') or 'Unknown')[:100]} | {desc}"
    )


@mcp.tool()
async def get_alerts(state: str) -> str:
    """Get active weather alerts for a US state.

    Returns at most 5 alerts, most severe first, one per line.

    Args:
        state: Two-letter US state code (e.g. CA, NY)
    """
    url = f"{NWS_API_BASE}/alerts/active/area/{state}"
    data = await make_nws_request(url)

    if not data or "features" not in data:
        return "Unable to fetch alerts or no alerts found."
    if not data["features"]:
        return "No active alerts for this state."

    rank = {"Extreme": 0, "Severe": 1, "Moderate": 2, "Minor": 3}
    features = sorted(
        data["features"],
        key=lambda f: rank.get(f["properties"].get("severity"), 9),
    )

    total = len(features)
    lines = [format_alert(f) for f in features[:MAX_ALERTS]]
    if total > MAX_ALERTS:
        lines.append(f"({total - MAX_ALERTS} more alerts not shown)")
    return "\n".join(lines)

@mcp.tool()
async def get_forecast(latitude: float, longitude: float) -> str:
    """Get weather forecast for a location.

    Args:
        latitude: Latitude of the location
        longitude: Longitude of the location
    """
    # First get the forecast grid endpoint
    points_url = f"{NWS_API_BASE}/points/{latitude},{longitude}"
    points_data = await make_nws_request(points_url)

    if not points_data:
        return "Unable to fetch forecast data for this location."

    # Get the forecast URL from the points response
    forecast_url = points_data["properties"]["forecast"]
    forecast_data = await make_nws_request(forecast_url)

    if not forecast_data:
        return "Unable to fetch detailed forecast."

    # Format the periods into a readable forecast
    periods = forecast_data["properties"]["periods"]
    forecasts = []
    for period in periods[:5]:  # Only show next 5 periods
        forecast = f"""
                {period['name']}:
                Temperature: {period['temperature']}°{period['temperatureUnit']}
                Wind: {period['windSpeed']} {period['windDirection']}
                Forecast: {period['detailedForecast']}
                """
        forecasts.append(forecast)

    return "\n---\n".join(forecasts)

# Run the server
if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        print("Running server with stdio transport")
        mcp.run(transport="stdio")
    elif transport == "sse":
        print("Running server with SSE transport")
        mcp.run(transport="sse")
    else:
        raise ValueError(f"Unknown transport: {transport}")