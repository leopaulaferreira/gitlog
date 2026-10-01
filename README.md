# GitLog

**Transformando a atividade do GitHub em dados estruturados e análises úteis.**

<img width="1914" height="871" alt="Captura de tela de 2026-09-30 22-41-40" src="https://github.com/user-attachments/assets/cf17b5ee-cb0f-4bc6-8850-6bc7e9712285" />


## O que é o GitLog?

GitLog é um projeto de portfólio de Engenharia de Dados e Software que irá
transformar atividade real da API pública do GitHub em dados estruturados e análises.
O desenvolvimento é incremental, com entregas executáveis e verificáveis.

**Estado atual: Fases 6 e 7 — dbt e GitLog — Análises no Metabase.** A ingestão
real de repositories, commits, issues e PRs preserva JSON, valida dados e carrega
PostgreSQL com UPSERT, auditoria e checkpoints. O dbt gera dimensões e fatos; o
Metabase apresenta métricas sobre os repositórios monitorados, com filtros.

## Arquitetura

O fluxo abaixo está implementado. Airflow, MinIO e Spring Boot permanecem futuros:

```mermaid
flowchart LR
    A[API REST do GitHub] --> B[Ingestão Python]
    B --> C[JSON bruto]
    C --> D[PostgreSQL]
    D --> E[dbt]
    E --> F[Camada analítica]
    F --> G[Metabase]
```

Detalhes e limites de cada etapa: [arquitetura](docs/architecture.md).

## Tecnologias

| Camada | Tecnologia | Estado |
| --- | --- | --- |
| Pacote / CLI | Python 3.12+, argparse, setuptools | Configurado |
| Banco local | PostgreSQL 16, Docker Compose | Configurado |
| Qualidade | pytest, Ruff, Black | Configurado |
| Cliente GitHub | HTTPX síncrono | Implementado |
| Validação / configuração do pipeline | Pydantic, python-dotenv | Implementado |
| Persistência | Psycopg 3, migrações SQL | Repositories, commits, issues, PRs, checkpoints e auditoria |
| Transformação | dbt Core 1.12.5 + dbt-postgres 1.11.0 | 12 models, documentação e testes |
| Visualização | Metabase 0.63.18 | GitLog — Análises com 14 cards e filtros |
| Orquestração / data lake | Airflow, MinIO | Fases posteriores |

## Funcionalidades

- Pacote instalável em ambiente virtual, com comandos de ajuda e versão.
- PostgreSQL com healthcheck, volume persistente e porta limitada ao localhost.
- Configurações de teste, lint e formatação centralizadas no `pyproject.toml`.
- Arquivo de exemplo de ambiente e exclusão de segredos e dados locais do Git.
- Cliente GitHub autenticado com paginação lazy, retries limitados e tratamento
  dos limites primário e secundário da API.
- Exceptions próprias e logs com campos estruturados, sem tokens ou payloads.
- Snapshots JSON imutáveis, validação de repositories e UPSERT pelo ID do GitHub.
- Migrações com checksum, role de ingestão restrita e auditoria transacional.
- Commits incrementais pela diferença entre SHAs, com checkpoint confirmado
  junto com os dados e a auditoria; reexecuções não criam duplicatas.
- Issues abertas/fechadas e PRs abertos/fechados/merged com identidade correta,
  snapshots completos e atualização incremental por entidade e repositório.

## Pipeline de dados

`gitlog repositories` executa `GitHub → Python → Raw JSON → PostgreSQL` para os
repositórios configurados. `gitlog migrate` prepara o schema e a role de ingestão.
`gitlog commits` ingere o histórico da branch padrão e, nas próximas execuções,
somente a diferença desde o checkpoint. Também atualiza os metadados do repositório;
não exige uma execução prévia de `repositories`.
`gitlog issues` e `gitlog pull-requests` carregam as novas entidades com checkpoints
independentes. `gitlog all` executa repositories, commits, issues e PRs nessa ordem.
Consulte o [contrato do pipeline](docs/pipeline.md).

