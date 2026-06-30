#!/usr/bin/env bash
set -e

PORT="${PORT:-5000}"
echo "Starting DocWallet Backend on port ${PORT}..."
exec gunicorn app:app --bind "0.0.0.0:${PORT}" --workers 2 --timeout 120 --log-level info
