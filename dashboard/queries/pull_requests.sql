select count(*) as "Total" from analytics.fact_pull_requests
join analytics.dim_repository using (repository_key)

where 1=1
[[and {{repository}}]]
[[and {{language}}]]
[[and {{date_range}}]]
