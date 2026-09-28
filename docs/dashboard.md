# GitLog — Análises — Metabase

## Idioma da interface

O painel, a coleção e a conexão se chamam **GitLog — Análises**. Títulos,
descrições, filtros, legendas, colunas dos resultados e nomes exibidos no catálogo
estão em português brasileiro. Linguagem ausente aparece como **Não informada**.
O Compose define `MB_SITE_LOCALE=pt_BR`; o setup também configura a conta usada
na instalação para português. Recarregue a página após aplicar a atualização.

`make dashboard-setup` reconhece os nomes anteriores em inglês e renomeia os
objetos existentes, preservando seus IDs e o endereço do painel. Os nomes antigos
ficam em `legacy_name` apenas para permitir essa migração sem duplicatas.
As traduções do catálogo e filtros ficam em `dashboard/pt_br.json`; os textos
dos cartões, em `dashboard/cards.json`. Identificadores SQL e valores originais
da API mantêm seus contratos, com nomes de apresentação traduzidos no Metabase.

Se outra conta tiver escolhido um idioma próprio, selecione **Português (Brasil)**
nas configurações pessoais. A preferência individual prevalece sobre o padrão
da instância, conforme a [documentação de localização do Metabase](https://www.metabase.com/docs/latest/configuring-metabase/localization).

## Serviços e acesso

`docker compose up -d` inicia PostgreSQL GitLog, Metabase **v0.63.18** e um segundo
PostgreSQL para os metadados do Metabase. A interface fica em
`http://localhost:3000` (ou `METABASE_PORT`). O healthcheck consulta `/api/health`.
O dbt é executado sob demanda, com profile `tools`.

O banco interno do Metabase guarda usuários, perguntas, filtros e dashboards no
volume `metabase_data`. Os dados GitLog ficam em `postgres_data`. Separar os
containers evita que permissões, backups ou migrações do produto de BI se misturem
com o warehouse. O banco interno não publica porta no host. A interface e o banco
GitLog publicam portas somente em loopback. Esta é uma stack de desenvolvimento,
sem TLS/proxy/SSO de produção. `docker compose down` preserva os dois volumes.

## Configuração automática e reproduzível

Após ingestão real e `make dbt-run && make dbt-test`:

```bash
make dashboard-setup
make dashboard-check
```

Em uma instância nova, o primeiro comando cria a conta administrativa local usando
`METABASE_ADMIN_EMAIL` e `METABASE_ADMIN_PASSWORD` do `.env`; não imprime a senha.
`make demo-config` gera as senhas locais ausentes/de exemplo antes do primeiro boot.
O e-mail padrão é `admin@gitlog.local`: é um login local, não uma conta de e-mail
externa. Leia a senha **localmente no seu `.env`** para entrar na interface.

Em uma instância já configurada, use credenciais de um administrador existente.
O setup não recria usuários nem redefine senhas. Credenciais de `.env` precisam
continuar correspondendo à instância; alterar o arquivo não altera a senha no BI.

O script cria a conexão restrita, a coleção e o dashboard **GitLog — Análises**.
Depois cria/atualiza os 14 cards definidos em
[`dashboard/cards.json`](../dashboard/cards.json) e suas
[queries SQL](../dashboard/queries), associa filtros e executa cada consulta.
Uma segunda execução mantém os objetos, sem duplicá-los. Nomes ambíguos ou
coleções/dashboards homônimos não gerenciados pelo GitLog são recusados.

A coleção é **gerenciada pelo código**: repetir setup atualiza perguntas e layout.
Para personalizar pela UI, duplique o dashboard em outra coleção. A automação usa
os endpoints da API da versão fixada, sem editar o banco interno do Metabase.
Antes de atualizar a imagem, valide novamente o setup e os filtros; a API pode
mudar entre versões. Também é possível montar tudo manualmente com as definições
abaixo, sem depender do script.

## Conexão manual da fonte de dados

No assistente inicial ou em **Administração → Bancos de dados → Adicionar banco de dados**:

| Configuração | Valor |
| --- | --- |
| Tipo | PostgreSQL |
| Nome | GitLog — Análises |
| Host | `postgres` (DNS da rede Compose, não localhost) |
| Porta | `5432` (porta interna, não POSTGRES_PORT do host) |
| Banco | Valor de `POSTGRES_DB`, padrão `gitlog` |
| Usuário | `METABASE_READER_USER`, padrão `gitlog_metabase` |
| Senha | `METABASE_READER_PASSWORD` |
| SSL | Desabilitado na rede local de demonstração |
| Schemas | Incluir somente `analytics` |
| Timezone de relatórios | UTC |

As roles são criadas por `make analytics-init`. Não conecte usando o administrador
ou o usuário da ingestão. A conta BI pode ler analytics, mas não pode escrever nos
marts nem ler raw. `staging`, `intermediate`, `raw` e os dados internos do Metabase
não são fontes para os cards. Após `dbt run`, sincronize schema se necessário.

## Cartões e significado

Crie cada pergunta como **SQL nativo**, usando o arquivo correspondente. O nome,
tipo de gráfico, séries e mapeamentos também constam em `cards.json`.

| Card | Query | Tipo / eixos | Período usado |
| --- | --- | --- | --- |
| Repositórios monitorados | `repositories.sql` | Número: Repositórios | Não aplica; snapshot atual |
| Commits coletados | `commits.sql` | Número: Total | commit_date; sem filtro inclui commits sem data |
| Solicitações de alteração | `pull_requests.sql` | Número: Total | created_date do PR |
| Questões registradas | `issues.sql` | Número: Total | created_date da issue |
| Solicitações mescladas | `merged_prs.sql` | Número: Total | merged_date |
| Questões fechadas | `closed_issues.sql` | Número: Total | closed_date, somente state=closed |
| Commits ao longo do tempo | `commits_over_time.sql` | Linha: Data × Commits | Data UTC do evento |
| Solicitações de alteração — Abertas e mescladas | `pull_requests_over_time.sql` | Linhas: Data × Abertas, Mescladas | Datas de abertura/merge, não coorte |
| Questões — Abertas e fechadas | `issues_over_time.sql` | Linhas: Data × Abertas, Fechadas | Abertura/último fechamento disponível |
| Repositórios mais ativos — Commits | `active_repositories.sql` | Barras: Repositório × Commits; top 10 | Data do evento |
| Tempo até a mesclagem — Horas | `merge_time.sql` | Barras: Repositório × Tempo médio até a mesclagem (horas) | Data do merge; merged_prs disponível na pergunta |
| Atividade dos repositórios ao longo do tempo | `repository_activity.sql` | Linhas: Data × Commits; série Repositório | Data do evento |
| Linguagens principais — Estado atual | `primary_languages.sql` | Pizza: Linguagem principal × Repositórios | Não aplica; linguagem atual |
| Comparação entre repositórios | `repository_comparison.sql` | Tabela: Repositório, contagens e média de merge | Data dos eventos |

Não existe KPI Contributors: logins opcionais de commits não representam uma
dimensão completa de contribuidores. **Solicitações mescladas** é o substituto real.
A lista inclui um sexto KPI, Questões fechadas. Repositórios mais ativos usa contagem
de commits, sem Activity Score nem soma de eventos incompatíveis.

A média de merge usa **soma de horas / quantidade de PRs merged**, agregando as
parcelas já calculadas pelo dbt. Não é média de médias. PRs fechados sem merge não
participam; se não há merges, a média não existe e o gráfico mostra ausência de
dados. Zero seria enganoso. `prs_closed` inclui merged, portanto não some essas
métricas para obter um total de PRs.

Títulos, chaves, UTC, separação issue/PR, datas e duração são responsabilidade do
dbt. As queries de BI apenas filtram e agregam os marts para visualização.

## Filtros e organização manual

Crie três filtros de dashboard, opcionais e sem período padrão oculto:

1. **Repositório**: texto/categoria com múltiplos valores; `dim_repository.full_name`.
2. **Período (UTC)**: período de datas; campo indicado na última coluna da tabela.
3. **Linguagem atual**: texto/categoria com múltiplos valores;
   `dim_repository.primary_language`.

No editor SQL, cada variável `{{repository}}`, `{{date_range}}`, `{{language}}`
é um **Filtro de campo**, não uma interpolação de texto. Conecte-a ao campo real
correspondente. As queries não usam aliases de tabelas, para evitar mapeamentos
ambíguos. Não escreva `coluna = {{filtro}}`: o próprio Field filter gera a condição.
Os blocos `[[and {{...}}]]` permitem deixar filtros vazios.

Para séries/compare/merge-time, `date_range` aponta para
`fact_repository_daily_metrics.activity_date`. Para KPIs, siga o campo de data
específico. Os filtros Repositório e Linguagem atual apontam sempre para `dim_repository`, incluída
na query. Período **não** se conecta ao KPI de repositórios nem à pizza de
linguagens, pois são snapshots. Linguagem filtra eventos por atributo **atual**,
sem afirmar qual era a linguagem no passado.

Na grade de 24 colunas: os seis KPIs ocupam a primeira linha, com 4 colunas cada.
Os oito gráficos seguintes ocupam duas colunas de 12 unidades, em quatro linhas,
na ordem da tabela. A automação aplica esse layout e as configurações das séries.

## Desempenho e validação

```bash
make dashboard-check
```

O comando executa `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` das 14 queries sem
filtros, usando a role BI. Também confirma acesso negado ao raw, quantidade de
cards/dashboard e consultas pela API do Metabase com filtros combinados, um
repositório inexistente e um período sem eventos. O relatório fica em
`data/validation/phase7-dashboard-validation.json`, ignorado pelo Git.

Na validação inicial, a consulta mais lenta levou aproximadamente **0,1 ms** no
PostgreSQL local, com 1 repositório e 25 commits. Isso é evidência de funcionamento
no volume atual, não benchmark de escala. Nenhum índice analítico adicional foi
criado: os planos e o volume não justificaram. Em maior volume, meça filtros por
repository/date e agregações antes de decidir por índices ou materializações.

O setup foi executado duas vezes sem duplicar dashboard/cards. A interface chegou
a healthy e todas as consultas retornaram pelo driver PostgreSQL do Metabase.
Os dados reais dessa validação possuem zero issues e zero PRs; as curvas desses
eventos mostram zero e o gráfico de tempo de merge fica sem dados. Cenários com
merges são exercitados pelos testes dbt, sem carregar fixtures na demonstração.

## Capturas de tela

Local reservado: [`docs/images/dashboard-overview.png`](images/README.md).
Abra o dashboard com dados reais, aplique os filtros desejados e capture a tela;
registre data da carga e período no contexto da imagem. Nenhuma imagem sintética
ou screenshot de dados fictícios foi gerada. O arquivo PNG só deve ser adicionado
quando houver uma captura real revisada.

## Atualização e limites

```bash
make ingest-all
make dbt-run
make dbt-test
# Recarregue o dashboard após a conclusão; setup só é necessário ao mudar definição.
```

O dashboard não dispara ingestão nem dbt automaticamente. Fechar/reabrir issues
não gera histórico de transições: os fatos têm apenas os campos observados no
snapshot mais recente. Stars/forks históricos, contributors completos e Activity
Score não são inferidos. A stack não inclui Airflow, MinIO ou Spring Boot.

Referências: [Docker e banco interno do Metabase](https://www.metabase.com/docs/latest/installation-and-operation/running-metabase-on-docker),
[application database](https://www.metabase.com/docs/latest/installation-and-operation/configuring-application-database),
[Field filters](https://www.metabase.com/docs/latest/questions/native-editor/field-filters).
A documentação da API da imagem em execução fica em `http://localhost:3000/api/docs`.

A atualização para português foi validada na instância e na conta administrativa
com `pt_BR`, preservando o painel de ID 2 e seus 14 cartões. As consultas, os filtros
e os nomes das colunas retornadas foram conferidos pela API. Os 354 testes Python,
Ruff, Black e a configuração Docker Compose também passaram. Nomes anteriores em
inglês permanecem apenas como identificadores de migração nas definições versionadas.
