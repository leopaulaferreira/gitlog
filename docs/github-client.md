# GitHub API Client — Fase 1

O cliente oferece somente GET e retorna JSON em memória. Não transforma campos
de repositories/commits, não salva raw, não acessa PostgreSQL e não avança checkpoints.

## Configuração e uso

Execute `make setup` para instalar HTTPX. O construtor exige `GITHUB_TOKEN` no
ambiente do processo. Não lê `.env` implicitamente; a autenticação usa o header.
No Bash, é possível informar o token sem colocá-lo no histórico do shell:

```bash
read -rs -p 'GITHUB_TOKEN: ' GITHUB_TOKEN
export GITHUB_TOKEN
```

Na IDE, configure a variável na execução Python. Use um token com apenas as
permissões de leitura necessárias para os endpoints escolhidos. Nunca versione
o valor em scripts ou configurações da IDE.

Exemplo Python (executa uma consulta real somente quando chamado):

```python
from ingestion.client import GitHubClient

with GitHubClient(timeout=30, max_retries=3) as github:
    repository = github.get("/repos/spring-projects/spring-boot")
    limits = github.rate_limit
```

Cada instância mantém uma sessão HTTP e deve ser fechada com `with` ou `close()`.
Use a instância sequencialmente. O cliente não coordena a cota entre processos ou
outros consumidores do mesmo token. `trust_env=False` desativa proxies e
configurações TLS herdadas implicitamente do ambiente; TLS permanece verificado.

Headers enviados:

- `Authorization: Bearer <GITHUB_TOKEN>`
- `Accept: application/vnd.github+json`
- `X-GitHub-Api-Version: 2026-03-10`
- `User-Agent: GitLog`

