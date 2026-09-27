# ADR-001 — PostgreSQL para persistência local

- Estado: aceito
- Fase: 0

## Contexto

O GitLog precisa de armazenamento relacional com constraints, transações e UPSERT,
além de integração posterior com dbt e Metabase. O ambiente deve ser simples de
executar localmente.

## Decisão

Usar PostgreSQL 16 pela imagem oficial `postgres:16-bookworm`, iniciado via Docker
Compose com volume nomeado, healthcheck e porta publicada somente no localhost.
Credenciais são fornecidas via variáveis de ambiente. Nesta fase, o banco não
possui tabelas da aplicação.

## Consequências

- O banco pode ser iniciado independentemente da CLI e persiste após `make down`.
- Alterar credenciais no `.env` não modifica um volume previamente inicializado.
- O usuário de inicialização é administrador local. Antes do loader, criar uma
  role da aplicação com privilégios mínimos e separar DDL de ingestão.
- Schemas, migrações e testes de integração serão adicionados com a persistência.
- A tag pode receber correções da versão 16; não fixa um digest imutável.

## Alternativa considerada

SQLite simplificaria a instalação, mas não exercitaria a mesma infraestrutura e
os recursos PostgreSQL pretendidos para o pipeline e as transformações.
