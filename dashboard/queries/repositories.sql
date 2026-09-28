select count(*) as repositories from analytics.dim_repository

where 1=1
[[and {{repository}}]]
[[and {{language}}]]
