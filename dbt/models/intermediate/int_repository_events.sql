-- One row per observed lifecycle event, not an invented state-change history.
select repository_key, commit_date as event_date, 'commit'::text as event_type,
       null::numeric as merge_time_hours
from {{ ref('fact_commits') }} where commit_date is not null
union all
select repository_key, created_date, 'issue_opened', null::numeric
from {{ ref('fact_issues') }}
union all
select repository_key, closed_date, 'issue_closed', null::numeric
from {{ ref('fact_issues') }} where state = 'closed' and closed_date is not null
union all
select repository_key, created_date, 'pr_opened', null::numeric
from {{ ref('fact_pull_requests') }}
union all
select repository_key, closed_date, 'pr_closed', null::numeric
from {{ ref('fact_pull_requests') }} where state = 'closed' and closed_date is not null
union all
select repository_key, merged_date, 'pr_merged', merge_time_hours
from {{ ref('fact_pull_requests') }} where is_merged
