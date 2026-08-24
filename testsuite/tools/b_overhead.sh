#!/usr/bin/env bash
# Vad planerartelemetrin kostar: samma körning med cvarerna av och på.
#
# MÅTTET, OCH VARFÖR DET ÄR DET HÄR
# ---------------------------------
# Kravet är "tic-vakten opåverkad, <1 %". Serverns interna tic-räknare går inte
# att läsa utifrån kontrollkanalen, men det den vakten skyddar går att mäta
# direkt: håller servern sin egen klocka mot väggklockan?
#
# `Status` bär serverns speltid. Sampla den mot väggtiden över ett fönster och
# kvoten säger hur väl servern hinner med. En server som halkar efter tappar
# speltid mot väggtid, och det är precis vad en överbelastad tic ser ut som
# utifrån. Skillnaden i kvot mellan av och på ÄR overheaden, uttryckt i det som
# faktiskt betyder något för körningen.
#
# Rapporterar också väggtid per fönster och antal event, så att kostnaden för
# själva strömmen syns skild från kostnaden för att producera den.
#
# ANVÄNDNING
#   ./b_overhead.sh --port 27995 --secs 60
#
# Vägrar hellre än gissar: avbryter om servern inte är redo, om cvarerna inte
# läser tillbaka som satta, eller om serverklockan står still (då mäter vi inget).
set -euo pipefail

PORT=27995
SECS=60

usage() { sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --secs) SECS="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "okänd flagga: $1 (--help)" >&2; exit 2 ;;
  esac
done

PORT="$PORT" SECS="$SECS" python3 - <<'PYEOF'
import os, sys, time
sys.path.insert(0, "/home/xerial/rtx-tools")
try:
    from labctl import Lab
except ImportError:
    sys.exit("kan inte importera labctl från ~/rtx-tools — kör på lanister")

port, secs = int(os.environ["PORT"]), float(os.environ["SECS"])

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

def satt(namn, v):
    try:
        lab.set(namn, str(v)); back = lab.get(namn)
    except Exception as e:
        dö(f"kan inte sätta/läsa {namn}: {e}")
    if isinstance(back, dict):
        back = back.get("Get", back)  # Fable-fix: Lab.request returnerar {"Get": {...}}
    val = back.get("value") if isinstance(back, dict) else back
    try:
        if abs(float(str(val)) - float(v)) > 1e-6:
            dö(f"{namn} läste tillbaka {val!r}, väntade {v}")
    except (TypeError, ValueError):
        dö(f"{namn} läste tillbaka {val!r}, går inte att tolka")

def mat(etikett):
    """Serverklocka mot väggklocka över fönstret, plus eventvolym."""
    n = [0]
    def sink(kind, f):
        n[0] += 1
    t_wall0 = time.monotonic()
    t_srv0 = lab.status().get("time")
    if t_srv0 is None:
        dö("Status bär ingen 'time' — kan inte mäta serverklockan")
    t_end = t_wall0 + secs
    while time.monotonic() < t_end:
        lab.drain(0.25, on_event=sink)
    t_srv1 = lab.status().get("time")
    wall = time.monotonic() - t_wall0
    srv = t_srv1 - t_srv0
    if srv <= 0:
        dö(f"serverklockan stod still ({t_srv0} -> {t_srv1}) — mätningen är meningslös")
    kvot = srv / wall
    print(f"{etikett:>10}: serverklocka {srv:7.2f}s / vaggtid {wall:7.2f}s "
          f"= {kvot:.4f}   event={n[0]}")
    return kvot, n[0]

# AV först, så en varm server inte gynnar det ena läget.
satt("rtx_telemetry", 0); satt("rtx_plan_telemetry", 0)
lab.drain(2.0)
av, n_av = mat("cvar AV")

satt("rtx_telemetry", 1); satt("rtx_plan_telemetry", 1); satt("rtx_plan_telemetry_div", 1)
lab.drain(2.0)
pa, n_pa = mat("cvar PA")

# Tillbaka till av — lämna inte servern påslagen efter en mätning.
satt("rtx_telemetry", 0); satt("rtx_plan_telemetry", 0)

if av <= 0:
    dö("nollkvot i av-läget")
forlust = (av - pa) / av * 100.0
print()
print(f"# overhead = {forlust:+.2f} %  (kvotfall fran {av:.4f} till {pa:.4f})")
print(f"# eventvolym: {n_av} -> {n_pa}")
print("# Kravet ar <1 %. Ett NEGATIVT tal betyder att pa-laget holl klockan battre,")
print("# vilket i praktiken ar matbrus — rapportera talet som det ar, jamna inte ut det.")
if forlust > 1.0:
    print(f"OVER GRANSEN: {forlust:.2f} % > 1 %", file=sys.stderr)
    sys.exit(1)
print("OK: inom 1 %.")
PYEOF
