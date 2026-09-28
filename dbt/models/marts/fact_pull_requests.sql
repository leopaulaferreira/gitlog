select
    'pr:' || p.github_pull_request_id::text as pull_request_key,
    p.github_pull_request_id,
    r.repository_key,
    p.pull_request_number,
    p.title,
    p.state,
    p.author_login,
    p.created_at,
    p.created_at::date as created_date,
    p.updated_at,
    p.closed_at,
    p.closed_at::date as closed_date,
    p.merged_at,
    p.merged_at::date as merged_date,
    p.merged_at is not null as is_merged,
    p.merge_commit_sha,
    p.is_draft,
    case when p.merged_at >= p.created_at
         then extract(epoch from (p.merged_at - p.created_at)) / 3600.0
    end::numeric as merge_time_hours
from {{ ref('stg_github_pull_requests') }} p
join {{ ref('dim_repository') }} r using (github_repository_id)
