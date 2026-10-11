#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. On Arch: sudo pacman -S --needed docker docker-compose-plugin" >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not running or this user cannot access it." >&2
  echo "Try: sudo systemctl enable --now docker" >&2
  echo "Then either use sudo docker compose for this folder, or configure Docker access according to your security preference." >&2
  exit 1
fi
if [[ ! -f .env ]]; then
  secret="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
  printf 'SEARXNG_SECRET=%s\n' "$secret" > .env
  chmod 600 .env
  echo "Generated local .env secret. It stays on this machine."
fi
docker compose up -d
printf '\nWaiting for SearXNG to respond'
for _ in $(seq 1 40); do
  if curl -fsS 'http://127.0.0.1:8888/' >/dev/null 2>&1; then
    printf '\nSearXNG is responding at http://127.0.0.1:8888\n'
    echo "Testing JSON search API..."
    if curl -fsSG 'http://127.0.0.1:8888/search' --data-urlencode 'q=Arch Linux' --data-urlencode 'format=json' | python -c 'import json,sys; d=json.load(sys.stdin); print("JSON API OK; results:",len(d.get("results",[])))'; then
      echo "Setup looks good. Run ./install.sh to install Kage integration."
      exit 0
    fi
    echo "SearXNG is up, but JSON API test failed. Check: docker compose logs --tail=100 searxng" >&2
    exit 1
  fi
  printf '.'
  sleep 3
done
echo "Timed out waiting for SearXNG. Inspect logs: docker compose logs --tail=100 searxng" >&2
exit 1
