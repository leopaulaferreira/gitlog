select activity_date as "Data", sum(commit_count) as "Commits"
from analytics.fact_repository_daily_metrics join analytics.dim_repository using (repository_key)

where 1=1
[[and {{repository}}]]
[[and {{language}}]]
[[and {{date_range}}]]

group by activity_date order by activity_date
