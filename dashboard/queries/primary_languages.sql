select coalesce(primary_language,'Unknown') as primary_language, count(*) as repositories
from analytics.dim_repository

where 1=1
[[and {{repository}}]]
[[and {{language}}]]

group by primary_language order by repositories desc
