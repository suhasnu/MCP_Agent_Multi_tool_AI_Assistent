"""Bronze layer.

Lands the raw DWD payloads in DuckDB exactly as delivered. Every column is
text.

Run:  python -m pipeline.bronze
"""

import zipfile
from pathlib import Path

import duckdb

RAW = Path("data/raw")
EXTRACTED = Path("data/extracted")
DB_PATH = Path("data/weather.duckdb")
STATIONS_JSON = Path("data/stations.json")


def extract_payloads() -> list[Path]:
    """Unpack the produkt_* member from every archive in data/raw.

    Each zip also holds a dozen Metadaten_* members describing instruments and
    outages. Only the produkt file carries observations.
    """
    EXTRACTED.mkdir(parents=True, exist_ok=True)
    written = []

    for archive in sorted(RAW.glob("*.zip")):
        with zipfile.ZipFile(archive) as zf:
            members = [n for n in zf.namelist() if n.startswith("produkt")]
            if not members:
                print(f"  warning: no produkt member in {archive.name}")
                continue

            target = EXTRACTED / members[0]
            if not target.exists():
                target.write_bytes(zf.read(members[0]))
            written.append(target)

    return written


def build_readings(con: duckdb.DuckDBPyConnection) -> int:
    """Load every extracted payload into one bronze table.

    all_varchar keeps DuckDB from guessing types. filename records which file
    each row came from, which is the provenance that makes bronze auditable.
    """
    glob = (EXTRACTED / "produkt_*.txt").as_posix()
    con.execute(
        f"""
        CREATE OR REPLACE TABLE bronze_readings AS
        SELECT
            *,
            parse_filename(filename) AS source_file,
            current_timestamp::TIMESTAMP AS ingested_at
        FROM read_csv(
            '{glob}',
            delim = ';',
            header = true,
            all_varchar = true,
            filename = true
        );
        """
    )
    con.execute("ALTER TABLE bronze_readings DROP COLUMN filename;")
    return con.execute("SELECT count(*) FROM bronze_readings").fetchone()[0]


def build_stations(con: duckdb.DuckDBPyConnection) -> int:
    """Load the station manifest written by pipeline.run."""
    if not STATIONS_JSON.exists():
        print(f"  warning: {STATIONS_JSON} missing, run pipeline.run first")
        return 0

    con.execute(
        f"""
        CREATE OR REPLACE TABLE bronze_stations AS
        SELECT *, current_timestamp::TIMESTAMP AS ingested_at
        FROM read_json_auto('{STATIONS_JSON.as_posix()}');
        """
    )
    return con.execute("SELECT count(*) FROM bronze_stations").fetchone()[0]


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    print("Extracting payloads...")
    payloads = extract_payloads()
    print(f"  {len(payloads)} payload files in {EXTRACTED}/")

    if not payloads:
        print("Nothing to load. Run: python -m pipeline.run --stations 5")
        return

    con = duckdb.connect(str(DB_PATH))
    try:
        print("\nLoading bronze_readings...")
        n_readings = build_readings(con)
        print(f"  {n_readings:,} rows")

        print("\nLoading bronze_stations...")
        n_stations = build_stations(con)
        print(f"  {n_stations:,} rows")

        print("\nSchema (everything is VARCHAR on purpose):")
        for name, dtype in con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'bronze_readings' ORDER BY ordinal_position"
        ).fetchall():
            print(f"  {name:<16} {dtype}")

        print("\nSample rows, padding intact:")
        rows = con.execute(
            "SELECT STATIONS_ID, MESS_DATUM, TT_TU, RF_TU, source_file "
            "FROM bronze_readings LIMIT 3"
        ).fetchall()
        for row in rows:
            print(f"  {row[0]!r} {row[1]!r} {row[2]!r} {row[3]!r} {row[4]}")

        print("\nRows per station:")
        for sid, count in con.execute(
            "SELECT trim(STATIONS_ID) AS sid, count(*) FROM bronze_readings "
            "GROUP BY sid ORDER BY count(*) DESC"
        ).fetchall():
            print(f"  station {sid:>6}  {count:>7,} rows")

        sentinels = con.execute(
            "SELECT count(*) FROM bronze_readings WHERE trim(TT_TU) = '-999.0'"
        ).fetchone()[0]
        print(f"\nSentinel (-999.0) temperature rows preserved: {sentinels:,}")
    finally:
        con.close()

    size_mb = DB_PATH.stat().st_size / 1024 / 1024
    print(f"\nWrote {DB_PATH} ({size_mb:.1f} MB)")
    print("Next: silver, where the padding gets stripped and types applied.")


if __name__ == "__main__":
    main()