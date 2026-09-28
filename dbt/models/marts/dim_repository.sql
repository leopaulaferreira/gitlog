select
    github_repository_id as repository_key,
    github_repository_id,
    name,
    full_name,
    owner,
    primary_language,
    created_at,
    updated_at,
    observed_at,
    current_stars,
    current_forks,
    is_archived,
    visibility
from {{ ref('stg_github_repositories') }}
