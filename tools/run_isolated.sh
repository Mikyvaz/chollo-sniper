#!/usr/bin/env bash
# Python en un contenedor efímero SIN RED, con COPIA de la BD y SIN secretos.
#   tools/run_isolated.sh -m unittest discover -s tests/unit -p 'test_*.py' -v
set -euo pipefail
REPO="${REPO:-/opt/wallapop-sniper}"; IMAGE="${IMAGE:-boti-sniper:v4.1}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/sniper-iso.XXXXXX")"; trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/kits" "$WORK/logs"
if [ -f "$REPO/data/sniper.db" ]; then
  python3 -I - "$REPO/data/sniper.db" "$WORK/test.db" <<'PY'
import sqlite3, sys
s = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=30); d = sqlite3.connect(sys.argv[2])
s.backup(d); d.close(); s.close()
PY
fi
docker run --rm --network none --user "$(id -u):$(id -g)" \
  -v "$REPO":/src:ro -v "$WORK":/work -w /src \
  -e DB_PATH=/work/test.db -e CONFIG_PATH=/src/config.json -e KITS_DIR=/work/kits -e LOG_DIR=/work/logs \
  -e HOME=/work -e TZ=Europe/Madrid -e PYTHONPATH=/src -e PYTHONDONTWRITEBYTECODE=1 -e SNIPER_ISOLATED=1 \
  --entrypoint python "$IMAGE" "$@"
