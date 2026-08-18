#!/usr/bin/env python3
"""Undo-bevis: rulla tillbaka en variant och skriv ett bevis som går att skilja åt.

VARFÖR FILEN FINNS
------------------
Bevisen skrevs först av ett engångsskript i /tmp som bara bokförde GRAFTILLSTÅND:
före, efter, förväntat, utfall. Två undo av samma variant mot samma fork ger då
byte-identiska filer — vilket hände: `paav-undo-o-bevis.json` (02:56Z) och
`paav-undo-o-timprov-bevis.json` (04:29Z) fick samma sha `afd82f20…` trots att de
dokumenterar två olika händelser (groks dashboardgranskning 18/8).

Innehållet var sant i sak. Problemet är att ett bevis som inte bär sin egen
händelse inte kan visa VILKEN händelse det bevisar — och ett bevis vars enda
identitet är dess filnamn är inte ett bevis, det är en anteckning.

Formatet bär därför tid, unit och händelsekontext, och `handelse_id` binder dem
till graftillståndet i ett enda jämförbart värde. Två undo kan ha identiskt
graftillstånd; de kan inte ha identiskt `handelse_id`.

Filen skapas med `O_EXCL`: ett bevis skrivs en gång och skrivs aldrig över.

BEVISET ÄR EN DEL AV OPERATIONEN
--------------------------------
En undo som inte lämnat ett bevis rapporterar inte ``undone`` utan
``undone-obevisad``. Skälet är inte bokföringsestetik: en undo utan bevis och en
undo som aldrig kördes ser likadana ut i efterhand, och det är exakt den skillnad
riggbokföringen finns för att kunna göra.

Därför RESERVERAS bevissökvägen före mutationen. Skrivs beviset först efteråt kan
skrivningen fela när grafen redan är ändrad, och då står man med precis det läge
gränsen ska förhindra: en genomförd undo som inte går att styrka. Reservationen
flyttar den möjliga felkällan till före mutationen, där den bara kostar en vägran.

FRYSGRINDEN
-----------
Verktyget talar ctlproto direkt via ``labctl`` och gick därför förbi ändringsfrysen:
``check_change_freeze`` sitter i ``fixa.py`` och ``d_deploy.py``, inte i motorn, så en
undo härifrån kunde köras rakt igenom en satt flagga. Hittat 18/8 när flaggan stod för
grok2:s räkning av M1-runda 3 — vägen användes inte, men den fanns.

Grinden ligger nu i ``undo_med_bevis``, alltså i den enda funktion varje undo-väg går
genom, och före både reservationen och identitetsläsningen: en fryst rigg ska kosta en
vägran innan något rörs, inte efter.

Ingen riggkontakt utöver den ctl-port som anges.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from d_failclosed import FreezeContext, check_change_freeze  # noqa: E402

SCHEMA = "undo-bevis/1"
SCHEMA_LEDGER = "undo-bevis-ledger/1"

#: Motorns utfall på tråden. Oförändrat — motorn ska inte veta något om ~/lab.
UNDONE = "undone"
#: Vad som RAPPORTERAS när motorn sa undone men beviset inte finns. Aldrig "undone".
UNDONE_OBEVISAD = "undone-obevisad"

#: ctl-port -> unit. Speglar d_failclosed.ALLOWED_DEPLOY_PAIRS; en port utanför den
#: mängden är inte en mätrigg och ska inte kunna producera ett bevis.
UNIT_FOR_CTL = {27996: "tbx-d1", 27998: "tbx-d3"}

REN_FORK = {
    "cells": 5983,
    "links": 48216,
    "graph_stamp": "11908727279900740725",
    "graph_content_hash": "cd800200cad72431e0cbfe0a2fc947bd94309e334103d6cc0abd076155ecf051",
}


class Vagran(Exception):
    """Vägrar hellre än gissar."""


def handelse_id(
    *, ts: str, unit: str, handelse: str, fore: dict, efter: dict, seq: int = 0
) -> str:
    """Ett värde som skiljer två undo åt även när graftillståndet är identiskt.

    Tiden och uniten skiljer händelserna; graftillståndet är med för att id:t ska
    vara falskt om någon ändrar tillståndet men behåller tidsstämpeln.

    ``seq`` är radens ordningstal i liggaren och finns för att tidsstämpeln har
    sekundupplösning: armväxling hinner undo:a två gånger inom samma sekund mot
    samma graf, och då räcker tid+unit+tillstånd inte. Det är samma brist som gav
    de två byte-identiska O-bevisen, en tidsskala ner. Upplösningen höjs INTE till
    mikrosekunder — varje annan tidsstämpel i kampanjen är ISO-sekunder, och två
    format är värre än ett ordningstal.
    """
    material = "|".join(
        [
            SCHEMA,
            ts,
            unit,
            handelse,
            str(seq),
            str(fore.get("graph_content_hash")),
            str(efter.get("graph_content_hash")),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def bygg_bevis(
    *, ts, unit, ctl_port, handelse, variant, fore, efter, undo_outcome,
    forvantat=None, seq: int = 0,
):
    forvantat = forvantat or REN_FORK
    bitidentisk = all(str(efter.get(k)) == str(forvantat[k]) for k in forvantat)
    bevis = {
        "schema": SCHEMA,
        "ts": ts,
        "unit": unit,
        "ctl_port": ctl_port,
        "handelse": handelse,
        "seq": seq,
        "variant": variant,
        "fore": fore,
        "efter": efter,
        "forvantat": forvantat,
        "bitidentisk_mot_forvantat": bitidentisk,
        "undo_outcome": undo_outcome,
    }
    bevis["handelse_id"] = handelse_id(
        ts=ts, unit=unit, handelse=handelse, fore=fore, efter=efter, seq=seq
    )
    return bevis


def skriv(bevis: dict, ut: Path) -> Path:
    """O_EXCL: ett bevis skrivs en gång. Ett andra försök är en vägran, inte en
    överskrivning — den fil som redan finns kan vara någon annans bevis."""
    blob = (json.dumps(bevis, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        fd = os.open(ut, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError as exc:
        raise Vagran(f"{ut} finns redan — ett bevis skrivs en gång och skrivs aldrig över") from exc
    with os.fdopen(fd, "wb") as f:
        f.write(blob)
    return ut


@dataclass(frozen=True)
class Reservation:
    """En bevissökväg som är tagen innan grafen rörs.

    ``ledger=True`` är radformen: en append-only JSONL för vägar som undo:ar ofta
    (armväxling i mätharnesset gör det hundratals gånger per session, och en fil per
    växling vore bokföring som drunknar i sig själv). Radformen bär samma fält och
    samma händelse-id — det är formatet som skiljer, inte bevisningen.
    """

    path: Path
    ledger: bool = False


def reservera(ut: Path | str, *, ledger: bool = False) -> Reservation:
    """Ta sökvägen FÖRE mutationen. En upptagen sökväg är en vägran, inte en fråga."""
    ut = Path(ut)
    ut.parent.mkdir(parents=True, exist_ok=True)
    if ledger:
        if ut.exists() and not ut.is_file():
            raise Vagran(f"{ut} finns men är ingen fil")
        return Reservation(ut, True)
    try:
        fd = os.open(ut, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise Vagran(
            f"{ut} finns redan — ett bevis skrivs en gång och skrivs aldrig över"
        ) from exc
    os.close(fd)
    return Reservation(ut, False)


def nasta_seq(res: Reservation) -> int:
    """Radens ordningstal i liggaren. Filformen har alltid 0 — där är sökvägen unik."""
    if not res.ledger or not res.path.is_file():
        return 0
    with open(res.path, "rb") as f:
        return sum(1 for rad in f if rad.strip())


def fullfolj(res: Reservation, bevis: dict) -> Path:
    """Skriv beviset i den reserverade sökvägen. Enda vägen till utfallet ``undone``."""
    blob = (json.dumps(bevis, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    if res.ledger:
        rad = json.dumps(dict(bevis, schema=SCHEMA_LEDGER), sort_keys=True, ensure_ascii=False)
        with open(res.path, "a", encoding="utf-8") as f:
            f.write(rad + "\n")
            f.flush()
            os.fsync(f.fileno())
        return res.path
    with open(res.path, "w", encoding="utf-8") as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(res.path, 0o444)
    return res.path


def rapporterat_utfall(motor_outcome, *, bevis_skrivet: bool) -> str:
    """Motorns utfall filtrerat genom bevisningen.

    Allt utom ``undone`` går igenom oförändrat: en undo som inte lyckades behöver
    inget bevis, den behöver sitt eget skäl.
    """
    ut = str(motor_outcome)
    if ut != UNDONE:
        return ut
    return UNDONE if bevis_skrivet else UNDONE_OBEVISAD


def undo_med_bevis(
    *,
    las_identitet,
    gor_undo,
    reservation: Reservation,
    unit: str,
    ctl_port: int,
    handelse: str,
    variant: str,
    ts: str | None = None,
    forvantat: dict | None = None,
    fore: dict | None = None,
    freeze: FreezeContext | None = None,
) -> dict:
    """Läs före → undo → läs efter → skriv bevis → rapportera.

    Ordningen är hela poängen. ``reservation`` ska vara tagen innan detta anropas.
    Misslyckas bevisskrivningen EFTER mutationen höjs ``Vagran`` med utfallet
    ``undone-obevisad`` i meddelandet — undo:t har hänt och får inte tystas, men det
    får inte heller rapporteras som styrkt.
    """
    # Frysen först av allt: före reservationen, före identitetsläsningen, före socketen.
    # En fryst rigg ska kosta en vägran innan något rörs.
    check_change_freeze(freeze if freeze is not None else FreezeContext.production())
    if not str(handelse).strip():
        raise Vagran("handelse får inte vara tom; det är den som skiljer två bevis åt")
    # Ordningstalet läses FÖRE mutationen, av samma skäl som sökvägen reserveras
    # då: efteråt är det för sent att upptäcka att liggaren inte går att läsa.
    seq = nasta_seq(reservation)
    fore = fore if fore is not None else las_identitet()
    reply = gor_undo()
    motor = (reply or {}).get("outcome")
    efter = las_identitet()
    bevis = bygg_bevis(
        ts=ts or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        unit=unit,
        ctl_port=ctl_port,
        handelse=handelse.strip(),
        variant=variant,
        fore=fore,
        efter=efter,
        undo_outcome=motor,
        forvantat=forvantat,
        seq=seq,
    )
    try:
        path = fullfolj(reservation, bevis)
    except OSError as exc:
        raise Vagran(
            f"undo genomfört men beviset gick inte att skriva till "
            f"{reservation.path}: {exc} — utfallet är {UNDONE_OBEVISAD}, inte {UNDONE}"
        ) from exc
    return {
        "bevis": bevis,
        "bevis_path": str(path),
        "motor_outcome": motor,
        "utfall": rapporterat_utfall(motor, bevis_skrivet=True),
        "reply": reply,
    }


def _ident(lab, tag: str) -> dict:
    r = lab.request({"Fixa": {"recipe": "komponat", "mode": "chain", "lock_token": ""}}, timeout=15)
    f = r.get("Fixa") or r
    d = {
        "cells": f.get("cells"),
        "links": f.get("links"),
        "graph_stamp": f.get("stamp"),
        "graph_content_hash": f.get("content_hash"),
        "chain": (f.get("audit") or "").strip(),
    }
    print(f"  {tag}: {d['cells']}/{d['links']} stamp={d['graph_stamp']}")
    print(f"          nivå-2 {d['graph_content_hash']}")
    print(f"          {d['chain']}")
    return d


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="undo_bevis.py",
        description="Undo en variant och skriv ett bevis som bär sin egen händelse.",
    )
    p.add_argument("--variant", required=True, help="G/F/O — vad som rullas tillbaka")
    p.add_argument("--port", type=int, required=True, help="ctl-port (27996/27998)")
    p.add_argument("--lock-token", required=True)
    p.add_argument("--handelse", required=True, help="varför: 'riggstädning efter dom', 'före binärdeploy', …")
    p.add_argument("--ut", required=True, type=Path)
    args = p.parse_args(argv)

    try:
        unit = UNIT_FOR_CTL.get(args.port)
        if unit is None:
            raise Vagran(f"ctl-port {args.port} är ingen mätrigg — kända {sorted(UNIT_FOR_CTL)}")
        if not args.handelse.strip():
            raise Vagran("--handelse får inte vara tom; det är den som skiljer två bevis åt")
        if args.ut.exists():
            raise Vagran(f"{args.ut} finns redan — välj en ny sökväg")

        # Sökvägen tas FÖRE någon socket öppnas. Kan beviset inte skrivas ska det
        # kosta en vägran, inte ett obevisat undo.
        res = reservera(args.ut)

        sys.path.insert(0, "/home/xerial/rtx-tools")
        from labctl import Lab

        lab = Lab(port=args.port)
        print(f"=== undo {args.variant} på {unit} (ctl {args.port})")

        def _undo():
            r = lab.request(
                {"Fixa": {"recipe": "komponat", "mode": "undo", "lock_token": args.lock_token}},
                timeout=60,
            )
            f = r.get("Fixa") or r
            print(f"  undo: {f.get('outcome')} {f.get('reason') or ''}")
            for rad in (f.get("audit") or "").strip().splitlines():
                print(f"        {rad}")
            return f

        ut = undo_med_bevis(
            las_identitet=lambda: _ident(lab, "IDENT"),
            gor_undo=_undo,
            reservation=res,
            unit=unit,
            ctl_port=args.port,
            handelse=args.handelse,
            variant=args.variant,
        )
        bevis = ut["bevis"]
        print(f"  BITIDENTISK MOT FÖRVÄNTAT: {bevis['bitidentisk_mot_forvantat']}")
        print(f"  händelse-id: {bevis['handelse_id']}")
        print(f"  utfall: {ut['utfall']}")
        print(f"  bevis: {ut['bevis_path']}")
        return 0 if bevis["bitidentisk_mot_forvantat"] and ut["utfall"] == UNDONE else 1
    except Vagran as exc:
        print(f"VÄGRAR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # FailClosed från frysgrinden
        if type(exc).__name__ != "FailClosed":
            raise
        print(f"VÄGRAR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
