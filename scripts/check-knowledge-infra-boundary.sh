#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
violations=0

check_pattern() {
  local pattern="$1"
  shift
  if rg -n --ignore-case "$pattern" "$@"; then
    violations=$((violations + 1))
  fi
}

check_pattern '(^|[[:space:]])(from[[:space:]]+neo4j|import[[:space:]]+neo4j|import[[:space:]]+boto3)([[:space:]]|$)' "$ROOT/backend/app"
check_pattern '(^|[^A-Z])(NEO4J|MINIO)_[A-Z0-9_]+' "$ROOT/backend/app" "$ROOT/.env.example"
check_pattern '^[[:space:]]{2}(neo4j|minio):|image:[^#]*(neo4j|minio)' "$ROOT"/docker-compose*.yml
check_pattern '^[[:space:]]{2}(postgres|redis|rabbitmq):|image:[^#]*(postgres|redis|rabbitmq)' "$ROOT"/docker-compose*.yml
check_pattern '^(neo4j|boto3)(\[.*\])?==' "$ROOT/backend/requirements.txt"

if [ "$violations" -ne 0 ]; then
  echo "[knowledge-infra-boundary] FAIL: GeoRank must use Knowledge and centrally managed shared dependencies." >&2
  exit 1
fi

echo "[knowledge-infra-boundary] OK: Knowledge and shared-infrastructure boundaries are enforced."
