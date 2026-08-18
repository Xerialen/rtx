#!/usr/bin/env python3
"""recept_lint — flagga SKAPADE envägslägen efter länk-op mot en grafdump.

Enväg: efter opsen finns en kvarvarande T=1-ingång P→C, men C inte längre
når P via walk/step inom 2 steg — och den återvägen FANNST före opsen.
F-fällan (10779 bort, 10084 kvar) skapar 1367→1461 utan gång/step-retur.
O (båda bort) skapar ingen ny enväg.

Ingen socket, ingen ~/lab-skrivning. Tester använder fixturer.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

WALK_STEP = frozenset({"walk", "step"})


def load_dump(path: str | Path) -> list[dict]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = doc.get("links") or []
    ids = doc.get("link_ids")
    out = []
    for i, L in enumerate(raw):
        lid = ids[i] if ids is not None else L.get("id", i)
        out.append(
            {
                "id": int(lid),
                "from": int(L["from"]),
                "to": int(L["to_cell"] if "to_cell" in L else L["to"]),
                "kind": str(L.get("kind") or "walk"),
                "T": int(L.get("T", 1)),
            }
        )
    return out


def removed_ids(recept: dict) -> list[int]:
    ids: list[int] = []
    for op in recept.get("ops") or []:
        if not isinstance(op, dict):
            continue
        for L in op.get("links") or []:
            if isinstance(L, dict) and "id" in L:
                ids.append(int(L["id"]))
            elif isinstance(L, int):
                ids.append(int(L))
        for x in op.get("remove_links") or []:
            ids.append(int(x if isinstance(x, int) else x["id"]))
    for L in recept.get("links") or []:
        if isinstance(L, dict) and "id" in L:
            ids.append(int(L["id"]))
    return ids


def _ws_adj(links: list[dict], gone: set[int]) -> dict[int, list[int]]:
    adj: dict[int, list[int]] = defaultdict(list)
    for L in links:
        if L["id"] in gone or L["T"] != 1:
            continue
        if L["kind"] in WALK_STEP:
            adj[L["from"]].append(L["to"])
    return adj


def _reach2(adj: dict[int, list[int]], src: int) -> set[int]:
    one = set(adj.get(src) or [])
    two = set(one)
    for n in one:
        two.update(adj.get(n) or [])
    return two


def _io_counts(links: list[dict], gone: set[int], cell: int) -> dict:
    inn = out = inn_ws = out_ws = 0
    for L in links:
        if L["id"] in gone or L["T"] != 1:
            continue
        if L["to"] == cell:
            inn += 1
            if L["kind"] in WALK_STEP:
                inn_ws += 1
        if L["from"] == cell:
            out += 1
            if L["kind"] in WALK_STEP:
                out_ws += 1
    return {"in": inn, "out": out, "in_ws": inn_ws, "out_ws": out_ws}


def lint(recept: dict, links: list[dict]) -> dict:
    gone = set(removed_ids(recept))
    before_adj = _ws_adj(links, set())
    after_adj = _ws_adj(links, gone)
    flags = []
    touched: set[int] = set()
    for L in links:
        if L["id"] in gone:
            touched.add(L["from"])
            touched.add(L["to"])
        if L["id"] in gone or L["T"] != 1:
            continue
        p, c = L["from"], L["to"]
        if p in _reach2(before_adj, c) and p not in _reach2(after_adj, c):
            flags.append(
                {
                    "cell": c,
                    "from": p,
                    "via_link": L["id"],
                    "via_kind": L["kind"],
                    "skal": (
                        "kvarvarande ingång %s→%s (%s %s) men gång/step-återväg "
                        "inom 2 steg försvann"
                        % (p, c, L["kind"], L["id"])
                    ),
                }
            )
            touched.add(c)
            touched.add(p)
    deltas = []
    for cell in sorted(touched):
        b = _io_counts(links, set(), cell)
        a = _io_counts(links, gone, cell)
        deltas.append(
            {
                "cell": cell,
                "in_before": b["in"],
                "in_after": a["in"],
                "d_in": a["in"] - b["in"],
                "out_before": b["out"],
                "out_after": a["out"],
                "d_out": a["out"] - b["out"],
                "in_ws_before": b["in_ws"],
                "in_ws_after": a["in_ws"],
                "d_in_ws": a["in_ws"] - b["in_ws"],
                "out_ws_before": b["out_ws"],
                "out_ws_after": a["out_ws"],
                "d_out_ws": a["out_ws"] - b["out_ws"],
            }
        )
    return {
        "ok": not flags,
        "removed": sorted(gone),
        "envag": flags,
        "cell_delta": deltas,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("recept")
    ap.add_argument("dump")
    args = ap.parse_args(argv)
    recept = json.loads(Path(args.recept).read_text(encoding="utf-8"))
    links = load_dump(args.dump)
    report = lint(recept, links)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    if not report["ok"]:
        print(
            "STOPP: %d skapad(e) enväg(er)" % len(report["envag"]),
            file=sys.stderr,
        )
        for f in report["envag"]:
            print(" ", f["skal"], file=sys.stderr)
        return 2
    print("OK: inga skapade envägslägen", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
