"""Silver layer tests.

The contract: everything bronze deliberately left alone gets resolved here,
and nothing gets silently dropped along the way.
"""

import duckdb
import pytest

from pipeline import silver

HEADER = "STATIONS_ID;MESS_DATUM;QN_9;TT_TU;RF_TU;eor"
ROWS = [
    ("           44", "2025011900", "    3", "  -1.9", " 100.0"),
    ("           44", "2025011901", "    3", "-999.0", "-999.0"),  # sentinel
    ("           44", "2025071512", "    1", "  24.6", "  55.0"),  # summer
    ("           99", "2025011900", "    3", "   5.0", "  70.0"),  # unknown station
]


@pytest.fixture
def con():
    """Build bronze tables by hand, then run the real silver SQL over them."""
    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE bronze_readings (
            STATIONS_ID VARCHAR, MESS_DATUM VARCHAR, QN_9 VARCHAR,
            TT_TU VARCHAR, RF_TU VARCHAR, eor VARCHAR,
            source_file VARCHAR, ingested_at TIMESTAMP
        );
        """
    )
    for sid, ts, qn, tt, rf in ROWS:
        conn.execute(
            "INSERT INTO bronze_readings VALUES (?,?,?,?,?,'eor','produkt_x.txt',now())",
            [sid, ts, qn, tt, rf],
        )

    conn.execute(
        """
        CREATE TABLE bronze_stations AS SELECT * FROM (VALUES
            (44, 'Großenkneten', 'Niedersachsen', 52.9336, 8.2370, 44,
             '20070401', '20260722')
        ) AS t(station_id, station_name, bundesland, latitude, longitude,
               elevation_m, from_date, to_date);
        """
    )

    silver.build_stations(conn)
    silver.build_readings(conn)
    yield conn
    conn.close()


def test_padding_is_stripped_and_cast(con):
    ids = con.execute("SELECT DISTINCT station_id FROM silver_readings ORDER BY 1").fetchall()
    assert [r[0] for r in ids] == [44, 99]


def test_mess_datum_becomes_a_timestamp(con):
    value = con.execute(
        "SELECT observed_at FROM silver_readings WHERE station_id = 44 ORDER BY observed_at"
    ).fetchone()[0]
    assert value.year == 2025 and value.month == 1 and value.day == 19
    assert value.hour == 0


def test_sentinel_becomes_null(con):
    row = con.execute(
        "SELECT air_temp_c, humidity_pct FROM silver_readings "
        "WHERE observed_at = '2025-01-19 01:00:00' AND station_id = 44"
    ).fetchone()
    assert row == (None, None)


def test_quality_level_survives_the_sentinel_row(con):
    """QN_9 of 3 is a real value on a row whose measurements are missing."""
    qn = con.execute(
        "SELECT quality_level FROM silver_readings "
        "WHERE observed_at = '2025-01-19 01:00:00' AND station_id = 44"
    ).fetchone()[0]
    assert qn == 3


def test_real_measurements_are_preserved(con):
    temp = con.execute(
        "SELECT air_temp_c FROM silver_readings "
        "WHERE observed_at = '2025-01-19 00:00:00' AND station_id = 44"
    ).fetchone()[0]
    assert temp == pytest.approx(-1.9)


def test_station_metadata_is_joined(con):
    row = con.execute(
        "SELECT station_name, bundesland FROM silver_readings WHERE station_id = 44 LIMIT 1"
    ).fetchone()
    assert row == ("Großenkneten", "Niedersachsen")


def test_unknown_station_is_kept_not_dropped(con):
    """A LEFT JOIN means an unmatched station loses its metadata, not its data."""
    row = con.execute(
        "SELECT station_name, air_temp_c FROM silver_readings WHERE station_id = 99"
    ).fetchone()
    assert row[0] is None
    assert row[1] == pytest.approx(5.0)


def test_eor_column_is_gone(con):
    cols = [
        c[0]
        for c in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'silver_readings'"
        ).fetchall()
    ]
    assert "eor" not in cols


def test_calendar_parts_are_derived(con):
    row = con.execute(
        "SELECT year, month, day, hour FROM silver_readings "
        "WHERE observed_at = '2025-07-15 12:00:00'"
    ).fetchone()
    assert row == (2025, 7, 15, 12)


def test_no_rows_are_lost(con):
    assert con.execute("SELECT count(*) FROM silver_readings").fetchone()[0] == len(ROWS)


def test_rebuild_is_idempotent(con):
    silver.build_readings(con)
    assert con.execute("SELECT count(*) FROM silver_readings").fetchone()[0] == len(ROWS)
