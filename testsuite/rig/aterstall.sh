#!/usr/bin/env bash
# Hela aterstallningskedjan efter match (RUNBOOK §14).
#
# Torrkorning som default. Utan --verkstall sager kedjan vad den skulle
# gora och kontrollerar allt som gar att kontrollera utan att rora nagot.
set -euo pipefail
HAR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

KVITTO=""; LASFIL=""; VERKSTALL=()
while [ $# -gt 0 ]; do
  case "$1" in
    --kvitto) KVITTO="$2"; shift 2;;
    --lasfil) LASFIL="$2"; shift 2;;
    --verkstall) VERKSTALL=( --verkstall ); shift;;
    *) echo "okand flagga: $1" >&2; exit 2;;
  esac
done

if [ -z "$KVITTO" ]; then
  echo "VAGRAD: --kvitto fattas. Bokforingsvagar har ingen default." >&2
  exit 2
fi

ARG=( aterstall --ogonblicksbild "$KVITTO/ogonblicksbild.json"
      --verdikt-ut "$KVITTO/restore-verdikt.json" )
[ -n "$LASFIL" ] && ARG+=( --lasfil "$LASFIL" )

exec python3 "$HAR/aterstall.py" "${ARG[@]}" ${VERKSTALL[@]+"${VERKSTALL[@]}"}
