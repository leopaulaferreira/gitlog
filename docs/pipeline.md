# Pipeline e execução

## Fase 0

No bootstrap, `make setup` instalava o pacote e as ferramentas e `make run`
mostrava a ajuda. `gitlog --version` informa a versão instalada. Naquela fase,
nenhum dado era extraído ou carregado.
`make up` inicia PostgreSQL e aguarda o healthcheck; `make down` preserva o volume.

Validação local:

```bash
make check
make compose-check
```

### Validação realizada no bootstrap

| Verificação | Resultado |
| --- | --- |
| `make setup` e `pip check` no ambiente virtual | Instalação concluída, dependências compatíveis |
| `make check` com Python 3.14 | Ruff e Black aprovados; 4 testes passaram |
| Instalação não editável e pytest com Python 3.12 em container | 4 testes passaram; comando `gitlog --version` disponível |
| `make compose-check` | Configuração válida |
| Compose em projeto temporário com porta dinâmica | PostgreSQL chegou a `healthy` |
| Conexão TCP autenticada e consulta SQL | Banco e usuário `gitlog`, `SELECT 1` bem-sucedido |
| Processo principal do PostgreSQL | UID 999, sem root |
| `git check-ignore` | `.env`, `.env.production`, `.venv`, dados e IDE ignorados |

O container, a rede e o volume usados na validação foram removidos. Nenhuma
chamada à API GitHub foi necessária. Essa verificação não testa ingestão ou
persistência de entidades, que ainda não existiam no bootstrap.

## Fase 1 — cliente HTTP

Na Fase 1, `GitHubClient` realizava consultas explícitas sem serviço de
ingestão, e `make run` exibia ajuda. A classe lê `GITHUB_TOKEN` do ambiente
e permite testar o acesso sem PostgreSQL. Veja o [contrato do cliente](github-client.md).

Os testes unitários usam mocks de HTTP e de tempo para validar os headers,
timeouts, classificação de erros, paginação, redirects, rate limit e limites de
retry. Nenhuma chamada à API real é feita na validação automatizada.

### Validação realizada na Fase 1

| Verificação | Resultado |
| --- | --- |
| `make check` no Python 3.14 | Ruff e Black aprovados; 132 testes passaram |
| Instalação não editável e pytest no Python 3.12 em container | 132 testes passaram |
| Cobertura de `ingestion/client` no Python 3.12 | 100% das linhas executáveis e ramificações, usando coverage.py |
| `pip check` | Dependências compatíveis |
| `make compose-check` | Configuração válida; sem alterações no serviço PostgreSQL |

A medição de cobertura usou uma ferramenta instalada somente no container
temporário, sem adicionar dependência ao projeto. Cobertura não substitui uma
validação com a API real: essa execução verificou contratos simulados, sem rede,
persistência ou credenciais reais.

## Fase 2 — repositories

```bash
make setup
# Configure .env: senhas do administrador e da ingestão, token e repositórios.
make up
make migrate
make run
```

`make migrate` usa as credenciais `POSTGRES_*` para aplicar migrações e provisionar
`GITLOG_DB_USER` com `GITLOG_DB_PASSWORD`. Cada migração possui checksum e é aplicada
uma vez; alterar um SQL já aplicado ou encontrar uma migração desconhecida causa
erro. A role de ingestão deve ser separada do administrador e não pode ter flags
administrativas ou associação a outras roles.

`make run` chama `gitlog repositories`. A CLI carrega `.env` do diretório atual
sem substituir valores exportados e valida `GITLOG_REPOSITORIES`, removendo
duplicatas sem diferenciar maiúsculas de minúsculas. Para cada repositório:

1. Registra uma execução `RUNNING` antes da consulta à API.
2. Consulta o repositório usando o cliente HTTP existente.
3. Preserva o JSON completo em arquivo imutável com nome UUID.
4. Valida os campos necessários pelo modelo Pydantic.
5. Executa UPSERT por ID do GitHub e registra `SUCCESS` na mesma transação.
6. Em falha, registra `FAILED` com a classe do erro e continua no próximo repositório.

