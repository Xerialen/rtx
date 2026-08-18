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

Ingen riggkontakt utöver den ctl-port som anges.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "undo-bevis/1"

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


def handelse_id(*, ts: str, unit: str, handelse: str, fore: dict, efter: dict) -> str:
    """Ett värde som skiljer två undo åt även när graftillståndet är identiskt.

    Tiden och uniten är det som faktiskt skiljer händelserna; graftillståndet är
    med för att id:t ska vara falskt om någon ändrar tillståndet men behåller
    tidsstämpeln.
    """
    material = "|".join(
        [
            SCHEMA,
            ts,
            unit,
            handelse,
            str(fore.get("graph_content_hash")),
            str(efter.get("graph_content_hash")),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def bygg_bevis(*, ts, unit, ctl_port, handelse, variant, fore, efter, undo_outcome, forvantat=None):
    forvantat = forvantat or REN_FORK
    bitidentisk = all(str(efter.get(k)) == str(forvantat[k]) for k in forvantat)
    bevis = {
        "schema": SCHEMA,
        "ts": ts,
        "unit": unit,
        "ctl_port": ctl_port,
        "handelse": handelse,
        "variant": variant,
        "fore": fore,
        "efter": efter,
        "forvantat": forvantat,
        "bitidentisk_mot_forvantat": bitidentisk,
        "undo_outcome": undo_outcome,
    }
    bevis["handelse_id"] = handelse_id(ts=ts, unit=unit, handelse=handelse, fore=fore, efter=efter)
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

        sys.path.insert(0, "/home/xerial/rtx-tools")
        from labctl import Lab

        lab = Lab(port=args.port)
        print(f"=== undo {args.variant} på {unit} (ctl {args.port})")
        fore = _ident(lab, "FÖRE ")
        r = lab.request({"Fixa": {"recipe": "komponat", "mode": "undo", "lock_token": args.lock_token}}, timeout=60)
        f = r.get("Fixa") or r
        print(f"  undo: {f.get('outcome')} {f.get('reason') or ''}")
        for rad in (f.get("audit") or "").strip().splitlines():
            print(f"        {rad}")
        efter = _ident(lab, "EFTER")

        bevis = bygg_bevis(
            ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            unit=unit,
            ctl_port=args.port,
            handelse=args.handelse.strip(),
            variant=args.variant,
            fore=fore,
            efter=efter,
            undo_outcome=f.get("outcome"),
        )
        skriv(bevis, args.ut)
        print(f"  BITIDENTISK MOT FÖRVÄNTAT: {bevis['bitidentisk_mot_forvantat']}")
        print(f"  händelse-id: {bevis['handelse_id']}")
        print(f"  bevis: {args.ut}")
        return 0 if bevis["bitidentisk_mot_forvantat"] and f.get("outcome") == "undone" else 1
    except Vagran as exc:
        print(f"VÄGRAR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
