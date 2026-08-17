#!/usr/bin/env bash
# seal.sh — försegla ett facit/manifest mot en kedje-HEAD, maskinformat.
#
# VARFÖR
#   Förseglingen görs idag för hand i WORK_LOGS/terra-d-facit-forsegling.md. Den
#   prosan är läsbar men går inte att verifiera: ingen kan säga om en rad har
#   ändrats efteråt, och ingenting hindrar att ett facit förseglas två gånger med
#   olika värden. Det här skriptet skriver en rad som går att räkna om, i en kedja
#   som går att kontrollera, och vägrar skriva över en befintlig försegling.
#   Prosan ersätts inte — nu finns bara något att jämföra den MOT.
#
# FACIT FÖRSEGLAS FÖRE KOD
#   Det är hela poängen med en försegling, och därför är det en grind och inte en
#   vana: har kodvägarna ocommittade ändringar vägrar skriptet. Ett facit som
#   skrivs medan koden ligger i arbetsträdet kan ha formats av koden, och då mäter
#   det ingenting.
#
# ANVÄNDNING
#   seal.sh --facit <fil> --head <ref> --ledger <katalog> --by <namn>
#           [--code-repo <katalog>] [--code-path <sökväg>]... [--allow-dirty-note <text>]
#
#   --facit      filen som förseglas. Hashas byte för byte.
#   --head       kedje-HEAD förseglingen pinnas till (ref eller sha).
#   --ledger     katalogen liggaren bor i. Skapas om den saknas.
#   --by         vem som förseglar (bokförs i raden).
#   --code-repo  git-repot vars arbetsträd kontrolleras. Default: repot skriptet
#                ligger i — facitet bor ofta i ett ANNAT repo, och det är kodens
#                träd som ska vara rent, inte facitets.
#   --code-path  sökväg (relativt code-repo) som måste vara ren. Upprepa för flera.
#                Default: crates testsuite
#
# EXIT
#   0 förseglat · 1 vägran på en grind (smutsigt träd, okänd HEAD, redan förseglat)
#   2 användningsfel
#
# Ingen riggkontakt: skriptet rör bara --facit, --ledger och git-metadata.
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LEDGER_PY="$HERE/seal_ledger.py"

facit=""; head_ref=""; ledger=""; by=""; code_repo=""
declare -a code_paths=()

usage() { sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --facit)     facit=${2:-}; shift 2 ;;
        --head)      head_ref=${2:-}; shift 2 ;;
        --ledger)    ledger=${2:-}; shift 2 ;;
        --by)        by=${2:-}; shift 2 ;;
        --code-repo) code_repo=${2:-}; shift 2 ;;
        --code-path) code_paths+=("${2:-}"); shift 2 ;;
        -h|--help)   usage; exit 0 ;;
        *) echo "okänd flagga: $1 (--help)" >&2; exit 2 ;;
    esac
done

for krav in facit head_ref ledger by; do
    if [[ -z "${!krav}" ]]; then
        usage >&2
        echo >&2
        echo "saknar --${krav//_ref/}" >&2
        exit 2
    fi
done
if [[ ${#code_paths[@]} -eq 0 ]]; then
    code_paths=(crates testsuite)
fi
if [[ -z "$code_repo" ]]; then
    code_repo=$(cd "$HERE" && git rev-parse --show-toplevel 2>/dev/null || true)
    if [[ -z "$code_repo" ]]; then
        echo "VÄGRAR: hittar inget git-repo runt $HERE — ange --code-repo" >&2
        exit 2
    fi
fi

if [[ ! -f "$facit" ]]; then
    echo "VÄGRAR: facit är ingen fil: $facit" >&2
    exit 1
fi
if [[ ! -d "$code_repo/.git" ]] && ! (cd "$code_repo" && git rev-parse --git-dir >/dev/null 2>&1); then
    echo "VÄGRAR: --code-repo $code_repo är inget git-repo" >&2
    exit 1
fi

# HEAD måste vara en riktig commit. En försegling mot en ref som inte finns pinnar
# ingenting.
if ! head_sha=$(cd "$code_repo" && git rev-parse --verify "${head_ref}^{commit}" 2>/dev/null); then
    echo "VÄGRAR: okänd commit: $head_ref" >&2
    exit 1
fi
head_subject=$(cd "$code_repo" && git log --format=%s -1 "$head_sha")

# GRINDEN: facit före kod. Ocommittade ändringar i kodvägarna betyder att koden
# ännu inte är den commit vi pinnar mot — och då kan facitet ha formats av kod som
# ingen kan gå tillbaka till.
dirty=$(cd "$code_repo" && git status --porcelain -- "${code_paths[@]}" || true)
if [[ -n "$dirty" ]]; then
    echo "VÄGRAR: kodvägarna har ocommittade ändringar — facit förseglas FÖRE kod." >&2
    echo "  repo: $code_repo" >&2
    echo "  vägar: ${code_paths[*]}" >&2
    printf '%s\n' "$dirty" | sed 's/^/  /' >&2
    echo "  committa (eller stasha) koden först; en försegling mot ett smutsigt träd pinnar ingenting." >&2
    exit 1
fi

sealed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

declare -a cp_args=()
for p in "${code_paths[@]}"; do cp_args+=(--code-path "$p"); done

mkdir -p "$ledger"
exec python3 "$LEDGER_PY" append \
    --ledger "$ledger" \
    --facit "$facit" \
    --head "$head_sha" \
    --head-subject "$head_subject" \
    --sealed-at "$sealed_at" \
    --sealed-by "$by" \
    "${cp_args[@]}"
