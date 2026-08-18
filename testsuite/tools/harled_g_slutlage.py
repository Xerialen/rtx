#!/usr/bin/env python3
"""B4 — oberoende härledning av G:s slutläge.

Läser ENDAST fork-dumpen och receptets op-lista. Manifestet, runbooken, apply-kvittona
och receptets egna `base`/`harledd_slut` strippas innan härledningen, så inget påstått
svar kan läcka in i beräkningen. Jämförelsen mot receptets påstående görs EFTERÅT.
"""
import json, sys, hashlib
from pathlib import Path
sys.path.insert(0, "/home/xerial/rtx-toolbox-d/testsuite/tools")
import transformator as T
import graphstamp

FORK = "/home/xerial/ben3d-ut/dm3-fork-full-graph.json"
RECEPT = "/home/xerial/rtx-toolbox-d/testsuite/tools/recept/paav-g-v1.json"

fork_bytes = Path(FORK).read_bytes()
print(f"basdump  {FORK}")
print(f"         sha256 {hashlib.sha256(fork_bytes).hexdigest()}")
bas = T.Graf.from_dump(json.loads(fork_bytes))
reg = graphstamp.load_register()
b = bas.identitet(reg)
print(f"         härledd bas {b['cells']}/{b['links']} rj={b['rj_links']}")
print(f"         FNV {b['graph_stamp']}")
print(f"         nivå-2 {b['graph_content_hash_utan_params']}")

rec = json.loads(Path(RECEPT).read_text(encoding="utf-8"))
pastatt = rec.pop("harledd_slut", None)     # strippas FÖRE härledningen
rec.pop("base", None)
print(f"\nrecept   {RECEPT}")
print(f"         sha256 {hashlib.sha256(Path(RECEPT).read_bytes()).hexdigest()}")
print(f"         op-lista: {[(o.get('op'), o.get('name')) for o in rec['ops']]}")

steg = T.kor_recept(bas, rec, reg)
slut = steg["slut"] if isinstance(steg, dict) and "slut" in steg else None
if slut is None:
    # kor_recept-formen varierar; plocka sista stegets identitet
    poster = steg.get("steg") if isinstance(steg, dict) else steg
    slut = poster[-1].get("identitet") or poster[-1]
print("\nHÄRLETT SLUTLÄGE (transformatorn, ur bas + op-lista):")
for k in ("cells", "links", "rj_links", "graph_stamp",
          "graph_content_hash", "graph_content_hash_utan_params"):
    if k in slut:
        print(f"   {k:32} {slut[k]}")

print("\nJÄMFÖRELSE mot receptets påstådda harledd_slut (efterhandskontroll):")
if pastatt:
    for k, v in pastatt.items():
        fick = slut.get(k)
        if k == "graph_content_hash":
            fick = slut.get("graph_content_hash_utan_params", fick)
        status = "LIKA" if str(fick) == str(v) else f"AVVIKER (härlett {fick})"
        print(f"   {k:24} {v}  -> {status}")
