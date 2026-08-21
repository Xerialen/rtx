#!/usr/bin/env python3
"""Additivregelns kontroll: ny fil MINUS de tillagda faltena = den certade filen.

Kors i reference/recept. Skriver ut nya sha256, den rekonstruerade certade
sha256 och domen. Negativkontroll ingar: en andrad planteringsparameter ska
gora att rekonstruktionen INTE langre traffar det certade vardet.
"""
import hashlib
import json
import sys
from pathlib import Path

CERTAD = {
    "ra_climb_planted.json":
        "42f49e6c798cd3eef48919650b33ce121dce97ec82e23b37166452e1fab934b3",
    "vast_296_planted.json":
        "00da285962f19b8e53aca279f3bbb670844282260395c6d1585ecbe435348ae8",
}
TILLAGT_TOPP = ("bas", "efter")
TILLAGT_POST = ("fran_cell", "mal_cell")


def dumpa(d):
    return json.dumps(d, indent=1, ensure_ascii=True).encode()


def utan_tillagg(d):
    ut = {}
    for k, v in d.items():
        if k in TILLAGT_TOPP:
            continue
        if isinstance(v, dict):
            v = {kk: vv for kk, vv in v.items() if kk not in TILLAGT_POST}
        ut[k] = v
    return ut


def main():
    kat = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    fel = 0
    for fil, cert in CERTAD.items():
        raw = (kat / fil).read_bytes()
        d = json.loads(raw)
        rek = hashlib.sha256(dumpa(utan_tillagg(d))).hexdigest()
        ok = rek == cert
        fel += 0 if ok else 1
        print("%-24s ny %s" % (fil, hashlib.sha256(raw).hexdigest()))
        print("%-24s minus tillagg %s  %s" % ("", rek, "= CERTAD" if ok else "AVVIKER"))

        # Negativkontroll: rora en certad parameter ska bryta rekonstruktionen.
        m = json.loads(raw)
        post = next(k for k, v in m.items()
                    if isinstance(v, dict) and ("frm" in v or "from" in v))
        m[post]["v_req"] = m[post]["v_req"] + 1.0
        neg = hashlib.sha256(dumpa(utan_tillagg(m))).hexdigest()
        print("%-24s negkontroll (v_req +1 i %r) -> %s  %s"
              % ("", post, neg[:16] + "...",
                 "SKILJER (ratt)" if neg != cert else "SAMMA (instrumentet ar dott)"))
        if neg == cert:
            fel += 1
    return 1 if fel else 0


if __name__ == "__main__":
    sys.exit(main())
