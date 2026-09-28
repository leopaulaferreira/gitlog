select full_name, sum(commit_count) as commits,
       sum(issues_opened) as issues_opened, sum(issues_closed) as issues_closed,
       sum(prs_opened) as prs_opened, sum(prs_closed) as prs_closed, sum(prs_merged) as prs_merged,
       sum(pr_merge_time_hours_sum) / nullif(sum(prs_merged),0) as average_pr_merge_time_hours
from analytics.fact_repository_daily_metrics join analytics.dim_repository using (repository_key)

where 1=1
[[and {{repository}}]]
[[and {{language}}]]
[[and {{date_range}}]]

group by full_name order by commits desc, full_name
