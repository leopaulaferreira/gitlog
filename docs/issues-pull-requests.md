# Issues e Pull Requests — Fase 4

## Fluxo e identidade

Os extractors usam `GitHubClient.get_pages`, que já trata `Link`, ciclos, limite
de páginas, rate limit e retries. Ambos consultam `/repos/{owner}/{repo}/issues`
com `state=all`, `sort=created`, `direction=asc` e `since` nas cargas incrementais.

A resposta é mista: o campo `pull_request` identifica PRs, e o `id` nesse objeto
pertence à issue associada. Issues são os itens sem esse campo. PRs são os itens
com o campo e são hidratados em `/repos/{owner}/{repo}/pulls/{number}`. O número
da resposta de detalhe deve corresponder ao número solicitado. URLs dentro do
payload não são usadas para construir requisições.

Referências: [GitHub — List repository issues](https://docs.github.com/en/rest/issues/issues#list-repository-issues)
e [GitHub — Get a pull request](https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request).

O marcador é verificado também no modelo de Issue para impedir uma carga direta
acidental de PR. O modelo de PR rejeita o envelope de issue: os campos de merge
devem vir da resposta de detalhe. `state=closed` com `merged_at` nulo representa
um PR fechado sem merge; `merged_at` preenchido representa um PR merged.
Um SHA de merge preenchido com `merged_at` nulo pode existir em um PR aberto.

## Raw e carga

`RawLoader.save_document` reutiliza a publicação atômica de snapshots existente:

```text
data/raw/issues/repository=owner_repo/year=YYYY/month=MM/day=DD/<run_id>/
  repository.json
  page-000001.json
  manifest.json

data/raw/pull_requests/repository=owner_repo/year=YYYY/month=MM/day=DD/<run_id>/
  repository.json
  page-000001.json
  pull-request-42.json
  manifest.json
```

Páginas preservam o JSON completo, inclusive itens da outra entidade e campos
desconhecidos. Essa preservação da origem não significa carregar o PR em duas
tabelas. Detalhes de PR são preservados antes da validação. Uma página ou detalhe
inválido impede a carga, mas continua disponível no raw. Falhas posteriores não
removem os arquivos já publicados; a auditoria indica o resultado definitivo.

`UpdatedEntityService` compartilha descoberta, auditoria, preservação e controle
do checkpoint entre os dois extractors. `UpdatedEntityLoader` compartilha
deduplicação e transação. Os métodos de UPSERT de registros alterados, lock e
finalização da auditoria também são reutilizados pelo loader de commits.

Na carga, registros repetidos são reduzidos à versão com maior `updated_at` por
ID. O PostgreSQL impede duplicatas por ID e por `(repository_id, number)`;
`updated_at` anterior não substitui a versão persistida. Autor ausente é nulo.
São armazenados somente os campos tipados necessários, mais proveniência; bodies
e outros atributos permanecem nos arquivos raw.

## Checkpoints e falhas

`raw.entity_checkpoints` usa `(repository_id, entity)`, com entidades `issues` e
`pull_requests`. Os checkpoints de commits continuam em `raw.ingestion_checkpoints`.
Não há dependência de uma carga anterior de repositories; seus metadados são
coletados e persistidos na transação da entidade.

O serviço adquire um advisory lock por entidade/repository ID e captura o horário
antes de começar a descoberta. Lê atualizações desde o checkpoint anterior menos
cinco minutos e consome todas as páginas. Repetições na janela são esperadas e
não duplicam dados. O horário capturado avança o checkpoint apenas junto com a
carga e a auditoria `SUCCESS`; não é derivado da maior data encontrada na origem.

Janelas vazias bem-sucedidas também avançam o checkpoint. Falhas de paginação,
detalhe, validação, raw ou SQL não o avançam. Um rollback deixa `records_loaded=0`.
Falhas individuais são auditadas e o próximo repositório é processado. Falha de
início ou finalização da própria auditoria interrompe o comando.

`records_extracted` conta as ocorrências selecionadas nas páginas de descoberta,
incluindo repetições, antes da gravação raw. Para PRs, uma falha de detalhe pode
deixar candidatos extraídos e nenhum registro carregado. `records_loaded` conta
IDs distintos inseridos ou alterados. Metadados de repositório não entram nessas
contagens. Logs incluem entidade, repositório, execução e métricas, sem bodies
ou mensagens de erro da API/SQL.

## Execução e limites

```bash
python -m ingestion.main migrate
python -m ingestion.main issues --per-page 100
python -m ingestion.main pull-requests --per-page 100
python -m ingestion.main all
python -m ingestion.main issues --full-refresh
python -m ingestion.main pull-requests --full-refresh
```

`all` executa repositories → commits → issues → pull-requests. Cada pipeline é
auditado e confirmado separadamente. O retorno é 1 se algum falhar. Esse comando
nunca aplica migrações implicitamente.

O token precisa permitir a leitura das issues e dos PRs do repositório. A
descoberta depende do acesso ao endpoint de issues também para a carga de PRs.
Falhas de permissão não são interpretadas como listas vazias.

A API usa paginação por posição, não um snapshot imutável. A ordenação por criação
reduz deslocamentos causados por edições; a janela de sobreposição cobre limites
de timestamps e atrasos curtos. Exclusões/transferências durante a paginação e
atrasos de visibilidade maiores que a janela podem exigir `--full-refresh`.
Não há remoção automática de itens que desapareceram da API nem agendamento de
reconciliação completa nesta fase. Uma queda do processo pode deixar uma auditoria
`RUNNING`, sem avançar o checkpoint.

## Validação

`make check` executa lint, formatação e testes sem rede real. `make test-integration`
executa os loaders e comandos contra PostgreSQL temporário, mantendo GitHub mockado.
As fixtures incluem issue aberta/fechada e PR aberto/fechado/merged, com IDs de
issue e PR diferentes. A suíte cobre paginação, incremental, repetição, versões
antigas, checkpoints independentes, rollback, raw, locks, permissões, a CLI `all`
e migração preservando repositories, commits e seus checkpoints.
