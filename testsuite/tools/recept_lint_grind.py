#!/usr/bin/env python3
"""Envägsgrinden: recept_lint inkopplad i apply-vägen, inte i minnet.

VARFÖR EN GRIND OCH INTE ETT VERKTYG
------------------------------------
`recept_lint.py` fanns redan och hittade F-fällan (10779 bort, 10084 kvar skapar
1367→1461 utan gång/step-retur). Den kördes för hand, av den som kom ihåg den. Ett
verktyg som måste kommas ihåg är inte en kontroll — det är en vana, och vanor tappas
klockan tre på natten. Grinden gör linten till ett steg i preflighten.

MOT VILKEN GRAF
---------------
Linten är meningslös mot fel graf, så dumpen måste bevisas vara den som står. Vi
litar INTE på dumpregistrets metadata: identiteten HÄRLEDS ur dumpens egna byte via
``transformator.Graf.from_dump().identitet()`` och jämförs med den live-identitet
preflighten just verifierat mot manifestets pin. Registret är därmed bara en
uppslagning — pekar det fel faller härledningen, inte tvärtom.

Nivå-2 jämförs på ``graph_content_hash_utan_params``: motorns graf bär inte
``carried`` (fältet finns inte i ``Cmd::PlanLink``), och att jämföra mot den
params-bärande hashen avbryter en korrekt graf (Sol F3).

UNDANTAG
--------
Ett avsiktligt envägsläge kräver ``envag_medveten: true`` OCH ``envag_skal`` i
receptet. Tyst passage finns inte: utan skäl vägras undantaget, och lintutfallet
skrivs i deploy-kvittot även när det är PASS.

Att undantaget står i RECEPTET och inte i ett flaggargument är avsiktligt: receptets
sha ligger i ``SEALED_DEPLOYABLE``, så ett undantag kan inte läggas till efter
förseglingen utan att receptet faller ur den förseglade mängden. Ett undantag är ett
beslut som kräver omförsegling, inte en flagga någon kan skriva vid tangentbordet.

Ingen socket, ingen ~/lab-skrivning.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import graphstamp
import recept_lint
from d_failclosed import FailClosed

HERE = Path(__file__).resolve().parent
DEFAULT_DUMPREGISTER = HERE / "recept" / "dumpregister.json"

#: (sökväg, byte-sha) -> (dokument, härledd identitet). Härledningen är ren
#: funktion av byten, så nyckeln räcker; en ändrad fil ger ny nyckel.
_DUMPCACHE: dict[tuple[str, str], tuple[dict, dict]] = {}

GRIND = "recept-lint"
ENVAG_MEDVETEN = "envag_medveten"
ENVAG_SKAL = "envag_skal"

#: Utfall som skrivs i kvittot. PASS skrivs också — ett tyst godkännande går inte
#: att skilja från en grind som aldrig kördes.
PASS = "PASS"
PASS_MEDVETEN = "PASS-MEDVETEN"
PASS_MEDVETEN_UTAN_FYND = "PASS-MEDVETEN-UTAN-FYND"


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _ident_ur_dumpbyte(doc: dict[str, Any], register: list[dict] | None) -> dict[str, Any]:
    """Härled dumpens identitet ur dumpen själv. Inget register-påstående används.

    En dump som inte går att läsa är en vägran i DENNA grind, inte ett rått undantag
    som faller ut som crash-detector längre upp: skälet ska peka på dumpen, annars
    letar nästa person i fel ände.
    """
    from transformator import Graf, Vagran

    try:
        return Graf.from_dump(doc).identitet(register or [])
    except (Vagran, ValueError, KeyError, TypeError) as exc:
        raise FailClosed(GRIND, f"dumpen går inte att härleda identitet ur: {exc}") from exc


def _jamfor(harledd: dict[str, Any], live: dict[str, Any]) -> str | None:
    """Motorjämförbara fält: counts + FNV + nivå-2 UTAN params."""
    h_hash = harledd.get("graph_content_hash_utan_params") or harledd.get("graph_content_hash")
    l_hash = live.get("graph_content_hash")
    if str(harledd.get("graph_stamp")) != str(live.get("graph_stamp")):
        return (
            f"FNV {harledd.get('graph_stamp')} ≠ live {live.get('graph_stamp')}"
        )
    if str(h_hash) != str(l_hash):
        return f"nivå-2 {str(h_hash)[:16]}… ≠ live {str(l_hash)[:16]}…"
    for f in ("cells", "links", "rj_links"):
        if int(harledd.get(f) or 0) != int(live.get(f) or 0):
            return f"{f} {harledd.get(f)} ≠ live {live.get(f)}"
    return None


def valj_dump(
    live: dict[str, Any],
    *,
    dumpregister: Path | str | None = None,
    register: list[dict] | None = None,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """Sökväg + dokument + HÄRLEDD identitet för den dump som ÄR live-grafen.

    Ingen träff är en vägran, inte en genväg: en lint mot fel graf är värre än ingen
    lint, eftersom den ser ut som en kontroll.
    """
    reg_path = Path(dumpregister or DEFAULT_DUMPREGISTER)
    if not reg_path.is_file():
        raise FailClosed(GRIND, f"dumpregister saknas: {reg_path}")
    try:
        doc = json.loads(reg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FailClosed(GRIND, f"dumpregister {reg_path} går inte att läsa: {exc}") from exc
    poster = list(doc.get("dumps") or [])
    if not poster:
        raise FailClosed(GRIND, f"dumpregister {reg_path} saknar dumps")

    want_stamp = str(live.get("graph_stamp"))
    want_hash = str(live.get("graph_content_hash"))
    kandidater = [
        p for p in poster
        if str(p.get("graph_stamp")) == want_stamp
        and str(p.get("graph_content_hash")) == want_hash
    ]
    if not kandidater:
        kant = ", ".join(
            f"{p.get('id')} {p.get('cells')}/{p.get('links')} {str(p.get('graph_stamp'))}"
            for p in poster
        ) or "—"
        raise FailClosed(
            GRIND,
            f"ingen registrerad dump för live-grafen {live.get('cells')}/{live.get('links')} "
            f"FNV {want_stamp} nivå-2 {want_hash[:16]}… — registret har: {kant}. "
            f"Linten kan inte köras mot en graf vi inte har, och en okörd lint "
            f"passerar inte som PASS",
        )
    if len(kandidater) > 1:
        raise FailClosed(
            GRIND,
            f"{len(kandidater)} dumpar gör anspråk på samma identitet {want_stamp} — "
            f"registret är tvetydigt: {[p.get('id') for p in kandidater]}",
        )
    post = kandidater[0]
    path = Path(str(post.get("path") or ""))
    if not path.is_file():
        raise FailClosed(GRIND, f"registrerad dump {post.get('id')} saknas på disk: {path}")
    fick = _sha256(path)
    vill = str(post.get("byte_sha256") or "")
    if vill and fick != vill:
        raise FailClosed(
            GRIND,
            f"dump {path} har sha256 {fick} ≠ registrerad {vill} — filen har ändrats "
            f"efter registreringen",
        )
    nyckel = (str(path), fick)
    if nyckel in _DUMPCACHE:
        dump, harledd = _DUMPCACHE[nyckel]
    else:
        dump = json.loads(path.read_text(encoding="utf-8"))
        harledd = _ident_ur_dumpbyte(dump, register)
        _DUMPCACHE[nyckel] = (dump, harledd)
    why = _jamfor(harledd, live)
    if why:
        raise FailClosed(
            GRIND,
            f"dump {path} härleder inte live-identiteten: {why}. Registret påstod "
            f"träff; dumpens egna byte säger något annat, och byten avgör",
        )
    return path, dump, harledd


def _rendera(report: dict[str, Any]) -> str:
    rader = [f"{len(report['envag'])} skapad(e) enväg(er):"]
    for f in report["envag"]:
        rader.append(f"  · {f['skal']}")
    for d in report.get("cell_delta") or []:
        if d.get("d_in_ws") or d.get("d_out_ws"):
            rader.append(
                f"  cell {d['cell']}: in_ws {d['in_ws_before']}→{d['in_ws_after']}, "
                f"out_ws {d['out_ws_before']}→{d['out_ws_after']}"
            )
    return "\n".join(rader)


def kor_grind(
    recept: dict[str, Any],
    live: dict[str, Any],
    *,
    dumpregister: Path | str | None = None,
    register: list[dict] | None = None,
) -> dict[str, Any]:
    """Kör linten mot live-grafen. Returnerar kvittoblocket, vägrar vid fynd.

    Anropas i preflighten EFTER att live bevisats vara manifestets pin — då är
    ``live`` inte ett påstående utan ett verifierat faktum.
    """
    if register is None:
        try:
            register = graphstamp.load_register()
        except Exception:
            register = []
    path, dump, harledd = valj_dump(live, dumpregister=dumpregister, register=register)
    links = recept_lint.load_dump(path)
    rapport = recept_lint.lint(recept, links)

    medveten = bool(recept.get(ENVAG_MEDVETEN))
    skal = str(recept.get(ENVAG_SKAL) or "").strip()
    if medveten and not skal:
        raise FailClosed(
            GRIND,
            f"receptet sätter {ENVAG_MEDVETEN}=true utan {ENVAG_SKAL} — ett undantag "
            f"utan skäl är tyst passage med en flagga framför",
        )

    block = {
        "grind": GRIND,
        "dump_path": str(path),
        "dump_sha256": _sha256(path),
        "dump_identitet_harledd": {
            "cells": harledd.get("cells"),
            "links": harledd.get("links"),
            "rj_links": harledd.get("rj_links"),
            "graph_stamp": str(harledd.get("graph_stamp")),
            "graph_content_hash_utan_params": str(
                harledd.get("graph_content_hash_utan_params")
                or harledd.get("graph_content_hash")
            ),
        },
        "removed": rapport.get("removed") or [],
        "envag": rapport.get("envag") or [],
        "cell_delta": rapport.get("cell_delta") or [],
        ENVAG_MEDVETEN: medveten,
        ENVAG_SKAL: skal or None,
    }

    if rapport["ok"]:
        block["utfall"] = PASS_MEDVETEN_UTAN_FYND if medveten else PASS
        return block

    if not medveten:
        raise FailClosed(
            GRIND,
            f"receptet skapar envägsläge(n) mot live-grafen "
            f"{live.get('cells')}/{live.get('links')}:\n{_rendera(rapport)}\n"
            f"Dump: {path}\n"
            f"Avsiktligt? Sätt {ENVAG_MEDVETEN}: true + {ENVAG_SKAL} i receptet och "
            f"omförsegla — undantaget är ett beslut, inte en flagga",
        )
    block["utfall"] = PASS_MEDVETEN
    return block
