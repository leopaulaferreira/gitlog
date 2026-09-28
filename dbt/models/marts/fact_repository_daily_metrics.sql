with event_counts as (
    select repository_key, event_date,
           count(*) filter (where event_type = 'commit') as commit_count,
           count(*) filter (where event_type = 'issue_opened') as issues_opened,
           count(*) filter (where event_type = 'issue_closed') as issues_closed,
           count(*) filter (where event_type = 'pr_opened') as prs_opened,
           count(*) filter (where event_type = 'pr_closed') as prs_closed,
           count(*) filter (where event_type = 'pr_merged') as prs_merged,
           sum(merge_time_hours) filter (where event_type = 'pr_merged') as pr_merge_time_hours_sum
    from {{ ref('int_repository_events') }}
    group by repository_key, event_date
)
select
    b.repository_key::text || ':' || d.date_day::text as repository_date_key,
    b.repository_key,
    d.date_key,
    d.date_day as activity_date,
    coalesce(e.commit_count, 0)::bigint as commit_count,
    coalesce(e.issues_opened, 0)::bigint as issues_opened,
    coalesce(e.issues_closed, 0)::bigint as issues_closed,
    coalesce(e.prs_opened, 0)::bigint as prs_opened,
    coalesce(e.prs_closed, 0)::bigint as prs_closed,
    coalesce(e.prs_merged, 0)::bigint as prs_merged,
    coalesce(e.pr_merge_time_hours_sum, 0)::numeric as pr_merge_time_hours_sum,
    e.pr_merge_time_hours_sum / nullif(e.prs_merged, 0) as average_pr_merge_time_hours
from {{ ref('int_repository_date_bounds') }} b
join {{ ref('dim_date') }} d on d.date_day between b.first_date and b.last_date
left join event_counts e on e.repository_key = b.repository_key and e.event_date = d.date_day
