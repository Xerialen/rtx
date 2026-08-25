#!/usr/bin/env bash
# T3-riggen: gren mot pinnad referens, inga frogbots.
# All logik i res.sh — tva kopior av en riggprocedur glider isar.
set -euo pipefail
HAR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HAR/res.sh" --tier t3 "$@"
