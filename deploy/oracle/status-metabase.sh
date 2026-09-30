#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
COMPOSE=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.oracle.yml)
"${COMPOSE[@]}" ps metabase
free -h
vmstat 1 2 | tail -n 1
if docker inspect gitlog-metabase-1 >/dev/null 2>&1; then
  docker logs --tail 20 gitlog-metabase-1 2>&1
  if [[ "$(docker inspect -f '{{.State.Running}}' gitlog-metabase-1)" == true ]]; then
    docker stats --no-stream gitlog-metabase-1
    docker exec gitlog-metabase-1 curl --fail --silent --show-error --max-time 5 \
      http://localhost:3000/api/health || true
  fi
fi
