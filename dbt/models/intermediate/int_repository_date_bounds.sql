select
    r.repository_key,
    least(r.created_at::date, min(e.event_date)) as first_date,
    greatest((current_timestamp at time zone 'UTC')::date,
             r.created_at::date, max(e.event_date)) as last_date
from {{ ref('dim_repository') }} r
left join {{ ref('int_repository_events') }} e using (repository_key)
group by r.repository_key, r.created_at
