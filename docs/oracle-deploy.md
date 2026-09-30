# Deploy na Oracle VM

A VM `amd64` com Docker Compose reutiliza a rede `ubuntu_default` e o PostgreSQL
existente. O banco `gitlog` e o banco interno `gitlog_metabase` são independentes
dos dados da aplicação já instalada. Metabase usa heap máximo de 224 MiB e teto
de 384 MiB sem swap próprio; não publica uma porta no host. O Nginx encaminha
`gitlog.leofe.com.br` para `metabase:3000` na rede Docker e atende TLS com
certificado Let's Encrypt.

Crie `.env` a partir de `.env.example`, troque as senhas e configure
`METABASE_SITE_URL=https://gitlog.leofe.com.br` e gere uma chave fixa
`MB_ENCRYPTION_SECRET_KEY` com pelo menos 16 caracteres. O setup usa uma role dedicada do
PostgreSQL já existente. Não publique a porta 5432.

```bash
docker compose -f docker-compose.yml -f docker-compose.oracle.yml config --quiet
./deploy/oracle/start-metabase.sh
docker compose -f docker-compose.yml -f docker-compose.oracle.yml run --rm ingestion migrate
docker compose -f docker-compose.yml -f docker-compose.oracle.yml run --rm --entrypoint python ingestion scripts/provision_analytics.py
docker compose -f docker-compose.yml -f docker-compose.oracle.yml run --rm ingestion all
docker compose -f docker-compose.yml -f docker-compose.oracle.yml run --rm dbt run
docker compose -f docker-compose.yml -f docker-compose.oracle.yml run --rm dbt test
docker compose -f docker-compose.yml -f docker-compose.oracle.yml run --rm --entrypoint python ingestion scripts/setup_metabase.py
```

O PostgreSQL da VM já existe e é compartilhado por outros serviços. O Compose
Oracle desativa seus containers PostgreSQL locais; um usuário PostgreSQL
específico do GitLog recebe apenas o necessário para administrar os dois novos
bancos. A ingestão pode usar um token GitHub apenas durante sua execução, sem
salvá-lo no arquivo `.env` do servidor.

O registro A de `gitlog.leofe.com.br` aponta para `147.15.127.35`. O certificado
foi emitido no host em `/home/ubuntu/certbot/conf/live/gitlog.leofe.com.br/` e
renova pelo cron existente, que executa `certbot renew` e recarrega o Nginx.
O bloco de servidor usado está em `deploy/oracle/nginx-gitlog.conf`; ele foi
adicionado à configuração existente sem substituir os hosts virtuais dos outros
sites. O Nginx redireciona HTTP para HTTPS.

Metabase precisa ficar saudável para o endereço servir a interface. No perfil
Oracle, falhas não reiniciam o processo automaticamente, o healthcheck tolera
15 minutos de startup, e syncs automáticos de metadados ficam desativados; o
script de configuração ainda pode solicitar um sync explícito. O limite de
memória e memória+swap igual protege os outros serviços se o Metabase ultrapassar
seu envelope.

Na VM de 1 GiB, prefira iniciar Metabase apenas durante demonstrações. Os scripts
em `deploy/oracle` validam Compose, rede e PostgreSQL, mostram memória/swap/logs e
param o container se a RAM disponível cair abaixo de 96 MiB, o swap crescer mais
de 256 MiB ou o healthcheck não passar em 18 minutos:

```bash
./deploy/oracle/start-metabase.sh
./deploy/oracle/status-metabase.sh
./deploy/oracle/stop-metabase.sh
```

O início não apaga nem recria o banco `gitlog_metabase`. Investigue logs de uma
falha de migration antes de tentar novamente; não limpe tabelas do Liquibase.
A porta 3000 não deve ser publicada no host.

No primeiro teste com esses limites, `/api/health` não ficou saudável e
`docker stats` expirou durante o startup. O container terminou parado por
intervenção, sem OOM; a memória usada pelo host voltou a cerca de 540 MiB e o
swap ficou perto de 970 MiB, após pico próximo de 1 GiB. Os demais containers
permaneceram ativos. Por isso, a configuração é para uso sob demanda, não para
manter Metabase 24/7 nesta VM.

## Deploy automático pelo GitHub Actions

Pushes para `main` executam `.github/workflows/deploy-oracle.yml`. O workflow
atualiza o clone na VM, valida o Compose, reconstrói ingestion/dbt, aplica
migrations e provisioning, roda os modelos e os testes dbt. Ele não reexecuta a
ingestão do GitHub em todo deploy; rode a ingestão manualmente quando quiser
atualizar os dados. O workflow também não inicia o Metabase, que deve permanecer
parado enquanto a VM não tiver memória suficiente.

Cadastre no repositório, em **Settings → Secrets and variables → Actions**:

- Secret `ORACLE_SSH_PRIVATE_KEY`: chave privada exclusiva do deploy.
- Secret `ORACLE_SSH_KNOWN_HOSTS`: linha validada da chave SSH do servidor.
- Variable `ORACLE_SSH_HOST`: IP ou hostname SSH da VM.
- Variable `ORACLE_SSH_USER`: usuário SSH (atualmente `ubuntu`).

O usuário SSH precisa poder atualizar `/home/ubuntu/gitlog` e executar Docker
sem senha. Nunca use a chave pessoal de administração como segredo do workflow.
O workflow pode ser iniciado manualmente pela aba **Actions**.
