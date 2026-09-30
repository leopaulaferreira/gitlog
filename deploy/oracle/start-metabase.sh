#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
COMPOSE=(docker compose --env-file .env -f docker-compose.yml -f docker-compose.oracle.yml)
CONTAINER=gitlog-metabase-1
MAX_START_SECONDS=1080
MIN_AVAILABLE_KB=$((96 * 1024))
MAX_EXTRA_SWAP_KB=$((256 * 1024))

stop_with_logs() {
  "${COMPOSE[@]}" stop -t 30 metabase || docker stop -t 10 "$CONTAINER" || true
  docker logs --tail 100 "$CONTAINER" 2>&1 || true
}

docker network inspect ubuntu_default >/dev/null
docker inspect ubuntu-postgres-1 >/dev/null
if [[ "$(docker inspect -f '{{.State.Running}}' ubuntu-postgres-1)" != true ]]; then
  echo "O PostgreSQL da VM não está ativo; Metabase não será iniciado."
  exit 1
fi
"${COMPOSE[@]}" config --quiet

baseline_swap_kb="$(awk '/^Swap:/ {print $3}' < <(free -k))"
echo "Iniciando Metabase com monitoramento limitado a ${MAX_START_SECONDS}s."
"${COMPOSE[@]}" up -d --no-deps metabase

deadline=$((SECONDS + MAX_START_SECONDS))
while (( SECONDS < deadline )); do
  state="$(docker inspect -f '{{.State.Status}}' "$CONTAINER")"
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER")"
  echo "Metabase: state=$state health=$health"
  if ! docker stats --no-stream "$CONTAINER" --format 'Container memory/CPU: {{.MemUsage}} / {{.CPUPerc}}'; then
    echo "Não consegui coletar docker stats; parando Metabase por segurança."
    stop_with_logs
    exit 1
  fi
  free -h
  if ! vmstat 1 2 | tail -n 1; then
    echo "Não consegui coletar vmstat; parando Metabase por segurança."
    stop_with_logs
    exit 1
  fi
  docker logs --tail 8 "$CONTAINER" 2>&1 || true

  if [[ "$health" == healthy ]]; then
    echo "Metabase está saudável em https://gitlog.leofe.com.br"
    exit 0
  fi
  if [[ "$state" != running ]]; then
    echo "Metabase encerrou durante a inicialização. Logs recentes:"
    docker logs --tail 100 "$CONTAINER" 2>&1
    exit 1
  fi

  read -r available_kb swap_used_kb < <(
    awk '/^MemAvailable:/ {available=$2} /^SwapTotal:/ {total=$2} /^SwapFree:/ {free=$2} END {print available, total-free}' /proc/meminfo
  )
  swap_growth_kb=$((swap_used_kb - baseline_swap_kb))
  if (( available_kb < MIN_AVAILABLE_KB || swap_growth_kb > MAX_EXTRA_SWAP_KB )); then
    echo "Parando Metabase por pressão de memória (disponível=${available_kb}KiB, swap adicional=${swap_growth_kb}KiB)."
    stop_with_logs
    exit 1
  fi
  sleep 30
done

echo "Metabase não ficou saudável dentro de ${MAX_START_SECONDS}s; parando sem reiniciar migrations automaticamente."
stop_with_logs
exit 1
