# Evaluation results

- Scenarios: 17 (6 errored, excluded below)
- Tool-selection accuracy: 91% of 11 usable
- SQL exact match: 38% (3/8)
- SQL containment: 50% (4/8) (correct answer, extra columns allowed)
- p50 latency: 12,775 ms
- p95 latency: 110,295 ms
- Errors: 6

## By difficulty

| Difficulty | Scenarios | Tool selection | Exact | Containment |
|---|---|---|---|---|
| easy | 4 | 75% | 67% | 67% |
| medium | 3 | 100% | 0% | 0% |
| hard | 4 | 100% | 50% | 100% |

## Per scenario

| Scenario | Difficulty | Tools | Exact | Contains | Latency | Notes |
|---|---|---|---|---|---|---|
| list_available_data | easy | pass | n/a | n/a | 653 ms |  |
| station_count | easy | pass | pass | pass | 493 ms |  |
| station_list | easy | fail | fail | fail | 12,775 ms | no query was generated |
| warmest_bundesland_month | easy | fail | pass | pass | 143,387 ms | Recursion limit of 30 reached without hitting a stop condition. You can increase the limit by setting the `recursion_lim |
| highest_station | easy | pass | pass | pass | 440 ms |  |
| coldest_reading | medium | pass | fail | fail | 21,342 ms | no column subset reproduces the expected result |
| mean_temp_per_station | medium | fail | fail | fail | 201,028 ms | Recursion limit of 30 reached without hitting a stop condition. You can increase the limit by setting the `recursion_lim |
| completeness_filter | medium | fail | fail | fail | 22,396 ms | Recursion limit of 30 reached without hitting a stop condition. You can increase the limit by setting the `recursion_lim |
| humidity_comparison | medium | pass | fail | fail | 110,295 ms | 1 rows, expected 5 |
| temperature_range | medium | pass | fail | fail | 52,125 ms | no column subset reproduces the expected result |
| monthly_ranking | hard | fail | fail | fail | 203,146 ms | Recursion limit of 30 reached without hitting a stop condition. You can increase the limit by setting the `recursion_lim |
| above_average_months | hard | pass | pass | pass | 597 ms |  |
| seasonal_swing | hard | pass | fail | pass | 7,310 ms | matched with 1 extra column(s) |
| ambiguous_best_weather | hard | pass | n/a | n/a | 55,289 ms |  |
| multi_step_comparison | hard | pass | n/a | n/a | 122,414 ms |  |
| no_tool_needed | easy | fail | n/a | n/a | 248 ms | Error code: 400 - {'error': {'message': "tool call validation failed: attempted to call tool 'brute_force_search' which  |
| out_of_scope | medium | fail | n/a | n/a | 295 ms | Error code: 400 - {'error': {'message': "tool call validation failed: attempted to call tool 'brave_search' which was no |

## Failures in detail

### station_list

- Expected tools `['run_query']`, called `['list_tables', 'describe_schema']`
- SQL: no query was generated

### coldest_reading

- SQL: same shape, different values

```sql
SELECT min_temp_c, station_name FROM gold_daily_by_station ORDER BY min_temp_c ASC LIMIT 1
```

### humidity_comparison

- SQL: shape (1, 2) != expected (5, 2)

```sql
SELECT bundesland, avg(avg_humidity_pct) FROM gold_monthly_by_bundesland GROUP BY bundesland ORDER BY avg(avg_humidity_pct) DESC LIMIT 1
```

### temperature_range

- SQL: shape (1, 2) != expected (1, 1)

```sql
SELECT station_name, max_temp_c - min_temp_c AS temp_gap FROM gold_daily_by_station GROUP BY station_name, max_temp_c, min_temp_c ORDER BY temp_gap DESC LIMIT 1
```

