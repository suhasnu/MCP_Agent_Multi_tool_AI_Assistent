# Evaluation results

- Scenarios: 17 (1 errored, excluded below)
- Tool-selection accuracy: 94% of 16 usable
- SQL exact match: 50% (6/12)
- SQL containment: 67% (8/12) (correct answer, extra columns allowed)
- p50 latency: 684.0 ms
- p95 latency: 25,471 ms
- Errors: 1

## By difficulty

| Difficulty | Scenarios | Tool selection | Exact | Containment |
|---|---|---|---|---|
| easy | 5 | 100% | 75% | 100% |
| medium | 6 | 83% | 40% | 60% |
| hard | 5 | 100% | 33% | 33% |

## Per scenario

| Scenario | Difficulty | Tools | Exact | Contains | Latency | Notes |
|---|---|---|---|---|---|---|
| list_available_data | easy | pass | n/a | n/a | 688 ms |  |
| station_count | easy | pass | pass | pass | 536 ms |  |
| station_list | easy | pass | pass | pass | 680 ms |  |
| warmest_bundesland_month | easy | pass | fail | pass | 509 ms | matched with 1 extra column(s) |
| highest_station | easy | pass | pass | pass | 521 ms |  |
| coldest_reading | medium | pass | pass | pass | 17,143 ms |  |
| mean_temp_per_station | medium | pass | fail | fail | 453 ms | 50 rows, expected 5 |
| completeness_filter | medium | pass | fail | fail | 13,252 ms | no column subset reproduces the expected result |
| humidity_comparison | medium | pass | pass | pass | 635 ms |  |
| temperature_range | medium | pass | fail | pass | 578 ms | matched with 1 extra column(s) |
| monthly_ranking | hard | pass | fail | fail | 1,132 ms | 50 rows, expected 5 |
| above_average_months | hard | pass | pass | pass | 571 ms |  |
| seasonal_swing | hard | pass | fail | fail | 78,017 ms | no column subset reproduces the expected result |
| ambiguous_best_weather | hard | pass | n/a | n/a | 829 ms |  |
| multi_step_comparison | hard | pass | n/a | n/a | 868 ms |  |
| no_tool_needed | easy | fail | n/a | n/a | 304 ms | Error code: 400 - {'error': {'message': "tool call validation failed: attempted to call tool 'brave_search' which was no |
| out_of_scope | medium | fail | n/a | n/a | 25,471 ms | called ['run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query'] |

## Failures in detail

### mean_temp_per_station

- SQL: shape (50, 2) != expected (5, 2)

```sql
SELECT station_name, avg_temp_c FROM gold_monthly_by_station ORDER BY min_temp_c DESC LIMIT 50
```

### completeness_filter

- SQL: same shape, different values

```sql
WITH ranked_months AS (SELECT station_id, year, month, avg_temp_c, completeness FROM gold_monthly_by_station WHERE completeness >= 0.8 ORDER BY avg_temp_c DESC) SELECT station_id, year, month FROM ranked_months LIMIT 1
```

### monthly_ranking

- SQL: shape (50, 3) != expected (5, 3)

```sql
WITH monthly_max AS (SELECT bundesland, year, month, MAX(avg_temp_c) AS max_temp FROM gold_monthly_by_station GROUP BY bundesland, year, month) SELECT bundesland, year, month FROM monthly_max ORDER BY max_temp DESC LIMIT 50
```

### seasonal_swing

- SQL: shape (1, 4) != expected (1, 1)

```sql
SELECT station_name, max_temp_c, min_temp_c, max_temp_c - min_temp_c AS temp_diff FROM gold_monthly_by_station WHERE completeness >= 0.8 GROUP BY station_name, max_temp_c, min_temp_c ORDER BY temp_diff DESC LIMIT 1
```

### out_of_scope

- Expected tools `[]`, called `['run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query', 'run_query']`

