{% for entity in ['repositories', 'commits', 'issues', 'pull_requests'] %}
select '{{ entity }}' as entity
where (select count(*) from {{ source('github', entity) }}) <>
      (select count(*) from {{ ref('dim_repository' if entity == 'repositories' else 'fact_' ~ entity) }})
{% if not loop.last %} union all {% endif %}
{% endfor %}
