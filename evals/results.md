# Evaluation results

- Scenarios: 17
- Tool-selection accuracy: 88%
- SQL execution accuracy: 33% (4/12)
- p50 latency: 18,940 ms
- p95 latency: 25,243 ms
- Errors: 0

## By difficulty

| Difficulty | Scenarios | Tool selection | Execution accuracy |
|---|---|---|---|
| easy | 6 | 83% | 25% |
| medium | 6 | 83% | 40% |
| hard | 5 | 100% | 33% |

## Per scenario

| Scenario | Difficulty | Tools | SQL | Latency | Notes |
|---|---|---|---|---|---|
| list_available_data | easy | pass | n/a | 1,375 ms |  |
| station_count | easy | fail | fail | 1,005 ms | no query was generated |
| station_list | easy | pass | pass | 15,416 ms |  |
| warmest_bundesland_month | easy | pass | fail | 19,007 ms | shape (1, 2) != expected (1, 1) |
| highest_station | easy | pass | fail | 10,720 ms | shape (1, 2) != expected (1, 1) |
| coldest_reading | medium | pass | pass | 18,940 ms |  |
| mean_temp_per_station | medium | pass | fail | 20,156 ms | same shape, different values |
| completeness_filter | medium | pass | fail | 20,273 ms | shape (1, 4) != expected (1, 3) |
| humidity_comparison | medium | pass | pass | 970 ms |  |
| temperature_range | medium | pass | fail | 31,526 ms | shape (1, 2) != expected (1, 1) |
| monthly_ranking | hard | pass | fail | 23,326 ms | shape (5, 2) != expected (5, 3) |
| above_average_months | hard | pass | pass | 9,906 ms |  |
| seasonal_swing | hard | pass | fail | 11,867 ms | shape (1, 2) != expected (1, 1) |
| ambiguous_best_weather | hard | pass | n/a | 23,683 ms |  |
| multi_step_comparison | hard | pass | n/a | 25,243 ms |  |
| no_tool_needed | easy | pass | n/a | 1,676 ms |  |
| out_of_scope | medium | fail | n/a | 22,596 ms | called ['list_tables', 'run_query'] |

## Failures in detail

### station_count

- Expected tools `['run_query']`, called `['list_tables', 'describe_schema']`
- SQL: no query was generated

### warmest_bundesland_month

- SQL: shape (1, 2) != expected (1, 1)

```sql
SELECT bundesland, AVG(avg_temp_c) as average_temp FROM gold_monthly_by_bundesland GROUP BY bundesland ORDER BY average_temp DESC LIMIT 1
```

### highest_station

- SQL: shape (1, 2) != expected (1, 1)

```sql
SELECT station_name, elevation_m FROM gold_stations ORDER BY elevation_m DESC LIMIT 1
```

### mean_temp_per_station

- SQL: same shape, different values

```sql
SELECT station_name, AVG(avg_temp_c) as average_temperature FROM gold_daily_by_station GROUP BY station_name ORDER BY average_temperature ASC;
```

### completeness_filter

- SQL: shape (1, 4) != expected (1, 3)

```sql
SELECT station_name, year, month, avg_temp_c FROM gold_monthly_by_station WHERE completeness >= 0.8 ORDER BY avg_temp_c DESC LIMIT 1
```

### temperature_range

- SQL: shape (1, 2) != expected (1, 1)

```sql
SELECT station_name, MAX(max_temp_c) - MIN(min_temp_c) as temp_gap FROM gold_daily_by_station GROUP BY station_name ORDER BY temp_gap DESC LIMIT 1
```

### monthly_ranking

- SQL: shape (5, 2) != expected (5, 3)

```sql
SELECT bundesland, month FROM (SELECT bundesland, month, ROW_NUMBER() OVER (PARTITION BY bundesland ORDER BY avg_temp_c DESC) as row_num FROM gold_monthly_by_bundesland) AS subquery WHERE row_num = 1
```

### seasonal_swing

- SQL: shape (1, 2) != expected (1, 1)

```sql
WITH ranked_stations AS ( SELECT station_name, AVG(CASE WHEN month = ( SELECT month FROM gold_monthly_by_station WHERE station_name = g.station_name AND avg_temp_c = ( SELECT MAX(avg_temp_c) FROM gold_monthly_by_station WHERE station_name = g.station_name ) LIMIT 1 ) THEN avg_temp_c END) AS warmest_month_avg, AVG(CASE WHEN month = ( SELECT month FROM gold_monthly_by_station WHERE station_name = g.station_name AND avg_temp_c = ( SELECT MIN(avg_temp_c) FROM gold_monthly_by_station WHERE station_name = g.station_name ) LIMIT 1 ) THEN avg_temp_c END) AS coldest_month_avg FROM gold_monthly_by_station g GROUP BY station_name ) SELECT station_name, warmest_month_avg - coldest_month_avg AS temp_diff FROM ranked_stations ORDER BY temp_diff DESC LIMIT 1
```

### out_of_scope

- Expected tools `[]`, called `['list_tables', 'run_query']`

