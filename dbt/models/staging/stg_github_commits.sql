select
    repository_id::bigint as github_repository_id,
    sha::text as sha,
    nullif(btrim(author_login), '')::text as author_login,
    authored_at at time zone 'UTC' as authored_at,
    committed_at at time zone 'UTC' as committed_at,
    coalesce(committed_at, authored_at) at time zone 'UTC' as commit_timestamp,
    case when committed_at is not null then 'committer'
         when authored_at is not null then 'author' else 'missing' end as timestamp_source
from {{ source('github', 'commits') }}
