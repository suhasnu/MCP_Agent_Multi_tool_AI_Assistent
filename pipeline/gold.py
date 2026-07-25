"""Gold layer.

Bronze and silver are shaped by the source. Gold is shaped by the questions.

These are the only tables the analytics MCP server exposes, so they are built
for a language model to query: few columns, obvious names, one row per
meaningful bucket. A model should be able to answer "warmest summer in Bayern"
without needing to know that -999 was ever a thing.

Run:  python -m pipeline.gold
"""

from pathlib import Path

import duckdb

DB_PATH = Path("data/weather.duckdb")

# A station reporting 12 hours in a month should not be compared against one
# reporting 744. Downstream queries filter on this.
MIN_COMPLETENESS = 0.8

GOLD_STATIONS = """
CREATE OR REPLACE TABLE gold_stations AS
SELECT
    s.station_id,
    s.station_name,
    s.bundesland,
    s.latitude,
    s.longitude,
    s.elevation_m,
    min(r.observed_at)::DATE AS first_reading,
    max(r.observed_at)::DATE AS last_reading,
    count(*)                 AS total_readings
FROM silver_stations s
JOIN silver_readings r USING (station_id)
GROUP BY ALL;
"""

GOLD_DAILY = """
CREATE OR REPLACE TABLE gold_daily_by_station AS
SELECT
    station_id,
    station_name,
    bundesland,
    observed_at::DATE                    AS day,
    year,
    month,
    round(avg(air_temp_c), 2)            AS avg_temp_c,
    round(min(air_temp_c), 2)            AS min_temp_c,
    round(max(air_temp_c), 2)            AS max_temp_c,
    round(avg(humidity_pct), 2)          AS avg_humidity_pct,
    count(air_temp_c)                    AS reading_count,
    round(count(air_temp_c) / 24.0, 3)   AS completeness
FROM silver_readings
WHERE station_name IS NOT NULL
GROUP BY ALL;
"""

GOLD_MONTHLY_STATION = """
CREATE OR REPLACE TABLE gold_monthly_by_station AS
SELECT
    station_id,
    station_name,
    bundesland,
    year,
    month,
    round(avg(air_temp_c), 2)   AS avg_temp_c,
    round(min(air_temp_c), 2)   AS min_temp_c,
    round(max(air_temp_c), 2)   AS max_temp_c,
    round(avg(humidity_pct), 2) AS avg_humidity_pct,
    count(air_temp_c)           AS reading_count,
    round(
        count(air_temp_c)
        / (EXTRACT(day FROM last_day(make_date(year, month, 1))) * 24.0),
        3
    )                           AS completeness
FROM silver_readings
WHERE station_name IS NOT NULL
GROUP BY station_id, station_name, bundesland, year, month;
"""
#
# Averaging the station monthly averages would weight a station with 40
# readings the same as one with 700. Aggregating the raw readings weights each
# observation equally, which is what "average temperature in Bayern" means.
# Built from silver, NOT from gold_monthly_by_station.
#
# Averaging the station monthly averages would weight a station with 40
# readings the same as one with 700. Aggregating the raw readings weights each
# observation equally, which is what "average temperature in Bayern" means.
GOLD_MONTHLY_LAND = """
CREATE OR REPLACE TABLE gold_monthly_by_bundesland AS
SELECT
    bundesland,
    year,
    month,
    round(avg(air_temp_c), 2)     AS avg_temp_c,
    round(min(air_temp_c), 2)     AS min_temp_c,
    round(max(air_temp_c), 2)     AS max_temp_c,
    round(avg(humidity_pct), 2)   AS avg_humidity_pct,
    count(DISTINCT station_id)    AS station_count,
    count(air_temp_c)             AS reading_count
FROM silver_readings
WHERE bundesland IS NOT NULL
GROUP BY ALL;
"""

TABLES = {
    "gold_stations": GOLD_STATIONS,
    "gold_daily_by_station": GOLD_DAILY,
    "gold_monthly_by_station": GOLD_MONTHLY_STATION,
    "gold_monthly_by_bundesland": GOLD_MONTHLY_LAND,
}


def build(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    counts = {}
    for name, sql in TABLES.items():
        con.execute(sql)
        counts[name] = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
    return counts


def report(con: duckdb.DuckDBPyConnection) -> None:
    silver = con.execute("SELECT count(*) FROM silver_readings").fetchone()[0]
    gold = con.execute("SELECT count(*) FROM gold_monthly_by_station").fetchone()[0]
    print(f"\nCompression: {silver:,} silver rows to {gold:,} monthly rows")

    print("\ngold_monthly_by_station sample:")
    for row in con.execute(
        "SELECT station_name, year, month, avg_temp_c, min_temp_c, max_temp_c, "
        "reading_count, completeness FROM gold_monthly_by_station "
        "ORDER BY year, month, station_name LIMIT 6"
    ).fetchall():
        name, y, m, avg, lo, hi, n, comp = row
        print(f"  {name:<22} {y}-{m:02d}  avg {avg:>6}  range {lo} to {hi}  "
              f"n={n:>4}  {comp:.0%} complete")

    print("\ngold_monthly_by_bundesland sample:")
    for row in con.execute(
        "SELECT bundesland, year, month, avg_temp_c, station_count, reading_count "
        "FROM gold_monthly_by_bundesland ORDER BY year, month, bundesland LIMIT 6"
    ).fetchall():
        land, y, m, avg, stations, n = row
        print(f"  {land:<22} {y}-{m:02d}  avg {avg:>6}  {stations} station(s)  n={n:,}")

    low = con.execute(
        "SELECT count(*) FROM gold_monthly_by_station WHERE completeness < ?",
        [MIN_COMPLETENESS],
    ).fetchone()[0]
    print(f"\nMonths below {MIN_COMPLETENESS:.0%} completeness: {low}")
    print("  (usually the first and last partial months of the archive window)")

    print("\nA question the agent can now answer in one query:")
    print("  'Which station was warmest, among months with good coverage?'")
    for row in con.execute(
        """
        SELECT station_name, bundesland, year, month, avg_temp_c
        FROM gold_monthly_by_station
        WHERE completeness >= ?
        ORDER BY avg_temp_c DESC
        LIMIT 3
        """,
        [MIN_COMPLETENESS],
    ).fetchall():
        print(f"    {row[0]:<22} {row[1]:<20} {row[2]}-{row[3]:02d}  {row[4]} C")


def main() -> None:
    if not DB_PATH.exists():
        print(f"{DB_PATH} not found. Run: python -m pipeline.silver")
        return

    con = duckdb.connect(str(DB_PATH))
    try:
        print("Building gold tables...")
        for name, count in build(con).items():
            print(f"  {name:<30} {count:>7,} rows")
        report(con)
    finally:
        con.close()

    print("\nNext: quality checks, then the analytics MCP server.")


if __name__ == "__main__":
    main()