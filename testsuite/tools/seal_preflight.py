#!/usr/bin/env python3
"""U5b — CI-preflight for the seal producer (opus5 seal.sh @ 323da87).

When a facit file (has ``facit-kalla/1``) changed vs --base, run seal.sh
so a new row is published in the ledger. Already-sealed is OK. Never
invents a Fable lock token (R5). --by defaults to ``ci``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEAL_SH = HERE / "seal.sh"
DEFAULT_LEDGER = HERE.parent / "seals"
KALLA_MARK = "facit-kalla/1"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def changed_paths(repo: Path, base: str | None) -> list[Path]:
    if not base:
        try:
            base = git(repo, "rev-parse", "HEAD~1").strip()
        except subprocess.CalledProcessError:
            return []
    try:
        out = git(repo, "diff", "--name-only", f"{base}...HEAD")
    except subprocess.CalledProcessError:
        out = git(repo, "diff", "--name-only", base)
    files: list[Path] = []
    for line in out.splitlines():
        p = repo / line.strip()
        if p.is_file():
            files.append(p)
    return files


def is_facit(path: Path) -> bool:
    try:
        return KALLA_MARK in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def publish_seal(*, facit: Path, repo: Path, ledger: Path, by: str) -> str:
    """Run seal.sh. Returns 'sealed' | 'already'."""
    head = git(repo, "rev-parse", "HEAD").strip()
    r = subprocess.run(
        [
            str(SEAL_SH),
            "--facit", str(facit),
            "--head", head,
            "--ledger", str(ledger),
            "--by", by,
            "--code-repo", str(repo),
        ],
        capture_output=True,
        text=True,
    )
    err = (r.stderr or "") + (r.stdout or "")
    if r.returncode == 0:
        return "sealed"
    if "redan förseglat" in err:
        return "already"
    raise RuntimeError(f"seal.sh rc={r.returncode}: {err.strip()}")


def verify_ledger(ledger: Path) -> list[str]:
    r = subprocess.run(
        [sys.executable, str(HERE / "seal_ledger.py"), "verify", "--ledger", str(ledger)],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        return []
    return [ln for ln in (r.stderr or r.stdout).splitlines() if ln.strip()]


def run(*, repo: Path, ledger: Path, by: str, base: str | None) -> dict:
    changed = [p for p in changed_paths(repo, base) if is_facit(p)]
    actions: list[dict[str, str]] = []
    for facit in changed:
        status = publish_seal(facit=facit, repo=repo, ledger=ledger, by=by)
        actions.append({"facit": str(facit.relative_to(repo)), "status": status})
    fel = verify_ledger(ledger)
    return {"changed": [a["facit"] for a in actions], "actions": actions, "verify": fel}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=None)
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument("--by", default="ci", help="sealed_by. Never a lock token.")
    ap.add_argument("--base", default=None, help="git ref to diff against (default HEAD~1)")
    args = ap.parse_args(argv)
    repo = args.repo
    if repo is None:
        try:
            repo = Path(
                subprocess.check_output(
                    ["git", "-C", str(HERE), "rev-parse", "--show-toplevel"],
                    text=True,
                ).strip()
            )
        except subprocess.CalledProcessError:
            print("VÄGRAR: ange --repo", file=sys.stderr)
            return 2
    try:
        doc = run(repo=repo, ledger=args.ledger, by=args.by, base=args.base)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not doc["changed"]:
        print("seal-preflight: inga facitändringar")
    for a in doc["actions"]:
        print(f"seal-preflight: {a['status']} {a['facit']}")
    if doc["verify"]:
        print("seal-preflight: liggaren håller inte:", file=sys.stderr)
        for ln in doc["verify"]:
            print(f"  {ln}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
