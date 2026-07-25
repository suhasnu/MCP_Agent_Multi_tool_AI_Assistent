"""Analytics MCP server tests.

Three concerns: the tools return what a model can use, the guard holds, and no
result can blow the context window.
"""

import duckdb
import pytest

from servers.analytics import main as analytics


@pytest.fixture
def server(tmp_path, monkeypatch):
    """A miniature warehouse wired into the server module."""
    db = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        """
        CREATE TABLE gold_monthly_by_bundesland AS SELECT
            bundesland,
            CAST(year AS INTEGER) AS year,
            CAST(month AS INTEGER) AS month,
            CAST(avg_temp_c AS DOUBLE) AS avg_temp_c,
            CAST(min_temp_c AS DOUBLE) AS min_temp_c,
            CAST(max_temp_c AS DOUBLE) AS max_temp_c,
            CAST(avg_humidity_pct AS DOUBLE) AS avg_humidity_pct,
            CAST(station_count AS BIGINT) AS station_count,
            CAST(reading_count AS BIGINT) AS reading_count
        FROM (VALUES
            ('Bayern', 2025, 6, 18.5, 8.0, 31.0, 62.0, 3, 2100),
            ('Hessen', 2025, 6, 19.2, 9.0, 33.0, 58.0, 2, 1400),
            ('Berlin', 2025, 6, 20.1, 10.0, 34.0, 55.0, 1,  700)
        ) AS t(bundesland, year, month, avg_temp_c, min_temp_c, max_temp_c,
               avg_humidity_pct, station_count, reading_count);

        CREATE TABLE gold_stations AS SELECT * FROM (VALUES
            (1, 'München', 'Bayern', 48.1, 11.5, 515, DATE '2025-01-01',
             DATE '2025-12-31', 8760)
        ) AS t(station_id, station_name, bundesland, latitude, longitude,
               elevation_m, first_reading, last_reading, total_readings);

        CREATE TABLE gold_daily_by_station AS SELECT 1 AS station_id;
        CREATE TABLE gold_monthly_by_station AS SELECT 1 AS station_id;
        CREATE TABLE silver_readings AS
            SELECT i AS station_id, 15.0 AS air_temp_c FROM range(200) AS t(i);
        """
    )
    con.close()

    monkeypatch.setattr(analytics, "DB_PATH", db)
    monkeypatch.setattr(analytics, "_conn", None)
    yield analytics
    if analytics._conn is not None:
        analytics._conn.close()
        analytics._conn = None


# --- tools ---

def test_list_tables_reports_counts(server):
    out = server.list_tables()
    assert "gold_monthly_by_bundesland (3 rows)" in out
    assert "gold_stations (1 rows)" in out


def test_describe_schema_includes_types_and_example(server):
    out = server.describe_schema("gold_monthly_by_bundesland")
    assert "bundesland: VARCHAR" in out
    assert "avg_temp_c: DOUBLE" in out
    assert "Example row:" in out
    assert "Bayern" in out


def test_describe_schema_rejects_unknown_table(server):
    out = server.describe_schema("secrets")
    assert "Unknown table" in out
    assert "gold_stations" in out


def test_run_query_returns_markdown(server):
    out = server.run_query(
        "SELECT bundesland, avg_temp_c FROM gold_monthly_by_bundesland "
        "ORDER BY avg_temp_c DESC"
    )
    assert out.startswith("| bundesland | avg_temp_c |")
    assert "Berlin" in out
    assert out.index("Berlin") < out.index("Bayern")


def test_empty_result_is_explained_not_blank(server):
    out = server.run_query("SELECT * FROM gold_monthly_by_bundesland WHERE year = 1800")
    assert "no rows" in out
    # the hint should name real stations so the model can recover
    assert "station names are" in out.lower()


# --- guard ---

@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE gold_stations",
        "DELETE FROM gold_stations",
        "INSERT INTO gold_stations VALUES (1)",
        "UPDATE gold_stations SET station_id = 2",
        "CREATE TABLE evil AS SELECT 1",
        "ATTACH 'other.db'",
    ],
)
def test_non_read_statements_are_rejected(server, sql):
    assert server.run_query(sql).startswith("Query rejected")


