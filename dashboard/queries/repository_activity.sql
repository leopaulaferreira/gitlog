select activity_date as "Data", full_name as "Repositório", sum(commit_count) as "Commits"
from analytics.fact_repository_daily_metrics join analytics.dim_repository using (repository_key)

where 1=1
[[and {{repository}}]]
[[and {{language}}]]
[[and {{date_range}}]]

group by activity_date, full_name order by activity_date, full_name
