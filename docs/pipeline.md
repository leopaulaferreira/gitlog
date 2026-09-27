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

Os testes unitários do cliente usarão mocks/fixtures, sem chamar a API real.
Os testes do loader usarão PostgreSQL de teste isolado. Dados de dashboard virão
exclusivamente de coletas reais; fixtures ficarão restritas aos testes.

## Aceitação futura do MVP

Após configurar o token e iniciar o banco, o usuário deverá ingerir repositories
e commits, consultar ambos no PostgreSQL, repetir a execução sem duplicatas e
executar `pytest` com sucesso. A Fase 0 ainda não satisfaz esse critério.
