#!/usr/bin/env bash
# forsegla_facit.sh — enda sanktionerade vägen att försegla ett facit.
#
# Kör facit_lint. Vägrar vid brist (exit 2). Annars sha256 + chmod 0444
# på facitet (och ev. addendum) samt sidokvitto <fil>.sha256, plus en
# kvittorad med UTC-tidsstämpel på stdout.
#
#   forsegla_facit.sh <facit.md> [--addendum <fil>]
#
# Manuell chmod ska inte förekomma i flödet.
# Ingen riggkontakt.
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LINT="$HERE/facit_lint.py"

if [[ $# -lt 1 || "$1" == "-h" || "$1" == "--help" ]]; then
    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
fi

facit=$1
shift
addendum=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --addendum) addendum=${2:-}; shift 2 ;;
        *) echo "okänd flagga: $1" >&2; exit 2 ;;
    esac
done

if [[ ! -f "$facit" ]]; then
    echo "VÄGRAR: ingen fil: $facit" >&2
    exit 2
fi
if [[ -n "$addendum" && ! -f "$addendum" ]]; then
    echo "VÄGRAR: addendum saknas: $addendum" >&2
    exit 2
fi

lint_args=("$facit")
if [[ -n "$addendum" ]]; then
    lint_args+=(--addendum "$addendum")
fi

if ! python3 "$LINT" "${lint_args[@]}" >/dev/null; then
    echo "VÄGRAR: facit_lint underkände — ingen försegling, ingen chmod" >&2
    exit 2
fi

seal_one() {
    local f=$1
    local sum
    sum=$(sha256sum -- "$f" | awk '{print $1}')
    local base
    base=$(basename -- "$f")
    printf '%s  %s\n' "$sum" "$base" > "${f}.sha256"
    chmod 0444 -- "$f" "${f}.sha256"
    local ts
    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    printf 'FORSEGLAD ts=%s sha256=%s path=%s\n' "$ts" "$sum" "$f"
}

seal_one "$facit"
if [[ -n "$addendum" ]]; then
    seal_one "$addendum"
fi
exit 0
