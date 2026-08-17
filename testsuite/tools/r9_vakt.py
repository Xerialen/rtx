#!/usr/bin/env python3
"""R9-vakt (U2d). STOPP-dom without a same-day CI-grind ⇒ alarm.

Never speculative: we only look at STOPP headings already in the
domfil. A same-day grind is a git commit on that calendar day (UTC)
that touches a gate path (r1_vakt / varvtrappa / wip_lint / r9_vakt /
lasfil / .github/workflows). Alarms; does not block (exit 0).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DEFAULT_DOMFIL = Path("/home/xerial/dev/buzz-4on4/WORK_LOGS/kimi-testprotokoll-domar.md")
HEAD = re.compile(r"^## DOM\s+(\S+)\s+—\s+(.*)$")
DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
GRIND_HINT = re.compile(
    r"(r1_vakt|varvtrappa|wip_lint|r9_vakt|lasfil|las_fil|\.github/workflows/)",
    re.I,
)
# U2 start — older STOPPs predate the grind layer.
DEFAULT_SINCE = date(2026, 8, 17)


def parse_stopp(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        m = HEAD.match(line.strip())
        if not m:
            continue
        rest = m.group(2).upper()
        if "STOPP" not in rest:
            continue
        dm = DATE.search(line)
        if not dm:
            continue
        rows.append({"raw": m.group(1), "day": dm.group(1), "heading": line.strip()})
    return rows


def grind_days_from_git(repo: Path) -> set[str]:
    """UTC dates of commits that touch a grind path."""
    if not (repo / ".git").exists() and not (repo / ".git").is_file():
        return set()
    try:
        out = subprocess.check_output(
            [
                "git", "-C", str(repo), "log",
                "--format=%cI", "--name-only", "--",
                "testsuite/tools/r1_vakt.py",
                "testsuite/tools/varvtrappa.py",
                "testsuite/tools/wip_lint.py",
                "testsuite/tools/r9_vakt.py",
                "testsuite/tools/lasfil.py",
                ".github/workflows",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()
    days: set[str] = set()
    current: str | None = None
    for line in out.splitlines():
        if not line.strip():
            continue
        if line[0].isdigit() and "T" in line:
            current = line[:10]
            continue
        if current and GRIND_HINT.search(line):
            days.add(current)
    return days


def lint(
    text: str,
    *,
    grind_days: Iterable[str],
    since: date = DEFAULT_SINCE,
) -> dict[str, Any]:
    known = set(grind_days)
    alarms: list[dict[str, str]] = []
    stopps = parse_stopp(text)
    for s in stopps:
        day = date.fromisoformat(s["day"])
        if day < since:
            continue
        if s["day"] not in known:
            alarms.append({"punkt": s["raw"], "day": s["day"], "heading": s["heading"][:160]})
    return {
        "n_stopp": len(stopps),
        "n_since": sum(1 for s in stopps if date.fromisoformat(s["day"]) >= since),
        "grind_days": sorted(known),
        "alarms": alarms,
        "ok": not alarms,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--domfil", type=Path, default=DEFAULT_DOMFIL if DEFAULT_DOMFIL.is_file() else None)
    ap.add_argument("--repo", type=Path, default=None, help="git repo whose grind commits count")
    ap.add_argument("--since", default=DEFAULT_SINCE.isoformat())
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.domfil is None:
        ap.error("--domfil required")
    days = grind_days_from_git(args.repo) if args.repo else set()
    doc = lint(
        args.domfil.read_text(encoding="utf-8"),
        grind_days=days,
        since=date.fromisoformat(args.since),
    )
    if args.json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
    elif doc["ok"]:
        print("r9-vakt: ok")
    else:
        for a in doc["alarms"]:
            print(f"LARM R9: STOPP {a['punkt']} {a['day']} utan CI-grind samma dag")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
