# ADR-002 — Cliente GitHub síncrono com retries limitados

- Estado: aceito
- Fase: 1

## Contexto

O MVP precisa consultar endpoints paginados, respeitar limites da API e tratar
falhas transitórias. Esse comportamento deve ser compartilhado pelos futuros
extractors, testável sem rede e independente de banco e orquestrador.

## Decisão

Usar `httpx.Client` síncrono com sessão reutilizável, autenticação exclusivamente
via `GITHUB_TOKEN`, versão de API explícita e context manager para liberar conexões.
Concentrar transporte, retries e paginação em `GitHubClient`; separar parsing de
rate limit e exceptions públicas em módulos pequenos.

Seguir `Link: rel=next` em um iterador lazy. Limitar tentativas, redirects e páginas.
Aceitar apenas a origem HTTPS `api.github.com`, incluindo links e redirects, para
evitar encaminhamento do token a outros serviços. Não adicionar suporte Enterprise.

Manter retries explícitos, sem uma biblioteca adicional de resiliência ou retries
ocultos no transporte. Headers do servidor definem uma espera mínima. Se ela
exceder o limite local, retornar erro com `retry_after` para decisão do chamador.
Pausar preventivamente perto do esgotamento da cota. Transporte e funções de tempo
são injetáveis para testes determinísticos.

## Consequências

- Apenas HTTPX é adicionado como dependência de runtime nesta fase.
- O cliente não carrega `.env` automaticamente e não configura logging global.
- O fluxo síncrono facilita controlar rate limit e é suficiente para o MVP.
- Não há coordenação entre processos, checkpoint persistente, deduplicação ou
  garantia de snapshot entre páginas. Essas responsabilidades pertencem ao pipeline.
- Pydantic, python-dotenv e modelos de domínio ficam para a configuração e
  validação da ingestão, quando forem necessários.
- O transporte HTTP é substituído por `MockTransport` e o tempo por um relógio
  simulado nos testes; nenhum token real é necessário.

## Alternativas consideradas

`requests` também atenderia ao requisito, mas HTTPX oferece transporte de teste
próprio e timeout explícito para cada operação. Async e paralelismo não são
necessários para o escopo atual e tornariam o controle da cota mais complexo.
