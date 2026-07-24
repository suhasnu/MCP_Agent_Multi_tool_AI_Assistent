"""Analytics MCP server.

Exposes read-only SQL over the gold layer. This is where the two halves of the
project meet: the agent writes queries against a warehouse the pipeline built.

Run:  python -m servers.analytics.main
      MCP_TRANSPORT=sse python -m servers.analytics.main
"""

import os
import re
import threading
from pathlib import Path

import duckdb
from mcp.server.fastmcp import FastMCP

DB_PATH = Path(os.getenv("WAREHOUSE_DB", "data/weather.duckdb"))

# The 413 lesson: a tool's return value is prompt real estate. Every result
# passes through the model's context on every subsequent loop iteration.
MAX_ROWS = 50
MAX_CELL_CHARS = 60

# The documented interface. Gold is what most questions need; silver is listed
# because some questions genuinely need hourly detail that gold aggregates
# away. Bronze stays private: its column names describe the file format
# (TT_TU, RF_TU) rather than anything a question would refer to.
PUBLIC_TABLES = {
    "gold_stations": "One row per weather station: location, elevation, coverage window.",
    "gold_daily_by_station": "Daily temperature and humidity per station.",
    "gold_monthly_by_station": "Monthly aggregates per station, with a completeness fraction.",
    "gold_monthly_by_bundesland": "Monthly aggregates per German federal state.",
    "silver_readings": "Hourly readings. Large. Prefer a gold table unless you need hour-level detail.",
}

READ_ONLY_START = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)

mcp = FastMCP(
    name="analytics",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", "8060")),
)

_conn: duckdb.DuckDBPyConnection | None = None
_lock = threading.Lock()


def _connect() -> duckdb.DuckDBPyConnection:
    """One shared read-only connection, hardened on first use."""
    global _conn
    if _conn is None:
        if not DB_PATH.exists():
            raise FileNotFoundError(
                f"{DB_PATH} not found. Build it with: python -m pipeline.gold"
            )
        _conn = duckdb.connect(str(DB_PATH), read_only=True)
        _conn.execute("SET enable_external_access=false")
        _conn.execute("SET lock_configuration=true")
    return _conn


def _check_statement(sql: str) -> str | None:
    """Return an error message, or None if the statement looks like a query."""
    stripped = sql.strip().rstrip(";")
    if not stripped:
        return "Empty query."
    if ";" in stripped:
        return "Only one statement at a time. Remove the semicolon."
    if not READ_ONLY_START.match(stripped):
        return "Only SELECT and WITH queries are allowed. This database is read-only."
    return None


def _run(con, sql: str) -> tuple[list[str], list[tuple]]:
    """Execute and return (column_names, rows).

    Deliberately plain. An earlier version wrapped this in a threading.Timer
    that called con.interrupt(), and used fetchdf() to get a pandas frame.
    On Windows that combination hung indefinitely: list_tables, which uses
    fetchall() and no timer, worked on the same connection in the same
    process. Cancellation is not worth a deadlock, and the row cap already
    bounds the result size.
    """
    cursor = con.execute(sql)
    columns = [d[0] for d in cursor.description]
    rows = cursor.fetchmany(MAX_ROWS + 1)
    return columns, rows


def _as_markdown(columns: list[str], rows: list[tuple]) -> str:
    """Render rows compactly, truncating wide cells."""
    truncated = len(rows) > MAX_ROWS
    shown = rows[:MAX_ROWS]

    def cell(value) -> str:
        text = "" if value is None else str(value)
        return text[:MAX_CELL_CHARS]

    header = "| " + " | ".join(columns) + " |"
    divider = "|" + "|".join(["---"] * len(columns)) + "|"
    body = ["| " + " | ".join(cell(v) for v in row) + " |" for row in shown]

    out = "\n".join([header, divider, *body])
    if truncated:
        out += (
            f"\n\n(more than {MAX_ROWS} rows, first {MAX_ROWS} shown. "
            "Add LIMIT or aggregate.)"
        )
    return out