## Qualidade dos dados

IDs, estados, timestamps, SHAs e campos obrigatórios são validados antes da carga.
Constraints e foreign keys protegem unicidade e referências no PostgreSQL. A migração
004 fortalece essas regras sem apagar dados inválidos: aplique com `make migrate`.
A separação entre issues e PRs continua respeitando o marcador `pull_request` da API.

## Tolerância a falhas

Cada repositório/entidade confirma dados, checkpoint e auditoria juntos. Uma falha
reverte essa unidade e permite continuar nas outras; o checkpoint anterior permanece.
Perda de conexão que impeça confirmar a auditoria interrompe o comando. Execuções
pendentes ficam visíveis, sem reset automático. Retries e rate limit reutilizam
`GitHubClient`. Veja a [estratégia de recuperação](docs/pipeline.md#falhas-e-recuperação).

## Observabilidade

Logs JSON correlacionam repositório, entidade e run ID, inclusive nos retries.
Cada carga produz resumo com status, extraídos, carregados, ignorados e duração.
`pipeline_runs` agora inclui `duration_ms` e `records_skipped`; este último conta
releituras/duplicatas somente em sucesso, sem classificar rollback como skip.

```bash
python -m ingestion.main status
python -m ingestion.main status --json
```

O comando mostra última execução, pipelines já executados, checkpoints e auditorias
pendentes. Usa somente PostgreSQL e não exige token GitHub. Um código 0 indica
consulta concluída, não ausência de falhas nos pipelines.

## Idempotência

Repositories usa ID GitHub; commits usa repository ID + SHA; issues e PRs usam
seus IDs próprios e unicidade por repository ID + number. UPSERT evita duplicação;
releituras idênticas não aumentam `records_loaded`. A sobreposição incremental é
segura e checkpoints só avançam após sucesso. Cada tentativa preserva auditoria e
raw próprios. `status` também detecta sobreposições conceituais entre issues e PRs.

O [contrato do pipeline](docs/pipeline.md) explica métricas, checkpoints, retries,
constraints e limites. Execute `make coverage` para medir linhas e branches das
suítes unitária e PostgreSQL, sem meta artificial de 100%.

## Camada analítica

O projeto [`dbt/`](dbt/) lê as quatro fontes raw e produz views em `staging` e
`intermediate`, e tabelas em `analytics`. Datas são normalizadas para UTC; valores
nulos legítimos são preservados. A role dbt é separada da ingestão e a role BI tem
somente leitura em analytics.

```bash
make analytics-init
make dbt-debug
make dbt-run
make dbt-test
make dbt-docs
```

Os 12 models incluem quatro staging, dois intermediários e seis marts. Os testes
verificam unicidade, campos obrigatórios, relacionamentos, valores permitidos e
reconciliação de métricas. Há testes unitários para UTC, commits sem data, PRs
merged e preenchimento diário. [Operação e limites](docs/analytics.md).

## Modelo de dados

O schema `raw` mantém repositories, commits, issues, pull_requests, checkpoints e
pipeline_runs. O schema `analytics` contém:

| Dimensões / fatos | Granularidade |
| --- | --- |
| `dim_repository` | Repository ID estável, atributos atuais |
| `dim_date` | Data UTC |
| `fact_commits` | Repository + SHA |
| `fact_issues` | Issue |
| `fact_pull_requests` | Pull request |
| `fact_repository_daily_metrics` | Repository + data |

A série diária contém commits, aberturas/fechamentos de issues e PRs, merges e
média ponderável de horas até merge. Stars/forks ficam somente no snapshot atual;
não há histórico inventado, dimensão artificial de contributors ou Activity Score.
Veja os campos, chaves e o [diagrama Mermaid dimensional](docs/data-model.md).

## Demonstração rápida

Pré-requisitos: Docker/Compose em execução, Python 3.12+ e Make. Para uma nova instalação:

```bash
git clone https://github.com/leopaulaferreira/gitlog.git
cd gitlog
cp .env.example .env
make setup
make demo-config  # Gera senhas locais distintas e preserva valores já configurados
# Edite .env: configure GITHUB_TOKEN e os repositories desejados.
docker compose up -d
make migrate
make analytics-init
docker compose build dbt
make ingest-all
make dbt-debug
make dbt-run
make dbt-test
make dashboard-setup
make dashboard-check
```

A primeira inicialização do Metabase pode levar alguns minutos. Aguarde
`docker compose ps` mostrar healthy antes de `dashboard-setup`.
Abra **http://localhost:3000**, entre com `METABASE_ADMIN_EMAIL` e a senha
`METABASE_ADMIN_PASSWORD` guardada no `.env`, e abra a coleção/dashboard
**GitLog — Análises**. O setup imprime o link exato, sem imprimir a senha.

Para apresentar o painel, use também a conta de demonstração já provisionada:
`user@teste.com` / `GitLogDemo123456`. Ela pode abrir o dashboard e usar os
filtros; não pode criar consultas, baixar dados, editar cards ou acessar outras
coleções. Essas credenciais são públicas e exclusivas para visualizar a demo.

Para atualizar uma instalação existente, preserve seu `.env` e suas senhas;
`make demo-config` preenche apenas senhas ausentes/de exemplo. Não sobrescreva
credenciais de volumes existentes. A demonstração usa a API real: se o repositório
não possui issues ou PRs, os respectivos indicadores ficam vazios/zerados.

Para o perfil de servidor Oracle, consulte o [guia de deploy](docs/oracle-deploy.md).

## Primeiros passos

Pré-requisitos: Python 3.12 ou superior com `venv` e `pip`, Docker Engine em execução,
Docker Compose v2+ e GNU Make. Os comandos abaixo pressupõem Linux, macOS ou WSL.
Depois de clonar o repositório, entre no diretório do projeto:

```bash
cp .env.example .env
# Configure senhas diferentes para POSTGRES_PASSWORD e GITLOG_DB_PASSWORD.
# Configure GITHUB_TOKEN e GITLOG_REPOSITORIES para a ingestão.
docker compose up -d --wait
make setup
make migrate
make run
make commits
make issues
make pull-requests
# Ou execute todos os pipelines com: make ingest-all
```

`make setup` cria `.venv` e instala o projeto com as dependências de desenvolvimento.
Para escolher um interpretador, use `make setup PYTHON=python3.12`.
Se o sistema não oferecer `venv`/`pip`, instale esses componentes pelo gerenciador
de pacotes da sua distribuição antes do setup.

O token pode permanecer vazio para setup, ajuda, testes, PostgreSQL e migrações.
`make run` consulta a API real e grava snapshots e dados no PostgreSQL.
Para mostrar somente a ajuda, execute `.venv/bin/gitlog --help`.

## Variáveis de ambiente

| Variável | Uso |
| --- | --- |
| `GITHUB_TOKEN` | Obrigatório para criar `GitHubClient`; nunca versionar |
| `GITLOG_REPOSITORIES` | Lista `owner/repository` separada por vírgulas, sem duplicatas por capitalização |
| `GITLOG_RAW_DIR` | Diretório de snapshots; padrão `data/raw` |
| `GITLOG_DB_USER` | Role restrita de ingestão; padrão `gitlog_ingestion` |
| `GITLOG_DB_PASSWORD` | Senha da role de ingestão, obrigatória para migração e carga |
| `POSTGRES_HOST` | Host do PostgreSQL; padrão `127.0.0.1` |
| `POSTGRES_PORT` | Porta publicada no host; padrão `5432` |
| `POSTGRES_DB` | Banco inicial; padrão `gitlog` |
| `POSTGRES_USER` | Usuário de inicialização local; padrão `gitlog` |
| `POSTGRES_PASSWORD` | Obrigatória no Compose; substitua o exemplo em `.env` |
| `DBT_USER`, `DBT_PASSWORD` | Role dona dos schemas analíticos; provisionada por analytics-init |
| `METABASE_READER_USER`, `METABASE_READER_PASSWORD` | Role BI com SELECT somente em analytics |
| `METABASE_DB_PASSWORD` | Senha do banco interno do Metabase, em volume separado |
| `METABASE_PORT` | Porta HTTP local, padrão 3000 |
| `METABASE_ADMIN_EMAIL`, `METABASE_ADMIN_PASSWORD` | Conta local usada pelo setup do dashboard |
| `METABASE_RECRUITER_EMAIL`, `METABASE_RECRUITER_PASSWORD` | Conta pública de demonstração, restrita ao dashboard; não usar para dados privados |
| `LOCAL_UID`, `LOCAL_GID` | Dono dos artefatos dbt no host Linux |

O Compose lê `.env` automaticamente. Os comandos de migração e ingestão
carregam `.env` do diretório atual sem sobrescrever variáveis já exportadas.
Ao usar `GitHubClient` diretamente em Python, defina `GITHUB_TOKEN` no ambiente;
a classe não carrega `.env` implicitamente.
O container PostgreSQL GitLog recebe somente suas variáveis de inicialização.
O dbt e o Metabase recebem as credenciais específicas para cada serviço; o token
GitHub não é repassado a esses containers.

O usuário criado pela imagem oficial é administrador do banco local, usado pelas
migrações. `make migrate` provisiona uma role separada com `USAGE` no schema `raw`
e `SELECT`, `INSERT`, `UPDATE` nas sete tabelas. A ingestão usa essa role.
Essa configuração não é uma implantação de produção.

## Execução local

```bash
make up                  # Inicia PostgreSQL, Metabase e seu banco interno
docker compose ps
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
make migrate             # Aplica migrações e provisiona a role restrita
make run                 # Ingere os repositórios configurados usando a API real
make commits             # Histórico de commits e próximas cargas incrementais
make issues              # Issues abertas/fechadas, excluindo PRs
make pull-requests       # PRs com os campos de merge
make ingest-all          # Os quatro pipelines, com auditorias independentes
.venv/bin/gitlog commits --full-refresh  # Reconcilia todo o histórico alcançável
.venv/bin/gitlog --version
make down                # Para e remove containers; preserva o volume
```

`docker compose up -d` também funciona, mas retorna antes de garantir que o banco
esteja saudável. Se a porta 5432 estiver ocupada, altere `POSTGRES_PORT` em `.env`.
As variáveis de inicialização só criam usuário, senha e banco quando o volume está
vazio; mudar `.env` não altera as credenciais de um banco já inicializado.
Não use `docker compose down -v` se precisar preservar os dados.

### Uso do cliente GitHub

Exemplo Python, após definir `GITHUB_TOKEN` no ambiente e executar `make setup`:

```python
from ingestion.client import GitHubClient

with GitHubClient() as github:
    repository = github.get("/repos/spring-projects/spring-boot")
    rate_limit = github.rate_limit
```

O exemplo consulta a API real e retorna JSON em memória. Não grava arquivos nem
tabelas. A API pública, parâmetros, política de retry e exemplos de paginação estão
documentados em [GitHub API Client](docs/github-client.md).

## Execução dos testes

```bash
make test
make test-integration    # PostgreSQL temporário; GitHub continua simulado
make coverage            # Mede linhas e branches das duas suítes
make dbt-test            # 142 testes de dados e 3 testes unitários dbt
make dashboard-check     # SQL, filtros e acesso pelo Metabase real
make lint
make format-check
make check               # Lint, formatação e testes
make compose-check       # Valida o Compose usando .env.example, sem exibir senhas
make format              # Organiza imports e formata o código
```

Para executar `pytest` diretamente:

```bash
source .venv/bin/activate
pytest
```

Os testes verificam instalação, CLI, autenticação, timeout, erros HTTP, redirects,
paginação, rate limits e retries. Usam `httpx.MockTransport`, token fictício e relógio
simulado; o transporte HTTP real é bloqueado nos testes unitários e de integração.
`make test` pula os testes de banco quando não há configuração do runner.
`make test-integration` cria um projeto Compose isolado com porta dinâmica e
credenciais temporárias, executa os testes e remove seu container e volume.
Os testes cobrem UPSERT, rollback, auditoria, migrações, permissões, paginação,
incremental, concorrência, histórico reescrito e a CLI.
Os testes de issues/PRs cobrem páginas mistas, ID de issue diferente do ID de PR,
estados aberto/fechado/merged, repetição sem duplicação, atualização de registros,
checkpoints separados, falhas de raw/SQL/paginação e preservação da Fase 3 na migração.

## Painel

**GitLog — Análises** disponibiliza analytics dos repositórios monitorados via
Metabase em português brasileiro. São 14 cards: seis KPIs, séries de commits/issues/PRs, ranking por commits,
tempo de merge, atividade por repositório, linguagens atuais e comparação entre
repositórios. Os filtros são Repositório, Período (UTC) e Linguagem atual.

`make dashboard-setup` cria a conexão de leitura, a coleção, as perguntas e o
layout de forma repetível. `make dashboard-check` executa consultas e filtros reais
pelo Metabase e mede planos SQL. Definições, instruções manuais, significado das
métricas e limites estão em [docs/dashboard.md](docs/dashboard.md).

Screenshots reais podem ser adicionados em `docs/images/dashboard-overview.png`;
consulte [instruções de captura](docs/images/README.md). Nenhuma captura fictícia
foi gerada. A camada visual consome os marts dbt, sem reproduzir a transformação
ou usar fixtures como dados da demonstração.

## Carga incremental

Commits fixam o SHA da branch padrão antes de paginar. A primeira execução lê
todo o histórico; a próxima compara o checkpoint com a nova ponta. Datas antigas
em commits incorporados por merge não os excluem da carga. Se o SHA não mudou,
nenhuma página de commits é requisitada e as contagens são zero.

Dados, checkpoint e auditoria de sucesso são confirmados na mesma transação.
Histórico divergente, base indisponível ou mudança de branch provocam reconciliação
completa. `--full-refresh` força essa reconciliação; commits já observados são
preservados mesmo após force-push. A carga cobre a branch padrão, não todas as branches.

`records_extracted` conta registros de commits recebidos nas páginas processadas;
`records_loaded` conta commits distintos inseridos ou efetivamente atualizados.
Registros idênticos não são regravados. Veja a [decisão de incremental](docs/decisions/ADR-003-incremental-commits.md).

### Questões e solicitações de alteração

```bash
source .venv/bin/activate
python -m ingestion.main migrate
python -m ingestion.main issues
python -m ingestion.main pull-requests
python -m ingestion.main all
```

Os três comandos aceitam `--per-page 1..100` e `--full-refresh`. Para atualizar uma
instalação da Fase 3, execute `migrate`: a migração `003_issues_pull_requests.sql`
adiciona as tabelas e permissões sem alterar repositories, commits ou seus checkpoints.

O endpoint `/issues` inclui PRs. A presença da chave `pull_request` exclui o item
da tabela de issues, mesmo quando seu valor é nulo. Para PRs, esse endpoint serve
apenas à descoberta incremental: cada número selecionado é consultado em
`/pulls/{number}` para obter o ID real e os campos `merged_at`, `merge_commit_sha`
e `draft`. `merge_commit_sha` preenchido não significa, sozinho, que houve merge.

Cada entidade mantém um checkpoint por repository ID. A consulta usa `state=all`
e `since=checkpoint-5 minutos`, seguindo todas as páginas pelo `GitHubClient`.
A ordenação por criação evita que uma edição mude a posição do item durante a
paginação. O checkpoint registra o início da descoberta, somente após confirmar
dados e auditoria na mesma transação. Em falha, permanece no valor anterior.

As tabelas têm PK pelo ID da própria entidade e `UNIQUE (repository_id, number)`.
O UPSERT não regrava dados iguais e não substitui uma versão por outra com
`updated_at` anterior. As métricas contam os itens selecionados da entidade em
`records_extracted` e as identidades inseridas/alteradas em `records_loaded`.
Uma releitura da janela pode extrair registros conhecidos e carregar zero.

Em `all`, cada pipeline/repositório tem sua auditoria e transação; uma falha
individual auditável não impede os pipelines seguintes. O comando retorna 1 se
houver qualquer falha e 0 se todos terminarem com sucesso. `all` não executa
migrações automaticamente. Detalhes e limites: [Issues e PRs](docs/issues-pull-requests.md).

## Estrutura do projeto

```text
gitlog/
├── ingestion/
│   ├── __init__.py
│   ├── main.py              # CLI: migrate, repositories, commits, issues, pull-requests, all
│   ├── config.py
│   ├── logging_config.py
│   ├── db/                  # Runner de migrações e SQL versionado
│   ├── extractors/
│   ├── loaders/             # Snapshots JSON e PostgreSQL
│   ├── models/
│   ├── services/
│   └── client/
│       ├── __init__.py
│       ├── github_client.py
│       ├── rate_limit.py
│       └── exceptions.py
├── tests/
│   ├── conftest.py
│   ├── test_cli.py
│   ├── test_github_client.py
│   ├── test_rate_limit.py
│   ├── test_repositories.py
│   ├── test_commits.py
│   ├── test_issues_pull_requests.py
│   ├── fixtures/
│   └── integration/
├── dbt/                    # Sources, staging, intermediate, marts e testes
├── dashboard/              # Cards versionados e SQL nativo do Metabase
├── docker/dbt/             # Ambiente dbt Python 3.12 separado
├── scripts/                # Provisionamento, setup BI, validação e testes
│   └── test_integration.py
├── docs/
│   ├── architecture.md
│   ├── data-model.md
│   ├── pipeline.md
│   ├── github-client.md
│   ├── issues-pull-requests.md
│   └── decisions/
│       ├── ADR-001-use-postgresql.md
│       ├── ADR-002-github-client.md
│       └── ADR-003-incremental-commits.md
├── docker-compose.yml
├── pyproject.toml
├── .env.example
├── .gitignore
├── Makefile
└── README.md
```

Airflow, MinIO, Spring Boot e workflows de CI não fazem parte desta entrega.

## Etapas do projeto

- [x] Fase 0: bootstrap Python, qualidade, documentação e PostgreSQL local.
- [x] Fase 1: GitHub API Client — autenticação, paginação, timeout, rate limit e retries.
- [x] Fase 2: repositories — API → Raw JSON → PostgreSQL.
- [x] Fase 3: commits — paginação, incremental e idempotência; validação do MVP.
- [x] Fase 4: issues e pull requests.
- [x] Fase 5: qualidade, auditoria, status e recuperação documentada de falhas.
- [x] Fase 6: dbt — staging, dimensões, fatos e testes.
- [x] Fase 7: GitLog — Análises no Metabase; sem inventar contributors ou histórico.
- [ ] Fase 8: Airflow.
- [ ] Fase 9: MinIO.
- [ ] Fase 10: CI/CD com GitHub Actions.
- [ ] Fase 11: Spring Boot Analytics API.
- [ ] Fase 12: deploy em cloud.

Checkpoints, constraints e rastreabilidade necessários ao MVP foram
implementados nas fases 2–3; a Fase 5 amplia essas garantias.

## Melhorias futuras

Activity Score com fórmula documentada antes da implementação; releases, tags,
branches, workflows e deployments. Kafka, Spark, AWS S3/Glue/Athena/Redshift,
BigQuery, Databricks, Terraform e Kubernetes são possibilidades a avaliar,
sem compromisso de adoção ou implementação antecipada.

## Licença

Licença ainda não definida. Uma licença explícita deverá ser escolhida antes de
distribuir o projeto como software open source.
