select 'negative_merge_time' as violation from {{ ref('fact_pull_requests') }}
where merge_time_hours < 0 or (is_merged and merge_time_hours is null)
union all
select 'issue_pr_overlap' from {{ ref('fact_issues') }} i
join {{ ref('fact_pull_requests') }} p
on i.repository_key=p.repository_key and i.issue_number=p.pull_request_number
union all
select 'invalid_daily_average' from {{ ref('fact_repository_daily_metrics') }}
where (prs_merged=0 and average_pr_merge_time_hours is not null)
   or (prs_merged>0 and average_pr_merge_time_hours is distinct from pr_merge_time_hours_sum/prs_merged)
