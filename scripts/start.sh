#!/usr/bin/env bash
# One command to run MediTriage with docker compose, even if the ports are busy:
#   ./scripts/start.sh
#
# 1. Stops whatever of THIS project is using the ports: a local uvicorn/streamlit started
#    from this folder, an old "docker run --name meditriage" container, an old compose stack.
# 2. If a port is still taken by something else, says what it is and how to use another port.
# 3. Builds and starts the stack, waits until both containers are healthy, prints the URLs.
#
# Other ports:  API_PORT=8003 UI_PORT=8503 ./scripts/start.sh   (or put them in .env)
set -euo pipefail
cd "$(dirname "$0")/.."                       # run from the project root
PROJECT_DIR="$(pwd)"

# Same defaults as docker-compose.yml. Values in .env are used when not set in the shell.
if [[ -f .env ]]; then
    API_PORT="${API_PORT:-$(sed -n 's/^API_PORT=//p' .env | tail -1)}"
    UI_PORT="${UI_PORT:-$(sed -n 's/^UI_PORT=//p' .env | tail -1)}"
fi
export API_PORT="${API_PORT:-8000}" UI_PORT="${UI_PORT:-8501}"

echo ">> Cleaning up old MediTriage containers"
docker compose down --remove-orphans >/dev/null 2>&1 || true
docker rm -f meditriage >/dev/null 2>&1 || true

# Stop a local uvicorn/streamlit only if it was started from this project folder.
for port in "$API_PORT" "$UI_PORT"; do
    for pid in $(ss -ltnpH "sport = :$port" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u); do
        if [[ "$(readlink "/proc/$pid/cwd" 2>/dev/null)" == "$PROJECT_DIR" ]]; then
            echo ">> Stopping local process on port $port (pid $pid): $(ps -o args= -p "$pid" | cut -c1-80)"
            kill "$pid"
        fi
    done
done
sleep 1

# Anything left on the ports belongs to something else: don't touch it, explain instead.
for port in "$API_PORT" "$UI_PORT"; do
    if ss -ltnH "sport = :$port" | grep -q .; then
        echo ">> Port $port is still used by another program:"
        ss -ltnpH "sport = :$port" || true
        echo ">> Stop it, or pick free ports:  API_PORT=8003 UI_PORT=8503 ./scripts/start.sh"
        exit 1
    fi
done

echo ">> Building and starting (api :$API_PORT, ui :$UI_PORT)"
docker compose up -d --build

for i in $(seq 1 40); do
    if [[ "$(docker compose ps --format '{{.Health}}' | grep -c '^healthy$')" == "2" ]]; then
        docker compose ps
        echo
        echo ">> Ready:"
        echo "   Web UI + API : http://localhost:$API_PORT   (docs: /docs)"
        echo "   Streamlit UI : http://localhost:$UI_PORT"
        echo "   Stop         : ./scripts/stop.sh"
        exit 0
    fi
    sleep 3
done
echo ">> Containers did not become healthy in time"
docker compose ps
docker compose logs --tail=30
exit 1
