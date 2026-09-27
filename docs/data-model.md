# Modelo de dados

**Estado: Fase 3.** A migração `001_repositories.sql` cria o schema `raw` com
repositories e auditoria; `002_commits.sql` adiciona commits e checkpoints.
`public.gitlog_schema_migrations` registra nome,
checksum SHA-256 e data de aplicação de cada migração.

## Entidades do MVP

| Entidade | Identidade | Estratégia |
| --- | --- | --- |
| Repository | ID do GitHub | UPSERT dos atributos atuais |
| Commit | Repository ID + SHA | UPSERT sem duplicar commits dentro do repositório |
| Ingestion checkpoint de commits | Repository ID | Branch + SHA da última ponta carregada com sucesso |
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

## `raw.commits`

Chave primária composta `(repository_id, sha)` com FK para `raw.repositories`.
O mesmo SHA em dois repositórios representa duas linhas distintas. Armazena
mensagem completa, nomes/emails/datas de autor e committer, logins GitHub opcionais
e lista de SHAs dos pais. Identidades Git e contas GitHub podem estar ausentes;
esses campos aceitam nulo. Mensagens vazias são válidas.

`ingested_at`, `raw_path` e `pipeline_run_id` apontam para a carga e página JSON
originais. O UPSERT só atualiza quando algum campo da origem mudou; uma releitura
idêntica preserva a proveniência. Índice por repository ID e data do committer
suporta consultas temporais. Estatísticas de arquivos e linhas não são coletadas.

## `raw.ingestion_checkpoints`

Uma linha por repository ID para o pipeline de commits da branch padrão:
`branch`, `head_sha`, `updated_at`, `pipeline_run_id`. O checkpoint não usa datas
de autoria ou de commit. Renomear o repositório mantém a identidade; mudar a
branch padrão inicia reconciliação completa e atualiza a mesma linha.

O serviço mantém um advisory lock de sessão por repository ID desde a leitura
do checkpoint até a conclusão. Outra execução concorrente falha sem sobrescrever
o estado. O PostgreSQL libera o lock quando a sessão termina. Commits, metadados
do repositório, checkpoint e auditoria de sucesso usam uma única transação.

Em auditorias `commit_ingestion`, `records_extracted` conta os itens recebidos nas
páginas de dados, incluindo repetições. `records_loaded` conta chaves distintas
inseridas ou alteradas; uma falha deixa zero registros carregados. Respostas de
metadados e comparações que acionam fallback não entram nas contagens.
`raw_path` aponta ao manifesto em sucesso e ao diretório dos documentos
preservados em falha. Um repositório vazio retorna sucesso com zero e não cria checkpoint.

## Camadas posteriores

- `raw`: dados próximos da origem, com IDs e metadados de ingestão.
- `staging`: dados limpos e tipados pelo dbt.
- `analytics`: `dim_repository`, `dim_contributor`, `dim_date`, `fact_commits`,
  `fact_issues`, `fact_pull_requests` e `fact_repository_daily_metrics`.

Stars e forks históricos dependem de snapshots coletados ao longo do tempo;
o estado atual de um repositório não reconstrói sua série histórica.
O Activity Score só será criado após definição explícita de fórmula, pesos e janela.
