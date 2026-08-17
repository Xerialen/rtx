#!/usr/bin/env python3
"""Deploy-runner: apply a komponat-manifest/1 op list in order.

Facit §1 / DOM T20M-PRELIM B1. Consumes the sealed compose manifest,
never a standalone LABB fixture. Each op is sent on the same ctl paths
as fixa (Fixa apply/undo) or send_plan_link (PlanLink). After every
mutation both stamp levels are checked against the manifest intermediate.
Deviation → abort, undo every applied op, write receipts.

Live apply waits for a binary that knows ram-rail-v2. Tests use mock-ctl.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from d_failclosed import (
    DEPLOY_OK,
    FailClosed,
    FreezeContext,
    check_change_freeze,
    check_deploy_status,
    send_plan_link,
)
from d_kvitto import write_exclusive
from fixa import run_fixa, stamp_from_reply

HERE = Path(__file__).resolve().parent
DEFAULT_MANIFEST = HERE / "recept" / "komponat-v296-ram.manifest.json"
DEFAULT_RECEPT = HERE / "recept" / "komponat-v296-ram.json"

# Byte-pin of the deploy-candidate manifest (DOM T20M-PRELIM steg 1).
SEALED_MANIFEST_SHA256 = {
    "komponat-v296-ram.manifest.json": (
        "bcba5897a9af7887d63fcf7466081a52d0bc84885c6d227002ca3d67727bc8e8"
    ),
}

SCHEMA_RUN = "deploy-run/1"
SCHEMA_STEG = "deploy-steg-kvitto/1"


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ident_of(block: dict[str, Any]) -> dict[str, Any]:
    """Nivå-1 FNV + nivå-2 SHA (+ counts). Both hash levels are required."""
    return {
        "cells": int(block["cells"]),
        "links": int(block["links"]),
        "rj_links": int(block.get("rj_links") or 0),
        "graph_stamp": str(block["graph_stamp"]),
        "graph_content_hash": str(block["graph_content_hash"]),
    }


def ident_sha256(ident: dict[str, Any]) -> str:
    blob = json.dumps(ident_of(ident), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def same_identity(live: dict[str, Any], want: dict[str, Any]) -> str | None:
    """None if live matches want on BOTH hash levels. Counts alone never suffice."""
    a, b = ident_of(live), ident_of(want)
    if a["graph_stamp"] != b["graph_stamp"] or a["graph_content_hash"] != b["graph_content_hash"]:
        return (
            f"live ≠ förseglad identitet "
            f"(FNV {a['graph_stamp']} sha={a['graph_content_hash'][:16]}… "
            f"≠ FNV {b['graph_stamp']} sha={b['graph_content_hash'][:16]}…; "
            f"counts {a['cells']}/{a['links']} vs {b['cells']}/{b['links']}) "
            f"— nivå-1 räcker inte (5983/48214-kollisionen)"
        )
    if (a["cells"], a["links"], a["rj_links"]) != (b["cells"], b["links"], b["rj_links"]):
        return (
            f"hash matchar men counts skiljer "
            f"{a['cells']}/{a['links']} ≠ {b['cells']}/{b['links']}"
        )
    return None


def _guard_recipe(identities: list[dict[str, Any]]) -> dict[str, Any]:
    """LABB wrapper so _send_fixa accepts the live stamp of a compose step.

    Child fixtures (ram-rail-v2, ram-prevent) seal BASE+ON, not the
    V296-intermediate. The compose is the deploy; children stay LABB.
    """
    sealed = [ident_of(i) for i in identities]
    return {
        "id": "deploy-steg",
        "status": "LABB",
        "status_skal": "delsteg i komponat-manifest; deployas inte fristående",
        "off": sealed[0],
        "on_expected": sealed[-1],
        "sealed_stamps": sealed,
    }


def live_identity(ctl) -> dict[str, Any]:
    """Current graph identity via the same dry-run path as fixa."""
    reply = run_fixa(
        ctl,
        recipe_id="west-shelf",
        mode="dry-run",
        from_cell=None,
        to_cell=None,
    )
    return stamp_from_reply(reply)


def load_manifest(path: Path) -> dict[str, Any]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema") != "komponat-manifest/1":
        raise FailClosed(
            "deploy-status",
            f"{path} har schema {doc.get('schema')!r}, kräver komponat-manifest/1",
        )
    return doc


def load_recept(path: Path) -> dict[str, Any]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema") != "komponat/1":
        raise FailClosed(
            "deploy-status",
            f"{path} har schema {doc.get('schema')!r}, kräver komponat/1",
        )
    return doc


def apply_ops_of(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Steg after the pin (index 0). Those are the mutations."""
    steg = list(manifest.get("steg") or [])
    return [s for s in steg if str(s.get("op") or "") != "pin"]


