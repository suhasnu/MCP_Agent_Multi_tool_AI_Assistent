"""Evaluation runner.

Scores two things per scenario:

  tool selection    did the agent reach for the right tool
  execution match   does its SQL return the same rows as a hand-written query

Results are cached on disk by prompt hash, because the Groq free tier is 6000
tokens per minute and a full rerun after editing one scenario would be wasteful.
Delete evals/.cache to force a fresh run.

Run:  python -m evals.run
      python -m evals.run --only warmest_bundesland_month
      python -m evals.run --no-cache --delay 5
"""

import argparse
import asyncio
import hashlib
import json
import re
import statistics
import time
from pathlib import Path

import yaml

from evals.sql_scoring import containment_match, execution_match
from orchestrator.agent import build_agent
from orchestrator.tracing import Trace

HERE = Path(__file__).parent
SCENARIOS = HERE / "scenarios.yaml"
RESULTS = HERE / "results.md"
CACHE = HERE / ".cache"
DB_PATH = Path("data/weather.duckdb")

RATE_LIMIT_PATTERN = re.compile(r"rate_limit|429|413|too large", re.IGNORECASE)
MAX_RETRIES = 3


def cache_key(scenario: dict) -> str:
    raw = json.dumps({"prompt": scenario["prompt"], "id": scenario["id"]}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_cached(scenario: dict) -> dict | None:
    path = CACHE / f"{cache_key(scenario)}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_cached(scenario: dict, result: dict) -> None:
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"{cache_key(scenario)}.json"
    path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")


def extract_sql(trace: Trace) -> str:
    """Pull the last SQL the agent ran. The last one is its final attempt."""
    queries = [
        e.arguments.get("sql", "")
        for e in trace.events
        if e.tool == "run_query" and "sql" in e.arguments
    ]
    return queries[-1] if queries else ""


def score_tools(called: list[str], expected: list[str]) -> bool:
    """Expected tools must all appear. If none are expected, none may be used."""
    if not expected:
        return not called
    return set(expected).issubset(set(called))


async def evaluate(scenario: dict, delay: float) -> dict:
    trace = Trace()
    agent, client, _ = await build_agent(trace=trace)

    started = time.perf_counter()
    answer, error = "", ""

    for attempt in range(MAX_RETRIES):
        try:
            answer = await agent.run(scenario["prompt"])
            break
        except Exception as exc:
            message = str(exc)
            if RATE_LIMIT_PATTERN.search(message) and attempt < MAX_RETRIES - 1:
                wait = delay * (attempt + 2)
                print(f"    rate limited, waiting {wait:.0f}s...")
                await asyncio.sleep(wait)
                continue
            error = message.splitlines()[0][:120]
            break

    elapsed_ms = int((time.perf_counter() - started) * 1000)

    if client and client.get_all_active_sessions():
        await client.close_all_sessions()

    called = [e.tool for e in trace.events]
    expected = scenario.get("expect_tools", [])
    predicted_sql = extract_sql(trace)

    sql_ok, sql_reason = None, ""
    sql_contains, contains_reason = None, ""
    if scenario.get("gold_sql"):
        sql_ok, sql_reason = execution_match(
            predicted_sql, scenario["gold_sql"], DB_PATH
        )
        sql_contains, contains_reason = containment_match(
            predicted_sql, scenario["gold_sql"], DB_PATH
        )

    # A scenario that errored called no tools, which would otherwise score as
    # a pass for any scenario expecting none. Never count an error as a pass.
    tools_ok = (not error) and score_tools(called, expected)

    return {
        "id": scenario["id"],
        "difficulty": scenario.get("difficulty", "unknown"),
        "tools_ok": tools_ok,
        "expected_tools": expected,
        "called_tools": called,
        "sql_ok": sql_ok,
        "sql_reason": sql_reason,
        "sql_contains": sql_contains,
        "contains_reason": contains_reason,
        "predicted_sql": predicted_sql,
        "latency_ms": elapsed_ms,
        "tool_calls": len(trace.events),
        "error": error,
        "answer": (answer or "")[:300],
    }


def summarise(results: list[dict]) -> list[str]:
    lines = ["# Evaluation results", ""]

    errored = [r for r in results if r["error"]]
    usable = [r for r in results if not r["error"]]

    if not usable:
        return [
            "# Evaluation results",
            "",
            f"All {len(results)} scenarios errored. No accuracy can be reported.",
            "",
            f"First error: {errored[0]['error'][:200]}",
            "",
            "Check quota with: python scripts/check_quota.py",
        ]

    scored_sql = [r for r in usable if r["sql_ok"] is not None]
    tool_acc = sum(r["tools_ok"] for r in usable) / len(usable)
    latencies = sorted(r["latency_ms"] for r in usable)
    p50 = statistics.median(latencies)
    p95 = latencies[max(0, int(len(latencies) * 0.95) - 1)]

    lines += [
        f"- Scenarios: {len(results)} ({len(errored)} errored, excluded below)",
        f"- Tool-selection accuracy: {tool_acc:.0%} of {len(usable)} usable",
    ]
    if scored_sql:
        exact = sum(bool(r["sql_ok"]) for r in scored_sql)
        contains = sum(bool(r.get("sql_contains")) for r in scored_sql)
        lines += [
            f"- SQL exact match: {exact / len(scored_sql):.0%} "
            f"({exact}/{len(scored_sql)})",
            f"- SQL containment: {contains / len(scored_sql):.0%} "
            f"({contains}/{len(scored_sql)}) "
            f"(correct answer, extra columns allowed)",
        ]
    lines += [
        f"- p50 latency: {p50:,} ms",
        f"- p95 latency: {p95:,} ms",
        f"- Errors: {sum(1 for r in results if r['error'])}",
        "",
        "## By difficulty",
        "",
        "| Difficulty | Scenarios | Tool selection | Exact | Containment |",
        "|---|---|---|---|---|",
    ]

    for band in ("easy", "medium", "hard"):
        group = [r for r in usable if r["difficulty"] == band]
        if not group:
            continue
        t_acc = sum(r["tools_ok"] for r in group) / len(group)
        sql_group = [r for r in group if r["sql_ok"] is not None]
        if sql_group:
            exact = f"{sum(bool(r['sql_ok']) for r in sql_group) / len(sql_group):.0%}"
            cont = f"{sum(bool(r.get('sql_contains')) for r in sql_group) / len(sql_group):.0%}"
        else:
            exact = cont = "n/a"
        lines.append(f"| {band} | {len(group)} | {t_acc:.0%} | {exact} | {cont} |")

    lines += [
        "",
        "## Per scenario",
        "",
        "| Scenario | Difficulty | Tools | Exact | Contains | Latency | Notes |",
        "|---|---|---|---|---|---|---|",
    ]

    for r in results:
        tools = "pass" if r["tools_ok"] else "fail"
        if r["sql_ok"] is None:
            sql = cont = "n/a"
        else:
            sql = "pass" if r["sql_ok"] else "fail"
            cont = "pass" if r.get("sql_contains") else "fail"
        note = r["error"] or r.get("contains_reason") or r["sql_reason"] or ""
        if not r["tools_ok"]:
            note = note or f"called {r['called_tools'] or 'nothing'}"
        lines.append(
            f"| {r['id']} | {r['difficulty']} | {tools} | {sql} | {cont} | "
            f"{r['latency_ms']:,} ms | {note} |"
        )

    # Detail only genuine failures. A query that scored exact-fail but
    # containment-pass answered the question with extra columns.
    failures = [
        r for r in usable
        if not r["tools_ok"] or (r["sql_ok"] is False and not r.get("sql_contains"))
    ]
    if failures:
        lines += ["", "## Failures in detail", ""]
        for r in failures:
            lines += [f"### {r['id']}", ""]
            if not r["tools_ok"]:
                lines.append(
                    f"- Expected tools `{r['expected_tools']}`, called `{r['called_tools']}`"
                )
            if r["sql_ok"] is False:
                lines.append(f"- SQL: {r['sql_reason']}")
                if r["predicted_sql"]:
                    lines += ["", "```sql", r["predicted_sql"].strip(), "```"]
            lines.append("")

    return lines


async def main_async(args) -> None:
    scenarios = yaml.safe_load(SCENARIOS.read_text(encoding="utf-8"))
    if args.only:
        scenarios = [s for s in scenarios if s["id"] in args.only]
        if not scenarios:
            print(f"No scenario matched {args.only}")
            return

    if not DB_PATH.exists():
        print(f"{DB_PATH} not found. Run the pipeline first.")
        return

    results = []
    consecutive_errors = 0
    for i, scenario in enumerate(scenarios, 1):
        cached = None if args.no_cache else load_cached(scenario)
        if cached:
            print(f"[{i}/{len(scenarios)}] {scenario['id']}  (cached)")
            results.append(cached)
            continue

        print(f"[{i}/{len(scenarios)}] {scenario['id']}")
        result = await evaluate(scenario, args.delay)
        if not result["error"]:
            save_cached(scenario, result)
        results.append(result)

        if result["error"]:
            consecutive_errors += 1
            if consecutive_errors >= 3:
                print("\n3 scenarios failed in a row. Stopping instead of")
                print("burning the rest against the same error:")
                print(f"  {result['error'][:160]}")
                print("\nCheck quota with: python scripts/check_quota.py")
                break
        else:
            consecutive_errors = 0

        marks = "tools " + ("ok" if result["tools_ok"] else "FAIL")
        if result["sql_ok"] is not None:
            marks += ", sql " + ("ok" if result["sql_ok"] else "FAIL")
        print(f"    {marks}, {result['latency_ms']:,} ms, {result['tool_calls']} calls")

        if i < len(scenarios):
            await asyncio.sleep(args.delay)

    lines = summarise(results)
    RESULTS.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n" + "\n".join(lines[:12]))
    print(f"\nFull report: {RESULTS}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", help="scenario ids to run")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument(
        "--delay", type=float, default=4.0, help="seconds between scenarios"
    )
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
