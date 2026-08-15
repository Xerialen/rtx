#!/usr/bin/env python3
"""V296-återspelning mot en dedikerad B-instans — råa fält, ingen tolkning.

Vad det gör: planterar V296-länken i två varianter, flyger boten över den N
gånger per variant, och skriver ut exakt de fält terras förseglade facit
(`WORK_LOGS/facit-verktygslada/facit-v296-controllerstate.md`) kräver — en rad
per försök, plus hela PlanTick-strömmen som JSONL.

Vad det INTE gör: dömer. Skriptet skriver aldrig "godkänt", "ground race" eller
någon annan etikett. Facit är terras och domen är kimis; det här levererar bara
underlaget i en form som kan läsas utan motorkunskap.

De två fallen (siffrorna är facitets, inte mina):

  C1  falskt hopp vid 312-kanten (negativt facit)
      källcell 1139 · Prestrafe→Hop · on_ground=false · jump_cmd=false
      första luft-vz ≤ 0 (facit såg -9,6)
  C2  groundad jump (positivt kontrollfacit)
      källcell 1167 · jump-cmd på övergångsframen · första luft-vz > 0
      (facit såg +260,4)

Båda kör v_req=320. Det är hela poängen: farten kan inte skilja dem åt, vilket
är varför `v_req_deficit` är en falsifierad etikett. Skiljelinjen ligger i
controller-tillståndet, och det är det som skrivs ut.

Vägrar hellre än gissar. Avbryter med kod 2 om servern inte är redo, om
planteringen nekas, om cvarerna inte går att sätta, eller om noll PlanTick
kommer in — det sista betyder fel bygge eller avstängd telemetri, och att köra
vidare hade producerat tomma rader som ser ut som ett resultat.

Exempel:
    python3 b_v296_replay.py --port 27995 --n 10 --out ~/lab/v296-replay.jsonl
"""
import argparse
import json
import math
import os
import sys
import time

sys.path.insert(0, "/home/xerial/rtx-tools")
try:
    from labctl import Lab
except ImportError:
    sys.exit("kan inte importera labctl från ~/rtx-tools — kör på lanister")

GAIN = 5.5
V_REQ = 320.0

# Länkgeometrin ur facitets källor (vast296_kontroll.json / kontroll2.json,
# toppfältet `lank`). Ändra inte utan att facit ändras först.
CASES = {
    "c1": {
        "namn": "C1 falskt hopp vid 312-kanten",
        "frm": [70.9, -532.0, 296.0],
        "takeoff": [93.1, -587.8, 296.0],
        "tgt": [138.1, -701.0, 328.0],
        "carried": True,
        "vantad_kallcell": 1139,
    },
    "c2": {
        "namn": "C2 groundad jump (kontrollfall)",
        "frm": [107.0, -582.0, 296.0],
        "takeoff": [92.0, -588.0, 296.0],
        "tgt": [138.1, -701.0, 328.0],
        "carried": False,
        "vantad_kallcell": 1167,
    },
}

# Fälten facit pekar ut, i den ordning de skrivs. Inget härleds, inget utelämnas.
FACIT_FALT = [
    "t", "seq", "cell", "link", "kind", "takeoff_cell", "link_to",
    "phase_prev", "phase", "on_ground", "jump_cmd",
    "first_air_vz", "first_air_vz_measured",
    "sj_progress", "sj_progress_measured", "runway", "runway_measured",
    "v_req", "speed", "vz", "curl_gain", "chained",
]


def dö(msg, kod=2):
    print(f"AVBRYTER: {msg}", file=sys.stderr)
    sys.exit(kod)


def preflight(lab, bot):
    """Servern ska vara redo och telemetrin påslagen — annars avbryt."""
    try:
        st = lab.status()
    except Exception as e:
        dö(f"når inte kontrollkanalen: {e}")
    if st.get("navmesh") != "ready":
        dö(f"navmesh är {st.get('navmesh')!r}, inte 'ready' — vänta ut bygget")
    if not st.get("cells"):
        dö("servern rapporterar noll celler")
    bots = [b for b in (st.get("bots") or []) if b.get("bot") == bot or b.get("client") == bot]
    if not bots and not st.get("bots"):
        dö(f"ingen bot på servern — kan inte flyga bot {bot}")
    print(f"# server: map={st.get('map')} celler={st.get('cells')} "
          f"lankar={st.get('links')} navmesh={st.get('navmesh')}", flush=True)
    return st


def satt_cvar(lab, namn, varde):
    """Sätt och LÄS TILLBAKA. En cvar som inte finns svarar 0 i mvdsv, vilket är
    oskiljbart från 'registrerad och avstängd' — därför räcker det inte att sätta."""
    try:
        lab.set(namn, str(varde))
    except Exception as e:
        dö(f"kan inte sätta {namn}: {e}")
    try:
        back = lab.get(namn)
    except Exception as e:
        dö(f"kan inte läsa tillbaka {namn}: {e}")
    if isinstance(back, dict):
        back = back.get("Get", back)  # Fable-fix: Lab.request returnerar {"Get": {...}}
    val = back.get("value") if isinstance(back, dict) else back
    try:
        ok = abs(float(str(val)) - float(varde)) < 1e-6
    except (TypeError, ValueError):
        ok = str(val) == str(varde)
    if not ok:
        dö(f"{namn} läste tillbaka {val!r}, väntade {varde!r} — "
           f"är det här ett bygge med planerartelemetri?")
    print(f"# cvar {namn} = {val}", flush=True)


