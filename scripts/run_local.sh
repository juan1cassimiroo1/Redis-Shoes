#!/usr/bin/env bash
# Modo local (sem Docker): Redis real + Mongo simulado (mongomock) + pedidos em memória.
set -e
cd "$(dirname "$0")/.."
redis-cli ping >/dev/null 2>&1 || (redis-server --port 6379 --save "" --daemonize yes >/dev/null && sleep 0.5)
BACKENDS=local SEED_ON_START=1 exec python3 -m uvicorn app.main:app --port 8000