def test_stacked_statements_are_rejected(server):
    out = server.run_query("SELECT 1; DROP TABLE gold_stations")
    assert "one statement" in out


def test_empty_query_is_rejected(server):
    assert server.run_query("   ").startswith("Query rejected")


def test_with_clause_is_allowed(server):
    out = server.run_query(
        "WITH t AS (SELECT * FROM gold_monthly_by_bundesland) "
        "SELECT count(*) AS n FROM t"
    )
    assert "| n |" in out


def test_external_file_access_is_blocked(server):
    out = server.run_query("SELECT * FROM read_csv('/etc/passwd')")
    assert out.startswith("Query failed")


def test_write_is_blocked_by_the_engine_not_just_the_regex(server):
    """Defence in depth: even if the regex were bypassed, the connection is
    read-only, so DuckDB itself refuses."""
    con = server._connect()
    with pytest.raises(Exception):
        con.execute("CREATE TABLE bypass AS SELECT 1")


def test_syntax_error_is_reported_readably(server):
    out = server.run_query("SELECT FROM WHERE")
    assert out.startswith("Query failed")
    assert "\n" not in out.strip()


# --- token discipline ---

def test_results_are_capped(server):
    """fetchmany(MAX_ROWS + 1) caps the result without materialising all of it,
    so the notice says "more than N" rather than an exact total."""
    out = server.run_query("SELECT * FROM silver_readings")
    rows = [line for line in out.splitlines() if line.startswith("| ")]
    assert len(rows) <= server.MAX_ROWS + 1        # +1 for the header
    assert f"more than {server.MAX_ROWS} rows" in out


def test_small_result_has_no_truncation_notice(server):
    out = server.run_query("SELECT bundesland FROM gold_monthly_by_bundesland")
    assert "more than" not in out


def test_nulls_render_as_empty_cells(server):
    """str(None) would print the word None into the model's context."""
    out = server.run_query("SELECT NULL AS a, 1 AS b")
    assert "None" not in out


def test_wide_cells_are_truncated(server):
    out = server.run_query(f"SELECT repeat('x', 500) AS wide")
    longest = max(len(line) for line in out.splitlines())
    assert longest < server.MAX_CELL_CHARS + 20


def test_query_guidance_documents_the_data(server):
    """The tool description is the prompt. It must explain units and the
    completeness filter, or the model will compare partial months."""
    doc = server.QUERY_GUIDANCE
    assert "Celsius" in doc
    assert "completeness" in doc
    assert "NULL" in doc


def test_tool_description_embeds_the_schema(server):
    """Regression for 2026-07-24: the model emitted list_tables and run_query
    in one message, so it wrote SQL before any schema arrived and invented
    table names. Anything needed before the first query must be in the
    description, which reaches the model in the system message."""
    description = server._build_query_description()
    assert "gold_monthly_by_bundesland(" in description
    assert "bundesland" in description
    assert "avg_temp_c" in description
    assert "Do not guess names" in description


# --- regression: the loop from 2026-07-24 ---
#
# The agent emitted describe_schema and run_query as parallel tool calls, so it
# wrote SQL before the schema came back, guessed `state` and `avg_temp`, and
# then looped because the binder error named no valid alternative.

def test_list_tables_includes_columns(server):
    """One call must be enough. describe_schema may never be awaited."""
    out = server.list_tables()
    assert "columns:" in out
    assert "bundesland" in out
    assert "avg_temp_c" in out


def test_column_error_lists_available_columns(server):
    out = server.run_query(
        "SELECT state FROM gold_monthly_by_bundesland ORDER BY avg_temp DESC LIMIT 1"
    )
    assert "Query failed" in out
    assert "Available tables and columns" in out
    assert "avg_temp_c" in out          # the name it should have used
    assert "bundesland" in out


def test_unknown_table_error_lists_tables(server):
    out = server.run_query("SELECT * FROM monthly_germany")
    assert "gold_monthly_by_bundesland" in out


def test_successful_query_does_not_carry_schema_overhead(server):
    """The schema dump costs tokens, so it must appear only on failure."""
    out = server.run_query("SELECT bundesland FROM gold_monthly_by_bundesland LIMIT 1")
    assert "Available tables and columns" not in out