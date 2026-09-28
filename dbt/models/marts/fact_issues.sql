select
    'issue:' || i.github_issue_id::text as issue_key,
    i.github_issue_id,
    r.repository_key,
    i.issue_number,
    i.title,
    i.state,
    i.author_login,
    i.created_at,
    i.created_at::date as created_date,
    i.updated_at,
    i.closed_at,
    i.closed_at::date as closed_date,
    i.comments_count
from {{ ref('stg_github_issues') }} i
join {{ ref('dim_repository') }} r using (github_repository_id)
