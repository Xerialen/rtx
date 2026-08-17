#!/usr/bin/env python3
"""Varvtrappe-räknare (U2b). Mechanical over a kimi-domfil.

2 consecutive FAIL on the same punkt ⇒ "live-session krävs".
3 ⇒ "konstruera om". Newest heading wins (append-only, newest first).

Punkt = DOM-id with trailing -rN / -Rn[B] stripped (protokoll §5 + file
practice). A collection title (slash-variants and " + "-joined names)
attributes its verdict to each named punkt. FAIL = RÖTT / UNDERKÄND /
STOPP / EJ KÖRBAR / FAIL / JUSTERAS. PASS = GRÖNT / GODKÄND (and not
also UNDERKÄND). Other headings are skipped.

Default --domfil is unused here; the buzz-4on4 launcher points at
WORK_LOGS/kimi-testprotokoll-domar.md. Unit tests feed a tempfile.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# Title may be a collection: "TURNERING-K1/K2/K3 + RAM-SJALVBEVIS".
# Capture up to the first emdash so spaces in the title are kept.
HEAD = re.compile(r"^## DOM\s+(.+?)\s+—\s+(.*)$")
# First -rN / -Rn… to end-of-token is the revision cluster (R6R4, R6B, r2, r3-beslut).
REV = re.compile(r"(?i)-r\d+.*$")
PLUS = re.compile(r"\s+\+\s+")

FAIL_MARKERS = (
    "UNDERKÄND",
    "UNDERKAND",
    "RÖTT",
    "ROTT",
    "STOPP",
    "EJ KÖRBAR",
    "EJ KORBAR",
    "FAIL",
    "JUSTERAS",
)
PASS_MARKERS = ("GRÖNT", "GRONT", "GODKÄND", "GODKAND")


def punkt_id(raw: str) -> str:
    """Strip revision suffixes. TURNERING-K2-R6B → TURNERING-K2."""
    return REV.sub("", raw)


def _expand_slash(token: str) -> list[str]:
    """TURNERING-K1/K2/K3 → [TURNERING-K1, TURNERING-K2, TURNERING-K3]."""
    parts = [p for p in token.split("/") if p]
    if len(parts) <= 1:
        return [token] if token else []
    first = parts[0]
    prefix = first.rsplit("-", 1)[0] + "-" if "-" in first else ""
    names = [first]
    for part in parts[1:]:
        names.append(part if part.startswith(prefix) else prefix + part)
    return names


def named_punkter(title: str) -> list[str]:
    """Split a collection title into each named punkt.

    'TURNERING-K1/K2/K3 + RAM-SJALVBEVIS' →
    TURNERING-K1, TURNERING-K2, TURNERING-K3, RAM-SJALVBEVIS.
    A single-name title is returned unchanged.
    """
    out: list[str] = []
    for group in PLUS.split(title.strip()):
        g = group.strip()
        if g:
            out.extend(_expand_slash(g))
    return out


def verdict_of(rest: str) -> str | None:
    """FAIL / PASS / None (skip). UNDERKÄND wins over GODKÄND if both appear."""
    u = rest.upper()
    if any(m in u for m in ("UNDERKÄND", "UNDERKAND", "RÖTT", "ROTT", "STOPP", "EJ KÖRBAR", "EJ KORBAR")):
        return "FAIL"
    if "JUSTERAS" in u or re.search(r"\bFAIL\b", u):
        return "FAIL"
    if any(m in u for m in PASS_MARKERS):
        return "PASS"
    return None


def parse_domfil(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        m = HEAD.match(line.strip())
        if not m:
            continue
        title, rest = m.group(1), m.group(2)
        verd = verdict_of(rest)
        if verd is None:
            continue
        names = named_punkter(title)
        if not names:
            continue
        heading = line.strip()
        for raw in names:
            rows.append(
                {
                    "raw": raw,
                    "punkt": punkt_id(raw),
                    "verdict": verd,
                    "heading": heading,
                }
            )
    return rows


def staircase(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Newest-first consecutive FAIL count per punkt."""
    by: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        by[row["punkt"]].append(row["verdict"])
    out: list[dict[str, Any]] = []
    for punkt, verdics in by.items():
        n = 0
        for v in verdics:
            if v == "FAIL":
                n += 1
            else:
                break
        if n >= 3:
            action = "konstruera om"
        elif n >= 2:
            action = "live-session krävs"
        else:
            action = None
        out.append({"punkt": punkt, "consecutive_fail": n, "n_domar": len(verdics), "action": action})
    out.sort(key=lambda r: (-r["consecutive_fail"], r["punkt"]))
    return out


def report(text: str) -> dict[str, Any]:
    rows = parse_domfil(text)
    steps = staircase(rows)
    live = [s for s in steps if s["action"] == "live-session krävs"]
    rebuild = [s for s in steps if s["action"] == "konstruera om"]
    return {
        "n_domar": len({r["heading"] for r in rows}),
        "n_punkter": len(steps),
        "live_session": [s["punkt"] for s in live],
        "konstruera_om": [s["punkt"] for s in rebuild],
        "punkter": steps,
    }


DEFAULT_DOMFIL = Path("/home/xerial/dev/buzz-4on4/WORK_LOGS/kimi-testprotokoll-domar.md")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--domfil",
        type=Path,
        default=DEFAULT_DOMFIL if DEFAULT_DOMFIL.is_file() else None,
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.domfil is None:
        ap.error("--domfil required (no default domfil on this host)")
    text = args.domfil.read_text(encoding="utf-8")
    doc = report(text)
    if args.json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
    else:
        if not doc["live_session"] and not doc["konstruera_om"]:
            print("varvtrappa: ingen punkt på 2/3 FAIL")
        for p in doc["live_session"]:
            print(f"live-session krävs: {p}")
        for p in doc["konstruera_om"]:
            print(f"konstruera om: {p}")
    if doc["konstruera_om"]:
        return 2
    if doc["live_session"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
