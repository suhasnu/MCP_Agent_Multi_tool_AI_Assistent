"""Gold layer tests.

The headline test is test_bundesland_average_is_weighted_by_readings. Everything
else guards the shape of the tables the agent will see.
"""

import duckdb
import pytest

from pipeline import gold

# Two stations in one Bundesland with deliberately lopsided coverage:
#   Munich  reports 100 hours at 10 C
#   Passau  reports  10 hours at 30 C
#
# Naive average of the two station means = (10 + 30) / 2 = 20 C
# Reading-weighted average = (100*10 + 10*30) / 110 = 11.8 C
#
# The second is correct. "Average temperature in Bayern" means every observed
# hour counts once, not every station counts once.
BULK_STATION = (1, "Munich", 100, 10.0)
SPARSE_STATION = (2, "Passau", 10, 30.0)


@pytest.fixture
def con():
    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE silver_stations (
            station_id INTEGER, station_name VARCHAR, bundesland VARCHAR,
            latitude DOUBLE, longitude DOUBLE, elevation_m INTEGER,
            active_from DATE, active_to DATE
        );
        CREATE TABLE silver_readings (
            station_id INTEGER, observed_at TIMESTAMP, quality_level INTEGER,
            air_temp_c DOUBLE, humidity_pct DOUBLE, station_name VARCHAR,
            bundesland VARCHAR, latitude DOUBLE, longitude DOUBLE,
            elevation_m INTEGER, source_file VARCHAR, ingested_at TIMESTAMP,
            year INTEGER, month INTEGER, day INTEGER, hour INTEGER
        );
        """
    )

    for sid, name, hours, temp in (BULK_STATION, SPARSE_STATION):
        conn.execute(
            "INSERT INTO silver_stations VALUES (?,?,'Bayern',48.1,11.5,500,"
            "'2020-01-01','2026-01-01')",
            [sid, name],
        )
        for h in range(hours):
            day = 1 + h // 24
            hour = h % 24
            conn.execute(
                "INSERT INTO silver_readings VALUES "
                "(?, ?, 3, ?, 60.0, ?, 'Bayern', 48.1, 11.5, 500, 'f.txt', now(), "
                "2025, 6, ?, ?)",
                [sid, f"2025-06-{day:02d} {hour:02d}:00:00", temp, name, day, hour],
            )

    # one NULL reading, to prove nulls are excluded rather than counted as zero
    conn.execute(
        "INSERT INTO silver_readings VALUES "
        "(1, '2025-06-28 00:00:00', 3, NULL, NULL, 'Munich', 'Bayern', "
        "48.1, 11.5, 500, 'f.txt', now(), 2025, 6, 28, 0)"
    )

    gold.build(conn)
    yield conn
    conn.close()


def test_bundesland_average_is_weighted_by_readings(con):
    """The trap: averaging station averages over-weights sparse stations."""
    actual = con.execute(
        "SELECT avg_temp_c FROM gold_monthly_by_bundesland WHERE bundesland = 'Bayern'"
    ).fetchone()[0]

    naive = con.execute(
        "SELECT round(avg(avg_temp_c), 2) FROM gold_monthly_by_station "
        "WHERE bundesland = 'Bayern'"
    ).fetchone()[0]

    assert actual == pytest.approx(11.82, abs=0.01)
    assert naive == pytest.approx(20.0, abs=0.01)
    assert actual != naive


def test_nulls_are_excluded_not_counted_as_zero(con):
    """count(air_temp_c) skips NULLs; count(*) would not."""
    row = con.execute(
        "SELECT avg_temp_c, reading_count FROM gold_monthly_by_station "
        "WHERE station_name = 'Munich'"
    ).fetchone()
    assert row[0] == pytest.approx(10.0)
    assert row[1] == BULK_STATION[2]          # 100, not 101


def test_completeness_reflects_hours_in_month(con):
    """June has 30 days, so 720 possible hourly readings."""
    completeness = con.execute(
        "SELECT completeness FROM gold_monthly_by_station WHERE station_name = 'Munich'"
    ).fetchone()[0]
    assert completeness == pytest.approx(100 / 720, abs=0.001)


def test_station_count_is_distinct(con):
    count = con.execute(
        "SELECT station_count FROM gold_monthly_by_bundesland WHERE bundesland = 'Bayern'"
    ).fetchone()[0]
    assert count == 2


def test_min_and_max_span_both_stations(con):
    row = con.execute(
        "SELECT min_temp_c, max_temp_c FROM gold_monthly_by_bundesland "
        "WHERE bundesland = 'Bayern'"
    ).fetchone()
    assert row == (pytest.approx(10.0), pytest.approx(30.0))


def test_daily_table_groups_by_calendar_day(con):
    """100 consecutive hours from day 1 span 5 calendar days, plus the
    null-only day 28, which still gets a row with reading_count 0."""
    rows = con.execute(
        "SELECT day, reading_count FROM gold_daily_by_station "
        "WHERE station_name = 'Munich' ORDER BY day"
    ).fetchall()
    assert len(rows) == 6

    with_data = [r for r in rows if r[1] > 0]
    assert len(with_data) == 5
    assert sum(r[1] for r in with_data) == BULK_STATION[2]

    null_day = [r for r in rows if r[1] == 0]
    assert len(null_day) == 1


def test_gold_stations_has_one_row_per_station(con):
    assert con.execute("SELECT count(*) FROM gold_stations").fetchone()[0] == 2


def test_gold_is_much_smaller_than_silver(con):
    silver = con.execute("SELECT count(*) FROM silver_readings").fetchone()[0]
    monthly = con.execute("SELECT count(*) FROM gold_monthly_by_station").fetchone()[0]
    assert monthly < silver / 10


def test_rebuild_is_idempotent(con):
    before = con.execute("SELECT count(*) FROM gold_monthly_by_station").fetchone()[0]
    gold.build(con)
    after = con.execute("SELECT count(*) FROM gold_monthly_by_station").fetchone()[0]
    assert before == after
