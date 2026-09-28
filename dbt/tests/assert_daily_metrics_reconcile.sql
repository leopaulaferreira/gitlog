with expected as (
    select repository_key,
        count(*) filter (where event_type='commit') as commit_count,
        count(*) filter (where event_type='issue_opened') as issues_opened,
        count(*) filter (where event_type='issue_closed') as issues_closed,
        count(*) filter (where event_type='pr_opened') as prs_opened,
        count(*) filter (where event_type='pr_closed') as prs_closed,
        count(*) filter (where event_type='pr_merged') as prs_merged,
        coalesce(sum(merge_time_hours),0) as merge_hours
    from {{ ref('int_repository_events') }} group by repository_key
), actual as (
    select repository_key, sum(commit_count) as commit_count,
        sum(issues_opened) as issues_opened, sum(issues_closed) as issues_closed,
        sum(prs_opened) as prs_opened, sum(prs_closed) as prs_closed,
        sum(prs_merged) as prs_merged, sum(pr_merge_time_hours_sum) as merge_hours
    from {{ ref('fact_repository_daily_metrics') }} group by repository_key
)
select coalesce(a.repository_key,e.repository_key) as repository_key
from actual a full join expected e using (repository_key)
where {% for metric in ['commit_count','issues_opened','issues_closed','prs_opened','prs_closed','prs_merged','merge_hours'] %}
coalesce(a.{{metric}},0) <> coalesce(e.{{metric}},0)
{% if not loop.last %} or {% endif %}
{% endfor %}
