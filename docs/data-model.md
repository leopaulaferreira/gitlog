# Modelo de dados

**Estado: Fase 2.** A migração `001_repositories.sql` cria o schema `raw` com
repositories e auditoria. `public.gitlog_schema_migrations` registra nome,
checksum SHA-256 e data de aplicação de cada migração.

## Entidades do MVP

| Entidade | Identidade | Estratégia |
| --- | --- | --- |
| Repository | ID do GitHub | UPSERT dos atributos atuais |
| Commit (Fase 3) | Repository ID + SHA | UPSERT sem duplicar commits compartilhados por repositórios |
| Ingestion checkpoint (Fase 3) | Source + entity + repository | Avançar após persistência bem-sucedida |
| Pipeline run | ID da execução | Rastrear início, fim, status, contagens e erro sanitizado |

## `raw.repositories`

Uma linha por ID estável do GitHub, atualizada por UPSERT. Renomear o repositório
mantém a identidade; `full_name` possui índice, sem unicidade. O estado atual não
é substituído por uma extração com `ingested_at` anterior.

| Colunas | Regra |
| --- | --- |
| `id`, `node_id`, `name`, `full_name`, `owner` | Identidade obrigatória; `id` é bigint positivo e chave primária |
| `description`, `language` | Texto anulável |
| `created_at`, `updated_at`, `pushed_at` | Timestamps com fuso; somente `pushed_at` aceita nulo |
| `stars`, `forks`, `watchers`, `open_issues` | Bigints não negativos |
| `default_branch`, `archived`, `visibility` | Obrigatórios; visibilidade public/private/internal |
| `ingested_at`, `raw_path`, `pipeline_run_id` | Rastreiam extração, snapshot e execução; FK para `pipeline_runs` |

O modelo mapeia `owner.login`, `stargazers_count`, `forks_count`,
`subscribers_count` e `open_issues_count`. `watchers` usa `subscribers_count`.
Campos desconhecidos permanecem no snapshot original e são ignorados pelo modelo.

## `raw.pipeline_runs`

Cada tentativa de ingestão de um repositório recebe um UUID e registra
`pipeline_name`, `repository`, `started_at`, `finished_at`, `status`,
`records_extracted`, `records_loaded`, `raw_path` e `error_message`.
Um índice por repositório e início facilita consultar execuções recentes.

As constraints permitem `RUNNING` sem fim, `SUCCESS` com fim e sem erro, e
`FAILED` com fim e erro. Contagens são não negativas e a carga não pode exceder a
extração. O erro contém somente o nome da classe da exceção. `raw_path` pode ser
nulo quando a falha ocorre antes da preservação do JSON.

O UPSERT e a transição para `SUCCESS` usam a mesma transação. Uma falha de carga
faz rollback e depois registra `FAILED`; se o banco ficar indisponível, a execução
pode permanecer `RUNNING`. Arquivos já publicados permanecem disponíveis após
falhas de validação ou SQL. Caminhos relativos são resolvidos a partir do diretório
de execução; configure `GITLOG_RAW_DIR` absoluto quando precisar de referência estável.

## Camadas posteriores

- `raw`: dados próximos da origem, com IDs e metadados de ingestão.
- `staging`: dados limpos e tipados pelo dbt.
- `analytics`: `dim_repository`, `dim_contributor`, `dim_date`, `fact_commits`,
  `fact_issues`, `fact_pull_requests` e `fact_repository_daily_metrics`.

Stars e forks históricos dependem de snapshots coletados ao longo do tempo;
o estado atual de um repositório não reconstrói sua série histórica.
O Activity Score só será criado após definição explícita de fórmula, pesos e janela.
