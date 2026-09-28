# GitLog Analytics — Metabase

## Serviços e acesso

`docker compose up -d` inicia PostgreSQL GitLog, Metabase **v0.63.18** e um segundo
PostgreSQL para os metadados do Metabase. A interface fica em
`http://localhost:3000` (ou `METABASE_PORT`). O healthcheck consulta `/api/health`.
O dbt é executado sob demanda, com profile `tools`.

O banco interno do Metabase guarda usuários, perguntas, filtros e dashboards no
volume `metabase_data`. Os dados GitLog ficam em `postgres_data`. Separar os
containers evita que permissões, backups ou migrações do produto de BI se misturem
com o warehouse. O banco interno não publica porta no host. A interface e o banco
GitLog publicam portas somente em loopback. Esta é uma stack de desenvolvimento,
sem TLS/proxy/SSO de produção. `docker compose down` preserva os dois volumes.

## Setup automático, reproduzível

Após ingestão real e `make dbt-run && make dbt-test`:

```bash
make dashboard-setup
make dashboard-check
```

Em uma instância nova, o primeiro comando cria a conta administrativa local usando
`METABASE_ADMIN_EMAIL` e `METABASE_ADMIN_PASSWORD` do `.env`; não imprime a senha.
`make demo-config` gera as senhas locais ausentes/de exemplo antes do primeiro boot.
O e-mail padrão é `admin@gitlog.local`: é um login local, não uma conta de e-mail
externa. Leia a senha **localmente no seu `.env`** para entrar na interface.

Em uma instância já configurada, use credenciais de um administrador existente.
O setup não recria usuários nem redefine senhas. Credenciais de `.env` precisam
continuar correspondendo à instância; alterar o arquivo não altera a senha no BI.

O script cria a conexão restrita, a coleção e o dashboard **GitLog Analytics**.
Depois cria/atualiza os 14 cards definidos em
[`dashboard/cards.json`](../dashboard/cards.json) e suas
[queries SQL](../dashboard/queries), associa filtros e executa cada consulta.
Uma segunda execução mantém os objetos, sem duplicá-los. Nomes ambíguos ou
coleções/dashboards homônimos não gerenciados pelo GitLog são recusados.

A coleção é **gerenciada pelo código**: repetir setup atualiza perguntas e layout.
Para personalizar pela UI, duplique o dashboard em outra coleção. A automação usa
os endpoints da API da versão fixada, sem editar o banco interno do Metabase.
Antes de atualizar a imagem, valide novamente o setup e os filtros; a API pode
mudar entre versões. Também é possível montar tudo manualmente com as definições
abaixo, sem depender do script.

## Conexão manual do data source

No assistente inicial ou em **Admin → Databases → Add database**:

| Configuração | Valor |
| --- | --- |
| Tipo | PostgreSQL |
| Nome | GitLog Analytics |
| Host | `postgres` (DNS da rede Compose, não localhost) |
| Porta | `5432` (porta interna, não POSTGRES_PORT do host) |
| Banco | Valor de `POSTGRES_DB`, padrão `gitlog` |
| Usuário | `METABASE_READER_USER`, padrão `gitlog_metabase` |
| Senha | `METABASE_READER_PASSWORD` |
| SSL | Desabilitado na rede local de demonstração |
| Schemas | Incluir somente `analytics` |
| Timezone de relatórios | UTC |

As roles são criadas por `make analytics-init`. Não conecte usando o administrador
ou o usuário da ingestão. A conta BI pode ler analytics, mas não pode escrever nos
marts nem ler raw. `staging`, `intermediate`, `raw` e os dados internos do Metabase
não são fontes para os cards. Após `dbt run`, sincronize schema se necessário.

## Cards e significado

Crie cada pergunta como **Native SQL**, usando o arquivo correspondente. O nome,
tipo de gráfico, séries e mapeamentos também constam em `cards.json`.

| Card | Query | Tipo / eixos | Período usado |
| --- | --- | --- | --- |
| Repositories Monitored | `repositories.sql` | Número: repositories | Não aplica; snapshot atual |
| Commits Collected | `commits.sql` | Número: total | commit_date; sem filtro inclui commits sem data |
| Pull Requests | `pull_requests.sql` | Número: total | created_date do PR |
| Issues | `issues.sql` | Número: total | created_date da issue |
| Merged Pull Requests | `merged_prs.sql` | Número: total | merged_date |
| Closed Issues | `closed_issues.sql` | Número: total | closed_date, somente state=closed |
| Commits over time | `commits_over_time.sql` | Linha: activity_date × commits | Data UTC do evento |
| Pull Requests — Opened vs Merged | `pull_requests_over_time.sql` | Linhas: activity_date × opened, merged | Datas de abertura/merge, não coorte |
| Issues — Opened vs Closed | `issues_over_time.sql` | Linhas: activity_date × opened, closed | Abertura/último fechamento disponível |
| Most Active Repositories — Commits | `active_repositories.sql` | Barras: full_name × commits; top 10 | Data do evento |
| Pull Request Merge Time — Hours | `merge_time.sql` | Barras: full_name × average_pr_merge_time_hours | Data do merge; merged_prs disponível na pergunta |
| Repository Activity Over Time — Commits | `repository_activity.sql` | Linhas: activity_date × commits; série full_name | Data do evento |
| Primary Languages — Current Snapshot | `primary_languages.sql` | Pizza: primary_language × repositories | Não aplica; linguagem atual |
| Repository comparison | `repository_comparison.sql` | Tabela: full_name, contagens e média de merge | Data dos eventos |

