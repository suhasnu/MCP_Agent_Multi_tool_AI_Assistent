"""Scoring logic tests.

No LLM and no network, so these run in CI. The database is built per test via
tmp_path rather than at a hardcoded path: an earlier version used /tmp/... and
Windows read the leading slashes as a UNC network share.
"""

import duckdb
import pytest

from evals.run import extract_sql, score_tools
from evals.sql_scoring import execution_match
from orchestrator.tracing import ToolEvent, Trace

GOLD = "SELECT bundesland FROM t ORDER BY avg_temp_c DESC LIMIT 1"


@pytest.fixture
def db(tmp_path):
    """A tiny warehouse. Returns the path as a string."""
    path = tmp_path / "score.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        """
        CREATE TABLE t AS SELECT * FROM (VALUES
            ('Bayern', 18.5), ('Hessen', 19.2), ('Berlin', 20.1)
        ) AS v(bundesland, avg_temp_c);

        CREATE TABLE f AS SELECT 1.23456 AS x;
        """
    )
    con.close()
    return str(path)


# --- execution accuracy ---

def test_identical_query_matches(db):
    ok, why = execution_match(GOLD, GOLD, db)
    assert ok, why


def test_different_syntax_same_result_matches(db):
    """The reason we compare results, not strings."""
    predicted = (
        "SELECT bundesland FROM t WHERE avg_temp_c = (SELECT max(avg_temp_c) FROM t)"
    )
    ok, why = execution_match(predicted, GOLD, db)
    assert ok, why


def test_different_column_alias_still_matches(db):
    predicted = "SELECT bundesland AS warmest FROM t ORDER BY avg_temp_c DESC LIMIT 1"
    ok, why = execution_match(predicted, GOLD, db)
    assert ok, why


def test_row_order_is_ignored(db):
    gold = "SELECT bundesland FROM t ORDER BY bundesland"
    predicted = "SELECT bundesland FROM t ORDER BY bundesland DESC"
    ok, why = execution_match(predicted, gold, db)
    assert ok, why


def test_wrong_answer_fails(db):
    predicted = "SELECT bundesland FROM t ORDER BY avg_temp_c ASC LIMIT 1"
    ok, why = execution_match(predicted, GOLD, db)
    assert not ok
    assert "different values" in why


def test_missing_limit_fails_on_shape(db):
    ok, why = execution_match("SELECT bundesland FROM t", GOLD, db)
    assert not ok
    assert "shape" in why


def test_broken_sql_reports_the_error(db):
    ok, why = execution_match("SELECT FROM WHERE", GOLD, db)
    assert not ok
    assert "query failed" in why


def test_no_query_generated(db):
    ok, why = execution_match("", GOLD, db)
    assert not ok
    assert "no query" in why


def test_float_rounding_tolerance(db):
    """1.23456 and 1.23457 are the same answer at two decimal places."""
    ok, why = execution_match("SELECT 1.23457 AS x", "SELECT x FROM f", db)
    assert ok, why


# --- tool selection ---

def test_tools_subset_passes():
    assert score_tools(["list_tables", "run_query"], ["run_query"])


def test_missing_expected_tool_fails():
    assert not score_tools(["list_tables"], ["run_query"])


def test_no_tools_expected_and_none_used():
    assert score_tools([], [])


def test_no_tools_expected_but_one_used_fails():
    """Querying the warehouse to answer "what does MCP stand for" is wrong."""
    assert not score_tools(["run_query"], [])


# --- sql extraction from a trace ---

def _event(tool: str, args: dict) -> ToolEvent:
    return ToolEvent("id", tool, args, 1, True, "")


def test_extract_takes_the_last_query():
    """After a failed attempt the model retries. The last one is its answer."""
    trace = Trace()
    trace.events = [
        _event("list_tables", {}),
        _event("run_query", {"sql": "SELECT bad"}),
        _event("run_query", {"sql": "SELECT good"}),
    ]
    assert extract_sql(trace) == "SELECT good"


def test_extract_returns_empty_when_no_query_was_run():
    trace = Trace()
    trace.events = [_event("list_tables", {})]
    assert extract_sql(trace) == ""