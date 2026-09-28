select count(*) as total from analytics.fact_commits
join analytics.dim_repository using (repository_key)

where 1=1
[[and {{repository}}]]
[[and {{language}}]]
[[and {{date_range}}]]


