# Modelo de dados

**Estado: planejamento.** A Fase 0 cria apenas o banco PostgreSQL vazio, sem
schemas de aplicação, tabelas, índices ou migrações.

## MVP — próximas fases

| Entidade | Identidade prevista | Estratégia |
| --- | --- | --- |
| Repository | ID do GitHub | UPSERT dos atributos atuais |
| Commit | Repository ID + SHA | UPSERT sem duplicar commits compartilhados por repositórios |
| Ingestion checkpoint | Source + entity + repository | Avançar após persistência bem-sucedida |
| Pipeline run | ID da execução | Rastrear início, fim, status, contagens e erro sanitizado |

O payload bruto será preservado em arquivos antes de qualquer transformação.
As colunas e regras de nulabilidade serão definidas ao implementar os extractors
com base nos contratos reais da API, incluindo autores não associados a usuários.

## Camadas posteriores

- `raw`: dados próximos da origem, com IDs e metadados de ingestão.
- `staging`: dados limpos e tipados pelo dbt.
- `analytics`: `dim_repository`, `dim_contributor`, `dim_date`, `fact_commits`,
  `fact_issues`, `fact_pull_requests` e `fact_repository_daily_metrics`.

Stars e forks históricos dependem de snapshots coletados ao longo do tempo;
o estado atual de um repositório não reconstrói sua série histórica.
O Activity Score só será criado após definição explícita de fórmula, pesos e janela.
