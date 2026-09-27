# Pipeline e execução

## Fase 0

`make setup` instala o pacote e as ferramentas. `make run` mostra a ajuda;
`gitlog --version` informa a versão instalada. Nenhum dado é extraído ou carregado.
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
persistência de entidades, que ainda não existem.

## Fase 1 — cliente HTTP

`GitHubClient` realiza consultas explícitas, mas ainda não há um serviço de
ingestão. `make run` continua exibindo ajuda. A classe lê `GITHUB_TOKEN` do ambiente
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

## Contrato planejado para o MVP

1. Ler e validar configuração e repositórios.
2. Registrar execução e consultar checkpoint da entidade.
3. Extrair páginas da API com timeout e número limitado de tentativas.
4. Preservar o JSON original antes da transformação.
5. Validar e carregar entidades com queries parametrizadas e UPSERT.
6. Confirmar dados e checkpoint de forma consistente após sucesso.
7. Registrar resultado, contagens e duração sem expor segredos.

Uma falha não deve avançar o checkpoint nem impedir a reexecução segura. Paginação,
rate limit, janela incremental e transações serão especificados e testados nas
fases em que forem implementados. O armazenamento raw e a transação SQL não são
atômicos entre si: arquivos já preservados podem permanecer após uma falha SQL.

Particionamento raw proposto:

```text
data/raw/commits/repository=spring-projects_spring-boot/year=2026/month=09/day=26/
```

Os testes unitários do cliente usam mocks/fixtures, sem chamar a API real.
Os testes do loader usarão PostgreSQL de teste isolado. Dados de dashboard virão
exclusivamente de coletas reais; fixtures ficarão restritas aos testes.

## Aceitação futura do MVP

Após configurar o token e iniciar o banco, o usuário deverá ingerir repositories
e commits, consultar ambos no PostgreSQL, repetir a execução sem duplicatas e
executar `pytest` com sucesso. A Fase 1 ainda não satisfaz esse critério.
