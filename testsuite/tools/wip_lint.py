#!/usr/bin/env python3
"""WIP-lint (U2c). Reads a KANBAN.md table. Alarms, never blocks.

Format (from the file header): `| säte | punkt | status |`
status ∈ PÅGÅR / KÖ / KLAR / GRANSKAS.
Tak: WIP ≤2/säte (PÅGÅR), ködjup ≤2 (KÖ).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

DEFAULT_KANBAN = Path("/home/xerial/dev/buzz-4on4/PLANS/KANBAN.md")
STATUSES = {"PÅGÅR", "KÖ", "KLAR", "GRANSKAS"}
ROW = None


def _row_re():
    import re
    return re.compile(
        r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(PÅGÅR|KÖ|KLAR|GRANSKAS)\s*\|\s*$"
    )


def parse_kanban(text: str) -> list[dict[str, str]]:
    rx = _row_re()
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        m = rx.match(line.strip())
        if not m:
            continue
        sate, punkt, status = (g.strip() for g in m.groups())
        if sate.lower() == "säte" or set(sate) <= {"-", ":"}:
            continue
        rows.append({"sate": sate, "punkt": punkt, "status": status})
    return rows


def lint(rows: list[dict[str, str]], *, wip_cap: int = 2, ko_cap: int = 2) -> dict[str, Any]:
    wip: dict[str, int] = defaultdict(int)
    ko: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["status"] == "PÅGÅR":
            wip[r["sate"]] += 1
        elif r["status"] == "KÖ":
            ko[r["sate"]] += 1
    alarms: list[dict[str, Any]] = []
    for sate, n in sorted(wip.items()):
        if n > wip_cap:
            alarms.append({"kind": "WIP", "sate": sate, "n": n, "cap": wip_cap})
    for sate, n in sorted(ko.items()):
        if n > ko_cap:
            alarms.append({"kind": "KÖ", "sate": sate, "n": n, "cap": ko_cap})
    return {
        "n_rader": len(rows),
        "wip": dict(wip),
        "ko": dict(ko),
        "alarms": alarms,
        "ok": not alarms,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--kanban",
        type=Path,
        default=DEFAULT_KANBAN if DEFAULT_KANBAN.is_file() else None,
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.kanban is None:
        ap.error("--kanban required")
    doc = lint(parse_kanban(args.kanban.read_text(encoding="utf-8")))
    if args.json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
    elif doc["ok"]:
        print("wip-lint: ok")
    else:
        for a in doc["alarms"]:
            if a["kind"] == "WIP":
                print(f"LARM WIP>{a['cap']}/säte: {a['sate']} har {a['n']} PÅGÅR")
            else:
                print(f"LARM ködjup>{a['cap']}: {a['sate']} har {a['n']} KÖ")
    # Never blocks.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
