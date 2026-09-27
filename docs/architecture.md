# Arquitetura do GitLog

## Estado atual — Fase 2

O pacote `ingestion` fornece ajuda, versão, migrações e ingestão via `argparse`.
Ajuda e versão não abrem conexões; `repositories` executa a ingestão configurada.
O subpacote `ingestion.client` usa HTTPX para consultas GET explícitas ao GitHub,
com sessão reutilizável, paginação, tratamento de erros e esperas limitadas.
`rate_limit.py` interpreta os headers; `exceptions.py` define os erros públicos.
`extractors.repositories` consulta cada repositório; `loaders.raw_loader` preserva
o JSON antes da validação em `models.github_models`. O serviço coordena essas
etapas e o loader PostgreSQL faz UPSERT e confirma o sucesso da auditoria na mesma
transação. A configuração usa Pydantic e a conexão usa Psycopg 3.
`pyproject.toml` configura empacotamento, pytest, Ruff e Black. O Makefile usa o
ambiente virtual local sem exigir ativação manual.

O Docker Compose inicia PostgreSQL 16 com volume nomeado e healthcheck usando
`pg_isready`. A porta é publicada apenas em `127.0.0.1`. A imagem oficial usa
root durante a preparação das permissões do volume e executa o servidor como
usuário `postgres`; não se força um UID que prejudique a inicialização.

O healthcheck verifica disponibilidade do servidor, não migrações, permissões da
aplicação ou completude de dados. O usuário de bootstrap é administrador local;
`migrate` provisiona a role de ingestão com `USAGE` no schema `raw` e permissões
de leitura, inserção e atualização nas tabelas de repositories e auditoria.
Migrações SQL empacotadas são aplicadas em transação, com lock e checksum.

## Evolução planejada

1. **Ingestão Python:** ampliar o pipeline de repositories para commits e depois
   outras entidades, reutilizando cliente, validação e serviço de execução.
2. **Raw Layer local:** ampliar os snapshots imutáveis particionados por entidade,
   repositório e data UTC de extração para as novas entidades.
3. **PostgreSQL:** adicionar commits e checkpoints ao schema `raw`, que já contém
   repositories e auditoria. `staging` e `analytics` serão responsabilidade das
   transformações.
4. **dbt:** limpeza, dimensões, fatos, documentação e testes de qualidade.
5. **Metabase:** consumir a camada analítica com filtros e métricas reais.
6. **Airflow:** orquestrar o pipeline já funcional pela CLI; execução manual deve
   continuar possível sem o scheduler.
7. **MinIO:** substituir o armazenamento raw local quando houver necessidade.
8. **Spring Boot:** API opcional que consulta exclusivamente a camada analytics.

Esses componentes futuros não são requisitos para executar a Fase 2.

## Configuração e segurança

`.env.example` contém somente placeholders. `.env`, variações de ambiente,
dados locais e chaves são ignorados pelo Git. O token do GitHub não é passado ao
PostgreSQL. A CLI carrega `.env` sem substituir variáveis exportadas; o cliente
HTTP continua lendo apenas o ambiente. Entradas são validadas, SQL usa parâmetros
e identificadores compostos pelo Psycopg, e os logs JSON incluem somente campos
operacionais selecionados. Erros são registrados pela classe, sem seus payloads.

As dependências de desenvolvimento têm intervalos de versão no `pyproject.toml`;
não há lockfile nesta etapa. A imagem fixa a versão principal do PostgreSQL, mas
recebe atualizações da tag. Isso permite correções sem prometer builds idênticos;
fixação por lockfile/digest poderá ser adotada na fase de CI/CD.

## Referências

- [Especificação de serviços do Docker Compose](https://docs.docker.com/reference/compose-file/services/)
- [Imagem oficial PostgreSQL](https://hub.docker.com/_/postgres)
- [Guia oficial de pyproject.toml](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)
- [ADR-001: PostgreSQL](decisions/ADR-001-use-postgresql.md)
- [ADR-002: cliente GitHub síncrono](decisions/ADR-002-github-client.md)
- [Contrato do cliente](github-client.md)
