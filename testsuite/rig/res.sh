#!/usr/bin/env bash
# Reser T3- eller T4-riggen. Anropas via res_t3.sh / res_t4.sh.
#
# Torrkörning som default. Utan --verkstall rör skriptet ingenting skarpt:
# det läser portlistan, prövar förvillkoren och bygger gamediren i en
# katalog du pekar ut, men startar ingen server.
#
# Inga defaults för sökvägar. En bekväm default skriver bokföringen på fel
# stalle tyst, och det är en klass av fel ingen grind fångar.
set -euo pipefail

HAR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TIER=""; KORNING=""; GRUPP=""; KALLA=""; GAMEDIR=""; PORTLISTA=""
LASFIL=""; RCONFIL=""; TIMELIMIT=""; SEATS=""; DEMODIR=""; KVITTO=""
MVDSV=""; BOTS=""; BOTSMANIFEST=""; QWA=""; QWASHA=""; VERKSTALL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --tier) TIER="$2"; shift 2;;
    --korning) KORNING="$2"; shift 2;;
    --grupp) GRUPP="$2"; shift 2;;
    --kalla) KALLA="$2"; shift 2;;
    --gamedir) GAMEDIR="$2"; shift 2;;
    --portlista) PORTLISTA="$2"; shift 2;;
    --lasfil) LASFIL="$2"; shift 2;;
    --rcon-password-fil) RCONFIL="$2"; shift 2;;
    --timelimit-min) TIMELIMIT="$2"; shift 2;;
    --seats-per-side) SEATS="$2"; shift 2;;
    --demodir) DEMODIR="$2"; shift 2;;
    --kvitto) KVITTO="$2"; shift 2;;
    --mvdsv) MVDSV="$2"; shift 2;;
    --bots) BOTS="$2"; shift 2;;
    --bots-manifest) BOTSMANIFEST="$2"; shift 2;;
    --qw-analyze) QWA="$2"; shift 2;;
    --qw-analyze-sha256) QWASHA="$2"; shift 2;;
    --verkstall) VERKSTALL=1; shift;;
    *) echo "okand flagga: $1" >&2; exit 2;;
  esac
done

krav() {
  # Vagran ska saga vilken flagga som fattas, inte "preflight failed".
  local varde="$1" flagga="$2"
  if [ -z "$varde" ]; then
    echo "VAGRAD: $flagga fattas. Det finns ingen default for den." >&2
    exit 2
  fi
}
krav "$TIER" --tier
krav "$KORNING" --korning
krav "$GRUPP" --grupp
krav "$KALLA" --kalla
krav "$GAMEDIR" --gamedir
krav "$PORTLISTA" --portlista
krav "$LASFIL" --lasfil
krav "$RCONFIL" --rcon-password-fil
krav "$TIMELIMIT" --timelimit-min
krav "$SEATS" --seats-per-side
krav "$DEMODIR" --demodir
krav "$KVITTO" --kvitto
krav "$MVDSV" --mvdsv
if [ "$TIER" = "t4" ]; then
  krav "$BOTS" --bots
  krav "$BOTSMANIFEST" --bots-manifest
fi

# mvdsv loser `-game <namn>` mot <basedir>/<namn>, dar basedir ar mvdsv:s egen
# katalog. En privat gamedir nagon annanstans ar darfor inte en privat gamedir
# - servern hittar den inte och faller tillbaka pa det delade tradet, vilket ar
# precis det vi bygger den for att undvika. Vagra hellre an mata fel.
BASEDIR="$(cd "$(dirname "$MVDSV")" && pwd)"
GDFAR="$(cd "$(dirname "$GAMEDIR")" 2>/dev/null && pwd || echo "$(dirname "$GAMEDIR")")"
if [ "$BASEDIR" != "$GDFAR" ]; then
  echo "VAGRAD: --gamedir maste ligga direkt under mvdsv:s basedir." >&2
  echo "        mvdsv basedir: $BASEDIR" >&2
  echo "        --gamedir far: $GDFAR" >&2
  echo "        mvdsv loser '-game <namn>' mot <basedir>/<namn>; en gamedir" >&2
  echo "        nagon annanstans hittas inte och servern faller tillbaka pa" >&2
  echo "        det delade tradet." >&2
  exit 2
fi

echo "== 1. portar ur den kanoniska listan (inga egna nummer) =="
# Enda kallan for portnummer. Faller den, reser vi ingenting.
eval "$(python3 "$HAR/portar.py" --portlista "$PORTLISTA" --grupp "$GRUPP")"
echo "  grupp=$GRUPP spel=$SPEL ctl=$CTL qtv=$QTV"

echo "== 2. forvillkor =="
VAKT=( --portlista "$PORTLISTA" --korning "$KORNING" --lasfil "$LASFIL"
       --spel "$SPEL" --ctl "$CTL" --qtv "$QTV" )
[ -n "$BOTS" ] && VAKT+=( --bots "$BOTS" --bots-manifest "$BOTSMANIFEST" )
[ -n "$QWA" ] && VAKT+=( --qw-analyze "$QWA" )
[ -n "$QWASHA" ] && VAKT+=( --qw-analyze-sha256 "$QWASHA" )
python3 "$HAR/riggvakt.py" "${VAKT[@]}"

echo "== 3. ogonblicksbild FORE nagot rors =="
mkdir -p "$KVITTO"
BILD="$KVITTO/ogonblicksbild.json"
# Idempotens: en andra ogonblicksbild over den forsta hade gjort
# aterstallningen omojlig, eftersom "fore" da vore "efter". aterstall.py
# vagrar skriva over en befintlig bild.
python3 "$HAR/aterstall.py" bild --ut "$BILD" --korning "$KORNING" \
  --unit "rtx-$TIER-$KORNING" --spel "$SPEL" --ctl "$CTL" --qtv "$QTV"

echo "== 4. privat gamedir + de 15 fallgroparna som assertions =="
GD=( --tier "$TIER" --kalla "$KALLA" --gamedir "$GAMEDIR"
     --seats-per-side "$SEATS" --timelimit-min "$TIMELIMIT"
     --demodir "$DEMODIR" --rcon-password-fil "$RCONFIL" )
[ -n "$BOTS" ] && GD+=( --bots "$BOTS" )
python3 "$HAR/gamedir.py" "${GD[@]}"

echo "== 5. res riggen =="
UNIT="rtx-$TIER-$KORNING"
# Monsterunit saknas for T3/T4, sa transient med 3h-taket. Aldrig enable,
# aldrig daemon-reload: armerade drop-ins aktiveras retroaktivt av en reload.
START=( systemd-run --user --unit="$UNIT" --working-directory="$GAMEDIR"
        -p RuntimeMaxSec=10800 "$MVDSV" -port "$SPEL" -game "$(basename "$GAMEDIR")" )
if [ "$VERKSTALL" -eq 1 ]; then
  "${START[@]}"
  echo "  startade $UNIT"
else
  echo "  TORRKORNING - skulle kora: ${START[*]}"
  echo "  (lagg till --verkstall for att resa riggen skarpt)"
fi

echo "riggen $TIER klar (verkstalld=$VERKSTALL), kvitto i $KVITTO"
