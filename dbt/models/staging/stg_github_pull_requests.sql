select
    id::bigint as github_pull_request_id,
    number::bigint as pull_request_number,
    repository_id::bigint as github_repository_id,
    title::text as title,
    state::text as state,
    nullif(btrim(author_login), '')::text as author_login,
    created_at at time zone 'UTC' as created_at,
    updated_at at time zone 'UTC' as updated_at,
    closed_at at time zone 'UTC' as closed_at,
    merged_at at time zone 'UTC' as merged_at,
    merge_commit_sha::text as merge_commit_sha,
    draft::boolean as is_draft
from {{ source('github', 'pull_requests') }}
