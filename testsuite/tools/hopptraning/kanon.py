#!/usr/bin/env python3
"""Oberoende kanonisk inventering + nivå-2, skriven ur MOTORNS kalla.

Detta ar den andra, oberoende raknaren. Den delar ingen kod med
`mkgraph_full.py` — formatet ar skrivet av ur
`crates/rtx-game/src/nav_patch.rs::canonical_inventory`:

    C\\t{cell_id}\\t{x as i32}\\t{y as i32}\\t{z as i32}     for id 0..n-1, i ordning
    L\\t{from}\\t{to}\\t{kind_token}\\t{T}                   sorterade pa (from,to,kind,T)
    hopfogade med "\\n", ingen avslutande radbrytning
    T = 1 om lanken ligger i adjacensen, annars 0

Positiv kontroll forst: raknaren MASTE reproducera motorns egen levande
nivå-2 ur dumpen. Gor den inte det ar varje harlett slutlage vardelost och
korningen stannar.

Sedan harleds slutlaget for borttagningen — aldrig observerat, alltid raknat.
"""
import hashlib
import json
import struct
import sys
from pathlib import Path

DUMP = Path(sys.argv[1] if len(sys.argv) > 1
            else "/home/xerial/hopptraning/graf/dm3-rigg-full-graph.json")
# Motorns levande nivå-2 vid dumptillfallet, last ur `Fixa chain` — den positiva
# kontrollens facit, inte ett varde raknaren far anvanda.
FACIT_NIVA2 = "4c099331899d7aaecc8d23ccaa00ab6ca2ac192e135aecbb420853886c9643e5"
FACIT_STAMP = "17645347086516095554"

# De tva korsningarna obduktionen pekar ut.
BORT = [int(x) for x in sys.argv[2:]] or [34501, 34503]


def fnv1a64(b: bytes) -> int:
    h = 0xCBF29CE484222325
    for c in b:
        h ^= c
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def stamp(map_name: str, cells: int, links: int, rj: int) -> str:
    return str(fnv1a64(map_name.encode("utf-8") + struct.pack("<III", cells, links, rj)))


def inventering(cells, lrecs) -> str:
    rader = [f"C\t{i}\t{int(c[0])}\t{int(c[1])}\t{int(c[2])}" for i, c in enumerate(cells)]
    for src, dst, kind, t in sorted(lrecs):
        rader.append(f"L\t{src}\t{dst}\t{kind}\t{t}")
    return "\n".join(rader)


def niva2(cells, lrecs) -> str:
    return hashlib.sha256(inventering(cells, lrecs).encode("utf-8")).hexdigest()


