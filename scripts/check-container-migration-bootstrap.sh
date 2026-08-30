#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export POSTGRES_PASSWORD=test-contract-password
export GEORANK_ENV_FILE=/dev/null
compose_file="$repo_root/docker-compose.yml"
contract_override="$repo_root/docker-compose.migration-contract.yml"
project="georank-migration-contract-${GITHUB_RUN_ID:-local}-$$"
compose=(docker compose -f "$compose_file" -f "$contract_override" -p "$project")
database_created=0
network_created=0

database_contract() {
  local action="$1"
  local sql="${2:-}"
  CONTRACT_DB_ACTION="$action" CONTRACT_DB_SQL="$sql" \
    "${compose[@]}" run --rm -T --no-deps --entrypoint python \
      -e CONTRACT_DB_ACTION -e CONTRACT_DB_SQL api -c '
import asyncio
import os

import asyncpg

async def main():
    action = os.environ["CONTRACT_DB_ACTION"]
    database = "postgres" if action in {"create", "destroy"} else os.environ["POSTGRES_DB"]
    connection = await asyncpg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ["POSTGRES_PORT"]),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        database=database,
    )
    try:
        if action in {"create", "destroy"}:
            await connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                "georank_contract",
            )
            await connection.execute("DROP DATABASE IF EXISTS georank_contract")
            if action == "create":
                await connection.execute("CREATE DATABASE georank_contract")
        elif action == "query":
            value = await connection.fetchval(os.environ["CONTRACT_DB_SQL"])
            print(value)
        else:
            await connection.execute(os.environ["CONTRACT_DB_SQL"])
    finally:
        await connection.close()

asyncio.run(main())
'
}

