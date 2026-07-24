"""Quality gate tests.

A check that never fails is decoration. These build a healthy miniature
warehouse, corrupt it one way at a time, and assert the matching check fires
and the others stay quiet.
"""

import duckdb
import pytest

from pipeline import gold, quality

BY_NAME = {c.name: c for c in quality.CHECKS}


def make_warehouse() -> duckdb.DuckDBPyConnection:
    """A small, valid warehouse: one station, 48 clean hourly readings."""
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE silver_stations (
            station_id INTEGER, station_name VARCHAR, bundesland VARCHAR,
            latitude DOUBLE, longitude DOUBLE, elevation_m INTEGER,
            active_from DATE, active_to DATE
        );
        INSERT INTO silver_stations VALUES
            (1, 'Frankfurt/Main', 'Hessen', 50.02, 8.52, 100,
             '2020-01-01', '2026-01-01');

        CREATE TABLE silver_readings (
            station_id INTEGER, observed_at TIMESTAMP, quality_level INTEGER,
            air_temp_c DOUBLE, humidity_pct DOUBLE, station_name VARCHAR,
            bundesland VARCHAR, latitude DOUBLE, longitude DOUBLE,
            elevation_m INTEGER, source_file VARCHAR, ingested_at TIMESTAMP,
            year INTEGER, month INTEGER, day INTEGER, hour INTEGER
        );
        INSERT INTO silver_readings
        SELECT 1,
               TIMESTAMP '2025-06-01 00:00:00' + INTERVAL (i) HOUR,
               3, 15.0 + (i % 10), 60.0,
               'Frankfurt/Main', 'Hessen', 50.02, 8.52, 100,
               'produkt_x.txt', now(),
               2025, 6, 1 + i / 24, i % 24
        FROM range(48) AS t(i);
        """
    )
    gold.build(con)
    return con


def run(con, name: str) -> int:
    count, error = quality.run_check(con, BY_NAME[name])
    assert error == "", f"check is broken: {error}"
    return count


@pytest.fixture
def con():
    conn = make_warehouse()
    yield conn
    conn.close()


def test_healthy_warehouse_passes_every_check(con):
    failures = {
        c.name: run(con, c.name) for c in quality.CHECKS if run(con, c.name) != 0
    }
    assert failures == {}


def test_duplicate_readings_are_caught(con):
    assert run(con, "no duplicate station-hour readings") == 0
    con.execute("INSERT INTO silver_readings SELECT * FROM silver_readings LIMIT 2")
    assert run(con, "no duplicate station-hour readings") == 2


def test_surviving_sentinel_is_caught(con):
    con.execute("UPDATE silver_readings SET air_temp_c = -999 WHERE hour = 5")
    assert run(con, "no -999 sentinels survived") > 0
    assert run(con, "temperatures are physically plausible") > 0


def test_impossible_temperature_is_caught(con):
    con.execute("UPDATE silver_readings SET air_temp_c = 300 WHERE hour = 5")
    assert run(con, "temperatures are physically plausible") > 0


def test_future_reading_is_caught(con):
    con.execute("UPDATE silver_readings SET observed_at = '2099-01-01' WHERE hour = 5")
    assert run(con, "no readings from the future") > 0


def test_unknown_bundesland_is_caught(con):
    con.execute("UPDATE silver_readings SET bundesland = 'Atlantis'")
    assert run(con, "Bundesland values are recognised") > 0


def test_out_of_range_humidity_is_caught(con):
    con.execute("UPDATE silver_readings SET humidity_pct = 150 WHERE hour = 5")
    assert run(con, "humidity is a percentage") > 0


def test_stale_gold_is_caught_by_reconciliation(con):
    """Adding readings without rebuilding gold must not go unnoticed."""
    assert run(con, "monthly counts reconcile with silver") == 0
    con.execute(
        "INSERT INTO silver_readings SELECT * REPLACE "
        "(observed_at + INTERVAL 100 DAY AS observed_at) FROM silver_readings LIMIT 5"
    )
    assert run(con, "monthly counts reconcile with silver") == 5


def test_orphan_readings_only_warn(con):
    """A reading without station metadata is thin data, not corruption."""
    check = BY_NAME["readings resolve to known stations"]
    assert check.severity == quality.WARN


def test_empty_silver_is_an_error(con):
    con.execute("DELETE FROM silver_readings")
    assert run(con, "silver_readings is not empty") == 1
    assert BY_NAME["silver_readings is not empty"].severity == quality.ERROR


def test_every_check_has_a_hint(con):
    """A failure the reader cannot act on is a failure they will ignore."""
    assert all(c.hint for c in quality.CHECKS)