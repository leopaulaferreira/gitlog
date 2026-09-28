select coalesce(primary_language,'Não informada') as "Linguagem principal", count(*) as "Repositórios"
from analytics.dim_repository

where 1=1
[[and {{repository}}]]
[[and {{language}}]]

group by primary_language order by "Repositórios" desc
