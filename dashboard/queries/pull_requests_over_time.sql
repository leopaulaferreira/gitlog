select activity_date as "Data", sum(prs_opened) as "Abertas", sum(prs_merged) as "Mescladas"
from analytics.fact_repository_daily_metrics join analytics.dim_repository using (repository_key)

where 1=1
[[and {{repository}}]]
[[and {{language}}]]
[[and {{date_range}}]]

group by activity_date order by activity_date