cleanup() {
  if test "$database_created" = "1"; then
    database_contract destroy >/dev/null 2>&1 || true
  fi
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  docker image rm "${project}-api" "${project}-migrate" >/dev/null 2>&1 || true
  if test "$network_created" = "1"; then
    docker network rm common_network >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if ! docker network inspect common_network >/dev/null 2>&1; then
  docker network create common_network >/dev/null
  network_created=1
fi

echo "EFFECTIVE_PRODUCTION_COMPOSE: validate the merged production service graph"
"${compose[@]}" config --format json | python3 -c '
import json, sys
config = json.load(sys.stdin)
services = config["services"]
required = {"traefik", "frontend", "api", "worker", "beat", "crawler", "migrate", "qdrant"}
assert required.issubset(services)
assert not {"postgres", "redis", "rabbitmq"}.intersection(services)
assert services["migrate"]["command"] == ["python", "-m", "app.scripts.migrate"]
assert set(services["migrate"]["environment"]) == {
    "DEBUG", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"
}
assert services["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
for service_name in ("traefik", "frontend", "api"):
    assert "georank-net" in services[service_name]["networks"]
for service_name in ("traefik", "frontend"):
    assert services[service_name]["ports"] == [{
        "mode": "ingress", "host_ip": "127.0.0.1", "target": 80,
        "published": "0", "protocol": "tcp"
    }]
for service_name in ("api", "qdrant"):
    assert not services[service_name].get("ports")
assert not services["api"].get("volumes")
assert all(
    volume.get("source") != "/var/run/docker.sock"
    for volume in services["traefik"].get("volumes", [])
)
traefik_config_mount = next(
    volume for volume in services["traefik"]["volumes"]
    if volume["target"] == "/etc/traefik"
)
assert traefik_config_mount["read_only"] is True
'

echo "FRESH_DATABASE: build the production API image and migrate an empty database"
"${compose[@]}" build api migrate
database_contract create
database_created=1

echo "DIRECT_ENTRYPOINT_FAIL_CLOSED: application image rejects an empty database"
set +e
direct_output=$("${compose[@]}" run --rm --no-deps api 2>&1)
direct_code=$?
set -e
test "$direct_code" -ne 0
printf '%s\n' "$direct_output" | grep -q \
  "database has no alembic_version; run the migration service"

echo "CONCURRENT_EMPTY_DATABASE: serialize two migration processes with the advisory lock"
"${compose[@]}" run --rm migrate &
first_migration=$!
"${compose[@]}" run --rm migrate &
second_migration=$!
wait "$first_migration"
wait "$second_migration"

"${compose[@]}" up -d --wait api
fresh_state=$(
  database_contract query \
    "SELECT (SELECT string_agg(version_num, ',') FROM alembic_version) || '|' || (SELECT count(*)::text FROM expert_profiles)"
)
test "$fresh_state" = "016_merge_platform_iterations|5"
"${compose[@]}" exec -T api python -c \
  'import urllib.request; urllib.request.urlopen("http://localhost:8000/api/health", timeout=3).read()'
expert_count=$(
  "${compose[@]}" exec -T api \
    python -c 'import json,urllib.request; data=json.load(urllib.request.urlopen("http://localhost:8000/api/experts")); print(len(data["items"]))'
)
test "$expert_count" = "5"

echo "PRODUCTION_GATEWAY: exercise file-provider routes on the shared Compose network"
"${compose[@]}" up -d --wait traefik frontend
gateway_port=$("${compose[@]}" port traefik 80 | awk -F: '{print $NF}')
frontend_port=$("${compose[@]}" port frontend 80 | awk -F: '{print $NF}')
gateway="http://127.0.0.1:$gateway_port"
direct_frontend="http://127.0.0.1:$frontend_port"
attempt=0
until curl --fail --silent "$gateway/api/health" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if test "$attempt" -ge 30; then
    echo "gateway did not become ready within 30 seconds" >&2
    exit 1
  fi
  sleep 1
done
for iteration in $(seq 1 40); do
  curl --fail --silent "$gateway/" >/dev/null
  curl --fail --silent "$gateway/api/health" >/dev/null
done
curl --fail --silent "$gateway/" >/dev/null
curl --fail --silent "$gateway/api/health" >/dev/null
curl --fail --silent "$gateway/api/companies/" >/dev/null
curl --fail --silent "$gateway/tutorial" >/dev/null
curl --fail --silent "$gateway/experts" >/dev/null
curl --fail --silent "$gateway/admin/login" >/dev/null
curl --fail --silent "$gateway/apix" >/dev/null
curl --fail --silent "$direct_frontend/" >/dev/null
gateway_expert_count=$(
  curl --fail --silent "$gateway/api/experts" |
    python3 -c 'import json,sys; print(len(json.load(sys.stdin)["items"]))'
)
test "$gateway_expert_count" = "5"

traefik_logs=$("${compose[@]}" logs --no-color traefik 2>&1)
if printf '%s\n' "$traefik_logs" |
  grep -Eiq 'failed to retrieve information|provider.*error|status.?code.?502| 502 '; then
  printf '%s\n' "$traefik_logs"
  exit 1
fi
printf '%s\n' "$traefik_logs" | grep -Eq 'GET /apix.*frontend@file'

echo "IDEMPOTENT_RESTART: rerun the one-shot migrator at head"
"${compose[@]}" run --rm migrate
restart_state=$(
  database_contract query \
    "SELECT (SELECT string_agg(version_num, ',') FROM alembic_version) || '|' || (SELECT count(*)::text FROM expert_profiles)"
)
test "$restart_state" = "$fresh_state"

echo "LEGACY_FAIL_CLOSED: preserve a managed schema with no Alembic ownership"
"${compose[@]}" stop api >/dev/null
database_contract execute \
  "DROP SCHEMA public CASCADE; CREATE SCHEMA public; CREATE TABLE users(id uuid PRIMARY KEY);" \
  >/dev/null
"${compose[@]}" rm -sf migrate api >/dev/null

echo "MIGRATION_FAILURE_BLOCKS_API: Compose must keep API stopped when migration fails"
set +e
failure_output=$("${compose[@]}" up -d api 2>&1)
failure_code=$?
set -e
test "$failure_code" -ne 0
printf '%s\n' "$failure_output" | grep -q "didn't complete successfully: exit 1"
migration_logs=$("${compose[@]}" logs --no-color migrate 2>&1)
printf '%s\n' "$migration_logs" | grep -q \
  "managed tables exist without alembic_version"

api_container=$("${compose[@]}" ps -aq api)
if test -n "$api_container"; then
  test "$(docker inspect -f '{{.State.Running}}' "$api_container")" = "false"
fi

legacy_state=$(
  database_contract query \
    "SELECT (to_regclass('public.users') IS NOT NULL)::text || '|' || (to_regclass('public.alembic_version') IS NULL)::text"
)
test "$legacy_state" = "true|true"

echo "Container migration bootstrap contract passed"
