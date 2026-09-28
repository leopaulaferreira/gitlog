# Analytics com dbt

## Executar

O dbt Core 1.12.5 e o adaptador dbt-postgres 1.11.0 estão fixados em
`docker/dbt/requirements.txt`. O container usa Python 3.12, separado do ambiente
Python da ingestão. O serviço dbt é uma ferramenta com profile `tools`, não um
servidor permanente. Os comandos Make usam a rede Compose e `postgres:5432`;
a porta publicada no host pode ser diferente.

```bash
make setup
# Configure .env, incluindo as senhas distintas de ingestão, dbt e leitor BI.
docker compose up -d postgres
make migrate
make analytics-init
docker compose build dbt
make ingest-all
make dbt-debug
make dbt-run
make dbt-test
make dbt-docs
```

`make analytics-init` provisiona acessos também em volumes existentes; não depende
de scripts de inicialização executados apenas no primeiro boot. A role dbt lê
somente as quatro fontes raw e é dona de `staging`, `intermediate` e `analytics`.
A role Metabase recebe somente SELECT em analytics, incluindo tabelas futuras
criadas pelo dbt. Nenhuma das duas reutiliza a senha administrativa/da ingestão.

`dbt/profiles.yml` não contém segredos: lê variáveis de ambiente. Dentro do
container, `DBT_ENV_SECRET_PASSWORD` recebe `DBT_PASSWORD` e é tratado como segredo
pelo dbt. `LOCAL_UID`/`LOCAL_GID` devem corresponder ao usuário do host Linux para
que os artefatos sejam graváveis. O schema macro usa nomes fixos: use outro banco
ou outra stack para ambientes de desenvolvimento separados.

`make dbt-docs` gera catálogo, manifesto e site em `dbt/target` (ignorado pelo Git).
Para abrir localmente: `python -m http.server 8081 --bind 127.0.0.1 --directory dbt/target`,
e visite `http://localhost:8081`. Pare o servidor ao terminar.

## Decisões de modelagem

- Staging e intermediate são views; os seis marts são tabelas reconstruídas por
  `dbt run`. Reconstrução é deliberada neste volume: corrige atualizações retroativas
  e eventos de fechamento sem introduzir checkpoints analíticos desnecessários.
- O ID numérico estável do GitHub também é `repository_key`. Chaves de fatos são
  strings sem ambiguidade: `repository_id:sha`, `issue:id` e `pr:id`.
- Timestamps são normalizados para UTC e armazenados como timestamp sem timezone
  nos modelos dbt. Todas as datas e agrupamentos usam UTC; configure o BI em UTC.
- Commits usam data do committer, com fallback para autor. Se ambas faltarem, a
  linha permanece no fato com data nula e não entra na série diária. Nunca se usa
  data de ingestão como se fosse data do commit. `timestamp_source` informa a origem.
- Strings opcionais vazias viram NULL. Login desconhecido não vira contributor
  fictício. Não há dimensão de contributors nem Activity Score nesta fase.
- `dim_repository` é uma dimensão tipo 1 (estado atual). Stars, forks e linguagem
  pertencem ao snapshot atual; não são copiados para os dias como histórico.
- Um repositório pode herdar commits anteriores à própria criação. O calendário
  abrange criação e eventos observados, até hoje ou a maior data da fonte.
- A série diária é densa por repository/date, com zeros nos dias sem eventos
  coletados. Zero não comprova cobertura completa da API. Repositórios sem
  ingestão de uma entidade ainda não representam uma coleta completa dela.
- `issues_closed` e `prs_closed` contam o último fechamento disponível de itens
  atualmente fechados. Reaberturas e fechamentos repetidos não podem ser
  reconstruídos dos snapshots. `prs_closed` inclui merged; não some os dois
  indicadores para criar um total de PRs.
- `merge_time_hours` mede horas corridas entre criação e merge, somente para
  PRs merged. A média diária usa a data do merge. Para agregar dias/repositórios,
  use `sum(pr_merge_time_hours_sum) / nullif(sum(prs_merged), 0)`; nunca média de médias.
  Sem merges, a média é NULL, não zero. Isso não é lead time de produção nem horas úteis.

## Qualidade e publicação

Sources, models e colunas estão documentados nos `schema.yml`. Há testes
`unique`, `not_null`, `relationships`, `accepted_values`, reconciliação raw/fatos,
reconciliação diária por repositório e proteção contra issue/PR sobrepostos.
Testes unitários dbt validam UTC, valores nulos, duração de merge, múltiplos
repositórios e preenchimento de dias vazios sem inserir fixtures nos marts.

Execute ingestão, depois `dbt run`, depois `dbt test`. Um `run` bem-sucedido sozinho
não comprova qualidade. As tabelas são trocadas individualmente: a publicação não
é atômica entre todos os marts. Evite ingestão concorrente e atualizações do BI
durante o rebuild; em maior escala, publicação por schema/versionamento pode ser
adotada. Em falha, corrija a causa e repita run/test antes de consumir resultados.

Não foram adicionados índices aos marts por antecipação. Para o volume local,
as consultas fazem agregações e varreduras pequenas. O procedimento de performance
está em [dashboard](dashboard.md); reavalie planos e volume antes de criar índices.

Referências oficiais: [configuração PostgreSQL do dbt](https://docs.getdbt.com/docs/local/connect-data-platform/postgres-setup)
e [materializações e índices PostgreSQL](https://docs.getdbt.com/reference/resource-configs/postgres-configs).

## Validação das Fases 6–7

- `dbt debug`: conexão e configuração aprovadas.
- `dbt run`: 12 models (6 views e 6 tabelas), sem erros.
- `dbt test`: 142 data tests e 3 unit tests aprovados, sem warnings.
- `dbt docs generate`: catálogo e documentação gerados.
- Python: 235 testes unitários e 117 de integração PostgreSQL aprovados.
- Ruff, Black e `docker compose config`: aprovados.
- Ingestão real de todos os pipelines concluída; marts com 1 repositório,
  25 commits, zero issues e zero PRs na consulta realizada.
- Metabase: 14 cards consultados com filtros, inclusive após reconstruir os marts;
  segunda execução do setup sem duplicatas; acesso do leitor ao raw negado.

Os logs e relatórios locais ficam em `data/validation/phase6-*` e `phase7-*`.
Não são versionados porque descrevem uma execução local, não fixtures de produção.
