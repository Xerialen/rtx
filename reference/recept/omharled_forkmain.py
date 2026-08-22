#!/usr/bin/env python3
"""Omharled ett stegrecept fran en grafidentitet till en annan.

Anvant for att flytta vF5-receptet fran vF5-basen (lokal main 4f0b910,
5981 celler / 48217 lankar, niva-2 4c099331...) till fork mains bas
(5977 / 48207, niva-2 58787ce0...).

Cell-id och lank-id skiljer i allmanhet mellan tva motorversioners grafer, sa
id:n far ALDRIG kopieras rakt av. Ankaret ar geometrin: receptet bar
`fran_pos`/`mal_pos` per borttagen lank och varldskoordinater per plantering.

Tva led:
  1. positiv kontroll mot kallgrafen — varje id i receptet ska peka pa exakt den
     lank receptet pastar (from/to/kind + cellkoordinater). Gor den inte det ar
     receptet inte det som certades, och omharledningen avbryts.
  2. omharledning mot malgrafen — samma (fran_pos, mal_pos, kind) slas upp dar
     och maste ge exakt en traff. Dess id ar det nya.

Kor:
  python3 omharled_forkmain.py KALLDUMP.json MALDUMP.json RECEPT.json [UT.json]

Dumparna maste vara KOMPLETTA (out + out_pruned, T = adjacensmedlemskap).
Skrivs UT.json ut ar den utan `efter` — kor applicera_recept.py
--verifiera-offline mot maldumpen for att harleda den hashen.
"""
import json
import sys
from pathlib import Path


def ladda(p):
    g = json.load(open(p))
    g["_cellkey"] = {}
    for i, c in enumerate(g["cells"]):
        g["_cellkey"].setdefault(tuple(int(round(v)) for v in c), []).append(i)
    g["_linkidx"] = {}
    for i, l in enumerate(g["links"]):
        g["_linkidx"].setdefault((l["from"], l["to_cell"], l["kind"]), []).append(i)
    return g


def cell_pa(g, pos):
    """Cellindex vars koordinat matchar pos i x,y (z tas med nar den star i dumpen)."""
    px, py = int(round(pos[0])), int(round(pos[1]))
    tr = [(k, v) for k, v in g["_cellkey"].items() if k[0] == px and k[1] == py]
    if len(pos) > 2:
        pz = int(round(pos[2]))
        exakt = [(k, v) for k, v in tr if k[2] == pz]
        if exakt:
            tr = exakt
    return [i for _, v in tr for i in v]


