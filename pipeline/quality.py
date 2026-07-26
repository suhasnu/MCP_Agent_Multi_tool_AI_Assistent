"""Quality gate.

Every check counts violations. Zero means pass. Anything else is reported with
example rows so the failure is diagnosable rather than just red.

Run:  python -m pipeline.quality
      python -m pipeline.quality --strict     (warnings also fail)
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb

DB_PATH = Path("data/weather.duckdb")

ERROR = "ERROR"
WARN = "WARN"

# Germany's records are roughly -37.8 C and 42.6 C. A generous envelope still
# catches unit errors and surviving sentinels.
TEMP_MIN, TEMP_MAX = -50.0, 50.0
MAX_NULL_RATE = 0.10

BUNDESLAENDER = (
    "'Baden-Württemberg','Bayern','Berlin','Brandenburg','Bremen','Hamburg',"
    "'Hessen','Mecklenburg-Vorpommern','Niedersachsen','Nordrhein-Westfalen',"
    "'Rheinland-Pfalz','Saarland','Sachsen','Sachsen-Anhalt',"
    "'Schleswig-Holstein','Thüringen'"
)


@dataclass
class Check:
    name: str
    severity: str
    sql: str          # must return one integer: the violation count
    hint: str
    detail: str = ""  # optional: returns example offending rows


CHECKS = [
    # --- structure ---
    Check(
        "silver_readings is not empty",
        ERROR,
        "SELECT CASE WHEN count(*) = 0 THEN 1 ELSE 0 END FROM silver_readings",
        "Run: python -m pipeline.silver",
    ),
    Check(
        "gold tables are not empty",
        ERROR,
        """
        SELECT (SELECT CASE WHEN count(*) = 0 THEN 1 ELSE 0 END FROM gold_stations)
             + (SELECT CASE WHEN count(*) = 0 THEN 1 ELSE 0 END FROM gold_daily_by_station)
             + (SELECT CASE WHEN count(*) = 0 THEN 1 ELSE 0 END FROM gold_monthly_by_station)
             + (SELECT CASE WHEN count(*) = 0 THEN 1 ELSE 0 END FROM gold_monthly_by_bundesland)
        """,
        "Run: python -m pipeline.gold",
    ),

    # --- uniqueness ---
    # A station cannot observe the same hour twice. Duplicates would silently
    # double-weight those hours in every average downstream.
    Check(
        "no duplicate station-hour readings",
        ERROR,
        """
        SELECT coalesce(sum(n - 1), 0) FROM (
            SELECT count(*) AS n FROM silver_readings
            GROUP BY station_id, observed_at HAVING count(*) > 1
        )
        """,
        "The same archive was probably loaded twice. Rebuild bronze.",
        """
        SELECT station_id, observed_at, count(*) AS copies
        FROM silver_readings GROUP BY station_id, observed_at
        HAVING count(*) > 1 ORDER BY copies DESC LIMIT 5
        """,
    ),

    # --- physical plausibility ---
    Check(
        "temperatures are physically plausible",
        ERROR,
        f"""
        SELECT count(*) FROM silver_readings
        WHERE air_temp_c IS NOT NULL
          AND air_temp_c NOT BETWEEN {TEMP_MIN} AND {TEMP_MAX}
        """,
        "A sentinel survived the cast, or the source changed units.",
        f"""
        SELECT station_name, observed_at, air_temp_c FROM silver_readings
        WHERE air_temp_c NOT BETWEEN {TEMP_MIN} AND {TEMP_MAX} LIMIT 5
        """,
    ),
    Check(
        "no -999 sentinels survived",
        ERROR,
        "SELECT count(*) FROM silver_readings WHERE air_temp_c = -999 OR humidity_pct = -999",
        "NULLIF in pipeline.silver is not matching. Check the sentinel value.",
    ),
    Check(
        "humidity is a percentage",
        ERROR,
        "SELECT count(*) FROM silver_readings WHERE humidity_pct NOT BETWEEN 0 AND 100",
        "Source column may have changed meaning.",
    ),
    Check(
        "no readings from the future",
        ERROR,
        "SELECT count(*) FROM silver_readings WHERE observed_at > current_timestamp",
        "Timestamp parsing is wrong, or the source has bad rows.",
        "SELECT station_name, observed_at FROM silver_readings "
        "WHERE observed_at > current_timestamp LIMIT 5",
    ),

    # --- referential integrity ---
    Check(
        "Bundesland values are recognised",
        ERROR,
        f"SELECT count(DISTINCT bundesland) FROM silver_readings "
        f"WHERE bundesland IS NOT NULL AND bundesland NOT IN ({BUNDESLAENDER})",
        "The station metadata parser is mis-splitting names.",
        f"SELECT DISTINCT bundesland FROM silver_readings "
        f"WHERE bundesland IS NOT NULL AND bundesland NOT IN ({BUNDESLAENDER}) LIMIT 5",
    ),
    Check(
        "readings resolve to known stations",
        WARN,
        "SELECT count(*) FROM silver_readings WHERE station_name IS NULL",
        "Archives were downloaded for stations missing from the metadata file.",
    ),

    # --- aggregate reconciliation ---
    # If gold disagrees with silver, an aggregation dropped or duplicated rows.
    Check(
        "monthly counts reconcile with silver",
        ERROR,
        """
        SELECT abs(
            (SELECT coalesce(sum(reading_count), 0) FROM gold_monthly_by_station)
          - (SELECT count(*) FROM silver_readings
             WHERE air_temp_c IS NOT NULL AND station_name IS NOT NULL)
        )
        """,
        "A GROUP BY in pipeline.gold is losing or duplicating rows.",
    ),
    Check(
        "gold min <= avg <= max",
        ERROR,
        """
        SELECT count(*) FROM gold_monthly_by_station
        WHERE avg_temp_c IS NOT NULL
          AND (avg_temp_c < min_temp_c OR avg_temp_c > max_temp_c)
        """,
        "Aggregate functions are mismatched in pipeline.gold.",
    ),
    Check(
        "every gold station exists in silver",
        ERROR,
        """
        SELECT count(*) FROM gold_monthly_by_station g
        WHERE NOT EXISTS (
            SELECT 1 FROM silver_stations s WHERE s.station_id = g.station_id
        )
        """,
        "The station join in pipeline.gold is wrong.",
    ),

    # --- coverage ---
    Check(
        f"null rate below {MAX_NULL_RATE:.0%}",
        WARN,
        f"""
        SELECT CASE WHEN avg(CASE WHEN air_temp_c IS NULL THEN 1.0 ELSE 0 END)
                     > {MAX_NULL_RATE} THEN 1 ELSE 0 END
        FROM silver_readings
        """,
        "Many sensors were down, or the sentinel handling is over-eager.",
    ),
    Check(
        "each station has at least 24 readings",
        WARN,
        "SELECT count(*) FROM (SELECT station_id FROM silver_readings "
        "GROUP BY station_id HAVING count(*) < 24)",
        "A download was truncated.",
    ),
]


def run_check(con: duckdb.DuckDBPyConnection, check: Check) -> tuple[int, str]:
    """Return the violation count and any error message."""
    try:
        result = con.execute(check.sql).fetchone()[0]
        return int(result or 0), ""
    except Exception as exc:
        return -1, str(exc).splitlines()[0]


def show_examples(con: duckdb.DuckDBPyConnection, check: Check) -> None:
    if not check.detail:
        return
    try:
        rows = con.execute(check.detail).fetchall()
    except Exception:
        return
    for row in rows:
        print(f"        {row}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="warnings fail too")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"{DB_PATH} not found. Run the pipeline first.")
        sys.exit(1)

    con = duckdb.connect(str(DB_PATH), read_only=True)
    failed_errors, failed_warns, broken = [], [], []

    print(f"Running {len(CHECKS)} checks against {DB_PATH}\n")
    try:
        for check in CHECKS:
            count, error = run_check(con, check)

            if count < 0:
                print(f"  BROKEN  {check.name}")
                print(f"          {error}")
                broken.append(check.name)
                continue

            if count == 0:
                print(f"  pass    {check.name}")
                continue

            label = "FAIL" if check.severity == ERROR else "warn"
            print(f"  {label}    {check.name}  ({count:,} violations)")
            print(f"          {check.hint}")
            show_examples(con, check)

            (failed_errors if check.severity == ERROR else failed_warns).append(check.name)
    finally:
        con.close()

    passed = len(CHECKS) - len(failed_errors) - len(failed_warns) - len(broken)
    print(f"\n{passed} passed, {len(failed_errors)} failed, "
          f"{len(failed_warns)} warnings, {len(broken)} broken")

    if broken or failed_errors:
        sys.exit(1)
    if args.strict and failed_warns:
        print("--strict: treating warnings as failures")
        sys.exit(1)

    print("Warehouse is safe to serve.")


if __name__ == "__main__":
    main()
