"""DWD Climate Data Center ingest.

Written against the real archive structure, confirmed by scripts/explore_dwd.py:
"""

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

BASE = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate"
RESOLUTION = "hourly"
PARAMETER = "air_temperature"
MODE = "recent"

DIR_URL = f"{BASE}/{RESOLUTION}/{PARAMETER}/{MODE}/"
METADATA_FILE = "TU_Stundenwerte_Beschreibung_Stationen.txt"
RAW = Path("data/raw")
UA = {"User-Agent": "mcp-agent-portfolio/1.0 (github.com/suhasnu)"}

# Missing-value sentinel used throughout the CDC archive.
MISSING = -999

# All sixteen are single tokens (hyphenated, never spaced). This is what lets us
# split the ragged metadata rows from the right-hand side.
BUNDESLAENDER = {
    "Baden-Württemberg", "Bayern", "Berlin", "Brandenburg", "Bremen",
    "Hamburg", "Hessen", "Mecklenburg-Vorpommern", "Niedersachsen",
    "Nordrhein-Westfalen", "Rheinland-Pfalz", "Saarland", "Sachsen",
    "Sachsen-Anhalt", "Schleswig-Holstein", "Thüringen",
}


@dataclass
class Station:
    station_id: int
    from_date: str
    to_date: str
    elevation_m: int
    latitude: float
    longitude: float
    station_name: str
    bundesland: str


def _get(url: str) -> httpx.Response:
    resp = httpx.get(url, headers=UA, timeout=180, follow_redirects=True)
    resp.raise_for_status()
    return resp


def list_station_archives() -> list[str]:
    """Return every station zip filename in the directory index."""
    html = _get(DIR_URL).text
    names = re.findall(r'href="([^"?/][^"]*)"', html)
    return sorted(n for n in names if n.endswith(".zip"))


def parse_station_line(line: str) -> Station | None:
    """Parse one row of the station description file.

    The file looks fixed-width but is not: the dashes header disagrees with the
    data rows. So we tokenise. The first six fields are numeric and positional.
    Working backwards, the Bundesland is the last token that names a state, and
    everything between position six and there is the station name, which may
    contain spaces (e.g. "Bad Marienberg").
    """
    tokens = line.split()
    if len(tokens) < 8 or not tokens[0].isdigit():
        return None

    # Walk backwards to the Bundesland. Anything after it (e.g. the Abgabe
    # column) is metadata we do not need.
    land_idx = next(
        (i for i in range(len(tokens) - 1, 5, -1) if tokens[i] in BUNDESLAENDER),
        None,
    )
    if land_idx is None:
        return None

    name = " ".join(tokens[6:land_idx]).strip()
    try:
        return Station(
            station_id=int(tokens[0]),
            from_date=tokens[1],
            to_date=tokens[2],
            elevation_m=int(tokens[3]),
            latitude=float(tokens[4]),
            longitude=float(tokens[5]),
            station_name=name,
            bundesland=tokens[land_idx],
        )
    except ValueError:
        return None


def fetch_stations() -> list[Station]:
    """Download and parse the station description file."""
    text = _get(DIR_URL + METADATA_FILE).content.decode("latin-1")
    rows = text.splitlines()[2:]  # skip header and dashes
    stations = [s for s in (parse_station_line(r) for r in rows) if s]
    skipped = len(rows) - len(stations)
    if skipped:
        print(f"  note: skipped {skipped} unparseable metadata rows")
    return stations


def fetch_archive(name: str) -> Path:
    """Download one station zip, skipping the transfer if unchanged.

    The ETag cache is what makes reruns cheap. The nightly job re-checks 500
    files but only downloads the handful that actually changed.
    """
    target = RAW / name
    etag_file = target.with_suffix(".etag")
    target.parent.mkdir(parents=True, exist_ok=True)

    headers = dict(UA)
    if etag_file.exists() and target.exists():
        headers["If-None-Match"] = etag_file.read_text().strip()

    resp = httpx.get(
        DIR_URL + name, headers=headers, timeout=180, follow_redirects=True
    )
    if resp.status_code == 304:
        return target

    resp.raise_for_status()
    target.write_bytes(resp.content)
    if "etag" in resp.headers:
        etag_file.write_text(resp.headers["etag"])
    return target


def extract_readings(archive: Path) -> tuple[str, list[str]]:
    """Return the payload filename and its rows, header included.

    Each archive holds a dozen metadata members alongside one produkt_* file.
    Only the latter carries observations.
    """
    with zipfile.ZipFile(archive) as zf:
        name = next(n for n in zf.namelist() if n.startswith("produkt"))
        return name, zf.read(name).decode("latin-1").splitlines()


def parse_reading_row(row: str) -> dict | None:
    """Parse one observation. Every field is space padded and needs stripping."""
    parts = [p.strip() for p in row.split(";")]
    if len(parts) < 5 or not parts[0].isdigit():
        return None

    def num(value: str, cast):
        parsed = cast(value)
        return None if parsed == MISSING else parsed

    try:
        return {
            "station_id": int(parts[0]),
            "mess_datum": parts[1],           # YYYYMMDDHH, cast later
            "quality_level": num(parts[2], int),
            "air_temp_c": num(parts[3], float),
            "humidity_pct": num(parts[4], float),
        }
    except ValueError:
        return None
