#!/usr/bin/env python3
"""R9-vakt (U2d). STOPP-dom without a same-day CI-grind ⇒ alarm.

Never speculative: we only look at STOPP headings already in the
domfil. A same-day grind is a git commit on that calendar day (UTC)
that touches a gate path (r1_vakt / varvtrappa / wip_lint / r9_vakt /
lasfil / .github/workflows). Alarms; does not block (exit 0) when the
repo is reachable.

--repo is local path or host:path (ssh). Pinnacle cannot see
lanister's ~/rtx-toolbox-d locally; pass lanister:~/rtx-toolbox-d.
Unreachable repo (missing --repo, no .git, ssh/git failure) is its
own state: "REPO ONÅBART", exit 3. Never a silent alarm and never a
silent ok.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import date
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
GRIND_PATHS = (
    "testsuite/tools/r1_vakt.py",
    "testsuite/tools/varvtrappa.py",
    "testsuite/tools/wip_lint.py",
    "testsuite/tools/r9_vakt.py",
    "testsuite/tools/lasfil.py",
    ".github/workflows",
)
EXIT_REPO_ONABART = 3
PINNACLE_SSH_REPO = "lanister:~/rtx-toolbox-d"


class RepoUnreachable(Exception):
    """Grind git history cannot be read. Distinct from empty grind days."""


def split_repo_spec(spec: str) -> tuple[str | None, str]:
    """host:path → (host, path). Local path → (None, path)."""
    s = spec.strip()
    if s.startswith("ssh://"):
        rest = s[len("ssh://") :]
        host, _, path = rest.partition("/")
        return host, ("/" + path) if path else "~"
    if ":" in s:
        host, path = s.split(":", 1)
        if host and "/" not in host and path:
            return host, path
    return None, s


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


def _days_from_log(out: str) -> set[str]:
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


def _git_log_argv(path: str) -> list[str]:
    return [
        "git",
        "-C",
        path,
        "log",
        "--format=%cI",
        "--name-only",
        "--",
        *GRIND_PATHS,
    ]


def grind_days_from_git(repo: str | Path | None) -> set[str]:
    """UTC dates of commits that touch a grind path.

    Raises RepoUnreachable when the repo cannot be read. An empty set
    means the repo was read and no grind commit exists — that is not
    the same state.
    """
    if repo is None or str(repo).strip() == "":
        raise RepoUnreachable("REPO ONÅBART: --repo saknas")
    host, path = split_repo_spec(str(repo))
    if host:
        cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            host,
            *_git_log_argv(path),
        ]
    else:
        p = Path(path).expanduser()
        git_dir = p / ".git"
        if not git_dir.exists() and not git_dir.is_file():
            raise RepoUnreachable(f"REPO ONÅBART: {p} har ingen .git")
        cmd = _git_log_argv(str(p))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RepoUnreachable(f"REPO ONÅBART: {exc}") from exc
    if proc.returncode != 0:
        combined = (proc.stderr or "") + (proc.stdout or "")
        if "does not have any commits yet" in combined:
            return set()
        err = combined.strip().splitlines()
        tail = err[-1] if err else f"rc={proc.returncode}"
        raise RepoUnreachable(f"REPO ONÅBART: {tail}")
    return _days_from_log(proc.stdout)


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
    ap.add_argument(
        "--repo",
        default=None,
        help="local git path, or host:path via ssh (e.g. lanister:~/rtx-toolbox-d)",
    )
    ap.add_argument("--since", default=DEFAULT_SINCE.isoformat())
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.domfil is None:
        ap.error("--domfil required")
    try:
        days = grind_days_from_git(args.repo)
    except RepoUnreachable as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "repo": "ONÅBART",
                        "error": str(exc),
                        "grind_days": None,
                        "alarms": [],
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print("REPO ONÅBART")
            print(str(exc))
        return EXIT_REPO_ONABART
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
