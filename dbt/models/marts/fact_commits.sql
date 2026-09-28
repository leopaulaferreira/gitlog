select
    c.github_repository_id::text || ':' || c.sha as commit_key,
    r.repository_key,
    c.commit_timestamp,
    c.commit_timestamp::date as commit_date,
    c.timestamp_source,
    c.author_login,
    c.sha
from {{ ref('stg_github_commits') }} c
join {{ ref('dim_repository') }} r using (github_repository_id)
