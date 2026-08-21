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

----------------------------------------------------------------------------
TRE RATTELSER, QA-domen 2026-08-21 (WORK_LOGS/qa-dom-receptautostart-design.md)
----------------------------------------------------------------------------

L8 — `--verifiera-offline` gick aldrig att kora pa en planteringstabell.
  `planteringar()` normaliserade en tabell till steg UTAN `fran_cell`/`mal_cell`
  medan PlanLink-grenen slog upp just de faltena: `KeyError: 'fran_cell'`.
  Vagen har alltsa aldrig kunnat kora for tabellformen, bara for stegrecept.
  Nu (a) bar `planteringar()` faltena nar de star i filen, och (b) resolverar
  verifieringen dem GEOMETRISKT ur dumpen med en port av `NavGraph::nearest`
  nar de inte gor det. Star bada matas de mot varandra och en avvikelse ar
  STOPP — det ar den grind som gor de fem certade cellparen (facit §7 test 9)
  maskinellt provbara offline.

K2 — bas-/efter-grinden var en PREFIXgrind (`startswith`), sa atta hextecken
  rackte for att passera. Nu kravs FULL 64-teckens hex och EXAKT likhet. En
  forkortad konstant ar inte langre "nastan ratt", den ar ogiltig indata och
  ger STOPP. Samma stramning ar gjord i motorn (`crates/rtx-game/src/recept.rs`).
  Foljd som ar avsedd och bokford: `vf5_ring2quad.json` bar en 8-teckens
  `efter.niva2_sha256` (`d155c22e`) och avvisas darfor tills dess fulla varde
  harleds ur en vF5-basdump. Det receptet ar etapp 2, star utanfor manifestet
  och byggs inte nu.

K1/L9 — offlinevagen speglar nu motorns kedja. Flera receptfiler pa
  kommandoraden i --verifiera-offline utgor EN kedja i manifestordning: varje
  fils `bas` provas mot dumpens ursprungliga hash (precis som `recept.rs` gor
  fore forsta steget), stegen laggs pa kumulativt, och `efter` provas efter
  SISTA filen (design v2 §4.4). En icke-sista fil som deklarerar
  `efter.niva2_sha256` ar en receptkonfiguration motorn inte kan uppfylla och
  avvisas hogljutt i stallet for att tigas ihjal.