A versão da API é explícita e está documentada entre as
[versões suportadas pelo GitHub](https://docs.github.com/en/rest/about-the-rest-api/api-versions).

## Interface

| Operação | Comportamento |
| --- | --- |
| `get(path, params=None)` | Retorna objeto/lista JSON; `None` para HTTP 204 |
| `get_paginated(path, params=None, per_page=100, max_pages=10000)` | Iterador lazy de objetos de endpoints que retornam arrays |
| `rate_limit` | Últimos `limit`, `remaining`, `reset_at` (epoch UTC) e `retry_after` (segundos na recepção) observados |
| `close()` | Libera sessão e conexões |

`params` deve ser passado como argumento nomeado. Caminhos relativos e URLs
absolutas são aceitos somente na origem `https://api.github.com`, sem userinfo ou
fragmento. Redirects seguem essa mesma restrição, até 5 por operação.

Paginação com filtros, sem persistência:

```python
with GitHubClient() as github:
    commits = github.get_paginated(
        "/repos/spring-projects/spring-boot/commits",
        params={"since": "2026-09-26T00:00:00Z"},
        per_page=100,
    )
    first_commit = next(commits, None)
```

Só a página necessária é buscada. `per_page` aceita inteiros de 1 a 100 e substitui
eventual valor em `params` na primeira página. Páginas seguintes usam o link
`rel="next"` completo fornecido pelo servidor, preservando seus filtros e cursores.
Uma página vazia com `next` ainda permite avançar; a ausência de `next` encerra.
Ciclos e excesso de páginas geram erro em vez de truncar silenciosamente.

A paginação suporta arrays de objetos, usados pelos endpoints previstos no MVP.
Envelopes como `{"items": [...]}` de search não são suportados por esse método.
Uma falha posterior é propagada mesmo se itens anteriores já foram consumidos;
isso não representa uma carga completa. O futuro serviço controlará raw e checkpoints.

## Timeout, retries e rate limit

| Parâmetro do construtor | Padrão | Significado |
| --- | --- | --- |
| `timeout` | 30 s | Timeout HTTPX por operação de conexão, leitura, escrita e pool; não é prazo total do pipeline |
| `max_retries` | 3 | Até 4 tentativas, sem contar redirects; configurável de 0 a 10 |
| `backoff_factor` | 1 s | Backoff exponencial: fator × 2^retry |
| `max_wait` | 300 s | Limite de cada espera automática, não do tempo total da chamada |
| `rate_limit_threshold` | 10 | Warning e pausa antes da próxima chamada quando a cota chega a esse valor |

- **Conexão, protocolo, timeout e 5xx:** retries limitados; esperas padrão de
  1, 2 e 4 segundos. O backoff local é limitado por `max_wait`.
- **401, 404 e demais erros permanentes:** falham imediatamente, sem retry.
- **403:** só é repetido se houver evidência de rate limit nos headers ou na
  mensagem da API; um erro de permissão comum falha imediatamente.
- **429:** tratado como rate limit mesmo sem headers.
- **Retry-After:** aceita segundos ou HTTP-date. A próxima requisição respeita
  a espera mínima informada, inclusive em 503.
- **Cota esgotada:** aguarda até depois de `X-RateLimit-Reset`, com margem de 1 s.
  Se ambos os headers impuserem espera, vale o prazo mais longo.
- **Rate limit sem prazo utilizável:** espera inicial de 60 s e aumento
  exponencial em falhas sucessivas, sem ultrapassar o orçamento de tentativas.
- **Próximo do limite:** conserva a resposta bem-sucedida e adia a próxima
  requisição até o reset. Sem reset válido, a pausa é de 1 s se ainda há cota,
  ou 60 s se ela acabou. `rate_limit_threshold=0` restringe a pausa à cota esgotada.
- **Espera acima de `max_wait`:** lança `GitHubRateLimitError`, expondo
  `retry_after` e o status observado; não encurta a espera do servidor.

O prazo de bloqueio permanece na instância após uma falha, evitando que uma nova
chamada imediata contorne o cooldown. Headers ausentes ou malformados ficam como
`None`, sem assumir uma cota fixa. O estado é apenas em memória nesta fase.

## Exceptions e logs

Todas as exceções públicas derivam de `GitHubError`, em
`ingestion.client.exceptions`:

- `GitHubConfigurationError`: token, configuração ou URL inválida.
- `GitHubHTTPError`: status HTTP não bem-sucedido, incluindo 5xx esgotados;
  subclasses para autenticação, permissão e recurso não encontrado.
- `GitHubRateLimitError`: bloqueio por prazo ou tentativas; `retry_after` permite
  que o chamador decida quando retomar a execução.
- `GitHubTransportError` e `GitHubTimeoutError`: transporte esgotou as tentativas.
- `GitHubResponseError` e `GitHubPaginationError`: resposta ou paginação inválida.

Um 404 pode indicar recurso inexistente ou falta de acesso. O cliente não tenta
inferir qual dessas causas ocorreu. Erros não incluem payload, URL ou mensagem
bruta do transporte, pois podem conter dados sensíveis.

O logger `ingestion.client.github_client` emite eventos `github.request`,
`github.retry`, `github.rate_limit.low` e `github.rate_limit.wait`, com campos como
`status_code`, `attempt`, `retry`, `remaining` e `delay_seconds` em `LogRecord.extra`.
Ele não configura o root logger. Formatação JSON e contexto de pipeline serão
adicionados no serviço de ingestão. Não habilite logging de headers de bibliotecas
HTTP em ambientes com credenciais reais.

## Testes

```bash
make check
```

`httpx.MockTransport` substitui a rede e um relógio simulado avança durante as
esperas. Os testes validam respostas, requisições, número de tentativas, tempos,
fechamento da sessão, limites de paginação e ausência de tokens nos logs/erros.
A fixture global bloqueia o transporte HTTP real e usa um token fictício.

## Referências

- [GitHub: boas práticas, retries e redirects](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)
- [GitHub: rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
- [GitHub: paginação](https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api)
- [HTTPX: transportes e MockTransport](https://www.python-httpx.org/advanced/transports/)
- [HTTPX: timeouts](https://www.python-httpx.org/advanced/timeouts/)
