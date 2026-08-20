#!/usr/bin/env python3
"""Applicera — eller enbart verifiera — ett versionerat navmesh-recept.

Tva receptformer stods, och de skiljer sig i vad de gor med grafen:

  PLANTERINGSTABELL  (ra_climb_planted.json, vast_296_planted.json)
      En ordbok namn -> {frm|from, takeoff, tgt, v_req, gain}. Varje post
      planteras som en PlanLink. Rent additivt: inget tas bort, lanktalet ska
      oka med exakt antalet poster.

  STEGRECEPT  (vf5_ring2quad.json)
      En ordnad lista `steg` med op = PlanLink eller RemoveLinks. Ordningen ar
      betydelsebarande. RemoveLinks kraver riggens lastoken.

Tre korlagen:

  --applicera            skickar receptet till en levande rigg
  --torrkor              skriver ut vad som skulle skickas, ror inget
  --verifiera-offline    spelar upp receptet mot en grafdump och raknar fram
                         resulterande niva-2-hash utan att ta i nagon rigg

Det sista laget ar receptets egen positiva kontroll: stammer den harledda
hashen med `efter.niva2_sha256` i receptfilen ar filen intakt och beskriver
den graf den pastar sig beskriva. Kor den FORE varje applicering pa skarp rigg.

VIKTIGT om lank-ID: ett recept som tar bort lankar ar bundet till EN
grafidentitet. ID:na kompakteras nar lankar tas bort, och en annan motorversion
bygger en annan graf. Applicera aldrig ett stegrecept pa en rigg vars bas inte
matchar `bas.niva2_sha256`.
"""
import argparse
import json
import os
import sys
from pathlib import Path

VERKTYG = Path(__file__).resolve().parent


def las(p):
    return json.loads(Path(p).read_text())


def ar_stegrecept(d):
    return isinstance(d, dict) and "steg" in d


def planteringar(d):
    """Normalisera en planteringstabell till en lista av steg."""
    ut = []
    for namn, s in d.items():
        if not isinstance(s, dict):
            continue
        frm = s.get("frm") or s.get("from")
        if frm is None or "takeoff" not in s:
            continue
        ut.append({"op": "PlanLink", "namn": namn, "from": frm,
                   "takeoff": s["takeoff"], "tgt": s["tgt"],
                   "v_req": s["v_req"], "gain": s["gain"]})
    return ut


def steg_ur(p):
    d = las(p)
    if ar_stegrecept(d):
        return d, d["steg"]
    return d, planteringar(d)


# ---------------------------------------------------------------- offline

def verifiera_offline(receptfil, dumpfil):
    """Spela upp receptet mot en grafdump och rakna fram niva-2-hashen."""
    sys.path.insert(0, str(VERKTYG))
    import kanon

    d, steg = steg_ur(receptfil)
    g = las(dumpfil)
    celler, lankar, lids = g["cells"], g["links"], g["link_ids"]
    rader = [(l["from"], l["to_cell"], l["kind"], l["T"]) for l in lankar]
    bas_hash = kanon.niva2(celler, rader)
    vantad_bas = (d.get("bas") or {}).get("niva2_sha256")
    print("dump   : %d celler / %d lankar / niva2 %s" % (len(celler), len(lankar), bas_hash))
    if vantad_bas and not bas_hash.startswith(vantad_bas.rstrip(".")):
        print("STOPP: dumpens bas matchar inte receptets bas (%s)" % vantad_bas)
        return 2
    print("bas    : matchar receptets bas")

    # Receptet spelas upp i ordning. Plantering laggs till som T=1; borttagning
    # filtreras bort. Motorns aterupplivning av prunade lankar modelleras genom
    # att ALLA behallna lankar far T=1 efter ett RemoveLinks-steg — det ar den
    # pinnade semantiken, inte en gissning.
    aktuella = list(rader)
    kvar_lids = list(lids)
    for s in steg:
        if s["op"] == "PlanLink":
            # Cellerna star i receptet; den harledda kontrollen behover bara
            # veta vilken kant som tillkommer.
            aktuella = aktuella + [(s["fran_cell"], s["mal_cell"], "speedjump", 1)]
            kvar_lids = kvar_lids + [None]
            print("  + PlanLink  %-28s cell %d -> %d" % (s["namn"], s["fran_cell"], s["mal_cell"]))
        elif s["op"] == "RemoveLinks":
            bort = {l["id"] for l in s["lankar"]}
            par = [(r, i) for r, i in zip(aktuella, kvar_lids) if i not in bort]
            aktuella = [(f, t, k, 1) for (f, t, k, _), _ in par]
            kvar_lids = [i for _, i in par]
            print("  - RemoveLinks %-26s %d lankar bort, %d kvar (alla T=1)"
                  % (s["namn"], len(bort), len(aktuella)))
        else:
            print("STOPP: okand op %r" % s["op"])
            return 2

    slut = kanon.niva2(celler, aktuella)
    vantad = (d.get("efter") or {}).get("niva2_sha256")
    print("harlett: %d lankar / niva2 %s" % (len(aktuella), slut))
    if not vantad:
        print("receptet anger ingen forvantad slutdhash — inget att jamfora mot")
        return 1
    ok = slut.startswith(vantad.rstrip("."))
    print("forvantat: %s  -> %s" % (vantad, "MATCHAR" if ok else "MATCHAR INTE"))
    return 0 if ok else 3


