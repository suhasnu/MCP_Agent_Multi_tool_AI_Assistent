"""Bronze layer tests.

The contract being tested: bronze preserves the source bytes and records where
each row came from. If any of these fail, the layering has leaked and silver's
job has crept upstream.
"""

import zipfile

import duckdb
import pytest

from pipeline import bronze

HEADER = "STATIONS_ID;MESS_DATUM;QN_9;TT_TU;RF_TU;eor"
ROWS = [
    "           44;2025011900;    3;  -1.9; 100.0;eor",
    "           44;2025011901;    3;-999.0;-999.0;eor",
    "           44;2025011902;    1;  14.6;  80.0;eor",
]


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    """Build a one-archive fixture and run bronze against it."""
    raw = tmp_path / "raw"
    raw.mkdir()

    payload = "produkt_tu_stunde_20250119_20260722_00044.txt"
    with zipfile.ZipFile(raw / "stundenwerte_TU_00044_akt.zip", "w") as zf:
        zf.writestr("Metadaten_Geographie_00044.txt", "ignore me")
        zf.writestr("Metadaten_Parameter_tu_stunde_00044.txt", "ignore me")
        zf.writestr(payload, "\n".join([HEADER, *ROWS]).encode("latin-1"))

    monkeypatch.setattr(bronze, "RAW", raw)
    monkeypatch.setattr(bronze, "EXTRACTED", tmp_path / "extracted")
    monkeypatch.chdir(tmp_path)

    bronze.extract_payloads()
    con = duckdb.connect(str(tmp_path / "test.duckdb"))
    bronze.build_readings(con)
    yield con
    con.close()


def test_only_produkt_member_is_extracted(warehouse):
    files = list((warehouse.execute("SELECT DISTINCT source_file FROM bronze_readings")).fetchall())
    assert len(files) == 1
    assert files[0][0].startswith("produkt")


def test_all_rows_land(warehouse):
    assert warehouse.execute("SELECT count(*) FROM bronze_readings").fetchone()[0] == 3


def test_every_column_is_varchar(warehouse):
    types = warehouse.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'bronze_readings' AND column_name NOT IN ('ingested_at')"
    ).fetchall()
    assert all(t[0] == "VARCHAR" for t in types), types


def test_padding_is_preserved(warehouse):
    value = warehouse.execute(
        "SELECT TT_TU FROM bronze_readings WHERE MESS_DATUM = '2025011900'"
    ).fetchone()[0]
    assert value == "  -1.9", repr(value)


def test_sentinels_are_not_cleaned(warehouse):
    """Nulling -999 is silver's job. Doing it here would lose information."""
    count = warehouse.execute(
        "SELECT count(*) FROM bronze_readings WHERE trim(TT_TU) = '-999.0'"
    ).fetchone()[0]
    assert count == 1


def test_provenance_columns_exist(warehouse):
    row = warehouse.execute(
        "SELECT source_file, ingested_at FROM bronze_readings LIMIT 1"
    ).fetchone()
    assert row[0].endswith("_00044.txt")
    assert row[1] is not None


def test_reload_is_idempotent(warehouse):
    """CREATE OR REPLACE means re-running does not duplicate rows."""
    bronze.build_readings(warehouse)
    assert warehouse.execute("SELECT count(*) FROM bronze_readings").fetchone()[0] == 3
