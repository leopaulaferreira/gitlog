# ADR-003: Incremental de commits por histórico Git

- Status: aceito
- Data: 2026-09-27

## Contexto

O pipeline precisa retomar cargas sem duplicar commits nem perder registros com
datas antigas que entram na branch por merge. Um filtro temporal `since` depende
da data registrada no commit, que não representa o momento em que ele chegou ao
repositório. Uma janela de sobreposição reduz esse risco, mas não o elimina.

## Decisão

Persistir a branch padrão e o SHA da ponta carregada por repository ID. Antes de
paginar, resolver a referência da branch para um SHA fixo. Na primeira carga,
consultar `/repos/{owner}/{repo}/commits?sha={head}`; nas próximas,
`/repos/{owner}/{repo}/compare/{base}...{head}`. Sempre enviar `per_page` e seguir
`Link: rel=next`, usando as proteções, rate limit e retries do cliente comum.

O endpoint de comparação permite obter a diferença de histórico com paginação.
Sua resposta sem parâmetros de paginação limita commits; por isso o pipeline
nunca depende dessa resposta padrão. Validar `total_commits` contra o número de
SHAs distintos extraídos e exigir que a ponta esteja presente antes de avançar.
Fonte: [GitHub — Compare two commits](https://docs.github.com/en/rest/commits/commits#compare-two-commits).

Se a ponta não mudou, registrar sucesso com zero commits extraídos/carregados.
Se a branch mudou, a comparação divergiu/retrocedeu ou a base retorna 404, reler
todo o histórico alcançável pela ponta fixada. `--full-refresh` também força essa
leitura para reconciliar metadados, como logins associados posteriormente.

Preservar cada página completa antes de validar e carregar, inclusive os campos
desconhecidos dos envelopes de comparação. Reabrir páginas do disco para carregar
sem manter todos os payloads em memória. Os conjuntos de SHAs usados na verificação
e deduplicação ainda crescem com a quantidade de commits da execução.

Manter lock de sessão por repository ID e confirmar UPSERT, checkpoint e auditoria
de sucesso em uma transação. Falhas preservam páginas já salvas, fazem rollback
SQL e mantêm o checkpoint anterior. Reexecuções retomam a partir desse checkpoint.

## Limites

- Somente a branch padrão; não representa todas as branches e tags do repositório.
- Commits já observados permanecem no banco após force-push. A tabela representa
  histórico observado, não um inventário exato da alcançabilidade atual.
- O arquivo raw e a transação SQL são recursos separados. Arquivos podem existir
  sem carga bem-sucedida; o manifesto preservado não substitui o status da auditoria.
- Queda do processo pode deixar auditoria `RUNNING`. O lock é liberado ao encerrar
  a conexão e a próxima execução usa o último checkpoint confirmado.
- Cada repositório é confirmado por inteiro; cargas muito grandes podem exigir
  staging e promoção transacional em uma evolução posterior.
