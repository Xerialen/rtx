#!/usr/bin/env bash
# B-regressionsskyddet: med båda cvarerna AV ska eventströmmen vara densamma
# som mains.
#
# EN ÄRLIGHETSJUSTERING, LÄS DEN FÖRST
# ------------------------------------
# Ordern sade "byte-identisk gammal logg, verifieras med diff". En bokstavlig
# byte-diff av två råa eventströmmar kan aldrig gå igenom, och det är inte
# telemetrins fel: strömmen bär servertid, och två körningar mot en levande
# server startar aldrig på samma tick. Ett test som per konstruktion alltid är
# rött bevisar ingenting.
#
# Det som FAKTISKT står på spel är att inget nytt hamnar på tråden när cvarerna
# är av — en pre-branch nav-viewer tolkar en okänd variant som död koppling.
# Därför gör skriptet två saker, och båda är byte-jämförbara:
#
#   1. HÅRD GRIND: noll PlanTick och noll PlanContract med cvarerna av.
#      Fallerar det är regressionen ett faktum och skriptet returnerar 1.
#   2. SIGNATUR: en kanonisk fil med eventarterna och, per art, deras
#      sorterade fältnamn. Den är oberoende av tid och körordning, och ska vara
#      byte-identisk mot samma fil från ett main-bygge.
#
# Råströmmen sparas också, för den som vill titta för hand.
#
# ANVÄNDNING
#   ./b_regressionsdiff.sh --port 27995 --out ~/lab/b-regress/tbx
#   # ...sedan samma sak mot ett main-bygge (runtime3-kopia, egen port):
#   ./b_regressionsdiff.sh --port <mainport> --out ~/lab/b-regress/main
#   diff ~/lab/b-regress/tbx.signatur ~/lab/b-regress/main.signatur && echo LIKA
#
# Vägrar hellre än gissar: avbryter om servern inte är redo, om cvarerna inte
# går att läsa tillbaka som 0, eller om strömmen är helt tom (då mätte vi inget).
set -euo pipefail

PORT=27995
OUT="$HOME/lab/b-regress/kor"
SECS=25
BOT=1

usage() { sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --out)  OUT="$2";  shift 2 ;;
    --secs) SECS="$2"; shift 2 ;;
    --bot)  BOT="$2";  shift 2 ;;
    -h|--help) usage ;;
    *) echo "okänd flagga: $1 (--help)" >&2; exit 2 ;;
  esac
done

mkdir -p "$(dirname "$OUT")"

PORT="$PORT" OUT="$OUT" SECS="$SECS" BOT="$BOT" python3 - <<'PYEOF'
import collections, json, os, sys, time
sys.path.insert(0, "/home/xerial/rtx-tools")
try:
    from labctl import Lab
except ImportError:
    sys.exit("kan inte importera labctl från ~/rtx-tools — kör på lanister")

port, out, secs, bot = int(os.environ["PORT"]), os.environ["OUT"], float(os.environ["SECS"]), int(os.environ["BOT"])
NYA = ("PlanTick", "PlanContract")

def dö(m):
    print(f"AVBRYTER: {m}", file=sys.stderr); sys.exit(2)

try:
    lab = Lab(port=port)
except OSError as e:
    dö(f"når inte kontrollkanalen på {port}: {e}")

st = lab.status()
if st.get("navmesh") != "ready":
    dö(f"navmesh är {st.get('navmesh')!r}, inte 'ready'")
print(f"# server: map={st.get('map')} celler={st.get('cells')} lankar={st.get('links')}")

# Slå AV båda, och läs tillbaka. En cvar som inte finns svarar 0 i mvdsv, vilket
# är oskiljbart från "registrerad och avstängd" — men här är 0 det vi vill ha
# oavsett vilket, så avläsningen räcker som grind.
for namn in ("rtx_telemetry", "rtx_plan_telemetry"):
    try:
        lab.set(namn, "0")
        back = lab.get(namn)
    except Exception as e:
        dö(f"kan inte sätta/läsa {namn}: {e}")
    if isinstance(back, dict):
        back = back.get("Get", back)  # Fable-fix: Lab.request returnerar {"Get": {...}}
    val = back.get("value") if isinstance(back, dict) else back
    try:
        if float(str(val)) != 0.0:
            dö(f"{namn} läste tillbaka {val!r}, inte 0 — mätningen vore meningslös")
    except (TypeError, ValueError):
        dö(f"{namn} läste tillbaka {val!r}, går inte att tolka som tal")
    print(f"# cvar {namn} = {val}")

# Kör boten en stund så strömmen får innehåll. Ingen puppet-order: autonom drift
# är det som producerar den bredaste eventfloran.
try:
    lab.stop(bot)
except Exception:
    pass

hist = collections.Counter()
falt = collections.defaultdict(set)
raw_p = out + ".raw.jsonl"
n = 0
with open(raw_p, "w", encoding="utf-8") as raw:
    def sink(kind, f):
        global n
        hist[kind] += 1
        if isinstance(f, dict):
            falt[kind].update(f.keys())
        raw.write(json.dumps({"kind": kind, "ev": f}, sort_keys=True) + "\n")
        n += 1
    t0 = time.time()
    while time.time() - t0 < secs:
        lab.drain(0.25, on_event=sink)

if n == 0:
    dö("noll event under hela fönstret — servern producerade ingenting att jämföra. "
       "Kör med en aktiv bot, eller höj --secs.")

# Grind 1: inget nytt på tråden.
lackage = {k: hist[k] for k in NYA if hist.get(k)}

# Grind 2: den tidsoberoende signaturen.
sig_p = out + ".signatur"
with open(sig_p, "w", encoding="utf-8") as sig:
    for kind in sorted(falt):
        sig.write(f"{kind}\t{','.join(sorted(falt[kind]))}\n")

print(f"# {n} event i {secs:.0f}s: " + ", ".join(f"{k}={v}" for k, v in sorted(hist.items())))
print(f"# rå ström  : {raw_p}")
print(f"# signatur  : {sig_p}   <-- den här ska vara byte-identisk mot mains")

if lackage:
    print(f"REGRESSION: nya varianter på tråden med cvarerna AV: {lackage}. "
          f"En pre-branch nav-viewer hade tolkat dem som död koppling.", file=sys.stderr)
    sys.exit(1)
print("OK: noll PlanTick och noll PlanContract med cvarerna av.")
PYEOF