Se não conseguir iniciar ou finalizar a auditoria, o serviço interrompe a execução.
A CLI retorna 0 em sucesso e 1 se houver falha de configuração, conexão ou ingestão.
Logs JSON incluem repositório, execução, contagens e duração, sem corpos de erro.

Particionamento implementado, sob `GITLOG_RAW_DIR` (padrão `data/raw`):

```text
repositories/repository=octocat_hello-world/year=2026/month=09/day=27/<run_id>.json
```

A data é UTC. O arquivo temporário é sincronizado e publicado sem sobrescrever
snapshots anteriores. O arquivo raw e a transação SQL não são atômicos entre si:
um JSON preservado permanece após uma falha de validação ou carga. Uma interrupção
ou perda do banco pode deixar a auditoria em `RUNNING`; recuperação automática
ainda não está implementada.

Repositories mantém o estado atual e consulta cada repositório a cada execução.
Uma extração mais antiga não sobrescreve uma carga mais recente; nesse caso, a
auditoria registra sucesso com zero registros carregados. Não há checkpoint ou
carga incremental nesta fase.

### Validação da Fase 2

`make check` executa Ruff, Black e os testes unitários. Os testes PostgreSQL são
pulados sem a configuração do runner. `make test-integration` cria um projeto
Compose temporário, banco `gitlog_test`, porta dinâmica e credenciais aleatórias;
ao terminar, remove somente os recursos desse projeto. GitHub continua simulado.
Fixtures JSON ficam restritas aos testes e não são usadas pela CLI de ingestão.

A suíte verifica preservação raw, validação, UPSERT sem duplicatas, renomeação,
rollback com auditoria, falhas individuais, migrações, permissões da role e CLI.
`make compose-check` valida a configuração do Compose sem exibir credenciais.

## Fase 3 — commits incrementais

Após configurar `.env` e executar `make migrate`, use:

```bash
make commits
.venv/bin/gitlog commits --per-page 2  # Exercita paginação em históricos pequenos
.venv/bin/gitlog commits --full-refresh
```

O exemplo de ambiente aponta para `leopaulaferreira/gitlog`. A CLI lê metadados do
repositório, salva `repository.json` e resolve a branch padrão em uma referência
fixa, preservada em `reference.json`. Não é necessário executar `make run` antes.

Sem checkpoint, todas as páginas de commits alcançáveis pelo SHA fixado são lidas.
Com checkpoint, o pipeline usa a comparação paginada entre SHAs; datas de commits
antigos incorporados por merge não impedem a carga. A mesma ponta produz uma
execução de sucesso com zero commits. Divergência, base indisponível ou troca de
branch provocam reconciliação completa. Veja [ADR-003](decisions/ADR-003-incremental-commits.md).

Cada página original é preservada antes da validação em:

```text
data/raw/commits/repository=leopaulaferreira_gitlog/year=2026/month=09/day=27/<run_id>/
  repository.json
  reference.json
  page-000001.json
  page-000002.json
  manifest.json
```

O manifesto registra a branch, os SHAs, o modo, as páginas e a contagem extraída.
Páginas e manifesto podem permanecer após falha SQL; o status definitivo fica em
`raw.pipeline_runs`. Uma falha durante paginação pode deixar somente as primeiras
páginas e nenhum manifesto. Uma resposta de repositório vazio termina com zero,
sem criar checkpoint. O snapshot da referência pode estar ausente nesse caso.

O loader faz UPSERT pela chave `(repository_id, sha)`. Repetições entre páginas
não duplicam linhas, e dados idênticos não são regravados. `records_extracted`
conta os commits recebidos, inclusive quando a gravação raw falha;
`records_loaded` conta chaves distintas inseridas ou alteradas. Em falha SQL,
todos os commits dessa execução são revertidos e `records_loaded` fica zero.

Um lock por repository ID protege a leitura e atualização do checkpoint contra
execuções concorrentes. A transação final inclui metadados, commits, checkpoint
e auditoria. Se o processo cair, a execução pode permanecer `RUNNING`; a próxima
carga usa o último checkpoint confirmado. Rate limit, retries, timeout, ciclos
de paginação e limite de páginas reutilizam o cliente HTTP testado na Fase 1.

