#!/bin/bash
# PORTNINGSKVITTOT "fork-basbindningen".
#
# Kvittot ar INTE "antal grona". Det ar en namngiven kontroll av EN sak: att det
# omharledda receptet binder mot fork-basens niva-2 och mot ingen annan graf.
# K1 ar kvittot; N1-N6 ar dess negativkontroller — en grind som aldrig setts
# falla ar ingen grind.
#
#   bash kvitto_forkbasbindningen.sh [FORKDUMP.json] [VF5DUMP.json]
#
# Bada dumparna maste vara KOMPLETTA (out + out_pruned).
set -u
HAR=$(cd "$(dirname "$0")" && pwd)
FORK=${1:-/home/xerial/lab/toolbox/dm3-base-full-graph.json}
VF5=${2:-/home/xerial/hopptraning/graf/dm3-rigg-full-graph.json}
NY="$HAR/vf5_ring2quad_forkmain.json"
GAMMALT="$HAR/vf5_ring2quad.json"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
FEL=0

kor () { # namn, forvantad exitkod, receptfil, dump
  echo "---- $1"
  python3 "$HAR/applicera_recept.py" "$3" --verifiera-offline "$4" 2>&1 | sed 's/^/    /'
  local rc=${PIPESTATUS[0]}
  if [ "$rc" = "$2" ]; then echo "    UTFALL exit=$rc (forvantat $2)  OK"
  else echo "    UTFALL exit=$rc (forvantat $2)  FEL"; FEL=$((FEL+1)); fi
  echo
}

muta () { python3 -c "
import json,sys
d=json.load(open(sys.argv[1]))
exec(sys.argv[3])
json.dump(d,open(sys.argv[2],'w'))" "$NY" "$1" "$2"; }

echo "=== KVITTO: fork-basbindningen ==="
kor "K1 omharlett recept mot fork-basen -> ska BINDA och harleda efter-hashen" 0 "$NY" "$FORK"

echo "=== NEGATIVKONTROLLER ==="
kor "N1 omharlett recept mot vF5-basen -> ska VAGRA (fel bas)" 2 "$NY" "$VF5"
kor "N2 originalreceptet mot fork-basen -> ska VAGRA (fel bas)" 2 "$GAMMALT" "$FORK"
muta "$TMP/n3.json" 'd["steg"][1]["lankar"][5]["id"]=35593'
kor "N3 ett lank-id andrat 35592 -> 35593 -> MATCHAR INTE" 3 "$TMP/n3.json" "$FORK"
muta "$TMP/n4.json" 'd["steg"][0]["mal_cell"]=2072'
kor "N4 avfartens malcell andrad 2083 -> 2072 -> MATCHAR INTE" 3 "$TMP/n4.json" "$FORK"
muta "$TMP/n5.json" 'd["bas"]["niva2_sha256"]="deadbeef"+d["bas"]["niva2_sha256"][8:]'
kor "N5 basens niva-2 forvanskad -> ska VAGRA" 2 "$TMP/n5.json" "$FORK"
muta "$TMP/n6.json" 'd["efter"]["niva2_sha256"]="d155c22e"'
kor "N6 efter-hashen satt till lokala mains d155c22e -> MATCHAR INTE" 3 "$TMP/n6.json" "$FORK"

echo "=== SUMMA: $FEL avvikelser ==="
exit $((FEL > 0))