# ---------------------------------------------------------------- rigg

def mot_rigg(receptfil, port, torrkor, lock_token):
    sys.path.insert(0, "/home/xerial/rtx-tools")
    from labctl import Lab

    d, steg = steg_ur(receptfil)
    if torrkor:
        print("TORRKORNING — inget skickas")
        for s in steg:
            print("  %s" % json.dumps(s, ensure_ascii=False)[:180])
        return 0

    lab = Lab(port=port)
    s0 = lab.status()
    print("FORE : %d celler / %d lankar" % (s0["cells"], s0["links"]))
    vantat = 0
    kvitto = []
    for s in steg:
        if s["op"] == "PlanLink":
            r = lab.request({"PlanLink": {
                "from": [float(x) for x in s["from"]],
                "takeoff": [float(x) for x in s["takeoff"]],
                "tgt": [float(x) for x in s["tgt"]],
                "v_req": float(s["v_req"]), "gain": float(s["gain"]),
                "carried": True}}, timeout=30)
            sv = r.get("PlanLink") or r
            if sv.get("link") is None:
                print("  STOPP: %s gav ingen lank — %s" % (s["namn"], json.dumps(sv)[:200]))
                return 2
            vantat += 1
            kvitto.append({"namn": s["namn"], "link": sv["link"],
                           "from_cell": sv.get("from_cell"), "to_cell": sv.get("to_cell")})
            print("  + %-28s lank %-6s cell %s -> %s"
                  % (s["namn"], sv["link"], sv.get("from_cell"), sv.get("to_cell")))
        elif s["op"] == "RemoveLinks":
            if not lock_token:
                print("  STOPP: RemoveLinks kraver --lock-token")
                return 2
            ank = [{"id": l["id"], "from": l["from"], "to": l["to"], "kind": l["kind"]}
                   for l in s["lankar"]]
            r = lab.request({"RemoveLinks": {"links": ank, "lock_token": lock_token}},
                            timeout=60)
            vantat -= len(ank)
            kvitto.append({"namn": s["namn"], "borttagna": len(ank), "svar": r})
            print("  - %-28s %d lankar" % (s["namn"], len(ank)))
    s1 = lab.status()
    fakt = s1["links"] - s0["links"]
    print("EFTER: %d celler / %d lankar  (%+d, vantat %+d)"
          % (s1["cells"], s1["links"], fakt, vantat))
    Path(os.path.expanduser("~/recept-kvitto.json")).write_text(json.dumps(
        {"recept": str(receptfil), "port": port, "fore": s0["links"],
         "efter": s1["links"], "steg": kvitto}, indent=1, ensure_ascii=False))
    if fakt != vantat:
        print("STOPP: lanktalet stammer inte")
        return 3
    print("OK — receptet sitter. Kvitto: ~/recept-kvitto.json")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recept", nargs="+", help="en eller flera receptfiler")
    ap.add_argument("--port", type=int, default=int(os.environ.get("RTX_PORT", "27990")))
    ap.add_argument("--applicera", action="store_true")
    ap.add_argument("--torrkor", action="store_true")
    ap.add_argument("--verifiera-offline", metavar="DUMP.json")
    ap.add_argument("--lock-token", default=os.environ.get("RTX_LOCK_TOKEN"))
    a = ap.parse_args()
    if not (a.applicera or a.torrkor or a.verifiera_offline):
        ap.error("valj --applicera, --torrkor eller --verifiera-offline")
    kod = 0
    for r in a.recept:
        print("== %s ==" % r)
        if a.verifiera_offline:
            kod = max(kod, verifiera_offline(r, a.verifiera_offline))
        else:
            kod = max(kod, mot_rigg(r, a.port, a.torrkor, a.lock_token))
        print()
    return kod


if __name__ == "__main__":
    sys.exit(main())