def naramst(g, pos):
    best, bd = None, float("inf")
    for i, c in enumerate(g["cells"]):
        d = (c[0] - pos[0]) ** 2 + (c[1] - pos[1]) ** 2 + (c[2] - pos[2]) ** 2
        if d < bd:
            best, bd = i, d
    return best, bd ** 0.5


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    kalla, mal, receptfil = ladda(argv[1]), ladda(argv[2]), argv[3]
    ut = argv[4] if len(argv) > 4 else None
    r = json.load(open(receptfil))

    for namn, g in (("kalla", kalla), ("mal", mal)):
        print("%-6s: %d celler / %d lankar / niva2 %s"
              % (namn, len(g["cells"]), len(g["links"]), g.get("graph_content_hash")))
        print("        %s" % g["provenance"][:150])
    print()

    kalla_id2ix = {lid: i for i, lid in enumerate(kalla["link_ids"])}
    fel, nytt_steg = 0, []

    for s in r["steg"]:
        if s["op"] == "PlanLink":
            print("== PlanLink: %s" % s["namn"])
            fc, mc = s["fran_cell"], s["mal_cell"]
            for etikett, cid, pos in (("fran_cell", fc, s["from"]), ("mal_cell", mc, s["tgt"])):
                if cell_pa(kalla, pos) != [cid]:
                    print("   AVVIKELSE: %s %d matchar inte %s i kallgrafen (%s)"
                          % (etikett, cid, pos, cell_pa(kalla, pos)))
                    fel += 1
            nf, nm = cell_pa(mal, kalla["cells"][fc]), cell_pa(mal, kalla["cells"][mc])
            if len(nf) != 1 or len(nm) != 1:
                print("   AVVIKELSE: tvetydig cellmatchning i malgrafen %s / %s" % (nf, nm))
                fel += 1
                nf, nm = nf + [None], nm + [None]
            tk, dk = naramst(kalla, s["takeoff"])
            tm, dm = naramst(mal, s["takeoff"])
            print("   from %s: kalla %d -> mal %d" % (s["from"], fc, nf[0]))
            print("   tgt  %s: kalla %d -> mal %d" % (s["tgt"], mc, nm[0]))
            print("   avfart %s: kalla vardcell %d %s (d=%.2f) | mal vardcell %d %s (d=%.2f)"
                  % (s["takeoff"], tk, kalla["cells"][tk], dk, tm, mal["cells"][tm], dm))
            if tk != tm or dk != dm:
                print("   ANM: avfarten byter vardcell — kontrollera fotavtrycket for hand")
            ns = dict(s)
            ns["fran_cell"], ns["mal_cell"], ns["vardcell_avfart"] = nf[0], nm[0], tm
            nytt_steg.append(ns)
            print()

        elif s["op"] == "RemoveLinks":
            print("== RemoveLinks: %s" % s["namn"])
            nya = []
            for l in s["lankar"]:
                ix = kalla_id2ix.get(l["id"])
                if ix is None:
                    print("   AVVIKELSE: id %d finns inte i kallgrafen" % l["id"])
                    fel += 1
                    dom = "id saknas"
                else:
                    v = kalla["links"][ix]
                    lank_ok = (v["from"] == l["from"] and v["to_cell"] == l["to"]
                               and v["kind"] == l["kind"])
                    pos_ok = (cell_pa(kalla, l["fran_pos"]) == [l["from"]]
                              and cell_pa(kalla, l["mal_pos"]) == [l["to"]])
                    dom = "kalla OK" if (lank_ok and pos_ok) else \
                        "kalla AVVIKELSE (lank=%s pos=%s)" % (lank_ok, pos_ok)
                    if not (lank_ok and pos_ok):
                        fel += 1
                f_fr, f_ma = cell_pa(mal, l["fran_pos"]), cell_pa(mal, l["mal_pos"])
                nid = []
                if len(f_fr) == 1 and len(f_ma) == 1:
                    nid = [mal["link_ids"][i]
                           for i in mal["_linkidx"].get((f_fr[0], f_ma[0], l["kind"]), [])]
                print("   id %-6d %5d->%-5d %-10s %-18s -> mal celler %s->%s  id %s   [%s]"
                      % (l["id"], l["from"], l["to"], l["kind"], str(l["fran_pos"]),
                         f_fr, f_ma, nid, dom))
                if len(nid) != 1:
                    print("      AVVIKELSE: %d traffar i malgrafen" % len(nid))
                    fel += 1
                nl = dict(l)
                nl["vf5_id"], nl["id"] = l["id"], (nid[0] if len(nid) == 1 else None)
                nl["from"] = f_fr[0] if len(f_fr) == 1 else None
                nl["to"] = f_ma[0] if len(f_ma) == 1 else None
                nya.append(nl)
            ns = dict(s)
            ns["lankar"] = nya
            nytt_steg.append(ns)
            print()
        else:
            print("STOPP: okand op %r" % s["op"])
            return 2

    print("avvikelser:", fel)
    if fel:
        print("STOPP: omharledningen ar inte entydig — inget skrivs")
        return 3
    if ut:
        d = dict(r)
        d["bas"] = {"celler": len(mal["cells"]), "lankar_inkl_prunade": len(mal["links"]),
                    "niva2_sha256": mal.get("graph_content_hash")}
        d["steg"] = nytt_steg
        d.pop("efter", None)
        Path(ut).write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n")
        print("skrev", ut, "— harled `efter` med applicera_recept.py --verifiera-offline")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
