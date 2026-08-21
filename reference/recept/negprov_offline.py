#!/usr/bin/env python3
"""Mutationsprov for applicera_recept.py --verifiera-offline.

Husregeln: varje grind ska ses FALLA, och den ska fallas av exakt den kontroll
som pastar sig bevaka den. Varje rad nedan ar en mutation av en riktig
receptfil, med vantad exitkod och vantat textfragment.

Kors: python3 negprov.py <kitkatalog> <dump.json>
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RA = "ra_climb_planted.json"
V296 = "vast_296_planted.json"


def mut_cell(d, _):
    d[RA]["P1 z=60"]["fran_cell"] = 1457


def mut_bas_kort(d, _):
    d[RA]["bas"]["niva2_sha256"] = d[RA]["bas"]["niva2_sha256"][:8]


def mut_bas_fel(d, _):
    h = d[RA]["bas"]["niva2_sha256"]
    d[RA]["bas"]["niva2_sha256"] = ("f" if h[0] != "f" else "0") + h[1:]


def mut_efter_kort(d, _):
    d[V296]["efter"]["niva2_sha256"] = d[V296]["efter"]["niva2_sha256"][:8]


def mut_efter_fel(d, _):
    h = d[V296]["efter"]["niva2_sha256"]
    d[V296]["efter"]["niva2_sha256"] = ("f" if h[0] != "f" else "0") + h[1:]


def mut_efter_i_forsta(d, _):
    d[RA]["efter"]["niva2_sha256"] = d[V296]["efter"]["niva2_sha256"]


def mut_dump(_, g):
    g["links"][0]["to_cell"] = g["links"][0]["to_cell"] + 1


def mut_tgt(d, _):
    """Flytta en plantering till en annan malcell, utan deklarerade celler.

    Provar att den HARLEDDA sluthashen faktiskt foljer det som planteras — inte
    bara att den foljer dumpen.
    """
    p = d[V296]["V296 vasthyllan"]
    p.pop("fran_cell", None)
    p.pop("mal_cell", None)
    p["tgt"] = [144.62477111816406, -720.0106811523438, 331.4109191894531]


PROV = [
    ("kontroll: orord kedja", None, 0, "MATCHAR"),
    ("fran_cell 1456 -> 1457", mut_cell, 2, "geometrin resolverar 1456"),
    ("bas trunkerad till 8 hextecken", mut_bas_kort, 2, "ogiltig bas-konstant"),
    ("bas full langd men fel", mut_bas_fel, 2, "dumpens bas matchar inte"),
    ("efter trunkerad till 8 hextecken", mut_efter_kort, 2, "ogiltig efter-konstant"),
    ("efter full langd men fel", mut_efter_fel, 3, "MATCHAR INTE"),
    ("efter i icke-sista filen", mut_efter_i_forsta, 2, "inte sista receptet"),
    # En andrad lank i dumpen andrar BASEN, sa bas-grinden fyrar fore
    # slutlagesjamforelsen. Det ar ratt ordning: fel graf ska stoppas fore
    # forsta steget (facit paragraf 7 test 4), inte efter sista.
    ("en lank i dumpen andrad", mut_dump, 2, "dumpens bas matchar inte"),
    ("plantering flyttad till annan malcell", mut_tgt, 3, "MATCHAR INTE"),
]


def main():
    kit, dump = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    fel = 0
    for namn, mutation, vantad_kod, vantat_text in PROV:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for f in ("applicera_recept.py", "kanon.py", RA, V296):
                shutil.copy(kit / f, tmp / f)
            d = {f: json.loads((tmp / f).read_text()) for f in (RA, V296)}
            g = json.loads(dump.read_text())
            if mutation:
                mutation(d, g)
            for f in (RA, V296):
                (tmp / f).write_text(json.dumps(d[f], indent=1, ensure_ascii=True))
            (tmp / "dump.json").write_text(json.dumps(g))
            r = subprocess.run(
                [sys.executable, "applicera_recept.py", RA, V296,
                 "--verifiera-offline", "dump.json"],
                cwd=tmp, capture_output=True, text=True)
            ok = r.returncode == vantad_kod and vantat_text in r.stdout
            fel += 0 if ok else 1
            print("%-34s exit %d (vantat %d)  %-28s %s"
                  % (namn, r.returncode, vantad_kod, vantat_text,
                     "OK" if ok else "FALLERAR"))
            if not ok:
                print("".join("      | %s\n" % l for l in r.stdout.splitlines()[-6:]))
                print(r.stderr[-500:])
    print()
    print("SAMLAT: %d av %d prov gav vantat utfall" % (len(PROV) - fel, len(PROV)))
    return 1 if fel else 0


if __name__ == "__main__":
    sys.exit(main())
