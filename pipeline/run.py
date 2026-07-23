"""Phase 1 ingest runner.
Run:  python -m pipeline.run --stations 5
"""

import argparse
import json
from pathlib import Path

from pipeline.ingest import (
    RAW,
    extract_readings,
    fetch_archive,
    fetch_stations,
    list_station_archives,
    parse_reading_row,
)

# A spread of well-known stations across Germany. Keeping a fixed list means
# reruns are reproducible instead of depending on directory ordering.
PREFERRED = [
    1420,   # Frankfurt/Main
    433,    # Berlin-Tempelhof
    1975,   # Hamburg-Fuhlsbüttel
    3379,   # München-Stadt
    2667,   # Köln-Bonn
    4928,   # Stuttgart-Echterdingen
    1048,   # Dresden-Klotzsche
    2014,   # Hannover
    5906,   # Nürnberg
    691,    # Bremen
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stations", type=int, default=5, help="how many to fetch")
    args = parser.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)

    print("Fetching station metadata...")
    stations = fetch_stations()
    print(f"  parsed {len(stations)} stations")

    by_id = {s.station_id: s for s in stations}
    manifest_path = Path("data/stations.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps([s.__dict__ for s in stations], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  wrote {manifest_path}")

    print("\nListing archives...")
    archives = list_station_archives()
    print(f"  {len(archives)} available")

    # Map station id to its archive name: stundenwerte_TU_01420_akt.zip
    index = {}
    for name in archives:
        parts = name.split("_")
        if len(parts) >= 3 and parts[2].isdigit():
            index[int(parts[2])] = name

    wanted = [sid for sid in PREFERRED if sid in index][: args.stations]
    if len(wanted) < args.stations:
        extra = [sid for sid in sorted(index) if sid not in wanted]
        wanted += extra[: args.stations - len(wanted)]

    print(f"\nDownloading {len(wanted)} archives...")
    total_rows = 0
    for sid in wanted:
        name = index[sid]
        station = by_id.get(sid)
        label = f"{station.station_name}, {station.bundesland}" if station else "unknown"

        path = fetch_archive(name)
        payload, rows = extract_readings(path)
        parsed = [r for r in (parse_reading_row(r) for r in rows) if r]
        total_rows += len(parsed)

        size_kb = path.stat().st_size / 1024
        print(f"  {sid:>5}  {label:<38} {size_kb:>7.1f} KB  {len(parsed):>6} readings")

    print(f"\nDone. {total_rows} readings across {len(wanted)} stations in {RAW}/")
    print("Next: load these into DuckDB as the bronze layer.")


if __name__ == "__main__":
    main()