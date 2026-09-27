# Arquitetura do GitLog

## Estado atual — Fase 0

O pacote `ingestion` fornece apenas ajuda e versão via `argparse`. Não possui
dependências de runtime, não acessa rede e não se conecta ao banco.
`pyproject.toml` configura empacotamento, pytest, Ruff e Black. O Makefile usa o
ambiente virtual local sem exigir ativação manual.

O Docker Compose inicia PostgreSQL 16 com volume nomeado e healthcheck usando
`pg_isready`. A porta é publicada apenas em `127.0.0.1`. A imagem oficial usa
root durante a preparação das permissões do volume e executa o servidor como
usuário `postgres`; não se força um UID que prejudique a inicialização.

O healthcheck verifica disponibilidade do servidor, não migrações, permissões da
aplicação ou completude de dados. O usuário de bootstrap é administrador local;
uma role de ingestão com permissões restritas deve preceder a implementação do loader.

## Evolução planejada

1. **Ingestão Python:** cliente GitHub reutilizável, extractors por entidade,
   validação e serviço de execução. Repositórios serão configuráveis.
2. **Raw Layer local:** preservar os payloads originais antes de transformar e
   carregar; particionar arquivos por entidade, repositório e data de extração.
3. **PostgreSQL:** persistir entidades com constraints, UPSERT, estado e auditoria.
   A camada `raw` mantém proximidade da origem; `staging` e `analytics` serão
   responsabilidade das transformações.
4. **dbt:** limpeza, dimensões, fatos, documentação e testes de qualidade.
5. **Metabase:** consumir a camada analítica com filtros e métricas reais.
6. **Airflow:** orquestrar o pipeline já funcional pela CLI; execução manual deve
   continuar possível sem o scheduler.
7. **MinIO:** substituir o armazenamento raw local quando houver necessidade.
8. **Spring Boot:** API opcional que consulta exclusivamente a camada analytics.

Esses componentes futuros não são requisitos para executar a Fase 0.

## Configuração e segurança

`.env.example` contém somente placeholders. `.env`, variações de ambiente,
dados locais e chaves são ignorados pelo Git. O token do GitHub não é passado ao
PostgreSQL. Futuros clientes devem validar entradas, parametrizar SQL e evitar
tokens, credenciais e payloads sensíveis em logs.

As dependências de desenvolvimento têm intervalos de versão no `pyproject.toml`;
não há lockfile nesta etapa. A imagem fixa a versão principal do PostgreSQL, mas
recebe atualizações da tag. Isso permite correções sem prometer builds idênticos;
fixação por lockfile/digest poderá ser adotada na fase de CI/CD.

## Referências

- [Especificação de serviços do Docker Compose](https://docs.docker.com/reference/compose-file/services/)
- [Imagem oficial PostgreSQL](https://hub.docker.com/_/postgres)
- [Guia oficial de pyproject.toml](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)
- [ADR-001: PostgreSQL](decisions/ADR-001-use-postgresql.md)