def _schema_summary() -> str:
    """Compact one-line-per-table schema.

    This exists because a model cannot be relied on to call describe_schema
    before writing SQL: providers emit tool calls in parallel, so run_query can
    be issued in the same message as describe_schema, before its result exists.
    Giving the columns up front removes the dependency.
    """
    con = _connect()
    lines = []
    for name in PUBLIC_TABLES:
        try:
            cols = [r[0] for r in con.execute(f"DESCRIBE {name}").fetchall()]
        except Exception:
            continue
        lines.append(f"{name}({', '.join(cols)})")
    return "\n".join(lines)


@mcp.tool()
def list_tables() -> str:
    """List the tables available for querying, with row counts.

    Call this first when you do not know what data exists.
    """
    con = _connect()
    lines = []
    for name, purpose in PUBLIC_TABLES.items():
        try:
            count = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            cols = [r[0] for r in con.execute(f"DESCRIBE {name}").fetchall()]
        except Exception:
            continue
        lines.append(f"{name} ({count:,} rows): {purpose}")
        lines.append(f"  columns: {', '.join(cols)}")
    return "\n".join(lines) if lines else "No tables found. The warehouse is empty."


@mcp.tool()
def describe_schema(table: str) -> str:
    """Get the column names and types for one table.

    Call this before writing a query so the column names are correct.

    Args:
        table: Table name as returned by list_tables.
    """
    if table not in PUBLIC_TABLES:
        return (
            f"Unknown table '{table}'. Available: {', '.join(PUBLIC_TABLES)}"
        )

    con = _connect()
    rows = con.execute(f"DESCRIBE {table}").fetchall()
    cols = "\n".join(f"  {r[0]}: {r[1]}" for r in rows)

    sample = con.execute(f"SELECT * FROM {table} LIMIT 1").fetchall()
    example = ""
    if sample:
        pairs = zip([r[0] for r in rows], sample[0])
        example = "\nExample row:\n" + "\n".join(
            f"  {name} = {value}" for name, value in pairs
        )

    return f"{table}: {PUBLIC_TABLES[table]}\n\nColumns:\n{cols}{example}"


QUERY_GUIDANCE = """Run a read-only SQL query against the German weather warehouse.

Argument `sql`: a single DuckDB SELECT or WITH statement. No semicolons, no DDL.
At most 50 rows are returned, so aggregate or add LIMIT for large results.

Temperatures are Celsius, humidity is a percentage, and missing readings are
NULL rather than a sentinel value. In the monthly tables, `completeness` is the
fraction of possible hourly readings actually recorded: filter on
`completeness >= 0.8` before comparing months against each other.

Use the exact table and column names below. Do not guess names."""


def _build_query_description() -> str:
    """Embed the live schema in the tool description.

    Providers emit tool calls in parallel, so the model can issue run_query in
    the same message as list_tables, before any result exists. Anything the
    model must know before writing SQL therefore has to arrive in the system
    message, not from a prior tool result.
    """
    try:
        schema = _schema_summary()
    except Exception:
        schema = "(warehouse not built; call list_tables at runtime)"
    return f"{QUERY_GUIDANCE}\n\nTables and columns:\n{schema}"


@mcp.tool(description=_build_query_description())
def run_query(sql: str) -> str:
    problem = _check_statement(sql)
    if problem:
        return f"Query rejected: {problem}"

    try:
        con = _connect()
    except FileNotFoundError as exc:
        return str(exc)

    with _lock:
        try:
            columns, rows = _run(con, sql.strip().rstrip(";"))
        except Exception as exc:
            message = str(exc).splitlines()[0]
            # A bare "column not found" gives the model nothing to correct
            # towards, and it loops. Naming the real columns lets it recover.
            # DuckDB words these differently: Binder Error for columns,
            # Catalog Error for tables.
            lowered = message.lower()
            if any(
                marker in lowered
                for marker in ("not found", "does not exist", "binder error", "catalog error")
            ):
                return (
                    f"Query failed: {message}\n\n"
                    f"Available tables and columns:\n{_schema_summary()}"
                )
            return f"Query failed: {message}"

    if not rows:
        return "Query returned no rows."
    return _as_markdown(columns, rows)


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)