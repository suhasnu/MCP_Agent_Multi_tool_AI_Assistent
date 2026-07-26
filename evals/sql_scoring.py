"""Execution accuracy scoring.

The standard text-to-SQL metric from the Spider and BIRD benchmarks. It asks
whether the generated query returns the same rows as a hand-written gold query,
not whether the two look alike.
"""

from itertools import combinations
from pathlib import Path

import duckdb
import pandas as pd

ROUND_TO = 2


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Canonical form: column names dropped, floats rounded, rows sorted.

    Column names are ignored because `AS warmest` and `AS bundesland` are the
    same answer. Row order is ignored unless the query asked for a specific
    ordering, which we cannot detect, so we treat sets as equal.
    """
    out = df.copy()
    out.columns = range(len(out.columns))

    for col in out.columns:
        if pd.api.types.is_float_dtype(out[col]):
            out[col] = out[col].round(ROUND_TO)
        elif pd.api.types.is_numeric_dtype(out[col]):
            out[col] = out[col].astype(float).round(ROUND_TO)
        else:
            out[col] = out[col].astype(str).str.strip()

    return (
        out.sort_values(list(out.columns), kind="stable")
        .reset_index(drop=True)
    )


def execution_match(
    predicted_sql: str, gold_sql: str, db_path: str | Path
) -> tuple[bool, str]:
    """Return (matched, reason).

    A failure reason is returned rather than a bare False so a scoring run
    tells you why the model was wrong, not just that it was.
    """
    if not predicted_sql:
        return False, "no query was generated"

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        try:
            predicted = con.execute(predicted_sql).fetchdf()
        except Exception as exc:
            return False, f"query failed: {str(exc).splitlines()[0][:80]}"

        gold = con.execute(gold_sql).fetchdf()
    finally:
        con.close()

    if predicted.shape != gold.shape:
        return False, (
            f"shape {predicted.shape} != expected {gold.shape}"
        )

    if normalise(predicted).equals(normalise(gold)):
        return True, ""

    return False, "same shape, different values"


def containment_match(
    predicted_sql: str, gold_sql: str, db_path: str | Path
) -> tuple[bool, str]:
    """Looser metric: does the predicted result contain the gold answer?

    Exact match punishes a model for being more informative. Asked which
    station is highest, the gold query returns the name; the model returns the
    name and the elevation. Same answer, extra context, scored as a failure.

    This checks whether some subset of the predicted columns, in order,
    reproduces the gold result. Row counts must still agree, so a missing LIMIT
    is still wrong.

    Report both metrics. Exact match is the stricter published number; this one
    separates real errors from a benchmark that was phrased too tightly.
    """
    if not predicted_sql:
        return False, "no query was generated"

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        try:
            predicted = con.execute(predicted_sql).fetchdf()
        except Exception as exc:
            return False, f"query failed: {str(exc).splitlines()[0][:80]}"
        gold = con.execute(gold_sql).fetchdf()
    finally:
        con.close()

    if len(predicted) != len(gold):
        return False, f"{len(predicted)} rows, expected {len(gold)}"
    if len(predicted.columns) < len(gold.columns):
        return False, (
            f"{len(predicted.columns)} columns, expected at least {len(gold.columns)}"
        )

    target = normalise(gold)
    width = len(gold.columns)

    for combo in combinations(range(len(predicted.columns)), width):
        subset = predicted.iloc[:, list(combo)]
        if normalise(subset).equals(target):
            extra = len(predicted.columns) - width
            return True, f"matched with {extra} extra column(s)" if extra else ""

    return False, "no column subset reproduces the expected result"