Não existe KPI Contributors: logins opcionais de commits não representam uma
dimensão completa de contribuidores. **Merged Pull Requests** é o substituto real.
A lista inclui um sexto KPI, Closed Issues. Most Active Repositories usa contagem
de commits, sem Activity Score nem soma de eventos incompatíveis.

A média de merge usa **soma de horas / quantidade de PRs merged**, agregando as
parcelas já calculadas pelo dbt. Não é média de médias. PRs fechados sem merge não
participam; se não há merges, a média não existe e o gráfico mostra ausência de
dados. Zero seria enganoso. `prs_closed` inclui merged, portanto não some essas
métricas para obter um total de PRs.

Títulos, chaves, UTC, separação issue/PR, datas e duração são responsabilidade do
dbt. As queries de BI apenas filtram e agregam os marts para visualização.

## Filtros e layout manual

Crie três filtros de dashboard, opcionais e sem período padrão oculto:

1. **Repository**: texto/categoria com múltiplos valores; `dim_repository.full_name`.
2. **Date range (UTC)**: período de datas; campo indicado na última coluna da tabela.
3. **Current language**: texto/categoria com múltiplos valores;
   `dim_repository.primary_language`.

No editor SQL, cada variável `{{repository}}`, `{{date_range}}`, `{{language}}`
é um **Field filter**, não uma interpolação de texto. Conecte-a ao campo real
correspondente. As queries não usam aliases de tabelas, para evitar mapeamentos
ambíguos. Não escreva `coluna = {{filtro}}`: o próprio Field filter gera a condição.
Os blocos `[[and {{...}}]]` permitem deixar filtros vazios.

Para séries/compare/merge-time, `date_range` aponta para
`fact_repository_daily_metrics.activity_date`. Para KPIs, siga o campo de data
específico. Repository e language apontam sempre para `dim_repository`, incluída
na query. Date range **não** se conecta ao KPI de repositórios nem à pizza de
linguagens, pois são snapshots. Linguagem filtra eventos por atributo **atual**,
sem afirmar qual era a linguagem no passado.

Na grade de 24 colunas: os seis KPIs ocupam a primeira linha, com 4 colunas cada.
Os oito gráficos seguintes ocupam duas colunas de 12 unidades, em quatro linhas,
na ordem da tabela. A automação aplica esse layout e as configurações das séries.

## Performance e validação

```bash
make dashboard-check
```

O comando executa `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` das 14 queries sem
filtros, usando a role BI. Também confirma acesso negado ao raw, quantidade de
cards/dashboard e consultas pela API do Metabase com filtros combinados, um
repositório inexistente e um período sem eventos. O relatório fica em
`data/validation/phase7-dashboard-validation.json`, ignorado pelo Git.

Na validação inicial, a consulta mais lenta levou aproximadamente **0,1 ms** no
PostgreSQL local, com 1 repositório e 25 commits. Isso é evidência de funcionamento
no volume atual, não benchmark de escala. Nenhum índice analítico adicional foi
criado: os planos e o volume não justificaram. Em maior volume, meça filtros por
repository/date e agregações antes de decidir por índices ou materializações.

O setup foi executado duas vezes sem duplicar dashboard/cards. A interface chegou
a healthy e todas as consultas retornaram pelo driver PostgreSQL do Metabase.
Os dados reais dessa validação possuem zero issues e zero PRs; as curvas desses
eventos mostram zero e o gráfico de tempo de merge fica sem dados. Cenários com
merges são exercitados pelos testes dbt, sem carregar fixtures na demonstração.

## Screenshots

Local reservado: [`docs/images/dashboard-overview.png`](images/README.md).
Abra o dashboard com dados reais, aplique os filtros desejados e capture a tela;
registre data da carga e período no contexto da imagem. Nenhuma imagem sintética
ou screenshot de dados fictícios foi gerada. O arquivo PNG só deve ser adicionado
quando houver uma captura real revisada.

## Atualização e limites

```bash
make ingest-all
make dbt-run
make dbt-test
# Recarregue o dashboard após a conclusão; setup só é necessário ao mudar definição.
```

O dashboard não dispara ingestão nem dbt automaticamente. Fechar/reabrir issues
não gera histórico de transições: os fatos têm apenas os campos observados no
snapshot mais recente. Stars/forks históricos, contributors completos e Activity
Score não são inferidos. A stack não inclui Airflow, MinIO ou Spring Boot.

Referências: [Docker e banco interno do Metabase](https://www.metabase.com/docs/latest/installation-and-operation/running-metabase-on-docker),
[application database](https://www.metabase.com/docs/latest/installation-and-operation/configuring-application-database),
[Field filters](https://www.metabase.com/docs/latest/questions/native-editor/field-filters).
A documentação da API da imagem em execução fica em `http://localhost:3000/api/docs`.
