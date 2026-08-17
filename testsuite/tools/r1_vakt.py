#!/usr/bin/env python3
"""R1-vakt: a judged run is refused without a preceding rökdeploy receipt.

R1 (flödesregel): rökdeploy först. The receipt format is defined here and
tested in test_r1_vakt.py. No rig — callers pass a path; tests write
fixtures under tempfile (FreezeContext.for_test / never ~/lab).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from d_failclosed import ALLOWED_DEPLOY_PAIRS, FailClosed

SCHEMA = "r1-rokdeploy-kvitto/1"
REQUIRED = (
    "schema",
    "written_at",
    "runner_commit",
    "outcome",
    "recept_id",
    "manifest_sha256",
    "unit",
    "ctl_port",
    "game_port",
    "lock_token",
    "slut_observed",
)
STAMP_KEYS = ("cells", "links", "rj_links", "graph_stamp", "graph_content_hash")


def _iso(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise FailClosed("r1", "written_at saknas")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FailClosed("r1", f"written_at är inte ISO-8601: {value!r}") from exc


def parse_rokdeploy_kvitto(doc: Any) -> dict[str, Any]:
    """Validate a rökdeploy receipt. Raises FailClosed('r1', …) on any defect."""
    if not isinstance(doc, dict):
        raise FailClosed("r1", "rökdeploy-kvitto är inte ett objekt")
    missing = [k for k in REQUIRED if k not in doc]
    if missing:
        raise FailClosed("r1", f"rökdeploy-kvitto saknar fält: {', '.join(missing)}")
    if doc.get("schema") != SCHEMA:
        raise FailClosed(
            "r1",
            f"rökdeploy-kvitto schema {doc.get('schema')!r} ≠ {SCHEMA}",
        )
    _iso(doc.get("written_at"))
    if not str(doc.get("runner_commit") or "").strip():
        raise FailClosed("r1", "rökdeploy-kvitto saknar runner_commit")
    if doc.get("outcome") != "applied":
        raise FailClosed(
            "r1",
            f"rökdeploy-kvitto outcome {doc.get('outcome')!r} ≠ applied — "
            "dömd körning kräver lyckad rökdeploy",
        )
    if not str(doc.get("recept_id") or "").strip():
        raise FailClosed("r1", "rökdeploy-kvitto saknar recept_id")
    sha = str(doc.get("manifest_sha256") or "").strip().lower()
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise FailClosed("r1", "rökdeploy-kvitto manifest_sha256 är inte SHA-256")
    unit = str(doc.get("unit") or "")
    if unit not in ALLOWED_DEPLOY_PAIRS:
        raise FailClosed("r1", f"rökdeploy-kvitto unit {unit!r} är inte tbx-d1/d3")
    try:
        ctl = int(doc["ctl_port"])
        game = int(doc["game_port"])
    except (TypeError, ValueError) as exc:
        raise FailClosed("r1", "rökdeploy-kvitto portar är inte heltal") from exc
    if ALLOWED_DEPLOY_PAIRS[unit] != (ctl, game):
        raise FailClosed(
            "r1",
            f"rökdeploy-kvitto portpar {ctl}/{game} ≠ {unit} "
            f"{ALLOWED_DEPLOY_PAIRS[unit][0]}/{ALLOWED_DEPLOY_PAIRS[unit][1]}",
        )
    if not str(doc.get("lock_token") or "").strip():
        raise FailClosed("r1", "rökdeploy-kvitto saknar lock_token")
    slut = doc.get("slut_observed")
    if not isinstance(slut, dict):
        raise FailClosed("r1", "rökdeploy-kvitto slut_observed saknas")
    for key in STAMP_KEYS:
        if key not in slut:
            raise FailClosed("r1", f"rökdeploy-kvitto slut_observed saknar {key}")
    return doc


def require_rokdeploy(path: Path | str | None) -> dict[str, Any]:
    """R1 grind. Missing or invalid receipt ⇒ judged run is refused."""
    if path is None or str(path).strip() == "":
        raise FailClosed(
            "r1",
            "dömd körning vägras utan föregående rökdeploy-kvitto "
            f"({SCHEMA})",
        )
    p = Path(path)
    if not p.is_file():
        raise FailClosed("r1", f"rökdeploy-kvitto saknas: {p}")
    try:
        raw = p.read_text(encoding="utf-8")
        doc = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise FailClosed("r1", f"rökdeploy-kvitto oläsbart: {exc}") from exc
    return parse_rokdeploy_kvitto(doc)


def refuse_judged_run(path: Path | str | None) -> str | None:
    """None if R1 is satisfied. Error text if the judged run must stop."""
    try:
        require_rokdeploy(path)
    except FailClosed as exc:
        return str(exc)
    return None


def write_rokdeploy_kvitto(path: Path | str, **fields: Any) -> dict[str, Any]:
    """Write a complete receipt. Tests only — never invents a lock token."""
    doc = {
        "schema": SCHEMA,
        "written_at": fields.get("written_at") or "2026-08-17T12:00:00Z",
        "runner_commit": fields["runner_commit"],
        "outcome": fields.get("outcome") or "applied",
        "recept_id": fields.get("recept_id") or "v296-ram",
        "manifest_sha256": fields["manifest_sha256"],
        "unit": fields.get("unit") or "tbx-d1",
        "ctl_port": fields.get("ctl_port", 27996),
        "game_port": fields.get("game_port", 27592),
        "lock_token": fields["lock_token"],
        "slut_observed": fields["slut_observed"],
    }
    Path(path).write_text(
        json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kvitto", type=Path, required=True, help="path to r1-rokdeploy-kvitto/1")
    args = ap.parse_args(argv)
    try:
        require_rokdeploy(args.kvitto)
    except FailClosed as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("r1: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
