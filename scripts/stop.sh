#!/usr/bin/env bash
# Stops everything MediTriage runs locally with Docker (the Kubernetes cluster is left alone):
#   ./scripts/stop.sh
set -uo pipefail
cd "$(dirname "$0")/.."

docker compose down --remove-orphans
if docker container inspect meditriage >/dev/null 2>&1; then
    docker rm -f meditriage >/dev/null && echo ">> Removed container meditriage"
fi
echo ">> Stopped. (Kubernetes keeps running at http://localhost:30090; delete it with: kind delete cluster --name meditriage)"
