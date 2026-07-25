"""Live weather server tests.

HTTP is mocked with respx, so these run offline and in CI. They lock the two
things that could silently break: the WMO code mapping and the two-step
geocode-then-fetch flow.
"""

import httpx
import pytest
import respx

from servers.live_weather import main as lw

GEO = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"

FULDA_GEO = {
    "results": [
        {
            "name": "Fulda", "country": "Germany", "admin1": "Hesse",
            "latitude": 50.55, "longitude": 9.68,
        }
    ]
}


def _forecast(code: int) -> dict:
    return {
        "current": {
            "time": "2026-07-25T14:00", "temperature_2m": 22.4,
            "relative_humidity_2m": 61, "precipitation": 0.0,
            "weather_code": code, "wind_speed_10m": 11.3,
        },
        "current_units": {
            "temperature_2m": "°C", "relative_humidity_2m": "%",
            "precipitation": "mm", "wind_speed_10m": "km/h",
        },
    }


@respx.mock
def test_returns_formatted_current_conditions():
    respx.get(GEO).mock(return_value=httpx.Response(200, json=FULDA_GEO))
    respx.get(FORECAST).mock(return_value=httpx.Response(200, json=_forecast(2)))

    out = lw.get_current_weather("Fulda")
    assert "Fulda, Hesse, Germany" in out
    assert "partly cloudy" in out
    assert "22.4°C" in out
    assert "61%" in out
    assert "Open-Meteo" in out


@respx.mock
@pytest.mark.parametrize(
    "code,phrase",
    [(0, "clear sky"), (65, "heavy rain"), (75, "heavy snow"), (95, "thunderstorm")],
)
def test_weather_codes_map_to_words(code, phrase):
    respx.get(GEO).mock(return_value=httpx.Response(200, json=FULDA_GEO))
    respx.get(FORECAST).mock(return_value=httpx.Response(200, json=_forecast(code)))
    assert phrase in lw.get_current_weather("Fulda")


@respx.mock
def test_unknown_code_falls_back_to_number():
    respx.get(GEO).mock(return_value=httpx.Response(200, json=FULDA_GEO))
    respx.get(FORECAST).mock(return_value=httpx.Response(200, json=_forecast(123)))
    assert "code 123" in lw.get_current_weather("Fulda")


@respx.mock
def test_unknown_city_is_reported():
    respx.get(GEO).mock(return_value=httpx.Response(200, json={"results": []}))
    out = lw.get_current_weather("Xyzzyville")
    assert "No location found" in out


@respx.mock
def test_geocode_network_error_is_handled():
    respx.get(GEO).mock(side_effect=httpx.ConnectError("boom"))
    out = lw.get_current_weather("Fulda")
    assert "Could not reach the geocoding service" in out


@respx.mock
def test_forecast_network_error_is_handled():
    respx.get(GEO).mock(return_value=httpx.Response(200, json=FULDA_GEO))
    respx.get(FORECAST).mock(side_effect=httpx.ConnectError("boom"))
    out = lw.get_current_weather("Fulda")
    assert "Could not reach the weather service" in out


@respx.mock
def test_missing_field_shows_na_not_crash():
    respx.get(GEO).mock(return_value=httpx.Response(200, json=FULDA_GEO))
    partial = _forecast(1)
    del partial["current"]["wind_speed_10m"]
    respx.get(FORECAST).mock(return_value=httpx.Response(200, json=partial))
    out = lw.get_current_weather("Fulda")
    assert "Wind: n/a" in out