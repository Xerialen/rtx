#!/usr/bin/env python3
"""R1-vakt: a judged run is refused without a preceding rökdeploy receipt.

TRIPWIRE, not attestation. Schema r1-rokdeploy-kvitto/1 is unsigned JSON.
The gate checks FORM (required fields, sha hex, unit/port pair, outcome
applied) plus, when a deploy-run.json path is present AND the file is
reachable, a sha256 cross-check against that file. Anyone with filesystem
write can author a passing receipt. This upholds R1 (rökdeploy first)
against forgetfulness, not against intent. nature="tripwire" is written
on every receipt so that stands on the can.

No rig — callers pass a path; tests write fixtures under tempfile
(FreezeContext.for_test / never ~/lab).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from d_failclosed import ALLOWED_DEPLOY_PAIRS, FailClosed

SCHEMA = "r1-rokdeploy-kvitto/1"
# On the can: this schema is a tripwire. It is not a signed attestation.
NATURE = "tripwire"
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


def _sha256_hex(value: Any, label: str) -> str:
    sha = str(value or "").strip().lower()
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise FailClosed("r1", f"rökdeploy-kvitto {label} är inte SHA-256")
    return sha


def parse_rokdeploy_kvitto(doc: Any) -> dict[str, Any]:
    """Validate a rökdeploy receipt. Raises FailClosed('r1', …) on any defect.

    Form only, plus optional deploy_run_sha256 hex check. File cross-check
    lives in require_rokdeploy (needs the filesystem).
    """
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
    nature = doc.get("nature")
    if nature is not None and nature != NATURE:
        raise FailClosed(
            "r1",
            f"rökdeploy-kvitto nature {nature!r} ≠ {NATURE} "
            "(schema is a tripwire, not an attestation)",
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
    _sha256_hex(doc.get("manifest_sha256"), "manifest_sha256")
    if "deploy_run_sha256" in doc:
        _sha256_hex(doc.get("deploy_run_sha256"), "deploy_run_sha256")
    if "deploy_run_path" in doc and not str(doc.get("deploy_run_path") or "").strip():
        raise FailClosed("r1", "rökdeploy-kvitto deploy_run_path är tom")
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


def _cross_check_deploy_run(doc: dict[str, Any]) -> None:
    """When deploy-run.json is reachable, the bound sha256 must match the bytes.

    Unreachable path ⇒ no check (tripwire, not attestation).
    """
    raw_path = doc.get("deploy_run_path")
    if raw_path is None:
        return
    p = Path(str(raw_path))
    if not p.is_file():
        return
    sha = str(doc.get("deploy_run_sha256") or "").strip().lower()
    if not sha:
        raise FailClosed(
            "r1",
            "rökdeploy-kvitto har deploy_run_path men saknar deploy_run_sha256",
        )
    got = hashlib.sha256(p.read_bytes()).hexdigest()
    if got != sha:
        raise FailClosed(
            "r1",
            f"deploy_run_sha256 stämmer inte mot {p}",
        )


def require_rokdeploy(path: Path | str | None) -> dict[str, Any]:
    """R1 grind. Missing or invalid receipt ⇒ judged run is refused."""
    if path is None or str(path).strip() == "":
        raise FailClosed(
            "r1",
            "dömd körning vägras utan föregående rökdeploy-kvitto "
            f"({SCHEMA}, {NATURE})",
        )
    p = Path(path)
    if not p.is_file():
        raise FailClosed("r1", f"rökdeploy-kvitto saknas: {p}")
    try:
        raw = p.read_text(encoding="utf-8")
        doc = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise FailClosed("r1", f"rökdeploy-kvitto oläsbart: {exc}") from exc
    parsed = parse_rokdeploy_kvitto(doc)
    _cross_check_deploy_run(parsed)
    return parsed


def refuse_judged_run(path: Path | str | None) -> str | None:
    """None if R1 is satisfied. Error text if the judged run must stop."""
    try:
        require_rokdeploy(path)
    except FailClosed as exc:
        return str(exc)
    return None


def write_rokdeploy_kvitto(path: Path | str, **fields: Any) -> dict[str, Any]:
    """Write a complete receipt. Tests only — never invents a lock token.

    Always stamps nature=tripwire. If deploy_run_path is given and the
    file is readable, bind deploy_run_sha256 to those bytes.
    """
    doc = {
        "schema": SCHEMA,
        "nature": NATURE,
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
    deploy_path = fields.get("deploy_run_path")
    claimed = fields.get("deploy_run_sha256")
    if deploy_path:
        dp = Path(deploy_path)
        doc["deploy_run_path"] = str(dp)
        if dp.is_file():
            got = hashlib.sha256(dp.read_bytes()).hexdigest()
            if claimed and str(claimed).strip().lower() != got:
                raise ValueError("deploy_run_sha256 stämmer inte mot filen")
            doc["deploy_run_sha256"] = got
        elif claimed:
            doc["deploy_run_sha256"] = str(claimed).strip().lower()
    elif claimed:
        doc["deploy_run_sha256"] = str(claimed).strip().lower()
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
    print("r1: ok (tripwire)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
