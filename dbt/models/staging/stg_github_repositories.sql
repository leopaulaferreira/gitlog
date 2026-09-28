select
    id::bigint as github_repository_id,
    name::text as name,
    full_name::text as full_name,
    owner::text as owner,
    nullif(btrim(language), '')::text as primary_language,
    created_at at time zone 'UTC' as created_at,
    updated_at at time zone 'UTC' as updated_at,
    ingested_at at time zone 'UTC' as observed_at,
    stars::bigint as current_stars,
    forks::bigint as current_forks,
    archived::boolean as is_archived,
    visibility::text as visibility
from {{ source('github', 'repositories') }}