def main():
    g = json.loads(DUMP.read_bytes())
    cells = g["cells"]
    links = g["links"]
    link_ids = g["link_ids"]
    print("dump      ", DUMP)
    print("          sha256", hashlib.sha256(DUMP.read_bytes()).hexdigest())
    print("          celler", len(cells), " lankar", len(links))

    lrecs = [(l["from"], l["to_cell"], l["kind"], l["T"]) for l in links]
    n_prunade = sum(1 for l in links if l["T"] == 0)
    print("          prunade (T=0)", n_prunade)

    print("\n--- POSITIV KONTROLL ---")
    h = niva2(cells, lrecs)
    s = stamp(g["map"], len(cells), len(links), 0)
    print("raknad nivå-2 :", h)
    print("motorns nivå-2:", FACIT_NIVA2)
    print("raknad stamp  :", s)
    print("motorns stamp :", FACIT_STAMP)
    if h != FACIT_NIVA2 or s != FACIT_STAMP:
        print("\nSTOPP: raknaren reproducerar inte motorn. Inget harlett varde ar giltigt.")
        return 2
    print("PASS — raknaren ar bunden till motorn, harledningar ar giltiga.")

    # --- vilka ar de tva? -------------------------------------------------
    idx = {lid: i for i, lid in enumerate(link_ids)}
    print("\n--- LANKARNA SOM SKA BORT (ankare for receptet) ---")
    ank = []
    for lid in BORT:
        i = idx[lid]
        l = links[i]
        ank.append({"id": lid, "from": l["from"], "to": l["to_cell"], "kind": l["kind"], "T": l["T"]})
        print("  id %-6d %5d -> %-5d %-10s T=%d  %s -> %s"
              % (lid, l["from"], l["to_cell"], l["kind"], l["T"],
                 cells[l["from"]], cells[l["to_cell"]]))
    Path("/home/xerial/hopptraning/ankare.json").write_text(json.dumps(ank, indent=1))

    # --- kombinationen: in/ut for varje rord cell, fore och efter ---------
    rorda = sorted({a["from"] for a in ank} | {a["to"] for a in ank})
    print("\n--- KOMBINATION: in/ut per rord cell (recept_lint-disciplin) ---")
    bortset = set(BORT)
    for c in rorda:
        ut_f = [(link_ids[i], l["kind"], l["to_cell"]) for i, l in enumerate(links) if l["from"] == c and l["T"] == 1]
        in_f = [(link_ids[i], l["kind"], l["from"]) for i, l in enumerate(links) if l["to_cell"] == c and l["T"] == 1]
        ut_e = [x for x in ut_f if x[0] not in bortset]
        in_e = [x for x in in_f if x[0] not in bortset]
        print("  cell %-5d %s  ut %d -> %d   in %d -> %d" % (c, cells[c], len(ut_f), len(ut_e), len(in_f), len(in_e)))
        if not ut_e:
            print("      VARNING: cellen far NOLL utgangar — envagsfalla")
        if not in_e:
            print("      VARNING: cellen far NOLL ingangar — oatkomlig")
        kvar = [x for x in ut_f if x[0] in bortset]
        for k in kvar:
            print("      tar bort ut: id %-6d %-10s -> cell %d" % k)

    # --- harledda slutlagen ----------------------------------------------
    behall = [(l, lid) for l, lid in zip(links, link_ids) if lid not in bortset]
    print("\n--- HARLETT SLUTLAGE ---")
    print("celler 5981 (orort) · lankar %d -> %d · rj 0" % (len(links), len(behall)))

    # Variant A: de 15 prunade forblir prunade.
    lr_a = [(l["from"], l["to_cell"], l["kind"], l["T"]) for l, _ in behall]
    ha = niva2(cells, lr_a)
    sa = stamp(g["map"], len(cells), len(behall), 0)
    print("\nA) prunade bevaras (T oforandrat):")
    print("   stamp  %s" % sa)
    print("   nivå-2 %s" % ha)

    # Variant B: motorns `remove_links_by_id` rensar hela adjacensen och
    # `push_link`:ar varje behallen lank tillbaka — alltsa blir ALLA T=1.
    lr_b = [(l["from"], l["to_cell"], l["kind"], 1) for l, _ in behall]
    hb = niva2(cells, lr_b)
    print("\nB) motorns faktiska vag (alla behallna lankar ater i adjacensen):")
    print("   stamp  %s   (samma antal)" % sa)
    print("   nivå-2 %s" % hb)
    print("   OBS: %d avsiktligt rensade lankar blir traverserbara igen." % n_prunade)

    Path("/home/xerial/hopptraning/harlett-slutlage.json").write_text(json.dumps({
        "bas": {"cells": len(cells), "links": len(links), "rj_links": 0,
                "graph_stamp": FACIT_STAMP, "graph_content_hash": FACIT_NIVA2},
        "borttagna": ank,
        "A_prunade_bevaras": {"cells": len(cells), "links": len(behall), "rj_links": 0,
                              "graph_stamp": sa, "graph_content_hash": ha},
        "B_motorns_vag_prunade_ateruppstar": {"cells": len(cells), "links": len(behall), "rj_links": 0,
                                              "graph_stamp": sa, "graph_content_hash": hb,
                                              "ateruppstandna": n_prunade},
    }, indent=1))
    print("\nskrev /home/xerial/hopptraning/harlett-slutlage.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
