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

## Contrato planejado para o MVP completo

1. Ler e validar configuração e repositórios.
2. Registrar execução e consultar checkpoint da entidade.
3. Extrair páginas da API com timeout e número limitado de tentativas.
4. Preservar o JSON original antes da transformação.
5. Validar e carregar entidades com queries parametrizadas e UPSERT.
6. Confirmar dados e checkpoint de forma consistente após sucesso.
7. Registrar resultado, contagens e duração sem expor segredos.

Uma falha não deve avançar o checkpoint nem impedir a reexecução segura. Paginação,
rate limit e transações já possuem testes; janela incremental e checkpoints serão
adicionados com commits. O armazenamento raw e a transação SQL não são
atômicos entre si: arquivos já preservados podem permanecer após uma falha SQL.

Particionamento raw proposto:

```text
data/raw/commits/repository=spring-projects_spring-boot/year=2026/month=09/day=26/
```

Os testes unitários do cliente usam mocks/fixtures, sem chamar a API real.
Os testes do loader usam PostgreSQL de teste isolado. Dados de dashboard virão
exclusivamente de coletas reais; fixtures ficarão restritas aos testes.

## Aceitação futura do MVP

Após configurar o token e iniciar o banco, o usuário deverá ingerir repositories
e commits, consultar ambos no PostgreSQL, repetir a execução sem duplicatas e
executar `pytest` com sucesso. A Fase 2 cobre repositories; commits e checkpoints
ainda são necessários para satisfazer esse critério.
