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
# LÄGEN
# ----
# --av (default): båda cvarerna 0. Med dagens koda är HELA eventytan
#    telemetri-grindad, så tom ström är FACIT, inte ett fel — men bara
#    om mätningen var giltig. Därför: skriptet bevisar själv att mätningen
#    var giltig (status-rundresa + en bot som bevisligen rör sig, via en
#    Goto och positionsändring mellan två status-anrop), och skriver sedan
#    signaturfilen som en TOM kanonisk fil. Två tomma signaturfiler är
#    byte-identiska, och det är grunden i av-läget. Om event MOTTAS trots
#    cvarerna av (t.ex. Arrived/GotoStall, som ligger utanför rtx_telemetry)
#    är regressionen ett faktum: exit 1 med arterna utskrivna, ingen
#    signaturfil.
#
# --legacy: rtx_telemetry=1, rtx_plan_telemetry=0. Den jämförbara
#    fältnivåsignaturen (arter + sorterade fältnamn per art) för den
#    legacy-eventyta som fortfarande flödar med rtx_telemetry på.
#
# FLAGGOR: --port, --out, --secs, --bot, --av, --legacy, -h/--help.
#
# ANVÄNDNING
#   ./b_regressionsdiff.sh --port 27995 --out ~/lab/b-regress/tbx
#   # ...sedan samma sak mot ett main-bygge (runtime3-kopia, egen port):
#   ./b_regressionsdiff.sh --port <mainport> --out ~/lab/b-regress/main
#   diff ~/lab/b-regress/tbx.signatur ~/lab/b-regress/main.signatur && echo LIKA
#
# Vägrar hellre än gissar: avbryter om servern inte är redo, om cvarerna inte
# går att läsa tillbaka som väntat, om boten inte finns i status, eller om
# positionen inte ändras efter Goto (då mätte vi inget).

usage() {
  # Hjälp = de inledande kommentarssraderna, inget annat. Klipp vid den
  # första raden som inte är en kommentar, sa ingen skalkod läcker ut.
  sed -n '1,/^#\s*$/p' "$0" | head -n -1 | sed 's/^# \{0,1\}//'
  exit 0
}
set -euo pipefail

PORT=27995
OUT="$HOME/lab/b-regress/kor"
SECS=25
BOT=1
MODE=av

usage() { sed -n '2,52p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --out)  OUT="$2";  shift 2 ;;
    --secs) SECS="$2"; shift 2 ;;
    --bot)  BOT="$2";  shift 2 ;;
    --av)   MODE=av; shift ;;
    --legacy) MODE=legacy; shift ;;
    -h|--help) usage ;;
    *) echo "okänd flagga: $1 (--help)" >&2; exit 2 ;;
  esac
done

mkdir -p "$(dirname "$OUT")"

PORT="$PORT" OUT="$OUT" SECS="$SECS" BOT="$BOT" MODE="$MODE" python3 - <<'PYEOF'
import collections, json, os, sys, time
sys.path.insert(0, "/home/xerial/rtx-tools")
try:
    from labctl import Lab
except ImportError:
    sys.exit("kan inte importera labctl från ~/rtx-tools — kör på lanister")

port, out, secs, bot = int(os.environ["PORT"]), os.environ["OUT"], float(os.environ["SECS"]), int(os.environ["BOT"])
mode = os.environ["MODE"]
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

# Slå av/på cvarerna enligt läget, och läs tillbaka. En cvar som inte finns
# svarar 0 i mvdsv, vilket är oskiljbart från "registrerad och avstängd" —
# men i av-läge är 0 det vi vill ha oavsett, och i legacy-läge kräver vi
# explicit 1.0, så avläsningen är grunden i båda fallen.
expected = {"rtx_telemetry": "0", "rtx_plan_telemetry": "0"} if mode == "av" \
    else {"rtx_telemetry": "1", "rtx_plan_telemetry": "0"}
for namn, want in sorted(expected.items()):
    try:
        lab.set(namn, want)
        back = lab.get(namn)
    except Exception as e:
        dö(f"kan inte sätta/läsa {namn}: {e}")
    if isinstance(back, dict):
        back = back.get("Get", back)  # Fable-fix: Lab.request returnerar {"Get": {...}}
    val = back.get("value") if isinstance(back, dict) else back
    try:
        if float(str(val)) != float(want):
            dö(f"{namn} läste tillbaka {val!r}, inte {want} — mätningen vore meningslös")
    except (TypeError, ValueError):
        dö(f"{namn} läste tillbaka {val!r}, går inte att tolka som tal")
    print(f"# cvar {namn} = {val}")

def bot_origin():
    for b in lab.status().get("bots", []):
        if b.get("ent") == bot:
            return b
    return None

b0 = bot_origin()
if b0 is None:
    dö(f"bot {bot} finns inte i status — kan inte bevisa att mätningen var giltig")
print(f"# bot {bot}: name={b0.get('name')!r} origin={b0.get('origin')}")

# Bevisa att boten är aktiv: ge en Goto till en annan punkt i planen och
# verifiera att positionen faktiskt ändras mellan två status-anrop. Utan
# detta kan en tom ström betyda "servern stod stilla" istället för
# "telemetrin var av".
def dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5

o0 = b0.get("origin")
if not o0:
    dö("boten bär ingen origin i status")
# Mål: 160 unit i +y, samma x/z (planpunkt, ingen teleport).
target = [o0[0], o0[1] + 160.0, o0[2]]
try:
    lab.goto(bot, target)
except Exception as e:
    dö(f"Goto till {target} misslyckades: {e}")
moved = None
for _ in range(20):  # upp till ~10 s
    time.sleep(0.5)
    try:
        o1 = bot_origin().get("origin")
    except Exception:
        continue
    if o1 and dist(o0, o1) > 8.0:
        moved = o1
        break
if moved is None:
    try:
        lab.stop(bot)
    except Exception:
        pass
    dö(f"boten {bot} rörde sig inte efter Goto (o0={o0}, target={target}) — "
       "mätningen vore meningslös. Kontrollera att matchen/boten är i drift.")
print(f"# bot {bot} verifierad aktiv: {o0} -> {moved} (dist={dist(o0, moved):.1f})")
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

# Grind 1 (båda lägen): inget nytt på tråden.
lackage = {k: hist[k] for k in NYA if hist.get(k)}

# Signatur. I av-läge är tom ström FACIT (hela eventytan är telemetri-
# grindad) — och ovan bevisades att mätningen var giltig — så signaturfilen
# skrivs som en TOM kanonisk fil. I legacy-läge är den fältnivåsignaturen
# (arter + sorterade fältnamn per art).
sig_p = out + ".signatur"
if mode == "av":
    if n != 0:
        # Tom signatur skrivs ENDAST vid n==0: event trots cvarerna av är
        # regressionen ett faktum (t.ex. Arrived/GotoStall ligger utanför
        # rtx_telemetry), inte en OBS.
        print(f"REGRESSION: {n} event med cvarerna AV: "
              + ", ".join(f"{k}={v}" for k, v in sorted(hist.items())), file=sys.stderr)
        print(f"# rå ström  : {raw_p}", file=sys.stderr)
        sys.exit(1)
    with open(sig_p, "w", encoding="utf-8") as sig:
        sig.write("")
    print(f"# {n} event i {secs:.0f}s (av-läge: tom ström är facit)")
else:
    if n == 0:
        dö("noll event i legacy-läget (rtx_telemetry=1) — servern producerade "
           "ingenting att jämföra. Kör med en aktiv bot, eller höj --secs.")
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
print(f"OK: noll PlanTick och noll PlanContract (läge: {mode}).")
PYEOF
