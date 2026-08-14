#!/usr/bin/env python3
"""K1-plantering: deterministisk, ur JSON-facit, INGA svep och INGA
certflygningar (grok-baselineplan-review krav 2, deepseek krav 4).
Planterar P1-P4 (ra_climb_planted.json, P4 v_req=380), meshlänkarna
(ra_mesh_planted.json) och P1-56 (p1_56_planted.json, carried).
Inga spiraler. REFUSED på någon länk => exit 1 (fasen görs om).
Skriver manifestfragment till stdout som JSON."""
import hashlib, json, os, sys
sys.path.insert(0, "/home/xerial/rtx-tools")
from labctl import Lab

PORT = int(os.environ.get("RTX_PORT", "27990"))
FILES = {
    "climb": os.path.expanduser("~/lab/ra_climb_planted.json"),
    "mesh": os.path.expanduser("~/lab/ra_mesh_planted.json"),
    "p156": os.path.expanduser("~/lab/p1_56_planted.json"),
}

def sha_full(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()

lab = Lab(port=PORT)
lab.set("rtx_telemetry", "1")
planted, refused = [], []

def plan(name, frm, takeoff, tgt, v_req, gain, carried=False):
    cmd = {"PlanLink": {"from": [float(x) for x in frm],
                        "takeoff": [float(x) for x in takeoff],
                        "tgt": [float(x) for x in tgt],
                        "v_req": float(v_req), "gain": float(gain)}}
    if carried:
        cmd["PlanLink"]["carried"] = True
    try:
        r = lab.request(cmd, timeout=20)["PlanLink"]
        planted.append({"name": name, "link": r["link"],
                        "cost": round(r.get("cost", -1), 3),
                        "v_req": float(v_req), "gain": float(gain),
                        "carried": carried})
    except Exception as exc:
        refused.append({"name": name, "err": str(exc)})
        print("REFUSED %s: %s" % (name, exc), file=sys.stderr, flush=True)

climb = json.load(open(FILES["climb"]))
for name, f in climb.items():
    # carried=True för hela uppvägen (receptet; se plant_ra_climb.plant)
    plan("climb:" + name, f["frm"], f["takeoff"], f["tgt"], f["v_req"], f["gain"],
         carried=True)

mesh = json.load(open(FILES["mesh"]))
for name, f in mesh.items():
    plan("mesh:" + name, f["frm"], f["takeoff"], f["tgt"], f["v"], f["gain"])

p156 = json.load(open(FILES["p156"]))
f = p156[next(iter(p156))]
plan("p156", f["from"], f["takeoff"], f["tgt"], f["v_req"], f["gain"], carried=True)

frag = {"plant_files": {k: {"path": v, "sha256": sha_full(v)} for k, v in FILES.items()},
        "planted": planted, "refused": refused}
print(json.dumps(frag))
sys.exit(1 if refused else 0)
