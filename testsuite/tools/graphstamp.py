#!/usr/bin/env python3
"""Dump-L-poster + stamp (nivå-1 FNV, nivå-2 SHA-256) för dm3-grafer.

Punkt 9 — params-bärande nivå-2
    Dagens dumpformat hashar L-poster som ``from/to/kind/T``. Två grafer
    som bara skiljer V296-flaggan (``carried`` / ``v_req`` / ``gain`` på
    samma 1167→1191) får samma nivå-2. Den här filen utökar L-posten med
    de tre fälten **när de finns på länkobjektet**.

    Bakåtkompatibilitet (förseglad): saknade params utelämnas. En dump
    utan ``carried``/``v_req``/``gain`` ger **byte-identisk** inventering
    och **samma** SHA-256 som före utökningen (bas 58787ce0…88ad9,
    minifixturerna i test_graphstamp.py). Gyllene /1-värden räknas inte
    om. Detta är inte kontraktets framtida /2-sidotabell
    (chained/weave/airtime/takeoff) — bara V296-klassens tre fält.

Punkt 10 — kollisionsregister
    Nivå-1 (counts/FNV) namnkolliderar. Verktyget varnar i klartext när
    en beräknad stamp träffar ``kollisionsregister.json``. Det är en
    varning, inte ett fel — nivå-2 (och recept-id) skiljer aliasen.

Ingen riggkontakt. Läser bara dump-JSON eller counts på kommandoraden.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

FNV_OFFSET = 0xCBF29CE484222325
FNV_PRIME = 0x100000001B3
MASK64 = 0xFFFFFFFFFFFFFFFF

# Fast ordning. Bara nycklar som faktiskt sitter på länkobjektet skrivs.
LINK_PARAM_KEYS = ("carried", "v_req", "gain")

HERE = Path(__file__).resolve().parent
DEFAULT_REGISTER = HERE / "kollisionsregister.json"


def _fmt(v) -> str:
    """Talform: heltal utan decimal, annars %.2f (runda-halv-jämn)."""
    v = float(v)
    return str(int(v)) if v == int(v) else format(round(v, 2), ".2f")


def fnv1a64(data: bytes) -> int:
    h = FNV_OFFSET
    for b in data:
        h ^= b
        h = (h * FNV_PRIME) & MASK64
    return h


def graph_stamp(map_name: str, cells: int, links: int, rj_links: int) -> int:
    """Nivå 1: FNV-1a-64 över map_utf8 ++ LE32(cells) ++ LE32(links) ++ LE32(rj_links)."""
    return fnv1a64(
        map_name.encode("utf-8") + struct.pack("<III", cells, links, rj_links)
    )


def _truthy01(v) -> str:
    if v in (True, 1, "1", "true", "True"):
        return "1"
    if v in (False, 0, "0", "false", "False"):
        return "0"
    raise ValueError(f"carried måste vara bool/0/1, fick {v!r}")


def link_param_fields(link: dict) -> tuple[str, ...]:
    """Named ``k=v`` i fast ordning. Saknad eller null-nyckel → utelämnad."""
    fields: list[str] = []
    if "carried" in link and link["carried"] is not None:
        fields.append("carried=" + _truthy01(link["carried"]))
    if "v_req" in link and link["v_req"] is not None:
        fields.append("v_req=" + _fmt(link["v_req"]))
    if "gain" in link and link["gain"] is not None:
        fields.append("gain=" + _fmt(link["gain"]))
    return tuple(fields)


def format_l_post(src: int, dst: int, kind: str, t: int, params: tuple[str, ...] = ()) -> str:
    """En L-post. Utan params: ``L\\tsrc\\tdst\\tkind\\tT`` (oförändrat /1)."""
    base = f"L\t{src}\t{dst}\t{kind}\t{t}"
    if not params:
        return base
    return base + "\t" + "\t".join(params)


def dump_link_record(src, engine_link: dict, t: int) -> dict:
    """Dump-JSON-länk. Kopierar carried/v_req/gain när motorn/källan bär dem."""
    rec = {
        "from": int(src),
        "to_cell": int(engine_link["to_cell"] if "to_cell" in engine_link else engine_link["to"]),
        "kind": str(engine_link["kind"]).lower(),
        "T": 0 if t in (0, False) else 1,
    }
    for k in LINK_PARAM_KEYS:
        if k in engine_link and engine_link[k] is not None:
            rec[k] = engine_link[k]
    return rec


def _link_t(link: dict) -> int:
    t = link.get("T", link.get("traversable", 1))
    return 0 if t in (0, False) else 1


def canonical_inventory(doc: dict) -> bytes:
    """Nivå 2: C-poster + L-poster. Params bara när de finns.

    Separator: tab mellan fält, LF mellan poster, ingen avslutande LF.
    C-origin: int() mot noll (samma som stamp.py / motorns as i32).
    """
    lines: list[str] = []
    for cid, c in sorted(zip(doc["cell_ids"], doc["cells"])):
        lines.append(f"C\t{cid}\t{int(c[0])}\t{int(c[1])}\t{int(c[2])}")
    lrecs = []
    for link in doc["links"]:
        src = int(link["from"])
        dst = int(link["to_cell"])
        kind = str(link["kind"]).lower()
        t = _link_t(link)
        params = link_param_fields(link)
        lrecs.append((src, dst, kind, t, params))
    lrecs.sort()
    for src, dst, kind, t, params in lrecs:
        lines.append(format_l_post(src, dst, kind, t, params))
    return "\n".join(lines).encode("utf-8")


def graph_content_hash(doc: dict) -> str:
    """Nivå 2: SHA-256 hex över kanonisk inventering."""
    return hashlib.sha256(canonical_inventory(doc)).hexdigest()


def counts_from_doc(doc: dict) -> tuple[str, int, int, int]:
    """map, cells, links, rj_links ur en dump. rj_links saknas ⇒ 0."""
    map_name = str(doc.get("map") or "dm3")
    cells = len(doc["cells"])
    links = len(doc["links"])
    rj = doc.get("rj_links", 0)
    if rj is None:
        rj = 0
    return map_name, int(cells), int(links), int(rj)


def load_register(path: str | Path | None = None) -> list[dict]:
    p = Path(path) if path else DEFAULT_REGISTER
    data = json.loads(p.read_text(encoding="utf-8"))
    entries = data.get("entries", data if isinstance(data, list) else [])
    if not isinstance(entries, list):
        raise ValueError(f"kollisionsregister saknar entries: {p}")
    return entries


def match_kollision(
    cells: int,
    links: int,
    rj_links: int,
    stamp: int,
    register: list[dict] | None = None,
) -> dict | None:
    """Första registerträffen på counts eller FNV, annars None."""
    stamp_s = str(stamp)
    for ent in register if register is not None else []:
        if (
            int(ent.get("cells", -1)) == cells
            and int(ent.get("links", -1)) == links
            and int(ent.get("rj_links", 0)) == rj_links
        ):
            return ent
        if str(ent.get("graph_stamp", "")) == stamp_s:
            return ent
    return None


def warn_kollision(entry: dict) -> str:
    aliases = ", ".join(entry.get("aliases") or [])
    cells = entry.get("cells")
    links = entry.get("links")
    stamp = entry.get("graph_stamp")
    extra = entry.get("warn")
    msg = (
        f"VARNING: nivå-1-kollision {cells}/{links} (FNV {stamp}) — "
        f"kända namn: {aliases}. Nivå-1 räcker inte; läs nivå-2."
    )
    if extra:
        msg = extra
    return msg


def stamp_dump(doc: dict, register: list[dict] | None = None) -> dict:
    map_name, cells, links, rj = counts_from_doc(doc)
    stamp = graph_stamp(map_name, cells, links, rj)
    hit = match_kollision(cells, links, rj, stamp, register)
    return {
        "map": map_name,
        "cells": cells,
        "links": links,
        "rj_links": rj,
        "graph_stamp": str(stamp),
        "graph_content_hash": graph_content_hash(doc),
        "kollision": hit,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Beräkna nivå-1/nivå-2 och varna vid kända counts-kollisioner."
    )
    p.add_argument("dump", nargs="?", help="dm3-dump JSON (schema qw-nav-graph/1)")
    p.add_argument("--map", default="dm3")
    p.add_argument("--cells", type=int)
    p.add_argument("--links", type=int)
    p.add_argument("--rj-links", type=int, default=0)
    p.add_argument("--register", default=str(DEFAULT_REGISTER))
    p.add_argument(
        "--inventory",
        action="store_true",
        help="skriv kanonisk inventering (C/L-poster) till stdout",
    )
    args = p.parse_args(argv)

    try:
        register = load_register(args.register)
    except FileNotFoundError:
        print(f"saknar kollisionsregister: {args.register}", file=sys.stderr)
        return 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"kan inte läsa kollisionsregister: {exc}", file=sys.stderr)
        return 2

    if args.dump:
        try:
            doc = json.loads(Path(args.dump).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"kan inte läsa dump: {exc}", file=sys.stderr)
            return 2
        if args.inventory:
            sys.stdout.buffer.write(canonical_inventory(doc))
            sys.stdout.buffer.write(b"\n")
        result = stamp_dump(doc, register)
        print(f"map {result['map']}")
        print(f"cells {result['cells']}")
        print(f"links {result['links']}")
        print(f"rj_links {result['rj_links']}")
        print(f"nivå-1 {result['graph_stamp']}")
        print(f"nivå-2 {result['graph_content_hash']}")
        if result["kollision"]:
            print(warn_kollision(result["kollision"]), file=sys.stderr)
        return 0

    if args.cells is None or args.links is None:
        p.print_usage(sys.stderr)
        print("ange en dump eller --cells och --links", file=sys.stderr)
        return 2

    stamp = graph_stamp(args.map, args.cells, args.links, args.rj_links)
    print(f"nivå-1 {stamp}")
    hit = match_kollision(args.cells, args.links, args.rj_links, stamp, register)
    if hit:
        print(warn_kollision(hit), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
