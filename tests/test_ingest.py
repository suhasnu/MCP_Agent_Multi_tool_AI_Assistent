"""Parser tests.

Every fixture below is a verbatim line from the real DWD archive, captured by
scripts/explore_dwd.py. Invented fixtures would have hidden the fact that the
station file's dashes header disagrees with its data rows.

"""

import pytest

from pipeline.ingest import parse_reading_row, parse_station_line

STATION_ROWS = [
    "00003 19500401 20110331            202     50.7827    6.0941 Aachen                                   Nordrhein-Westfalen                     Frei",
    "00044 20070401 20260722             44     52.9336    8.2370 Großenkneten                             Niedersachsen                           Frei",
    "00052 19760101 19880101             46     53.6623   10.1990 Ahrensburg-Wulfsdorf                     Schleswig-Holstein                      Frei",
    "00071 20091201 20191231            759     48.2156    8.9784 Albstadt-Badkap                          Baden-Württemberg                       Frei",
]

HEADER = "Stations_id von_datum bis_datum Stationshoehe geoBreite geoLaenge Stationsname Bundesland Abgabe"
DASHES = "----------- --------- --------- ------------- --------- --------- ------------ ---------- -"

READING_ROWS = [
    "           44;2025011900;    3;  -1.9; 100.0;eor",
    "           44;2025011901;    3;  -2.0; 100.0;eor",
    "           44;2026072223;    1;  14.6;  80.0;eor",
]
SENTINEL_ROW = "           44;2025030512;    3;-999.0;-999.0;eor"
READING_HEADER = "STATIONS_ID;MESS_DATUM;QN_9;TT_TU;RF_TU;eor"


# --- station metadata ---

@pytest.mark.parametrize("line", STATION_ROWS)
def test_station_rows_parse(line):
    assert parse_station_line(line) is not None


def test_station_fields_are_correct():
    s = parse_station_line(STATION_ROWS[0])
    assert s.station_id == 3
    assert s.station_name == "Aachen"
    assert s.bundesland == "Nordrhein-Westfalen"
    assert s.latitude == pytest.approx(50.7827)
    assert s.longitude == pytest.approx(6.0941)
    assert s.elevation_m == 202


def test_station_name_with_spaces_is_preserved():
    line = "00161 20040101 20260722            180     50.6516    8.3618 Bad Marienberg                           Rheinland-Pfalz                         Frei"
    assert parse_station_line(line).station_name == "Bad Marienberg"


def test_station_row_without_abgabe_column():
    line = "00164 19310101 19860101             46     53.7660    9.6890 Bad Bramstedt                            Schleswig-Holstein"
    s = parse_station_line(line)
    assert s.station_name == "Bad Bramstedt"
    assert s.bundesland == "Schleswig-Holstein"


@pytest.mark.parametrize("junk", [HEADER, DASHES, "", "   "])
def test_non_data_rows_are_rejected(junk):
    assert parse_station_line(junk) is None


# --- observations ---

@pytest.mark.parametrize("row", READING_ROWS)
def test_reading_rows_parse(row):
    assert parse_reading_row(row) is not None


def test_reading_fields_are_stripped_and_cast():
    r = parse_reading_row(READING_ROWS[0])
    assert r["station_id"] == 44           # was space padded
    assert r["mess_datum"] == "2025011900"
    assert r["quality_level"] == 3
    assert r["air_temp_c"] == pytest.approx(-1.9)
    assert r["humidity_pct"] == pytest.approx(100.0)


def test_missing_sentinel_becomes_none():
    r = parse_reading_row(SENTINEL_ROW)
    assert r["air_temp_c"] is None
    assert r["humidity_pct"] is None
    assert r["quality_level"] == 3         # this one is a real value


def test_reading_header_is_rejected():
    assert parse_reading_row(READING_HEADER) is None