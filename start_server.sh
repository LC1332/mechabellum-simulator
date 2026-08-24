#!/usr/bin/env bash
# start the Mechabellum sandbox server on http://127.0.0.1:8300
cd "$(dirname "$0")"
PY=python3; command -v python3 >/dev/null 2>&1 || PY=python
exec $PY -m uvicorn server:app --app-dir web --port 8300
