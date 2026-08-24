#!/usr/bin/env python3
"""fixklass_c.py — C-instrumentet: femklassning av band för «teleport+ring→topp».

Namngiven, pinnad repofil enligt order 2026-08-24, byggd på prototypen
`buzz-4on4/tmp/c-offlineprov/fixklass_c.py` (sha `17d5c0a7…7844b`) med Sols
bindande villkor 1–5 (`2026-08-24-sol-fixklass-beslut-rev2.md`, GODKÄNN(C)).

VAD C ÄR, OCH INTE ÄR
---------------------
C är **fysisk «teleport+ring→topp»**: geometrin i bandet visar en
diskontinuitet som matchar kartteleporten, och boten når toppen. Av de tjugo
C-klassade R-banden var **14 planerade teleportval och 6 fysisk invandring i
samma kartteleport utan aktivt teleportben**.

Ett C-tal får därför **aldrig** beskrivas som bevis för att planeraren valde
teleportlänken, och **aldrig** som bevis för att västrutten exerceras. Varje
utdata från det här instrumentet bär den upplysningen (villkor 4); den är
inte en artighet utan en del av resultatet.

VILLKOREN, OCH VAR DE BOR
-------------------------
1. Kanonfilerna rörs inte. `at_topp` importeras ur
   `reference/ra-room/granskriterier.py` och dess sha256 pinnas — kriteriet
   kopieras aldrig hit. Ett kopierat kontrollvärde är samma fälla som en
   kopierad portlista.
2. Femklasschemat och trösklarna kommer ur spec v5; matchregeln ordagrant ur
   spec v2 (se `MATCHREGEL_V2` och `KLASSER`).
3. Stämpeln läses UR BANDET och jämförs mot en **förseglad per-arm
   proveniensartefakt**. Ingen hårdkodad, ingen unionerad identitetslista —
   det var precis det QA fällde som no-op. Tre led jämförs exakt:
   bandstämpel ↔ artefaktidentitet ↔ levande apply-/grafavläsning.
4. Utdata bär klass (b)-rubrik, `via_teleport`-märkning och fördelningen
   planerade/oplanerade teleportpassager ur PlanTick (`oattesterad` när
   telemetri saknas).
5. Självtestet är inbyggt och **körs automatiskt före varje klassning**.
   Faller det klassas ingenting. En grind som aldrig setts falla är en grön
   lampa, inte en grind.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Kanon (villkor 1) — importeras, kopieras aldrig
# ---------------------------------------------------------------------------

#: sha256 för `reference/ra-room/granskriterier.py`. Kanonfilen rörs inte;
#: den här pinnen gör att instrumentet vägrar om den ändå har ändrats.
KANON_SHA256 = "f19ffd18f75a56c5441dbc90f6ec3df0634be6adbdfb53108e9db0a598764cf9"

#: Standardplats, relativt reporoten. Härledd, inte en bokföringsväg.
KANON_STANDARD = Path(__file__).resolve().parents[2] / "reference" / "ra-room" / "granskriterier.py"


class Cfel(Exception):
    """Instrumentet kan inte lita på sin indata. Alltid ett stopp."""


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for bit in iter(lambda: fh.read(1 << 20), b""):
            h.update(bit)
    return h.hexdigest()


def ladda_kanon(path: Path = KANON_STANDARD):
    """Laddar kanonmodulen och pinnar dess sha256.

    `at_topp` hämtas härifrån i stället för att skrivas av. Skiljer sig
    hashen är antingen kanon ändrad eller fel fil pekad ut — båda är stopp.
    """
    p = Path(path)
    if not p.is_file():
        raise Cfel("kanonfilen saknas: %s" % p)
    sha = _sha256(p)
    if sha != KANON_SHA256:
        raise Cfel(
            "kanonfilen %s har sha256 %s, pinnen säger %s — instrumentet "
            "vägrar mäta mot en kanon det inte känner igen" % (p, sha, KANON_SHA256)
        )
    import importlib.util

    spec = importlib.util.spec_from_file_location("_c_kanon", p)
    mod = importlib.util.module_from_spec(spec)
    # Villkor 1 gäller ÄVEN oavsiktligt: utan det här skriver importen en
    # `__pycache__/` i kanonkatalogen, och «kanonfilerna orörda» blir en
    # katalog som instrumentet självt smutsat ner.
    _bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = _bytecode
    for namn in ("at_topp", "dxy"):
        if not hasattr(mod, namn):
            raise Cfel("kanonfilen saknar %s" % namn)
    return mod


# ---------------------------------------------------------------------------
# Matchregeln (villkor 2) — ordagrant ur spec v2, K-c
# ---------------------------------------------------------------------------

MATCHREGEL_V2 = """\
en diskontinuitet klassas `via_teleport` endast om SAMTLIGA: startpunkt <= 30 u
från cell 4633-origin [-536,-448,-16] · landning <= 20 u från [224,-320,75] ·
steglängd 776 +- 10 u · riktning 4633->1330 (aldrig omvänd) · bandet bär
grafstämpel vars hash är en registrerad kombi-identitet. Allt annat = okänd
diskontinuitet = kasseras."""

CELL_START = (-536.0, -448.0, -16.0)  # cell 4633-origin
CELL_LAND = (224.0, -320.0, 75.0)     # cell 1330
TOL_START = 30.0
TOL_LAND = 20.0
STEP_NOM = 776.0
STEP_TOL = 10.0
STEP_LO = STEP_NOM - STEP_TOL
STEP_HI = STEP_NOM + STEP_TOL

#: Kanonfacitets rörelsetak. Ur v5: `max_steg <= 300` — kontinuerligt hela
#: vägen, respektive före och efter hoppet var för sig.
MAX_STEG = 300.0

#: Generisk kandidatdetektor: «här finns en diskontinuitet». Skild från
#: matchregeln, som avgör VILKEN klass den får. Satt långt över normal
#: tick-till-tick-rörelse så att inga kandidater missas.
DISKONTINUITET_U = 150.0

# ---------------------------------------------------------------------------
# Femklasschemat och trösklarna (villkor 2) — ur spec v5, bindning 1
# ---------------------------------------------------------------------------

KLASSER = (
    "framme_via_teleport",
    "framme_kontinuerlig",
    "miss",
    "okand_diskontinuitet",
    "ogiltig",
)

N_FAST = 20
KVALIFICERAD_MIN = 19   # framme_via_teleport + framme_kontinuerlig >= 19/20
MISS_UNDERKAND = 2      # >= 2 miss => UNDERKÄND
OKAND_UNDERKAND = 1     # >= 1 okand_diskontinuitet => UNDERKÄND
OGILTIG_OMATT = 1       # >= 1 ogiltig => armen OMÄTT (instrumentfel, inte botfel)

RUBRIK_B = "klass (b) — omdefinierad teleportmätning «teleport+ring→topp»"

CAVEAT = (
    "C-talet är fysisk «teleport+ring→topp». Det är INTE bevis för att "
    "planeraren valde teleportlänken och INTE bevis för att västrutten "
    "exerceras."
)


def dist3(a, b) -> float:
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))


# ---------------------------------------------------------------------------
# Proveniens (villkor 3 / Sols villkor 5)
# ---------------------------------------------------------------------------

def las_proveniens(path, vantad_sha: Optional[str] = None) -> dict:
    """Läser den förseglade per-arm proveniensartefakten.

    Förseglingen har två lager, och de fångar olika saker:
      * sidofilen `<artefakt>.sha256` fångar oavsiktlig drift,
      * `--proveniens-sha256` (pinnen i kvittot) fångar avsiktligt byte,
        eftersom den kommer utifrån filparet.
    Saknas sidofilen vägras artefakten — en oförseglad artefakt är en
    identitetslista vem som helst kan skriva, alltså precis det villkor 5
    förbjuder.
    """
    p = Path(path)
    if not p.is_file():
        raise Cfel("proveniensartefakten saknas: %s" % p)
    sidofil = p.with_name(p.name + ".sha256")
    try:
        forseglad = sidofil.read_text(encoding="utf-8").split()[0].lower()
    except (OSError, IndexError):
        # EN grind, inte tva som doljer varandra: saknad, oläslig och tom
        # sidofil ar samma sak — artefakten ar oforseglad, och en oforseglad
        # artefakt ar en identitetslista vem som helst kan skriva.
        raise Cfel(
            "proveniensartefakten %s är oförseglad (ingen läsbar %s) — en "
            "oförseglad artefakt är ingen artefakt" % (p, sidofil.name)
        )
    sha = _sha256(p)
    if sha != forseglad:
        raise Cfel(
            "proveniensartefakten %s bryter sitt sigill: sha256 %s, sidofilen "
            "säger %s" % (p, sha, forseglad)
        )
    if vantad_sha and sha != vantad_sha.lower():
        raise Cfel(
            "proveniensartefakten %s har sha256 %s, den pinnade säger %s — "
            "artefakten är utbytt" % (p, sha, vantad_sha.lower())
        )

    obj = json.loads(p.read_text(encoding="utf-8"))
    ident = obj.get("graph_content_hash")
    if not isinstance(ident, str) or len(ident) != 64 or any(
        c not in "0123456789abcdef" for c in ident.lower()
    ):
        raise Cfel(
            "proveniensartefaktens graph_content_hash är inte 64 hex: %r" % (ident,)
        )
    if not obj.get("arm"):
        raise Cfel("proveniensartefakten saknar arm-namn")
    obj["graph_content_hash"] = ident.lower()
    obj["_artefakt_sha256"] = sha
    return obj


def identitetsdom(bandstampel: Optional[str], artefakt_id: str, levande_graf: Optional[str]) -> dict:
    """Jämför de TRE leden exakt (Sols villkor 5).

    Ingen union, ingen delmängd, ingen «minst ett stämmer». Alla tre ska
    vara samma sträng, annars är bandet `ogiltig`.

    `levande_graf` är den levande apply-/grafavläsningen. Den kommer utifrån
    (riggsätet läser den) och har medvetet ingen default: saknas den finns
    inget tredje led att jämföra, och då är domen inte «ok» utan «ogiltig».
    """
    led = {
        "bandstampel": (bandstampel or "").lower() or None,
        "artefaktidentitet": artefakt_id.lower(),
        "levande_graf": (levande_graf or "").lower() or None,
    }
    saknas = [k for k, v in led.items() if v is None]
    if saknas:
        return {
            "ok": False,
            "skal": "led saknas: %s" % ", ".join(sorted(saknas)),
            "led": led,
        }
    if led["bandstampel"] != led["artefaktidentitet"]:
        return {
            "ok": False,
            "skal": "bandstämpel != artefaktidentitet",
            "led": led,
        }
    if led["levande_graf"] != led["artefaktidentitet"]:
        return {
            "ok": False,
            "skal": "levande grafavläsning != artefaktidentitet",
            "led": led,
        }
    return {"ok": True, "skal": "tre led lika", "led": led}


# ---------------------------------------------------------------------------
# Bandinläsning
# ---------------------------------------------------------------------------

def _stampel_sidofil(path: Path) -> Optional[str]:
    sidofil = path.with_name(path.name + ".graf_stamp.json")
    if not sidofil.exists():
        return None
    try:
        obj = json.loads(sidofil.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return obj.get("graph_content_hash")


def las_band(path) -> dict:
    """Läser ett JSONL-band. Rör aldrig originalfiler — läser bara."""
    path = Path(path)
    rader = [r for r in path.read_text(encoding="utf-8").splitlines() if r.strip()]
    ticks = []
    meta = None
    stampel = _stampel_sidofil(path)
    label = None

    for i, rad in enumerate(rader):
        row = json.loads(rad)
        if i == 0 and "players" not in row and row.get("_meta"):
            meta = row
            label = row.get("_label")
            gs = row.get("_graf_stamp") or {}
            if stampel is None:
                stampel = gs.get("graph_content_hash")
            continue
        origin = row["players"][0]["origin"]
        ticks.append((row.get("t"), tuple(float(v) for v in origin)))

    ticks.sort(key=lambda tv: (tv[0] if tv[0] is not None else 0))
    return {
        "ticks": ticks,
        "stampel": stampel,
        "meta": meta,
        "label": label,
        "path": str(path),
    }


def hitta_diskontinuiteter(ticks, trosk: float = DISKONTINUITET_U):
    ut = []
    for i in range(len(ticks) - 1):
        _, a = ticks[i]
        _, b = ticks[i + 1]
        steg = dist3(a, b)
        if steg > trosk:
            ut.append({"idx": i, "a": a, "b": b, "steg_u": round(steg, 3)})
    return ut


def max_steg(ticks, i0: int = 0, i1: Optional[int] = None) -> float:
    """Största tick-till-tick-steget i [i0, i1). Tomt intervall => 0."""
    i1 = len(ticks) if i1 is None else i1
    varden = [
        dist3(ticks[i][1], ticks[i + 1][1]) for i in range(i0, min(i1, len(ticks)) - 1)
    ]
    return max(varden) if varden else 0.0


# ---------------------------------------------------------------------------
# Matchregeln, kriterium för kriterium
# ---------------------------------------------------------------------------

def matcha_hopp(a, b) -> dict:
    """Matchregelns GEOMETRI (kriterium 1–4) på paret a→b i TICKORDNING.

    Stämpeln (kriterium 5) är identitetsledet och prövas separat — annars
    hade ett stämpelfel sett ut som ett geometrifel.

    Riktningen («4633→1330, aldrig omvänd») ligger i att paret prövas
    ordnat: `a` mäts mot munnen och `b` mot landningen, aldrig tvärtom. Ett
    omvänt band faller därför på kriterium 1 och 2.

    Prototypen bar här en extra `not reversed_pair`-term. Den var **död**:
    för att den skulle slå måste `a` ligga inom 20 u från landningen, och då
    är `a` 776 u från munnen, så kriterium 1 hade redan fällt paret. En term
    som aldrig kan ändra ett utfall är ingen kontroll — den är en grön lampa.
    Riktningen bevakas i stället av ett eget testfall (`ix_omvand`), och den
    mutation som gör matchningen ordningsokänslig fälls av det.
    """
    start_dist = dist3(a, CELL_START)
    land_dist = dist3(b, CELL_LAND)
    steg = dist3(a, b)
    start_ok = start_dist <= TOL_START      # 1. a vid teleportmunnen
    land_ok = land_dist <= TOL_LAND         # 2. b vid landningen
    steg_ok = STEP_LO <= steg <= STEP_HI    # 3. steglängd 776 ± 10
    # Kriterium 4 (riktning) ar STRUKTURELLT uppfyllt: `a` mats mot munnen
    # och `b` mot landningen, aldrig tvartom. Prototypen bar har ett falt
    # `riktning_ok = start_ok and land_ok` — en tautologisk omskrivning av
    # kriterium 1 och 2 som utgav sig for att rapportera kriterium 4. QA
    # muterade det: hardsatt True fallde inget test, struket ur geometri_ok
    # fallde inget test. Ett dekorativt falt ar samma grona lampa som en dod
    # term, sa det ar borta. Riktningen bevakas av `ix_omvand`.
    return {
        "start_dist_u": round(start_dist, 3),
        "start_ok": start_ok,
        "land_dist_u": round(land_dist, 3),
        "land_ok": land_ok,
        "steg_u": round(steg, 3),
        "steg_ok": steg_ok,
        "riktning_struktur": "ordnat par: a mot munnen, b mot landningen",
        "geometri_ok": start_ok and land_ok and steg_ok,
        "a": a,
        "b": b,
    }


# ---------------------------------------------------------------------------
# PlanTick — planerade/oplanerade teleportpassager (villkor 4)
# ---------------------------------------------------------------------------

TELEPORT_KIND = "Teleport"          # LinkKind:s Debug-namn, i ev.kind
PLANTICK_KIND = "PlanTick"          # radens TOPPNIVA-kind: hondelsetypen

#: Toppnivans `kind` ar HANDELSETYPEN (`_capture_start`, `PlanTick`,
#: `_capture_slut`) — aldrig ett LinkKind. Lankens kind och raknaren bor i
#: `ev`. Att lasa toppnivan ger darfor alltid 0 teleportval, vilket lases
#: som «planeraren valde aldrig teleportlanken» — exakt den tolkning Sols
#: rev2 faller. Ett falskt 0 ar varre an ett saknat tal.
EV_NYCKEL = "ev"


def _plantick_handelser(rader):
    """Plockar ut PlanTick-handelserna och returnerar deras `ev`-objekt.

    Vagrar hellre an gissar: en rad som utger sig for att vara en PlanTick
    men saknar `ev` ar ett schemabrott, inte en rad att hoppa over tyst.
    """
    ut = []
    for r in rader:
        if r.get("kind") != PLANTICK_KIND:
            continue  # _capture_start / _capture_slut
        ev = r.get(EV_NYCKEL)
        if not isinstance(ev, dict):
            raise Cfel(
                "PlanTick-rad utan `%s`-objekt — okant schema, och att lasa "
                "toppnivans `kind` i stallet ger tyst 0 teleportval" % EV_NYCKEL
            )
        ut.append(ev)
    return ut


def seq_luckor(handelser) -> dict:
    """Raknar tappade rader per bot. `seq` ar per-bot monoton."""
    per_bot = {}
    for ev in handelser:
        s = ev.get("seq")
        if isinstance(s, int):
            per_bot.setdefault(ev.get("bot"), []).append(s)
    n_luckor = 0
    tappade = 0
    for seqs in per_bot.values():
        seqs.sort()
        for x, y in zip(seqs, seqs[1:]):
            if y - x > 1:
                n_luckor += 1
                tappade += y - x - 1
    return {"n_luckor": n_luckor, "tappade_rader": tappade}


def plantick_fordelning(plantick_rader, bandfonster=None) -> dict:
    """Delar TELEPORTPASSAGERNA i planerade/oplanerade — per BAND, inte per rad.

    `bandfonster` ar [(namn, t_lo, t_hi), ...] i simtid. Ett band raknas som
    PLANERAT om planeraren valde teleportlanken (`ev.kind == "Teleport"`)
    nagon gang inom bandets fonster, annars OPLANERAT. Fordelningen ar over
    de N banden — antalet rader i strommen ar inte en fordelning.

    Saknas strommen ar svaret `oattesterad` — aldrig 0, aldrig en gissning.
    `docs/plan-telemetry.md`: en saknad strom far inte lasas som franvaro av
    planerarval, och ett valt ben far aldrig harledas ur positioner.
    """
    if plantick_rader is None:
        return {"attesterad": False, "planerade": None, "oplanerade": None,
                "status": "oattesterad", "n_teleportval": None,
                "seq_luckor": None, "tappade_rader": None, "per_band": None}

    handelser = _plantick_handelser(plantick_rader)
    teleport_t = [
        ev["t"] for ev in handelser
        if ev.get("kind") == TELEPORT_KIND and isinstance(ev.get("t"), (int, float))
    ]
    luckor = seq_luckor(handelser)

    if not bandfonster:
        # Utan bandfonster finns ingen fordelning att gora. Att da svara
        # "0 planerade" vore att pasta nagot vi inte matt.
        return {"attesterad": True, "planerade": None, "oplanerade": None,
                "status": "utan_bandfonster", "n_teleportval": len(teleport_t),
                "seq_luckor": luckor["n_luckor"],
                "tappade_rader": luckor["tappade_rader"], "per_band": None}

    per_band = []
    planerade = 0
    for namn, lo, hi in bandfonster:
        n = sum(1 for tt in teleport_t if lo <= tt <= hi)
        per_band.append({"band": namn, "t_lo": round(lo, 2), "t_hi": round(hi, 2),
                         "teleportval": n, "planerad": bool(n)})
        if n:
            planerade += 1
    return {
        "attesterad": True,
        "planerade": planerade,
        "oplanerade": len(bandfonster) - planerade,
        "status": "attesterad",
        "n_teleportval": len(teleport_t),
        "seq_luckor": luckor["n_luckor"],
        "tappade_rader": luckor["tappade_rader"],
        "per_band": per_band,
    }


# ---------------------------------------------------------------------------
# Femklassningen
# ---------------------------------------------------------------------------

def klassificera_band(
    path,
    artefakt_id: str,
    levande_graf: Optional[str],
    kanon,
    trosk: float = DISKONTINUITET_U,
) -> dict:
    """Klassar ETT band som exakt en av `KLASSER`."""
    band = las_band(path)
    ticks = band["ticks"]

    dom = identitetsdom(band["stampel"], artefakt_id, levande_graf)
    if not dom["ok"]:
        # Stämpelfel är instrumentfel, inte botfel (v5). Bandet bokförs,
        # men det ger inget C-utfall.
        return {
            "path": band["path"],
            "label": band["label"],
            "klass": "ogiltig",
            "skal": dom["skal"],
            "identitet": dom,
            "via_teleport": False,
            "t_fonster": None,
        }

    if not ticks:
        return {
            "path": band["path"], "label": band["label"], "klass": "miss",
            "skal": "bandet har inga ticks", "identitet": dom, "via_teleport": False,
            "t_fonster": None,
        }

    tider = [tv[0] for tv in ticks if isinstance(tv[0], (int, float))]
    t_fonster = (min(tider), max(tider)) if tider else None
    framme = any(kanon.at_topp(o) for _, o in ticks)
    kandidater = hitta_diskontinuiteter(ticks, trosk=trosk)

    if not kandidater:
        steg = max_steg(ticks)
        if framme and steg <= MAX_STEG:
            klass, skal = "framme_kontinuerlig", "kanonfacit, max_steg <= %g" % MAX_STEG
        else:
            klass = "miss"
            skal = "nådde inte toppen" if not framme else "max_steg %.1f > %g" % (steg, MAX_STEG)
        return {
            "path": band["path"], "label": band["label"], "klass": klass, "skal": skal,
            "identitet": dom, "max_steg_u": round(steg, 3), "framme": framme,
            "n_kandidater": 0, "kandidater": [], "via_teleport": False,
            "t_fonster": t_fonster,
        }

    # Diskontinuitet finns: matchregeln avgör om den är kartteleporten.
    evald = []
    trafficad = None
    for c in kandidater:
        m = matcha_hopp(c["a"], c["b"]) | {"idx": c["idx"]}
        # Före/efter hoppet var för sig (v5), inte hela bandet.
        m["max_steg_fore_u"] = round(max_steg(ticks, 0, c["idx"] + 1), 3)
        m["max_steg_efter_u"] = round(max_steg(ticks, c["idx"] + 1), 3)
        m["steg_krav_ok"] = (
            m["max_steg_fore_u"] <= MAX_STEG and m["max_steg_efter_u"] <= MAX_STEG
        )
        evald.append(m)
        if m["geometri_ok"] and m["steg_krav_ok"] and trafficad is None:
            trafficad = m

    if trafficad is not None and framme:
        klass, skal = "framme_via_teleport", "matchregeln + at_topp + max_steg före/efter"
    elif trafficad is not None and not framme:
        klass, skal = "miss", "matchande teleport men nådde aldrig toppen"
    else:
        klass, skal = "okand_diskontinuitet", "diskontinuitet utan matchande teleport"

    return {
        "path": band["path"], "label": band["label"], "klass": klass, "skal": skal,
        "identitet": dom, "framme": framme, "n_kandidater": len(kandidater),
        "kandidater": evald, "via_teleport": klass == "framme_via_teleport",
        "t_fonster": t_fonster,
    }


def domsluta_arm(klasser: list) -> dict:
    """Armens utfall ur v5:s trösklar. Räknar i SAMMA N, ingen exkludering."""
    n = len(klasser)
    r = {k: klasser.count(k) for k in KLASSER}
    framme = r["framme_via_teleport"] + r["framme_kontinuerlig"]
    if r["ogiltig"] >= OGILTIG_OMATT:
        dom = "OMATT"
        skal = "%d ogiltig (stämpelfel = instrumentfel, inte botfel)" % r["ogiltig"]
    elif r["miss"] >= MISS_UNDERKAND or r["okand_diskontinuitet"] >= OKAND_UNDERKAND:
        dom = "UNDERKAND"
        skal = "%d miss, %d okand_diskontinuitet" % (r["miss"], r["okand_diskontinuitet"])
    elif framme >= KVALIFICERAD_MIN and n == N_FAST:
        dom = "KVALIFICERAD"
        skal = "framme %d/%d >= %d, ogiltig 0, okand 0" % (framme, n, KVALIFICERAD_MIN)
    else:
        dom = "UNDERKAND"
        skal = "framme %d/%d < %d (N=%d)" % (framme, n, KVALIFICERAD_MIN, n)
    return {"n": n, "raknare": r, "framme": framme, "dom": dom, "skal": skal}


def rapportera(arm: str, banddomar: list, plantick: dict, artefakt: dict) -> dict:
    """Bygger C-resultatet med alla etiketter villkor 4 kräver."""
    armdom = domsluta_arm([b["klass"] for b in banddomar])
    return {
        "rubrik": RUBRIK_B,
        "arm": arm,
        "via_teleport_markning": "via_teleport",
        "n_via_teleport": armdom["raknare"]["framme_via_teleport"],
        "teleportpassager": plantick,
        "armdom": armdom,
        "proveniens": {
            "arm": artefakt.get("arm"),
            "graph_content_hash": artefakt["graph_content_hash"],
            "artefakt_sha256": artefakt["_artefakt_sha256"],
        },
        "matchregel_v2": MATCHREGEL_V2,
        "caveat": CAVEAT,
        "band": banddomar,
    }


def skriv_text(rap: dict) -> str:
    p = rap["teleportpassager"]
    if p["status"] == "oattesterad":
        fordelning = "teleportpassager: OATTESTERAD (ingen PlanTick-ström)"
    elif p["status"] == "utan_bandfonster":
        fordelning = ("teleportpassager: EJ FÖRDELAD (inga bandfönster att "
                      "joina mot; %d teleportval i strömmen)" % p["n_teleportval"])
    else:
        fordelning = "teleportpassager: %d planerade / %d oplanerade" % (
            p["planerade"], p["oplanerade"]
        )
    if p.get("seq_luckor"):
        fordelning += "\n  OBS: %d seq-luckor = %d tappade rader — strömmen är ofullständig" % (
            p["seq_luckor"], p["tappade_rader"]
        )
    a = rap["armdom"]
    return "\n".join([
        rap["rubrik"],
        "arm %s · märkning: %s · %d band via_teleport" % (
            rap["arm"], rap["via_teleport_markning"], rap["n_via_teleport"]
        ),
        fordelning,
        "utfall: %s — %s" % (a["dom"], a["skal"]),
        "räknare: " + "  ".join("%s=%d" % (k, a["raknare"][k]) for k in KLASSER),
        "proveniens: %s @ %s… (artefakt sha %s…)" % (
            rap["proveniens"]["arm"],
            rap["proveniens"]["graph_content_hash"][:12],
            rap["proveniens"]["artefakt_sha256"][:12],
        ),
        rap["caveat"],
    ])


# ---------------------------------------------------------------------------
# Självtest (villkor 5) — (i)–(vii) + de två artefakt-negativkontrollerna
# ---------------------------------------------------------------------------

#: Uppenbart syntetisk stampel for sjalvtestet. Far ALDRIG vara en riktig
#: armidentitet: en grep efter ARM-R:s identitet ska inte ge traff inne i
#: instrumentet — det ar forvaxlingsbart med just den registerlista villkor 5
#: forbjuder, och en riktig identitet har blir inaktuell-men-gron om armen
#: nagonsin pinnas om. Vardet ar godtyckligt; sjalvtestet beror inte pa det.
SYNT_STAMPEL = "deadbeef" * 8  # 64 hex, uppenbart pahittad


def _band(tmp: Path, namn: str, punkter, stampel: Optional[str]) -> Path:
    """Skriver ett syntetiskt band. `punkter` = lista av (t, (x,y,z))."""
    p = tmp / namn
    rader = []
    if stampel is not None:
        rader.append(json.dumps({
            "_meta": True, "_label": namn,
            "_graf_stamp": {"graph_content_hash": stampel},
        }))
    for t, o in punkter:
        rader.append(json.dumps({"t": t, "players": [{"origin": list(o)}]}))
    p.write_text("\n".join(rader) + "\n", encoding="utf-8")
    return p


def _rakt_till_toppen(start, slut, n=12):
    """Kontinuerlig bana med små steg, slutar på RA-toppen."""
    ut = []
    for i in range(n + 1):
        f = i / n
        ut.append((i, tuple(s + (e - s) * f for s, e in zip(start, slut))))
    return ut


def _vinkelratt(mag: float) -> tuple:
    """En forskjutning med langden `mag` VINKELRATT mot hoppriktningen.

    Poangen: da ar steglangden invariant sa nar som pa andra ordningen
    (sqrt(|v|^2 + mag^2)), och toleransfallet provar sitt eget kriterium i
    stallet for att fallas av steg-kriteriet. QA:s anmarkning B2: de forra
    forskjutningarna var AXELRIKTADE, inte vinkelrata — (iii) lag 3,4 u fran
    steg-intervallets ovre grans och hade tyst borjat prova fel kriterium om
    STEP_TOL nagonsin snavades at.

    Harledd ur konstanterna, aldrig avskriven som siffror.
    """
    v = tuple(b - a for a, b in zip(CELL_START, CELL_LAND))
    pv = (v[1], -v[0], 0.0)                       # v x (0,0,1): vinkelrat mot v
    n = math.sqrt(sum(x * x for x in pv))
    return tuple(x / n * mag for x in pv)


def _teleportband(start_off=(0, 0, 0), land_off=(0, 0, 0), stampel=SYNT_STAMPEL, namn="b.jsonl", tmp=None):
    """Band som går fram till teleportmunnen, hoppar, och når toppen."""
    a = tuple(c + d for c, d in zip(CELL_START, start_off))
    b = tuple(c + d for c, d in zip(CELL_LAND, land_off))
    fore = _rakt_till_toppen((a[0] - 60, a[1], a[2]), a, n=6)
    efter = _rakt_till_toppen(b, (250.0, -703.0, 328.0), n=10)
    punkter = fore + [(len(fore) + i, o) for i, (_, o) in enumerate(efter)]
    return _band(tmp, namn, punkter, stampel)


def sjalvtest(verbose: bool = False) -> dict:
    """Kör hela NK-sviten offline. Returnerar {'ok': bool, 'fall': [...]}"""
    import tempfile

    fall = []

    def utfall(f):
        """Kor en kontroll och gor ett undantag till ett FALLET FALL.

        Ett oväntat undantag i ett fall avbrot forr hela sviten, sa
        mutationsprovet sag "ingen test foll" nar koden i sjalva verket
        kraschade. En krasch ar ett utfall, inte en avbruten matning.
        """
        try:
            return f()
        except Cfel as exc:
            return "VAGRAD"
        except Exception as exc:  # noqa: BLE001 — avsiktligt brett
            return "KRASCH:%s" % type(exc).__name__

    def kolla(namn, fick, vantat):
        ok = fick == vantat
        fall.append({"fall": namn, "vantat": vantat, "fick": fick, "ok": ok})
        if verbose:
            print("  (%s) vantat=%-22s fick=%-22s -> %s"
                  % (namn, vantat, fick, "OK" if ok else "FEL"))
        return ok

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        kanon = ladda_kanon()
        ident = SYNT_STAMPEL

        # --- artefakten sjalv ---
        art_p = tmp / "arm-prov.json"
        art_p.write_text(json.dumps({"arm": "ARM-PROV", "graph_content_hash": ident}), encoding="utf-8")
        (tmp / "arm-prov.json.sha256").write_text(_sha256(art_p) + "  arm-prov.json\n", encoding="utf-8")
        artefakt = las_proveniens(art_p)
        kolla("artefakt-lasbar", artefakt["graph_content_hash"], ident)

        def klass(p, levande=ident, aid=ident):
            return klassificera_band(p, aid, levande, kanon)["klass"]

        # (i) syntetiskt hopp med annan bana -> FALLS
        p = _teleportband(start_off=(900, 900, 0), land_off=(900, 900, 0), tmp=tmp, namn="i.jsonl")
        kolla("i_annan_bana", klass(p), "okand_diskontinuitet")

        # (ii) start 40 u fran 4633 -> FALLS.
        # Forskjutningen ar VINKELRATT mot hoppriktningen sa att steglangden
        # stannar inom 776+-10; annars hade steg-kriteriet fallt bandet och
        # fallet hade inte provat kriterium 1 alls.
        p = _teleportband(start_off=_vinkelratt(40.0), tmp=tmp, namn="ii.jsonl")
        kolla("ii_start_40u", klass(p), "okand_diskontinuitet")

        # (iii) landning 35 u fran 1330 -> FALLS. Vinkelratt, av samma skal
        # som (ii): kriterium 2 ska falla bandet ensamt.
        p = _teleportband(land_off=_vinkelratt(35.0), tmp=tmp, namn="iii.jsonl")
        kolla("iii_landning_35u", klass(p), "okand_diskontinuitet")

        # (iv) ratt hopp UTAN grafstampel -> ogiltig (identitetsledet saknas)
        p = _teleportband(stampel=None, tmp=tmp, namn="iv.jsonl")
        kolla("iv_utan_stampel", klass(p), "ogiltig")

        # (v) kontinuerligt band utan diskontinuitet -> framme_kontinuerlig
        p = _band(tmp, "v.jsonl", _rakt_till_toppen((200.0, -650.0, 300.0), (250.0, -703.0, 328.0)), ident)
        kolla("v_kontinuerlig", klass(p), "framme_kontinuerlig")

        # (vi) positivkontroll: exakt matchande teleport -> framme_via_teleport
        p = _teleportband(tmp=tmp, namn="vi.jsonl")
        kolla("vi_positivkontroll", klass(p), "framme_via_teleport")

        # (vii) landning forskjuten -> FALLS
        p = _teleportband(land_off=(0, 0, 60), tmp=tmp, namn="vii.jsonl")
        kolla("vii_landning_forskjuten", klass(p), "okand_diskontinuitet")

        # (viii) STEGLANGDEN ensam ur intervallet. Start 30 u FRAN landningen
        # sett, sa kriterium 1 och 2 haller bada men steget blir ~806 u.
        # Utan det har fallet vore steg-toleransen otestad: varje annat fall
        # bryter start eller landning samtidigt.
        p = _teleportband(start_off=(-30, 0, 0), tmp=tmp, namn="viii.jsonl")
        kolla("viii_steg_utanfor", klass(p), "okand_diskontinuitet")

        # Marginalen ar en assertion, inte en forhoppning: bryts den provar
        # toleransfallen fel kriterium igen, och mutationsprovet skulle inte
        # marka det (dess mutation VIDGAR toleransen).
        for _mag, _namn, _vem in ((40.0, "ii", "start"), (35.0, "iii", "land")):
            _d = _vinkelratt(_mag)
            if _vem == "start":
                _a = tuple(c + o for c, o in zip(CELL_START, _d)); _b = CELL_LAND
            else:
                _a = CELL_START; _b = tuple(c + o for c, o in zip(CELL_LAND, _d))
            _steg = dist3(_a, _b)   # DET VERKLIGA bandets steg
            kolla("marginal_%s" % _namn, [round(STEP_HI - _steg, 1) >= 5.0], [True])

        # (ix) OMVANT band: munnen och landningen byter plats i tiden.
        # Bevakar riktningskriteriet; faller den ordnade matchningen bort
        # (ordningsokanslig mutation) klassas det har bandet fel.
        a = CELL_START
        b = CELL_LAND
        omv = [(0, (b[0] - 40, b[1], b[2])), (1, b), (2, a)]
        omv += [(3 + i, o) for i, (_, o) in enumerate(
            _rakt_till_toppen(a, (250.0, -703.0, 328.0), n=10))]
        p = _band(tmp, "ix.jsonl", omv, ident)
        kolla("ix_omvand", klass(p), "okand_diskontinuitet")

        # (x) matchande teleport men boten nar ALDRIG toppen -> miss.
        # Bevakar at_topp-kravet ur v5.
        a2 = CELL_START
        b2 = CELL_LAND
        pkt = [(0, (a2[0] - 60, a2[1], a2[2])), (1, a2), (2, b2), (3, (b2[0] + 10, b2[1], b2[2]))]
        p = _band(tmp, "x.jsonl", pkt, ident)
        kolla("x_teleport_utan_topp", klass(p), "miss")

        # (xi) matchande teleport, men ett steg > 300 u EFTER hoppet.
        # Bevakar max_steg-kravet fore/efter var for sig.
        efter = [(3, b2), (4, (b2[0], b2[1] + 400.0, b2[2]))]
        efter += [(5 + i, o) for i, (_, o) in enumerate(
            _rakt_till_toppen((b2[0], b2[1] + 400.0, b2[2]), (250.0, -703.0, 328.0), n=8))]
        pkt = [(0, (a2[0] - 60, a2[1], a2[2])), (1, a2), (2, b2)] + efter
        p = _band(tmp, "xi.jsonl", pkt, ident)
        kolla("xi_max_steg_efter", klass(p), "okand_diskontinuitet")

        # --- Sols tva artefakt-negativkontroller (villkor 5) ---
        # NK-A: FEL ARTEFAKTIDENTITET. Bandet och den levande grafen ar
        # riktiga; artefakten pekar pa nagot annat => bandet ogiltigt.
        p = _teleportband(tmp=tmp, namn="nk_a.jsonl")
        kolla("NK-A_fel_artefaktidentitet", klass(p, levande=ident, aid="b" * 64), "ogiltig")

        # NK-B: FEL BANDSTAMPEL. Artefakten och den levande grafen ar
        # riktiga; bandet bar en annan stampel => bandet ogiltigt.
        p = _teleportband(stampel="c" * 64, tmp=tmp, namn="nk_b.jsonl")
        kolla("NK-B_fel_bandstampel", klass(p), "ogiltig")

        # NK-C: fel LEVANDE grafavlasning => ogiltig (tredje ledet)
        p = _teleportband(tmp=tmp, namn="nk_c.jsonl")
        kolla("NK-C_fel_levande_graf", klass(p, levande="d" * 64), "ogiltig")

        # NK-D: oforseglad artefakt vagras
        naken = tmp / "naken.json"
        naken.write_text(json.dumps({"arm": "X", "graph_content_hash": ident}), encoding="utf-8")
        try:
            las_proveniens(naken)
            kolla("NK-D_oforseglad_artefakt", "accepterad", "vagrad")
        except Cfel:
            kolla("NK-D_oforseglad_artefakt", "vagrad", "vagrad")

        # NK-E: brutet sigill vagras
        art_p.write_text(json.dumps({"arm": "ARM-PROV", "graph_content_hash": "e" * 64}), encoding="utf-8")
        try:
            las_proveniens(art_p)
            kolla("NK-E_brutet_sigill", "accepterad", "vagrad")
        except Cfel:
            kolla("NK-E_brutet_sigill", "vagrad", "vagrad")

        # NK-H: fel kanonfil vagras (kanonpinnen).
        falsk_kanon = tmp / "granskriterier.py"
        falsk_kanon.write_text(
            "def at_topp(o):\n    return True\ndef dxy(o, p):\n    return 0.0\n",
            encoding="utf-8")
        try:
            ladda_kanon(falsk_kanon)
            kolla("NK-H_fel_kanon", "accepterad", "vagrad")
        except Cfel:
            kolla("NK-H_fel_kanon", "vagrad", "vagrad")

        # NK-J: villkor 1 bokstavligt — inlasningen far inte smutsa ner
        # kanonkatalogen. En `__pycache__/` dar ar en rord kanonkatalog.
        #
        # Provas mot en KOPIA i egen katalog, inte mot den riktiga: kanon ar
        # redan inlast har ovan, sa en matning mot den riktiga katalogen hade
        # jamfort tva lagen efter smutsen och sett ren ut oavsett. Kopian har
        # samma sha256, sa kanonpinnen godtar den.
        kanon_kopia_dir = tmp / "kanonkopia"
        kanon_kopia_dir.mkdir()
        kanon_kopia = kanon_kopia_dir / "granskriterier.py"
        kanon_kopia.write_bytes(Path(KANON_STANDARD).read_bytes())
        fore = {q.name for q in kanon_kopia_dir.iterdir()}
        ladda_kanon(kanon_kopia)
        efter = {q.name for q in kanon_kopia_dir.iterdir()}
        kolla("NK-J_kanonkatalog_orord", sorted(efter - fore), [])

        # NK-I: fail-closed-grinden. Med ett rott sjalvtest far ingenting
        # klassas - grinden ar det som gor instrumentet oanvandbart oprovat.
        global _SJALVTEST_GRON
        _sparat = _SJALVTEST_GRON
        try:
            _SJALVTEST_GRON = False
            kras_om_sjalvtestet_inte_ar_gront()
            kolla("NK-I_fail_closed", "slapptes igenom", "vagrad")
        except Cfel:
            kolla("NK-I_fail_closed", "vagrad", "vagrad")
        finally:
            _SJALVTEST_GRON = _sparat

        # --- PlanTick: riggens VERKLIGA radform ---
        #
        # Den forra fixturen har var en pahittad platt form
        # ({"kind":"Teleport","seq":1}) som inte finns i nagon riggstrom. Den
        # last fast en parser som laser toppnivans `kind` — dar bara
        # handelsetypen bor — och AVVISADE rattningen. En fixtur byggd pa ett
        # uppdiktat schema gor buggen barande; grinden var inverterad.
        # Formen nedan ar tagen ur riggblock-plantick-bunten.
        def _rad(kind, seq, tid, bot=1):
            return {"kind": "PlanTick", "wall": 1787546217.6 + tid,
                    "ev": {"schema": "qw-nav-graph/1", "bot": bot, "t": tid,
                           "seq": seq, "kind": kind, "link": 4294967295}}

        strom = (
            [{"kind": "_capture_start", "wall": 1787546217.6, "port": 27970}]
            + [_rad("Walk", 100, 10.0), _rad(TELEPORT_KIND, 101, 10.5),
               _rad("JumpGap", 102, 11.0),          # band A: har teleportval
               _rad("Walk", 103, 20.0), _rad("Step", 105, 20.5)]  # band B: inget, + lucka
            + [{"kind": "_capture_slut", "wall": 1787546611.5, "n_plan_events": 5}]
        )
        fonster = [("A", 9.0, 12.0), ("B", 19.0, 22.0)]

        kolla("NK-F_plantick_saknas", utfall(lambda: plantick_fordelning(None)["status"]), "oattesterad")

        f = lambda: plantick_fordelning(strom, fonster)  # noqa: E731
        # NK-G: ratt niva, ratt join. En parser som laser toppnivans `kind`
        # far HAR 0 planerade, sa det har fallet faller den.
        kolla("NK-G_plantick_ratt_niva",
              utfall(lambda: [f()["planerade"], f()["oplanerade"]]), [1, 1])
        kolla("NK-G2_seq_luckor",
              utfall(lambda: [f()["seq_luckor"], f()["tappade_rader"]]), [1, 1])

        # NK-K: LOCKBETE pa toppnivan. Toppnivans kind sager "Teleport" men
        # lanken ar en Walk. En parser som laser fel niva raknar den som
        # planerad; ratt parser ser att handelsetypen inte ar PlanTick.
        lockbete = [{"kind": TELEPORT_KIND, "wall": 0.0,
                     "ev": {"bot": 1, "t": 10.5, "seq": 200, "kind": "Walk"}}]
        kolla("NK-K_lockbete_toppniva", utfall(
            lambda: plantick_fordelning(lockbete, [("A", 9.0, 12.0)])["planerade"]), 0)

        # NK-L: PlanTick-rad UTAN ev => schemabrott, inte tyst 0.
        kolla("NK-L_plantick_utan_ev", utfall(
            lambda: plantick_fordelning([{"kind": "PlanTick", "wall": 0.0}], fonster)
            and "accepterad"), "VAGRAD")

        # NK-M: teleportval UTANFOR bandets fonster raknas inte till bandet.
        kolla("NK-M_utanfor_fonster", utfall(
            lambda: plantick_fordelning([_rad(TELEPORT_KIND, 300, 99.0)],
                                        [("A", 9.0, 12.0)])["planerade"]), 0)

        # NK-N: strom utan bandfonster far INTE svara "0 planerade".
        kolla("NK-N_utan_bandfonster",
              utfall(lambda: plantick_fordelning(strom, [])["planerade"]), None)

    ok = all(f["ok"] for f in fall)
    return {"ok": ok, "n": len(fall), "fall": fall}


_SJALVTEST_GRON: Optional[bool] = None


def kras_om_sjalvtestet_inte_ar_gront() -> None:
    """Fail-closed (villkor 5): utan grön svit klassas ingenting.

    Sviten är offline och tar millisekunder, så den körs vid första
    användningen i processen i stället för att lita på att någon kört den.
    Ett instrument som får användas oprövat är inget instrument.
    """
    global _SJALVTEST_GRON
    if _SJALVTEST_GRON is None:
        _SJALVTEST_GRON = sjalvtest()["ok"]
    if not _SJALVTEST_GRON:
        raise Cfel(
            "självtestet är inte grönt — instrumentet vägrar klassa. "
            "Kör `fixklass_c.py --sjalvtest` för att se vilket fall som föll."
        )


def klassificera_korpus(
    band_vagar,
    proveniens,
    levande_graf: Optional[str],
    plantick_rader: Optional[list] = None,
    proveniens_sha256: Optional[str] = None,
    kanon_path: Path = KANON_STANDARD,
) -> dict:
    """Instrumentets huvudingång. Kör självtestet först, alltid."""
    kras_om_sjalvtestet_inte_ar_gront()
    kanon = ladda_kanon(kanon_path)
    artefakt = las_proveniens(proveniens, proveniens_sha256)
    ident = artefakt["graph_content_hash"]
    domar = [
        klassificera_band(p, ident, levande_graf, kanon) for p in band_vagar
    ]
    # Fordelningen joinas mot bandens simtidsfonster. Band utan fonster
    # (t.ex. tomma) kan inte joinas och far inte tyst bli "oplanerade".
    bandfonster = [
        (Path(d["path"]).name, d["t_fonster"][0], d["t_fonster"][1])
        for d in domar if d.get("t_fonster")
    ]
    return rapportera(
        artefakt.get("arm", "?"), domar,
        plantick_fordelning(plantick_rader, bandfonster), artefakt,
    )


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sjalvtest", action="store_true", help="kör NK-sviten och stanna")
    ap.add_argument("--proveniens", help="förseglad per-arm proveniensartefakt")
    ap.add_argument(
        "--proveniens-sha256",
        help="pinnad sha256 för artefakten (OBLIGATORISK: sidofilen är "
             "självförseglande, den externa pinnen är det enda som fångar "
             "ett avsiktligt byte)",
    )
    ap.add_argument("--levande-graf", help="graph_content_hash ur den levande apply-/grafavläsningen")
    ap.add_argument("--plantick", help="JSONL med PlanTick-rader; utelämnas => oattesterad")
    ap.add_argument("--kanon", default=str(KANON_STANDARD))
    ap.add_argument("--json", action="store_true", help="skriv hela rapporten som JSON")
    ap.add_argument("band", nargs="*", help="band att klassa")
    args = ap.parse_args(argv)

    if args.sjalvtest:
        print("=== fixklass_c självtest ===")
        r = sjalvtest(verbose=True)
        print("\n%d fall, %s" % (r["n"], "ALLA OK" if r["ok"] else "FALLNA FALL FINNS"))
        return 0 if r["ok"] else 2

    try:
        if not args.band:
            raise Cfel("inga band angivna")
        for flagga, varde in (("--proveniens", args.proveniens),
                              ("--proveniens-sha256", args.proveniens_sha256),
                              ("--levande-graf", args.levande_graf)):
            if not varde:
                raise Cfel(
                    "%s fattas. Identitetsdomen har tre led och kan inte "
                    "avges med tva, och sidofilen ar sjalvforseglande — den "
                    "som andrar artefakten kan skriva om sigillet, sa den "
                    "externa pinnen ar det enda som fangar ett avsiktligt "
                    "byte." % flagga
                )
        plantick = None
        if args.plantick:
            plantick = [
                json.loads(r) for r in Path(args.plantick).read_text(encoding="utf-8").splitlines() if r.strip()
            ]
        rap = klassificera_korpus(
            args.band, args.proveniens, args.levande_graf, plantick,
            args.proveniens_sha256, Path(args.kanon),
        )
    except Cfel as exc:
        print("C-INSTRUMENTET VÄGRAR: %s" % exc, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(rap, indent=1, ensure_ascii=False))
    else:
        print(skriv_text(rap))
        for b in rap["band"]:
            print("  %-22s %s" % (b["klass"], Path(b["path"]).name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