"""
import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

VERKTYG = Path(__file__).resolve().parent

# Rutnatets rutstorlek, ur crates/rtx-nav/src/navmesh/mod.rs: `pub const GRID: f32 = 32.0;`
GRID = 32.0

# K2: en bunden grafkonstant ar 64 hextecken. Inget annat godtas.
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


def las(p):
    return json.loads(Path(p).read_text())


def ar_stegrecept(d):
    return isinstance(d, dict) and "steg" in d


def planteringar(d):
    """Normalisera en planteringstabell till en lista av steg.

    Barare av `fran_cell`/`mal_cell` (L8): star de i tabellposten foljer de med
    till steget, sa offlineverifieringens PlanLink-gren har samma falt att lasa
    som ett stegrecept ger den. Star de inte dar resolveras de geometriskt av
    `verifiera_offline`. Alias `from_cell`/`to_cell` godtas, eftersom det ar de
    namn riggsvaret anvander.
    """
    ut = []
    for namn, s in d.items():
        if not isinstance(s, dict):
            continue
        frm = s.get("frm") or s.get("from")
        if frm is None or "takeoff" not in s:
            continue
        steg = {"op": "PlanLink", "namn": namn, "from": frm,
                "takeoff": s["takeoff"], "tgt": s["tgt"],
                "v_req": s["v_req"], "gain": s["gain"]}
        for falt, alias in (("fran_cell", "from_cell"), ("mal_cell", "to_cell")):
            v = s.get(falt)
            if v is None:
                v = s.get(alias)
            if v is not None:
                steg[falt] = int(v)
        ut.append(steg)
    return ut


def steg_ur(p):
    d = las(p)
    if ar_stegrecept(d):
        return d, d["steg"]
    return d, planteringar(d)


# ------------------------------------------------------- grafkonstanter (K2)

def granska_konstant(varde, etikett, fil):
    """None om konstanten ar en full 64-teckens hex, annars felmeddelandet.

    K2: prefixmatchning ar borttagen. Ett forkortat varde sag exakt ut som ett
    fullt i utskriften men provade bara sina egna forsta tecken; atta rackte.
    """
    if not isinstance(varde, str) or not HEX64.match(varde):
        return ("STOPP: %s: ogiltig %s-konstant %r — kraver full 64-teckens hex "
                "(K2, QA-domen 2026-08-21)" % (fil, etikett, varde))
    return None


def hash_lika(vantad, faktisk):
    """Exakt likhet, skiftlagesokansligt. Ingen prefixmatchning."""
    return vantad.lower() == faktisk.lower()


# --------------------------------------------------- nearest, portad ur Rust

def bygg_rutnat(celler):
    """Rutnatsindexet, byggt som `NavGraph::add_cell` bygger det: cell-id i
    stigande ordning i sin (gx, gy)-hink."""
    rut = {}
    for i, c in enumerate(celler):
        rut.setdefault((math.floor(c[0] / GRID), math.floor(c[1] / GRID)), []).append(i)
    return rut


def narmaste(pos, celler, rut):
    """Port av `NavGraph::nearest` (crates/rtx-nav/src/navmesh/query.rs:54).

    Soker utat fran punktens egen rutkolumn: hela kvadraten (2r+1)^2 for
    r = 0, 1, ... och slutar sa fort nagot hittats OCH r >= 1 — alltsa alltid
    minst 3x3 rutor. Returnerar (cell, d2, nast_basta_d2).
    """
    gx, gy = math.floor(pos[0] / GRID), math.floor(pos[1] / GRID)
    basta = None
    sedda = {}
    for radie in range(0, 5):
        for dx in range(-radie, radie + 1):
            for dy in range(-radie, radie + 1):
                for cid in rut.get((gx + dx, gy + dy), ()):
                    c = celler[cid]
                    d = ((c[0] - pos[0]) ** 2 + (c[1] - pos[1]) ** 2
                         + (c[2] - pos[2]) ** 2)
                    # Strikt <, precis som motorn: vid lika avstand vinner den
                    # forst sedda cellen. Kvadraten (2r+1)^2 skannar om samma
                    # hinkar for varje r, sa en redan sedd cell far aldrig
                    # trangas in igen — darfor `sedda`.
                    if cid not in sedda:
                        sedda[cid] = d
                        if basta is None or d < basta[1]:
                            basta = (cid, d)
        if basta is not None and radie >= 1:
            break
    if basta is None:
        return None
    ovriga = [d for cid, d in sedda.items() if cid != basta[0]]
    return basta[0], basta[1], (min(ovriga) if ovriga else None)


def narmaste_globalt(pos, celler):
    """Rak genomsokning av samtliga celler — instrumentets egen kontroll.

    Cellkoordinaterna i dumpen ar heltalstrunkerade (`c.origin.x as i32`), sa
    en cell kan i teorin hamna i fel rutnatshink jamfort med motorns f32-varden.
    Stammer rutnatssvaret med det globalt narmaste ar den frangan utan verkan:
    da ar svaret det narmaste som finns, oavsett hinkindelning.
    """
    basta = None
    for cid, c in enumerate(celler):
        d = ((c[0] - pos[0]) ** 2 + (c[1] - pos[1]) ** 2 + (c[2] - pos[2]) ** 2)
        if basta is None or d < basta[1]:
            basta = (cid, d)
    return basta


def losa_cell(s, faltpos, faltcell, celler, rut, fil):
    """Cellen ett PlanLink-steg resolverar till, och kontrollen av den.

    Tre utfall:
      * receptet deklarerar cellen OCH geometrin ger samma -> (cell, rad)
      * receptet deklarerar ingen cell -> geometrins svar anvands
      * de skiljer sig, eller rutnatet och den globala sokningen skiljer sig
        -> None + STOPP-rad
    """
    dekl = s.get(faltcell)
    pos = s.get(faltpos)
    if pos is None or len(pos) != 3:
        if dekl is None:
            return None, ("STOPP: %s: steget %r saknar bade %r och %r"
                          % (fil, s.get("namn"), faltpos, faltcell))
        return int(dekl), "    %-9s cell %-5d (deklarerad; ingen position att prova mot)" % (faltcell, int(dekl))
    rn = narmaste(pos, celler, rut)
    if rn is None:
        return None, "STOPP: %s: ingen cell nara %s %s" % (fil, faltpos, pos)
    cid, d2, nast_d2 = rn
    gid, gd2 = narmaste_globalt(pos, celler)
    if gid != cid:
        return None, ("STOPP: %s: rutnatssoket ger cell %d (d=%.2f) men global sokning "
                      "cell %d (d=%.2f) for %s" % (fil, cid, math.sqrt(d2), gid, math.sqrt(gd2), faltpos))
    if dekl is not None and int(dekl) != cid:
        return None, ("STOPP: %s: %s deklarerar cell %d men geometrin resolverar %d "
                      "(d=%.2f)" % (fil, faltcell, int(dekl), cid, math.sqrt(d2)))
    marginal = ("%.2f" % math.sqrt(nast_d2)) if nast_d2 is not None else "-"
    return cid, ("    %-9s cell %-5d d=%-7.2f nast basta d=%-7s %s"
                 % (faltcell, cid, math.sqrt(d2), marginal,
                    "(deklarerad, stammer)" if dekl is not None else "(resolverad)"))


# ---------------------------------------------------------------- offline

def verifiera_offline(receptfiler, dumpfil):
    """Spela upp en KEDJA av recept mot en grafdump och rakna fram niva-2.

    Kedjan speglar motorns ordning (`recept.rs::applicera`): varje fils `bas`
    provas mot dumpens ursprungliga identitet fore forsta steget, stegen laggs
    pa kumulativt i den ordning filerna star, och `efter` provas efter sista
    filen. Filerna ska darfor ges i manifestets ordning.
    """
    sys.path.insert(0, str(VERKTYG))
    import kanon

    g = las(dumpfil)
    celler, lankar, lids = g["cells"], g["links"], g["link_ids"]
    rut = bygg_rutnat(celler)
    rader = [(l["from"], l["to_cell"], l["kind"], l["T"]) for l in lankar]
    bas_hash = kanon.niva2(celler, rader)
    print("dump   : %d celler / %d lankar / niva2 %s" % (len(celler), len(lankar), bas_hash))

    lasta = []
    for fil in receptfiler:
        d, steg = steg_ur(fil)
        lasta.append((fil, d, steg))

    # Bindningen provas FORE forsta steget, for varje fil, mot dumpens egen
    # identitet — inte mot ett lopande mellanlage. `bas` ar den grafidentitet
    # receptet ar skrivet mot, inte lagerbilden precis fore just den filen.
    for fil, d, _ in lasta:
        vantad_bas = (d.get("bas") or {}).get("niva2_sha256")
        if vantad_bas is None:
            print("bas    : %s anger ingen bas — inget att prova" % fil)
            continue
        fel = granska_konstant(vantad_bas, "bas", fil)
        if fel:
            print(fel)
            return 2
        if not hash_lika(vantad_bas, bas_hash):
            print("STOPP: dumpens bas matchar inte receptets bas (%s: %s)" % (fil, vantad_bas))
            return 2
        print("bas    : %s matchar receptets bas" % fil)

    # K1/L9: bara sista filens `efter` kan vara slutlaget. En icke-sista fil som
    # deklarerar ett ska inte tigas ihjal — motorn kan inte uppfylla det.
    for fil, d, _ in lasta[:-1]:
        if (d.get("efter") or {}).get("niva2_sha256") is not None:
            print("STOPP: %s ar inte sista receptet i kedjan men deklarerar "
                  "efter.niva2_sha256 — bara slutlaget kan bindas (design v2 §4.4)" % fil)
            return 2

    # Receptet spelas upp i ordning. Plantering laggs till som T=1; borttagning
    # filtreras bort. Motorns aterupplivning av prunade lankar modelleras genom
    # att ALLA behallna lankar far T=1 efter ett RemoveLinks-steg — det ar den
    # pinnade semantiken, inte en gissning.
    aktuella = list(rader)
    kvar_lids = list(lids)
    for fil, d, steg in lasta:
        print("== %s ==" % fil)
        for s in steg:
            if s["op"] == "PlanLink":
                fran, rad = losa_cell(s, "from", "fran_cell", celler, rut, fil)
                if fran is None:
                    print(rad)
                    return 2
                fran_rad = rad
                mal, rad = losa_cell(s, "tgt", "mal_cell", celler, rut, fil)
                if mal is None:
                    print(rad)
                    return 2
                # Motorn planterar med kind = SpeedJump och lagger lanken i
                # adjacensen (`push_link`), alltsa T = 1. Cellmangden ar
                # orord: `plant_speed_jump` skapar ingen cell.
                aktuella = aktuella + [(fran, mal, "speedjump", 1)]
                kvar_lids = kvar_lids + [None]
                print("  + PlanLink  %-28s cell %d -> %d" % (s["namn"], fran, mal))
                print(fran_rad)
                print(rad)
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
        print("  delsumma: %d lankar / niva2 %s" % (len(aktuella), kanon.niva2(celler, aktuella)))

    slut = kanon.niva2(celler, aktuella)
    sista = lasta[-1]
    vantad = (sista[1].get("efter") or {}).get("niva2_sha256")
    print("harlett: %d lankar / niva2 %s" % (len(aktuella), slut))
    if not vantad:
        print("receptet anger ingen forvantad sluthash — inget att jamfora mot")
        return 1
    fel = granska_konstant(vantad, "efter", sista[0])
    if fel:
        print(fel)
        return 2
    ok = hash_lika(vantad, slut)
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
            # Deklarerade celler ar en grind aven pa skarp vag: svarar riggen
            # med ett annat cellpar an receptet pastar ar receptet inte det som
            # sitter, och det ska synas har och inte forst i sluthashen.
            for falt, svarsfalt in (("fran_cell", "from_cell"), ("mal_cell", "to_cell")):
                if s.get(falt) is not None and sv.get(svarsfalt) is not None \
                        and int(s[falt]) != int(sv[svarsfalt]):
                    print("  STOPP: %s: receptet anger %s=%d men riggen svarade %s"
                          % (s["namn"], falt, int(s[falt]), sv[svarsfalt]))
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
    ap.add_argument("recept", nargs="+",
                    help="en eller flera receptfiler; i --verifiera-offline "
                         "utgor de EN kedja och ska ges i manifestordning")
    ap.add_argument("--port", type=int, default=int(os.environ.get("RTX_PORT", "27990")))
    ap.add_argument("--applicera", action="store_true")
    ap.add_argument("--torrkor", action="store_true")
    ap.add_argument("--verifiera-offline", metavar="DUMP.json")
    ap.add_argument("--lock-token", default=os.environ.get("RTX_LOCK_TOKEN"))
    a = ap.parse_args()
    if not (a.applicera or a.torrkor or a.verifiera_offline):
        ap.error("valj --applicera, --torrkor eller --verifiera-offline")
    if a.verifiera_offline:
        return verifiera_offline(a.recept, a.verifiera_offline)
    kod = 0
    for r in a.recept:
        print("== %s ==" % r)
        kod = max(kod, mot_rigg(r, a.port, a.torrkor, a.lock_token))
        print()
    return kod


if __name__ == "__main__":
    sys.exit(main())
