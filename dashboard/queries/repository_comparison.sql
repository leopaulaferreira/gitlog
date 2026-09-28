select full_name as "Repositório", sum(commit_count) as "Commits",
       sum(issues_opened) as "Questões abertas", sum(issues_closed) as "Questões fechadas",
       sum(prs_opened) as "Solicitações abertas", sum(prs_closed) as "Solicitações fechadas", sum(prs_merged) as "Solicitações mescladas",
       sum(pr_merge_time_hours_sum) / nullif(sum(prs_merged),0) as "Tempo médio até a mesclagem (horas)"
from analytics.fact_repository_daily_metrics join analytics.dim_repository using (repository_key)

where 1=1
[[and {{repository}}]]
[[and {{language}}]]
[[and {{date_range}}]]

group by full_name order by "Commits" desc, full_name
