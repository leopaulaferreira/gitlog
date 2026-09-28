select full_name,
       sum(pr_merge_time_hours_sum) / nullif(sum(prs_merged),0) as average_pr_merge_time_hours,
       sum(prs_merged) as merged_prs
from analytics.fact_repository_daily_metrics join analytics.dim_repository using (repository_key)

where 1=1
[[and {{repository}}]]
[[and {{language}}]]
[[and {{date_range}}]]

group by full_name having sum(prs_merged)>0 order by average_pr_merge_time_hours desc
