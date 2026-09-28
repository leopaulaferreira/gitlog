with bounds as (
    select coalesce(min(first_date), (current_timestamp at time zone 'UTC')::date) as first_date,
           coalesce(max(last_date), (current_timestamp at time zone 'UTC')::date) as last_date
    from {{ ref('int_repository_date_bounds') }}
), dates as (
    select day::date as date_day
    from bounds,
    lateral generate_series(first_date::timestamp, last_date::timestamp, interval '1 day') day
)
select
    to_char(date_day, 'YYYYMMDD')::integer as date_key,
    date_day,
    extract(year from date_day)::integer as year,
    extract(quarter from date_day)::integer as quarter,
    extract(month from date_day)::integer as month,
    extract(isoyear from date_day)::integer as iso_year,
    extract(week from date_day)::integer as iso_week,
    extract(isodow from date_day)::integer as day_of_week,
    date_trunc('month', date_day)::date as month_start,
    extract(isodow from date_day) in (6, 7) as is_weekend
from dates
