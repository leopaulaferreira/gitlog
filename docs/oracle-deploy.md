# Deploy na Oracle VM

A VM `amd64` com Docker Compose reutiliza a rede `ubuntu_default` e o PostgreSQL
existente. O banco `gitlog` e o banco interno `gitlog_metabase` são independentes
dos dados da aplicação já instalada. Metabase usa heap limitado a 384 MiB e teto
de 640 MiB; não publica uma porta no host. O Nginx existente deverá encaminhar
`gitlog.leofe.com.br` para o serviço `metabase:3000` na rede Docker.

Crie `.env` a partir de `.env.example`, troque as senhas e configure
`METABASE_SITE_URL=https://gitlog.leofe.com.br` e gere uma chave fixa
`MB_ENCRYPTION_SECRET_KEY` com pelo menos 16 caracteres. O setup usa uma role dedicada do
PostgreSQL já existente. Não publique a porta 5432.

```bash
docker compose -f docker-compose.yml -f docker-compose.oracle.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.oracle.yml up -d --wait metabase
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

O subdomínio precisa apontar por DNS para `147.15.127.35`, e o Nginx precisa ter
um certificado TLS válido antes de expor a tela de login. Até isso ser feito, o
Metabase permanece apenas na rede privada dos containers. Com 1 GiB de RAM e
outros serviços ativos, confira `docker stats` e o uso de swap após a inicialização.
