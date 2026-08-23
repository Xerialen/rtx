#!/usr/bin/env bash
# T4-riggen: frogbotstegen. Samma procedur som T3 plus bots/-datat.
# All logik i res.sh — tva kopior av en riggprocedur glider isar.
set -euo pipefail
HAR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HAR/res.sh" --tier t4 "$@"