def plantera(lab, case):
    c = CASES[case]
    begaran = {"PlanLink": {"from": c["frm"], "takeoff": c["takeoff"], "tgt": c["tgt"],
                            "v_req": V_REQ, "gain": GAIN, "carried": c["carried"]}}
    try:
        r = lab.request(begaran, timeout=25)
    except Exception as e:
        dö(f"{case}: planteringen nekades ({e}) — facitgeometrin gick inte att lägga, "
           f"och att köra mot en annan länk vore inte samma försök")
    lank = (r.get("PlanLink") or r).get("link")
    if lank is None:
        dö(f"{case}: PlanLink svarade utan link-id: {r!r}")
    print(f"# {case}: planterad link={lank} ({c['namn']})", flush=True)
    return lank


def entry_vel(c, fart):
    d = [c["takeoff"][0] - c["frm"][0], c["takeoff"][1] - c["frm"][1]]
    L = math.hypot(*d) or 1.0
    return [d[0] / L * fart, d[1] / L * fart, 0.0]


def ett_forsok(lab, case, lank, bot, sekunder):
    """Ett försök: teleportera in med entry-fart, flyg länken, samla PlanTick."""
    c = CASES[case]
    rader = []

    def sink(kind, f):
        if kind == "PlanTick" and f.get("bot") == bot:
            rader.append(f)

    lab.stop(bot)
    lab.drain(0.2, on_event=sink)
    lab.teleport(bot, c["frm"], [0.0, 0.0, 0.0])
    lab.drain(0.3, on_event=sink)
    rader.clear()  # allt före starten är uppvärmning, inte försöket
    lab.teleport(bot, c["frm"], entry_vel(c, V_REQ))
    lab.fly(bot, lank)
    t0 = time.time()
    while time.time() - t0 < sekunder:
        lab.drain(0.1, on_event=sink)
    return rader


def skriv_forsok(case, i, rader, ut):
    """En rad per PlanTick där boten är på länken, i facitets fältordning."""
    c = CASES[case]
    if not rader:
        print(f"{case} forsok={i:02d}  INGA PlanTick — se sammanfattningen", flush=True)
        return 0
    for r in rader:
        ut.write(json.dumps({"case": case, "forsok": i, **r}, sort_keys=True) + "\n")
    # Skriv ut de ticks där en takeoff faktiskt observerades, plus den sista
    # groundade före den — det är fönstret facit beskriver.
    intressanta = [r for r in rader if r.get("first_air_vz_measured")
                   or r.get("phase_prev") != r.get("phase")]
    if not intressanta:
        intressanta = rader[-3:]
    print(f"--- {case} forsok={i:02d}  ({len(rader)} ticks, "
          f"vantad kallcell {c['vantad_kallcell']}) ---", flush=True)
    for r in intressanta:
        bitar = []
        for f in FACIT_FALT:
            v = r.get(f)
            if isinstance(v, float):
                v = round(v, 3)
            bitar.append(f"{f}={v}")
        print("  " + " ".join(bitar), flush=True)
    return len(rader)


def main():
    p = argparse.ArgumentParser(
        prog="b_v296_replay.py",
        description="V296-återspelning mot B-instansen: skriver facitets fält råa, dömer inte.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("--port", type=int, default=27995, help="kontrollport (B-instansen: 27995)")
    p.add_argument("--bot", type=int, default=1, help="klientnummer att flyga")
    p.add_argument("--n", type=int, default=10, help="försök per fall")
    p.add_argument("--case", choices=["c1", "c2", "both"], default="both")
    p.add_argument("--secs", type=float, default=6.0, help="lyssningsfönster per försök")
    p.add_argument("--out", default=os.path.expanduser("~/lab/v296-replay.jsonl"),
                   help="JSONL med hela PlanTick-strömmen")
    a = p.parse_args()

    try:
        lab = Lab(port=a.port)
    except OSError as e:
        dö(f"kan inte ansluta till kontrollkanalen på port {a.port}: {e}. "
           f"B-instansen lyssnar på 27995 — kör den, och kontrollera att riglocket är taget.")
    preflight(lab, a.bot)
    # Dubbelgrinden: BÅDA krävs. rtx_plan_telemetry ensam ger inget på tråden.
    satt_cvar(lab, "rtx_telemetry", 1)
    satt_cvar(lab, "rtx_plan_telemetry", 1)
    satt_cvar(lab, "rtx_plan_telemetry_div", 1)

    fall = ["c1", "c2"] if a.case == "both" else [a.case]
    totalt = 0
    with open(a.out, "w", encoding="utf-8") as ut:
        for case in fall:
            lank = plantera(lab, case)
            for i in range(1, a.n + 1):
                rader = ett_forsok(lab, case, lank, a.bot, a.secs)
                totalt += skriv_forsok(case, i, rader, ut)
            try:
                lab.request({"PlanDrop": {"link": lank}}, timeout=15)
            except Exception:
                pass  # planteringen är transient; en kvarlämnad länk stör inte facit

    print(f"\n# skrev {totalt} PlanTick-rader till {a.out}", flush=True)
    if totalt == 0:
        dö("noll PlanTick under hela körningen. Antingen är det inte ett bygge med "
           "planerartelemetri, eller så nådde cvarerna inte fram. Tomma rader hade "
           "sett ut som ett resultat, därför avbryts det här som ett fel.")
    print("# Fälten ovan är råa. Domen mot terras facit är kimis — det här skriptet "
          "etiketterar ingenting.", flush=True)


if __name__ == "__main__":
    main()
