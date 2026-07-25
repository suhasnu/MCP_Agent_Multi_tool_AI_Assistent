"""Silver layer.

Where opinions live. Bronze banked the raw bytes; silver decides what they
mean:
Run:  python -m pipeline.silver
"""

from pathlib import Path

import duckdb

DB_PATH = Path("data/weather.duckdb")

# Sentinel used throughout the CDC archive for "no measurement".
MISSING = -999

SILVER_STATIONS = """
CREATE OR REPLACE TABLE silver_stations AS
SELECT
    CAST(station_id AS INTEGER)   AS station_id,
    station_name,
    bundesland,
    CAST(latitude  AS DOUBLE)     AS latitude,
    CAST(longitude AS DOUBLE)     AS longitude,
    CAST(elevation_m AS INTEGER)  AS elevation_m,
    strptime(from_date, '%Y%m%d')::DATE AS active_from,
    strptime(to_date,   '%Y%m%d')::DATE AS active_to
FROM bronze_stations;
"""

# TRY_CAST returns NULL instead of raising, so one malformed row cannot kill the
# build. NULLIF then turns the -999 sentinel into a real NULL.
SILVER_READINGS = f"""
CREATE OR REPLACE TABLE silver_readings AS
SELECT
    CAST(trim(r.STATIONS_ID) AS INTEGER)                        AS station_id,
    strptime(trim(r.MESS_DATUM), '%Y%m%d%H')                    AS observed_at,
    TRY_CAST(trim(r.QN_9) AS INTEGER)                           AS quality_level,
    NULLIF(TRY_CAST(trim(r.TT_TU) AS DOUBLE), {MISSING})        AS air_temp_c,
    NULLIF(TRY_CAST(trim(r.RF_TU) AS DOUBLE), {MISSING})        AS humidity_pct,
    s.station_name,
    s.bundesland,
    s.latitude,
    s.longitude,
    s.elevation_m,
    r.source_file,
    r.ingested_at
FROM bronze_readings r
LEFT JOIN silver_stations s
       ON CAST(trim(r.STATIONS_ID) AS INTEGER) = s.station_id
WHERE trim(r.MESS_DATUM) <> ''
  AND TRY_CAST(trim(r.STATIONS_ID) AS INTEGER) IS NOT NULL;
"""

# Calendar parts are stored rather than computed on every query. The agent will
# write SQL like "WHERE year = 2024 AND month BETWEEN 6 AND 8", and asking a
# model to remember date-part syntax is a needless source of wrong answers.
SILVER_CALENDAR = """
ALTER TABLE silver_readings ADD COLUMN year   INTEGER;
ALTER TABLE silver_readings ADD COLUMN month  INTEGER;
ALTER TABLE silver_readings ADD COLUMN day    INTEGER;
ALTER TABLE silver_readings ADD COLUMN hour   INTEGER;
UPDATE silver_readings SET
    year  = EXTRACT(year   FROM observed_at),
    month = EXTRACT(month  FROM observed_at),
    day   = EXTRACT(day    FROM observed_at),
    hour  = EXTRACT(hour   FROM observed_at);
"""


def build_stations(con: duckdb.DuckDBPyConnection) -> int:
    con.execute(SILVER_STATIONS)
    return con.execute("SELECT count(*) FROM silver_stations").fetchone()[0]


def build_readings(con: duckdb.DuckDBPyConnection) -> int:
    con.execute(SILVER_READINGS)
    con.execute(SILVER_CALENDAR)
    return con.execute("SELECT count(*) FROM silver_readings").fetchone()[0]


def report(con: duckdb.DuckDBPyConnection) -> None:
    print("\nTypes are real now:")
    for name, dtype in con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'silver_readings' ORDER BY ordinal_position"
    ).fetchall():
        print(f"  {name:<16} {dtype}")

    print("\nSample:")
    for row in con.execute(
        "SELECT station_id, observed_at, air_temp_c, humidity_pct, bundesland "
        "FROM silver_readings WHERE air_temp_c IS NOT NULL LIMIT 3"
    ).fetchall():
        print(f"  {row[0]:>5}  {row[1]}  {row[2]:>6.1f} C  {row[3]:>5.1f} %  {row[4]}")

    print("\nNull rates, sentinels resolved:")
    total, null_t, null_h, orphan = con.execute(
        """
        SELECT count(*),
               count(*) FILTER (WHERE air_temp_c IS NULL),
               count(*) FILTER (WHERE humidity_pct IS NULL),
               count(*) FILTER (WHERE station_name IS NULL)
        FROM silver_readings
        """
    ).fetchone()
    print(f"  air_temp_c   {null_t:>6,} of {total:,}  ({null_t / total:.2%})")
    print(f"  humidity_pct {null_h:>6,} of {total:,}  ({null_h / total:.2%})")
    print(f"  unmatched station metadata: {orphan:,}")

    print("\nCoverage:")
    for row in con.execute(
        """
        SELECT bundesland, station_name,
               min(observed_at) AS first_seen,
               max(observed_at) AS last_seen,
               count(*) AS readings,
               round(avg(air_temp_c), 1) AS mean_temp
        FROM silver_readings
        GROUP BY bundesland, station_name
        ORDER BY bundesland
        """
    ).fetchall():
        land, name, first, last, n, mean = row
        print(f"  {str(land):<22} {str(name):<24} {n:>7,}  mean {mean} C")
        print(f"  {'':<22} {first} to {last}")


def main() -> None:
    if not DB_PATH.exists():
        print(f"{DB_PATH} not found. Run: python -m pipeline.bronze")
        return

    con = duckdb.connect(str(DB_PATH))
    try:
        print("Building silver_stations...")
        print(f"  {build_stations(con):,} rows")

        print("\nBuilding silver_readings...")
        print(f"  {build_readings(con):,} rows")

        report(con)
    finally:
        con.close()

    print("\nNext: quality checks, then the gold aggregates.")


if __name__ == "__main__":
    main()