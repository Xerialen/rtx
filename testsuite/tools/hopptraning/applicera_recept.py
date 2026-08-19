#!/usr/bin/env python3
"""Applicera receptet ring2quad-stang-kedjade-v1 mot riggen.

Pinnen ar HARLEDD, inte avlast: `harledd_slut` kommer ur kanon.py:s egen
inventeringsraknare, positivt kontrollerad mot baslagets nivå-2. Motorn far
bekrafta eller vagra — den far aldrig leverera forvantan.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/xerial/hopptraning")
import hoppa

REC = json.loads(Path("/home/xerial/hopptraning/recept-ring2quad-stang-kedjade.json").read_text())
TOK = open("/home/xerial/hopptraning/.rig-lock").read().strip().splitlines()[0]


def ident(d):
    return {"cells": d["cells"], "links": d["links"], "rj_links": d["rj_links"],
            "graph_stamp": d["graph_stamp"], "graph_content_hash": d["graph_content_hash"]}


bas = ident(REC["base"])
slut = ident(REC["harledd_slut"])
lankar = [{"id": L["id"], "from": L["from"], "to": L["to"], "kind": L["kind"]}
          for L in REC["ops"][0]["links"]]

lab = hoppa.Lab()
fore = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
print("FORE:", json.dumps({k: fore.get(k) for k in ("cells", "links", "stamp", "content_hash")}))
if fore.get("stamp") != bas["graph_stamp"] or fore.get("content_hash") != bas["graph_content_hash"]:
    print("STOPP: riggen star inte pa receptets bas.")
    raise SystemExit(2)

cmd = {"Komponat": {
    "recept_id": REC["recept_id"],
    "base": bas,
    "steps": [{
        "name": REC["ops"][0]["name"],
        "op": {"RemoveLinks": {"links": lankar}},
        "expect_before": bas,
        "expect_after": slut,
    }],
    "expect_final": slut,
    "lock_token": TOK,
}}
r = lab.request(cmd, timeout=60)
print("\nKVITTO:", json.dumps(r, indent=1)[:2500])

efter = lab.request({"Fixa": {"recipe": "", "mode": "chain", "lock_token": ""}}, timeout=20)
print("\nEFTER:", json.dumps({k: efter.get(k) for k in ("recipe", "outcome", "cells", "links", "stamp", "content_hash")}))
print("HARLETT :", slut["graph_stamp"], slut["graph_content_hash"])
print("MATCHAR :", efter.get("stamp") == slut["graph_stamp"]
      and efter.get("content_hash") == slut["graph_content_hash"])
Path("/home/xerial/hopptraning/recept-kvitto.json").write_text(json.dumps(
    {"recept": REC["recept_id"], "fore": fore, "kvitto": r, "efter": efter, "harledd_slut": slut}, indent=1))