### Verificação de duas cargas

Execute `make commits` duas vezes sem novos pushes entre elas. Consulte:

```sql
SELECT id, status, records_extracted, records_loaded, started_at
FROM raw.pipeline_runs
WHERE pipeline_name = 'commit_ingestion'
  AND lower(repository) = 'leopaulaferreira/gitlog'
ORDER BY started_at DESC;

SELECT repository_id, count(*) AS total, count(DISTINCT sha) AS unique_commits
FROM raw.commits
GROUP BY repository_id;

SELECT repository_id, sha, count(*)
FROM raw.commits
GROUP BY repository_id, sha
HAVING count(*) > 1;
```

A segunda carga deve registrar zero extraídos/carregados se a ponta não mudou;
a última consulta não deve retornar linhas. Para verificar a deduplicação com
releitura efetiva, execute `--full-refresh`: os registros idênticos continuam com
zero carregados. A suíte de integração verifica esse comportamento, comparações
com mais de 250 commits, falhas entre páginas, rollback, retry, locks e permissões.

### Validação real em 2026-09-27

Após publicar o histórico local em `leopaulaferreira/gitlog`, duas execuções reais
com `--per-page 2` resultaram em:

| Carga | Páginas de commits | Extraídos | Carregados | Total / únicos no banco |
| --- | --- | --- | --- | --- |
| Primeira | 7 | 13 | 13 | 13 / 13 |
| Segunda, mesma ponta | 0 | 0 | 0 | 13 / 13 |

Ambas terminaram com `SUCCESS`. A ponta validada foi
`edd6c36829116b8e40d672e62fa1c8a492fdb3d9`. O relatório local fica em
`data/validation/phase3-first-two-loads.json`; os dados raw e o relatório não são
versionados. Esses números registram esse instante; novos commits ampliam o histórico.

## Fase 4 — issues e pull requests

`python -m ingestion.main issues`, `pull-requests` e `all` adicionam ingestão das
duas entidades com raw por página, modelos próprios, UPSERT e checkpoints
separados. PRs encontrados em `/issues` são excluídos da tabela de issues e
hidratados em `/pulls/{number}` para preservar sua identidade correta.

O comando `all` executa repositories, commits, issues e PRs, com transações e
auditorias independentes. A migração 003 preserva os dados das fases anteriores.
Veja [Issues e Pull Requests](issues-pull-requests.md) para os campos, incremental,
semântica das métricas, testes e limites de reconciliação.

## Contrato do MVP

1. Ler e validar configuração e repositórios.
2. Registrar execução e consultar checkpoint de commits por repositório.
3. Extrair páginas da API com timeout e número limitado de tentativas.
4. Preservar o JSON original antes da transformação.
5. Validar e carregar entidades com queries parametrizadas e UPSERT.
6. Confirmar dados e checkpoint de forma consistente após sucesso.
7. Registrar resultado, contagens e duração sem expor segredos.

Uma falha não deve avançar o checkpoint nem impedir a reexecução segura. Paginação,
rate limit, transações e checkpoints incrementais já possuem testes.
O armazenamento raw e a transação SQL não são
atômicos entre si: arquivos já preservados podem permanecer após uma falha SQL.

Particionamento raw implementado:

```text
data/raw/commits/repository=leopaulaferreira_gitlog/year=2026/month=09/day=27/<run_id>/
```

Os testes unitários do cliente usam mocks/fixtures, sem chamar a API real.
Os testes do loader usam PostgreSQL de teste isolado. Dados de dashboard virão
exclusivamente de coletas reais; fixtures ficarão restritas aos testes.

## Aceitação do MVP

Após configurar o token e iniciar o banco, o usuário pode ingerir repositories
e commits, consultar ambos no PostgreSQL, repetir a execução sem duplicatas e
executar `pytest` com sucesso. A Fase 3 implementa esse fluxo; testes de banco
continuam disponíveis separadamente com `make test-integration`.