def preflight(
    *,
    manifest_path: Path,
    ctl,
    freeze: FreezeContext | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Byte-sha, DEPLOY-KANDIDAT, freeze, pin=bas on both hash levels."""
    manifest_path = Path(manifest_path)
    sha = file_sha256(manifest_path)
    want = SEALED_MANIFEST_SHA256.get(manifest_path.name)
    if want is not None and sha != want:
        raise FailClosed(
            "crash-detector",
            f"manifest {manifest_path.name} SHA-256 {sha} ≠ förseglad {want}",
        )
    man = load_manifest(manifest_path)
    check_deploy_status(man, deploy=True)
    if man.get("status") != DEPLOY_OK:
        raise FailClosed("deploy-status", f"manifest status {man.get('status')!r}")
    check_change_freeze(freeze)
    live = live_identity(ctl)
    pin = ident_of((man["steg"][0]).get("identitet") or man["steg"][0])
    why = same_identity(live, pin)
    if why:
        raise FailClosed("crash-detector", f"pin=bas misslyckades: {why}")
    return man, live, sha


def _apply_one(ctl, op: dict[str, Any], *, freeze: FreezeContext, lock_token: str | None,
               before: dict[str, Any], after: dict[str, Any]) -> None:
    kind = str(op.get("op") or "")
    if kind == "plan_link":
        payload = {
            "from": op["from"],
            "takeoff": op["takeoff"],
            "tgt": op["tgt"],
            "v_req": float(op["v_req"]),
            "gain": float(op["gain"]),
        }
        if op.get("carried"):
            payload["carried"] = True
        send_plan_link(ctl, payload, recipe=_guard_recipe([before, after]), freeze=freeze)
        return
    if kind == "shelf_patch":
        rid = Path(str(op.get("kalla") or op.get("name") or "")).stem
        if not rid:
            raise FailClosed("deploy", "shelf_patch saknar kalla/name")
        run_fixa(
            ctl,
            recipe_id=rid,
            mode="apply",
            from_cell=None,
            to_cell=None,
            lock_token=lock_token,
            recipe=_guard_recipe([before, after]),
            freeze=freeze,
        )
        return
    raise FailClosed("deploy", f"okänd op {kind!r}")


def _undo_one(ctl, applied: dict[str, Any], *, freeze: FreezeContext, lock_token: str | None,
              now: dict[str, Any], then: dict[str, Any]) -> None:
    rid = applied.get("recipe_id") or "compose"
    run_fixa(
        ctl,
        recipe_id=rid,
        mode="undo",
        from_cell=None,
        to_cell=None,
        lock_token=lock_token,
        recipe=_guard_recipe([then, now]),
        freeze=freeze,
    )


def _write_steg_kvitto(
    outdir: Path,
    *,
    index: int,
    name: str,
    op: str,
    outcome: str,
    expected: dict[str, Any],
    observed: dict[str, Any] | None,
    manifest_sha256: str,
    note: str = "",
) -> Path:
    exp = ident_of(expected)
    obs = ident_of(observed) if observed else None
    doc = {
        "schema": SCHEMA_STEG,
        "steg": index,
        "name": name,
        "op": op,
        "outcome": outcome,
        "expected": exp,
        "expected_sha256": ident_sha256(exp),
        "observed": obs,
        "observed_sha256": ident_sha256(obs) if obs else None,
        "manifest_sha256": manifest_sha256,
        "note": note,
        "written_at": _utc(),
    }
    path = outdir / f"steg-{index:02d}-{name}.json"
    write_exclusive(path, json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return path


def run_deploy(
    ctl,
    *,
    manifest_path: Path | None = None,
    recept_path: Path | None = None,
    freeze: FreezeContext | None = None,
    lock_token: str | None = "fable",
    outdir: Path | None = None,
) -> dict[str, Any]:
    """Apply compose ops in order. Returns the durable run document."""
    manifest_path = Path(manifest_path or DEFAULT_MANIFEST)
    recept_path = Path(recept_path or DEFAULT_RECEPT)
    ctx = freeze if freeze is not None else FreezeContext.production()
    started = _utc()
    man, pin_live, man_sha = preflight(
        manifest_path=manifest_path, ctl=ctl, freeze=ctx
    )
    recept = load_recept(recept_path)
    ops = list(recept.get("ops") or [])
    steg_mut = apply_ops_of(man)
    if len(ops) != len(steg_mut):
        raise FailClosed(
            "deploy",
            f"recept har {len(ops)} ops, manifest {len(steg_mut)} muterande steg",
        )

    out = Path(outdir or Path("."))
    out.mkdir(parents=True, exist_ok=True)

    pin = ident_of(man["steg"][0].get("identitet") or man["steg"][0])
    slut = ident_of(man["slut"])
    applied: list[dict[str, Any]] = []
    steg_kvitton: list[str] = []
    abort_reason: str | None = None
    live = pin_live

    for i, (op, steg) in enumerate(zip(ops, steg_mut), start=1):
        want = ident_of(steg.get("identitet") or steg)
        name = str(steg.get("name") or op.get("name") or f"op{i}")
        kind = str(op.get("op") or steg.get("op") or "")
        rid = Path(str(op.get("kalla") or name)).stem
        if kind == "plan_link":
            rid = "compose"
        try:
            _apply_one(
                ctl, op, freeze=ctx, lock_token=lock_token,
                before=live, after=want,
            )
        except FailClosed:
            abort_reason = f"steg {i} ({name}) vägrades av grind"
            raise
        applied.append({"index": i, "name": name, "op": kind, "recipe_id": rid, "before": live, "after": want})
        live = live_identity(ctl)
        why = same_identity(live, want)
        if why:
            abort_reason = f"steg {i} ({name}): {why}"
            kpath = _write_steg_kvitto(
                out, index=i, name=name, op=kind, outcome="avvikelse",
                expected=want, observed=live, manifest_sha256=man_sha,
                note=abort_reason,
            )
            steg_kvitton.append(str(kpath))
            break
        kpath = _write_steg_kvitto(
            out, index=i, name=name, op=kind, outcome="ok",
            expected=want, observed=live, manifest_sha256=man_sha,
        )
        steg_kvitton.append(str(kpath))

    undid: list[str] = []
    if abort_reason:
        for rec in reversed(applied):
            _undo_one(
                ctl, rec, freeze=ctx, lock_token=lock_token,
                now=live, then=rec["before"],
            )
            live = live_identity(ctl)
            undid.append(rec["name"])
            _write_steg_kvitto(
                out, index=rec["index"], name=f"{rec['name']}-undo",
                op="undo", outcome="undo",
                expected=rec["before"], observed=live, manifest_sha256=man_sha,
            )
        why_pin = same_identity(live, pin)
        if why_pin:
            raise FailClosed(
                "crash-detector",
                f"undo nådde inte bas: {why_pin} (avbröt: {abort_reason})",
            )

    if not abort_reason:
        why_slut = same_identity(live, slut)
        if why_slut:
            abort_reason = f"slutverifiering: {why_slut}"
            # Treat as deviation after last op: undo everything.
            for rec in reversed(applied):
                _undo_one(
                    ctl, rec, freeze=ctx, lock_token=lock_token,
                    now=live, then=rec["before"],
                )
                live = live_identity(ctl)
                undid.append(rec["name"])
            why_pin = same_identity(live, pin)
            if why_pin:
                raise FailClosed(
                    "crash-detector",
                    f"undo nådde inte bas efter slutfel: {why_pin}",
                )

    ended = _utc()
    outcome = "aborted" if abort_reason else "applied"
    run_doc = {
        "schema": SCHEMA_RUN,
        "outcome": outcome,
        "abort_reason": abort_reason,
        "manifest_path": str(manifest_path),
        "manifest_sha256": man_sha,
        "recept_path": str(recept_path),
        "recept_id": man.get("recept_id"),
        "status": man.get("status"),
        "started_at": started,
        "ended_at": ended,
        "pin": pin,
        "pin_sha256": ident_sha256(pin),
        "slut_expected": slut,
        "slut_expected_sha256": ident_sha256(slut),
        "slut_observed": ident_of(live),
        "slut_observed_sha256": ident_sha256(live),
        "applied": [a["name"] for a in applied],
        "undid": undid,
        "steg_kvitton": steg_kvitton,
        "freeze": ctx.as_kvitto(),
        "n_ops": len(ops),
    }
    run_path = out / "deploy-run.json"
    write_exclusive(run_path, json.dumps(run_doc, indent=2, sort_keys=True) + "\n")
    run_doc["run_kvitto"] = str(run_path)
    return run_doc


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--recept", type=Path, default=DEFAULT_RECEPT)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--lock-token", default=None)
    args = ap.parse_args(argv)
    from runner.control import Control
    ctl = Control(args.host, args.port)
    try:
        doc = run_deploy(
            ctl,
            manifest_path=args.manifest,
            recept_path=args.recept,
            freeze=FreezeContext.production(),
            lock_token=args.lock_token,
            outdir=args.out,
        )
    except FailClosed as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        ctl.close()
    print(json.dumps({"outcome": doc["outcome"], "abort_reason": doc["abort_reason"]}, indent=2))
    return 0 if doc["outcome"] == "applied" else 1


if __name__ == "__main__":
    raise SystemExit(main())
