#!/usr/bin/env python3
"""Belagg for VILKEN graf fork-basdumpen beskriver — och hur den forhaller sig
till vF5-basen.

Fork mains kontrollkanal saknar `out_pruned`, sa en dump tagen dar bar bara
adjacensen (48192 av 48207 lankar) och kan inte ge niva-2. Den enda kompletta
dumpen pa 5977/48207 togs pa en annan gren (toolbox/b-planner-telemetry, nav_patch
AV). Skriptet visar att den anda beskriver samma graf, i stallet for att anta det:

  A. cellerna och cell_ids identiska mellan fork-dumpen och fork mains egen
     levande dump, och fork-dumpens 48192 T=1-lankar identiska med fork mains
     lankar i samma ordning med samma link_ids;
  B. hur vF5-basen (5981/48217) forhaller sig till fork-basen — prefixlikhet
     eller inte. Det avgor om ett stegrecepts id kan bara over eller maste
     raknas om.

  python3 forkbas_kalla.py FORK_KOMPLETT.json FORK_LIVE.json VF5_KOMPLETT.json
"""
import collections
import hashlib
import json
import sys


def las(p):
    with open(p, "rb") as f:
        raw = f.read()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def koord(cells):
    return [tuple(int(round(x)) for x in c) for c in cells]


def main(argv):
    if len(argv) != 4:
        print(__doc__)
        return 2
    (F, fsha), (L, lsha), (V, vsha) = (las(p) for p in argv[1:4])
    for namn, g, sha, p in (("fork komplett", F, fsha, argv[1]),
                            ("fork live", L, lsha, argv[2]),
                            ("vF5 komplett", V, vsha, argv[3])):
        print("%-14s %-58s %d celler / %d lankar" % (namn, p, len(g["cells"]), len(g["links"])))
        print("               sha256 %s" % sha)
        print("               %s" % g["provenance"][:150])
    print()

    print("A. beskriver den kompletta fork-dumpen fork mains graf?")
    ok = True
    c1, c2 = koord(F["cells"]), koord(L["cells"])
    print("   celler identiska (ordning + koordinat): %s" % (c1 == c2))
    print("   cell_ids identiska:                     %s" % (F["cell_ids"] == L["cell_ids"]))
    fadj = [(l["from"], l["to_cell"], l["kind"]) for l in F["links"] if l["T"] == 1]
    fids = [i for i, l in zip(F["link_ids"], F["links"]) if l["T"] == 1]
    ladj = [(l["from"], l["to_cell"], l["kind"]) for l in L["links"]]
    print("   T=1-lankarna identiska med fork live:   %s  (%d st)" % (fadj == ladj, len(fadj)))
    print("   link_ids identiska for adjacensen:      %s" % (fids == L["link_ids"]))
    ok = (c1 == c2 and F["cell_ids"] == L["cell_ids"] and fadj == ladj and fids == L["link_ids"])
    prun = [(i, l) for i, l in zip(F["link_ids"], F["links"]) if l["T"] == 0]
    print("   prunade lankar bara fork-dumpen kan ge: %d" % len(prun))
    for i, l in prun:
        print("      id %-6s %5d -> %-5d %s" % (i, l["from"], l["to_cell"], l["kind"]))
    print("   => %s" % ("SAMMA GRAF" if ok else "AVVIKER — dumpen far inte anvandas som fork-bas"))
    print()

    print("B. vF5-basen mot fork-basen")
    cv, cf = koord(V["cells"]), koord(F["cells"])
    lv = [(l["from"], l["to_cell"], l["kind"], l["T"]) for l in V["links"]]
    lf = [(l["from"], l["to_cell"], l["kind"], l["T"]) for l in F["links"]]
    pc, pl = cv[:len(cf)] == cf, lv[:len(lf)] == lf
    print("   cellprefixet 0..%d identiskt:  %s" % (len(cf) - 1, pc))
    print("   lankprefixet 0..%d identiskt: %s" % (len(lf) - 1, pl))
    print("   cell_ids-prefix identiskt:      %s" % (V["cell_ids"][:len(cf)] == F["cell_ids"]))
    print("   link_ids-prefix identiskt:      %s" % (V["link_ids"][:len(lf)] == F["link_ids"]))
    print("   T-fordelning prefix: vF5 %s  fork %s"
          % (dict(collections.Counter(t for *_, t in lv[:len(lf)])),
             dict(collections.Counter(t for *_, t in lf))))
    print("   extra celler i vF5: %s" % [(i, cv[i]) for i in range(len(cf), len(cv))])
    print("   extra lankar i vF5:")
    for i in range(len(lf), len(lv)):
        print("      ix %-6d id %-8s %s" % (i, V["link_ids"][i], lv[i]))
    if pc and pl:
        print("   => vF5-basen ar fork-basen + paklistrade celler/lankar. Cell- och")
        print("      lank-id i det delade prefixet bar over oforandrade.")
    else:
        print("   => numreringarna skiljer i prefixet — varje id MASTE raknas om.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
