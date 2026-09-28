select
    id::bigint as github_issue_id,
    number::bigint as issue_number,
    repository_id::bigint as github_repository_id,
    title::text as title,
    state::text as state,
    nullif(btrim(author_login), '')::text as author_login,
    created_at at time zone 'UTC' as created_at,
    updated_at at time zone 'UTC' as updated_at,
    closed_at at time zone 'UTC' as closed_at,
    comments_count::bigint as comments_count
from {{ source('github', 'issues') }}
