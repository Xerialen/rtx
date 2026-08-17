#!/usr/bin/env python3
"""Deploy-runner: sealed komponat-manifest/1 apply, fail-closed.

Varv 2 (Sol af56481): DeployContext after complete preflight; motor
nivå-2 is graph_content_hash_utan_params; exception-safe undo; PlanLink
undo uses recipe-id plan-link; SHA pin is basename-independent.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from d_failclosed import (
    DEPLOY_OK,
    DeployContext,
    FailClosed,
    FreezeContext,
    activate_deploy_context,
    check_change_freeze,
    check_deploy_status,
    clear_deploy_context,
    send_plan_link,
)
from d_kvitto import write_exclusive
from d_strata import FORBIDDEN_CTL, FORBIDDEN_GAME
from fixa import run_fixa, stamp_from_reply

HERE = Path(__file__).resolve().parent
DEFAULT_MANIFEST = HERE / "recept" / "komponat-v296-ram.manifest.json"
DEFAULT_RECEPT = HERE / "recept" / "komponat-v296-ram.json"

# Byte-pins. Compared always — never keyed on basename (Sol F1/F2).
SEALED_MANIFEST_SHA256 = (
    "bcba5897a9af7887d63fcf7466081a52d0bc84885c6d227002ca3d67727bc8e8"
)
SEALED_RECEPT_SHA256 = (
    "e327251e215a7a459e356fc7fa96e4d3d18f3e2dddb4c72412757614dd0df9ec"
)
# B2-binär (a564b4f). Not a facit-r2 seal; runner still refuse-mismatches.
EXPECTED_QWPROGS_SHA256 = (
    "65af0b5eb065c3f16f2a00a159af9b26f6c516bddc7d475a94983c73b334076b"
)

SCHEMA_RUN = "deploy-run/1"
SCHEMA_STEG = "deploy-steg-kvitto/1"
PLAN_LINK_UNDO_ID = "plan-link"


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def motor_ident(block: dict[str, Any]) -> dict[str, Any]:
    """Identity the engine can see: FNV + graph_content_hash_utan_params.

    Live fixa replies carry only the motor hash. Comparing them to the
    params-bearing graph_content_hash is Sol F3 and aborts a correct graph.
    """
    utan = block.get("graph_content_hash_utan_params")
    params = block.get("graph_content_hash")
    if not utan:
        utan = params
    if not utan:
        raise FailClosed("crash-detector", "identitet saknar nivå-2 hash")
    return {
        "cells": int(block["cells"]),
        "links": int(block["links"]),
        "rj_links": int(block.get("rj_links") or 0),
        "graph_stamp": str(block["graph_stamp"]),
        "graph_content_hash": str(utan),
        "graph_content_hash_params": str(params or utan),
    }


def live_to_motor(live: dict[str, Any]) -> dict[str, Any]:
    """Normalize a fixa/status reply (hash already motor-comparable)."""
    sha = live.get("graph_content_hash") or live.get("content_hash")
    return {
        "cells": int(live["cells"]),
        "links": int(live["links"]),
        "rj_links": int(live.get("rj_links") or 0),
        "graph_stamp": str(live.get("graph_stamp") or live.get("stamp")),
        "graph_content_hash": str(sha),
        "graph_content_hash_params": str(
            live.get("graph_content_hash_params") or sha
        ),
    }


def ident_sha256(ident: dict[str, Any]) -> str:
    key = {
        "cells": ident["cells"],
        "links": ident["links"],
        "rj_links": ident["rj_links"],
        "graph_stamp": ident["graph_stamp"],
        "graph_content_hash": ident["graph_content_hash"],
    }
    blob = json.dumps(key, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def same_identity(live: dict[str, Any], want: dict[str, Any]) -> str | None:
    """Compare motor hashes (utan_params) + FNV + counts."""
    a, b = live_to_motor(live), live_to_motor(want)
    if a["graph_stamp"] != b["graph_stamp"] or a["graph_content_hash"] != b["graph_content_hash"]:
        return (
            f"live ≠ motor-identitet "
            f"(FNV {a['graph_stamp']} utan={a['graph_content_hash'][:16]}… "
            f"≠ FNV {b['graph_stamp']} utan={b['graph_content_hash'][:16]}…; "
            f"counts {a['cells']}/{a['links']} vs {b['cells']}/{b['links']}) "
            f"— jämför graph_content_hash_utan_params, inte params-hash "
            f"(5983/48214-kollisionen)"
        )
    if (a["cells"], a["links"], a["rj_links"]) != (b["cells"], b["links"], b["rj_links"]):
        return (
            f"motorhash matchar men counts skiljer "
            f"{a['cells']}/{a['links']} ≠ {b['cells']}/{b['links']}"
        )
    return None


def _step_recipe(manifest: dict[str, Any], identities: list[dict[str, Any]]) -> dict[str, Any]:
    """DEPLOY-KANDIDAT wrapper: child live-stamps + compose status for deploy=True."""
    sealed = [live_to_motor(i) for i in identities]
    return {
        "id": manifest.get("recept_id") or "v296-ram",
        "recept_id": manifest.get("recept_id") or "v296-ram",
        "schema": "komponat-manifest/1",
        "status": DEPLOY_OK,
        "status_skal": "delsteg under aktiv DeployContext; inte fristående LABB-apply",
        "off": sealed[0],
        "on_expected": sealed[-1],
        "sealed_stamps": sealed,
    }


def live_identity(ctl) -> dict[str, Any]:
    reply = run_fixa(
        ctl,
        recipe_id="west-shelf",
        mode="dry-run",
        from_cell=None,
        to_cell=None,
    )
    return live_to_motor(stamp_from_reply(reply))


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


def mutating_steg(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    steg = list(manifest.get("steg") or [])
    return [s for s in steg if str(s.get("op") or "") != "pin"]


def op_projection(op: dict[str, Any]) -> tuple[Any, ...]:
    kind = str(op.get("op") or "")
    name = str(op.get("name") or "")
    if kind == "shelf_patch":
        return (kind, name, Path(str(op.get("kalla") or name)).stem)
    return (kind, name)


def bind_ops(recept: dict[str, Any], manifest: dict[str, Any]) -> None:
    """Refuse extra/missing/reordered ops before any mutation (Sol F2)."""
    if recept.get("id") != manifest.get("recept_id"):
        raise FailClosed(
            "deploy",
            f"recept id {recept.get('id')!r} ≠ manifest recept_id "
            f"{manifest.get('recept_id')!r}",
        )
    if recept.get("map") != manifest.get("map"):
        raise FailClosed(
            "deploy",
            f"recept map {recept.get('map')!r} ≠ manifest map {manifest.get('map')!r}",
        )
    steg = list(manifest.get("steg") or [])
    if [s.get("index") for s in steg] != [0, 1, 2, 3]:
        raise FailClosed("deploy", "manifest kräver exakt stegindex 0..3")
    if str(steg[0].get("op") or "") != "pin":
        raise FailClosed("deploy", "steg 0 måste vara pin")
    mut = mutating_steg(manifest)
    ops = list(recept.get("ops") or [])
    if len(ops) != 3 or len(mut) != 3:
        raise FailClosed(
            "deploy",
            f"förseglad op-ordning är 3 steg, fick recept={len(ops)} "
            f"manifest-mut={len(mut)}",
        )
    for i, (op, st) in enumerate(zip(ops, mut), start=1):
        if int(st.get("index") or -1) != i:
            raise FailClosed("deploy", f"steg {i} har index {st.get('index')!r}")
        if op_projection(op) != op_projection(st):
            raise FailClosed(
                "deploy",
                f"steg {i}: recept {op_projection(op)} ≠ manifest {op_projection(st)}",
            )


def check_portvakt(ctl_port: int | None, game_port: int | None) -> None:
    if ctl_port is not None and ctl_port in FORBIDDEN_CTL:
        raise FailClosed("portvakt", f"ctl port {ctl_port} är RA/main")
    if game_port is not None and game_port in FORBIDDEN_GAME:
        raise FailClosed("portvakt", f"game port {game_port} är RA/main")
    if ctl_port is not None and ctl_port in FORBIDDEN_GAME:
        raise FailClosed("portvakt", f"ctl port {ctl_port} är RA/main-spelport")


def check_rig_lock(lock_path: Path, freeze: FreezeContext | None) -> str:
    check_change_freeze(freeze)
    path = Path(lock_path)
    if not path.is_file():
        raise FailClosed("lock", f"ingen rig-lock {path}")
    body = path.read_text(encoding="utf-8", errors="replace").strip()
    if not body:
        raise FailClosed("lock", f"{path} är tom")
    return body.split()[0]


def check_binary_pin(qwprogs_sha256: str | None) -> str:
    if not qwprogs_sha256:
        raise FailClosed("binary", "qwprogs-sha saknas — binärpin krävs i preflight")
    got = str(qwprogs_sha256).strip().lower()
    want = EXPECTED_QWPROGS_SHA256.lower()
    if got != want:
        raise FailClosed(
            "binary",
            f"qwprogs SHA-256 {got} ≠ förväntad {want}",
        )
    return got


def _reserve_outdir(outdir: Path, names: list[str]) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    for name in names:
        p = outdir / name
        if p.exists():
            raise FailClosed("kvitto", f"output {p} finns redan — reservera före mutation")


def preflight(
    *,
    manifest_path: Path,
    recept_path: Path,
    ctl,
    freeze: FreezeContext | None,
    lock_path: Path | None,
    qwprogs_sha256: str | None,
    ctl_port: int | None,
    game_port: int | None,
) -> tuple[dict[str, Any], dict[str, Any], str, str, str, str]:
    """SHA+recept-sigill+status+pin+portvakt+lock+binär. Then caller activates context."""
    manifest_path = Path(manifest_path)
    recept_path = Path(recept_path)
    man_sha = file_sha256(manifest_path)
    if man_sha != SEALED_MANIFEST_SHA256:
        raise FailClosed(
            "crash-detector",
            f"manifest SHA-256 {man_sha} ≠ förseglad {SEALED_MANIFEST_SHA256} "
            f"(okänd identitet vägras oavsett filnamn)",
        )
    rec_sha = file_sha256(recept_path)
    if rec_sha != SEALED_RECEPT_SHA256:
        raise FailClosed(
            "crash-detector",
            f"recept SHA-256 {rec_sha} ≠ förseglad {SEALED_RECEPT_SHA256}",
        )
    man = load_manifest(manifest_path)
    rec = load_recept(recept_path)
    check_deploy_status(man, deploy=True)
    check_deploy_status(rec, deploy=True)
    bind_ops(rec, man)
    check_portvakt(ctl_port, game_port)
    lock_owner = ""
    if lock_path is not None:
        lock_owner = check_rig_lock(Path(lock_path), freeze)
    else:
        raise FailClosed("lock", "rig-lock krävs i deploy-preflight")
    check_binary_pin(qwprogs_sha256)
    check_change_freeze(freeze)
    live = live_identity(ctl)
    pin = motor_ident(man["steg"][0].get("identitet") or man["steg"][0])
    why = same_identity(live, pin)
    if why:
        raise FailClosed("crash-detector", f"pin=bas misslyckades: {why}")
    return man, rec, live, man_sha, rec_sha, lock_owner


def _apply_one(
    ctl,
    op: dict[str, Any],
    *,
    freeze: FreezeContext,
    lock_token: str | None,
    before: dict[str, Any],
    after: dict[str, Any],
    deploy_ctx: DeployContext,
    manifest: dict[str, Any],
) -> None:
    kind = str(op.get("op") or "")
    syn = _step_recipe(manifest, [before, after])
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
        send_plan_link(
            ctl, payload, recipe=syn, freeze=freeze,
            deploy=True, deploy_ctx=deploy_ctx,
        )
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
            recipe=syn,
            freeze=freeze,
            deploy=True,
            deploy_ctx=deploy_ctx,
        )
        return
    raise FailClosed("deploy", f"okänd op {kind!r}")


def _undo_one(
    ctl,
    applied: dict[str, Any],
    *,
    freeze: FreezeContext,
    lock_token: str | None,
    now: dict[str, Any],
    then: dict[str, Any],
    deploy_ctx: DeployContext,
    manifest: dict[str, Any],
) -> None:
    rid = applied.get("recipe_id") or PLAN_LINK_UNDO_ID
    run_fixa(
        ctl,
        recipe_id=rid,
        mode="undo",
        from_cell=None,
        to_cell=None,
        lock_token=lock_token,
        recipe=_step_recipe(manifest, [then, now]),
        freeze=freeze,
        deploy=True,
        deploy_ctx=deploy_ctx,
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
    exp = live_to_motor(expected)
    obs = live_to_motor(observed) if observed else None
    doc = {
        "schema": SCHEMA_STEG,
        "steg": index,
        "name": name,
        "op": op,
        "outcome": outcome,
        "expected_motor": exp,
        "expected_motor_sha256": ident_sha256(exp),
        "expected_params_hash": exp.get("graph_content_hash_params"),
        "observed_motor": obs,
        "observed_motor_sha256": ident_sha256(obs) if obs else None,
        "manifest_sha256": manifest_sha256,
        "note": note,
        "written_at": _utc(),
    }
    path = outdir / f"steg-{index:02d}-{name}.json"
    write_exclusive(path, json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return path


def _safe_undo(
    ctl,
    applied: list[dict[str, Any]],
    *,
    live: dict[str, Any],
    pin: dict[str, Any],
    freeze: FreezeContext,
    lock_token: str | None,
    deploy_ctx: DeployContext,
    out: Path,
    man_sha: str,
    steg_kvitton: list[str],
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], list[str], str | None]:
    """Pop every applied frame. Verify each waypoint. Never swallow leftover."""
    undid: list[str] = []
    leftover: str | None = None
    for rec in reversed(applied):
        try:
            _undo_one(
                ctl, rec, freeze=freeze, lock_token=lock_token,
                now=live, then=rec["before"], deploy_ctx=deploy_ctx,
                manifest=manifest,
            )
            live = live_identity(ctl)
            why = same_identity(live, rec["before"])
            if why:
                leftover = f"undo-waypoint {rec['name']}: {why}"
                break
            undid.append(rec["name"])
            kpath = _write_steg_kvitto(
                out, index=rec["index"], name=f"{rec['name']}-undo",
                op="undo", outcome="undo",
                expected=rec["before"], observed=live, manifest_sha256=man_sha,
            )
            steg_kvitton.append(str(kpath))
        except Exception as exc:
            leftover = f"undo av {rec['name']} misslyckades: {exc}"
            break
    if leftover is None:
        why_pin = same_identity(live, pin)
        if why_pin:
            leftover = f"undo nådde inte bas: {why_pin}"
    return live, undid, leftover


def _write_run(path: Path, doc: dict[str, Any]) -> None:
    try:
        write_exclusive(path, json.dumps(doc, indent=2, sort_keys=True) + "\n")
    except FileExistsError:
        alt = path.with_name(path.stem + "-recovery.json")
        write_exclusive(alt, json.dumps(doc, indent=2, sort_keys=True) + "\n")
        doc["run_kvitto_recovery"] = str(alt)


def run_deploy(
    ctl,
    *,
    manifest_path: Path | None = None,
    recept_path: Path | None = None,
    freeze: FreezeContext | None = None,
    lock_token: str | None = None,
    lock_path: Path | None = None,
    qwprogs_sha256: str | None = None,
    mvdsv_sha256: str | None = None,
    ctl_port: int | None = None,
    game_port: int | None = None,
    unit: str | None = None,
    commit: str | None = None,
    outdir: Path | None = None,
) -> dict[str, Any]:
    manifest_path = Path(manifest_path or DEFAULT_MANIFEST)
    recept_path = Path(recept_path or DEFAULT_RECEPT)
    ctx = freeze if freeze is not None else FreezeContext.production()
    started = _utc()
    out = Path(outdir or Path("."))
    out.mkdir(parents=True, exist_ok=True)
    run_path = out / "deploy-run.json"
    deploy_ctx: DeployContext | None = None
    applied: list[dict[str, Any]] = []
    steg_kvitton: list[str] = []
    abort_reason: str | None = None
    leftover: str | None = None
    undid: list[str] = []
    man: dict[str, Any] = {}
    live: dict[str, Any] | None = None
    man_sha = ""
    rec_sha = ""
    lock_owner = ""
    pin: dict[str, Any] = {}
    slut: dict[str, Any] = {}

    def _run_doc(outcome: str) -> dict[str, Any]:
        return {
            "schema": SCHEMA_RUN,
            "outcome": outcome,
            "abort_reason": abort_reason,
            "leftover": leftover,
            "manifest_path": str(manifest_path),
            "manifest_sha256": man_sha,
            "recept_path": str(recept_path),
            "recept_sha256": rec_sha,
            "recept_id": man.get("recept_id"),
            "status": man.get("status"),
            "started_at": started,
            "ended_at": _utc(),
            "runner_commit": commit or "unknown",
            "qwprogs_sha256": qwprogs_sha256,
            "mvdsv_sha256": mvdsv_sha256,
            "unit": unit,
            "ctl_port": ctl_port,
            "game_port": game_port,
            "lock_path": str(lock_path) if lock_path else None,
            "lock_owner": lock_owner,
            "lock_token": lock_token,
            "pin": pin or None,
            "pin_sha256": ident_sha256(pin) if pin else None,
            "slut_expected": slut or None,
            "slut_expected_sha256": ident_sha256(slut) if slut else None,
            "slut_observed": live,
            "slut_observed_sha256": ident_sha256(live) if live else None,
            "applied": [a["name"] for a in applied],
            "undid": undid,
            "steg_kvitton": steg_kvitton,
            "freeze": ctx.as_kvitto(),
            "n_ops": len(applied) if abort_reason else 3,
        }

    try:
        man, recept, live, man_sha, rec_sha, lock_owner = preflight(
            manifest_path=manifest_path,
            recept_path=recept_path,
            ctl=ctl,
            freeze=ctx,
            lock_path=lock_path,
            qwprogs_sha256=qwprogs_sha256,
            ctl_port=ctl_port,
            game_port=game_port,
        )
        pin = motor_ident(man["steg"][0].get("identitet") or man["steg"][0])
        slut = motor_ident(man["slut"])
        ops = list(recept.get("ops") or [])
        steg_mut = mutating_steg(man)
        reserved = ["deploy-run.json"]
        for i, st in enumerate(steg_mut, start=1):
            reserved.append(f"steg-{i:02d}-{st['name']}.json")
            reserved.append(f"steg-{i:02d}-{st['name']}-undo.json")
        _reserve_outdir(out, reserved)

        deploy_ctx = DeployContext(
            token=secrets.token_hex(16),
            manifest_sha256=man_sha,
            recept_sha256=rec_sha,
        )
        activate_deploy_context(deploy_ctx)

        token = lock_token or lock_owner
        for i, (op, steg) in enumerate(zip(ops, steg_mut), start=1):
            want = motor_ident(steg.get("identitet") or steg)
            name = str(steg.get("name") or op.get("name") or f"op{i}")
            kind = str(op.get("op") or steg.get("op") or "")
            rid = Path(str(op.get("kalla") or name)).stem
            if kind == "plan_link":
                rid = PLAN_LINK_UNDO_ID
            try:
                _apply_one(
                    ctl, op, freeze=ctx, lock_token=token,
                    before=live, after=want, deploy_ctx=deploy_ctx,
                    manifest=man,
                )
            except Exception as exc:
                abort_reason = f"steg {i} ({name}) vägrades: {exc}"
                break
            applied.append({
                "index": i, "name": name, "op": kind,
                "recipe_id": rid, "before": live, "after": want,
            })
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

        if not abort_reason:
            why_slut = same_identity(live, slut)
            if why_slut:
                abort_reason = f"slutverifiering: {why_slut}"

        if abort_reason and applied and deploy_ctx is not None:
            live, undid, leftover = _safe_undo(
                ctl, applied, live=live, pin=pin, freeze=ctx,
                lock_token=token, deploy_ctx=deploy_ctx, out=out,
                man_sha=man_sha, steg_kvitton=steg_kvitton, manifest=man,
            )
    except FailClosed as exc:
        abort_reason = abort_reason or str(exc)
        if applied and deploy_ctx is not None and live is not None:
            try:
                live, undid, leftover = _safe_undo(
                    ctl, applied, live=live, pin=pin, freeze=ctx,
                    lock_token=lock_token or lock_owner, deploy_ctx=deploy_ctx,
                    out=out, man_sha=man_sha, steg_kvitton=steg_kvitton,
                    manifest=man,
                )
            except Exception as undo_exc:
                leftover = f"undo-recovery misslyckades: {undo_exc}"
        doc = _run_doc("aborted")
        _write_run(run_path, doc)
        doc["run_kvitto"] = str(run_path)
        if leftover:
            raise FailClosed("crash-detector", leftover) from exc
        raise
    finally:
        if deploy_ctx is not None:
            clear_deploy_context(deploy_ctx.token)

    outcome = "aborted" if abort_reason else "applied"
    if leftover:
        outcome = "aborted"
        abort_reason = (abort_reason or "") + f" | KRITISKT RESTTILLSTÅND: {leftover}"
    doc = _run_doc(outcome)
    _write_run(run_path, doc)
    doc["run_kvitto"] = str(run_path)
    if leftover:
        raise FailClosed("crash-detector", leftover)
    return doc


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--recept", type=Path, default=DEFAULT_RECEPT)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--game-port", type=int, default=0)
    ap.add_argument("--lock", type=Path, required=True)
    ap.add_argument("--lock-token", default=None)
    ap.add_argument("--qwprogs-sha256", required=True)
    ap.add_argument("--mvdsv-sha256", default=None)
    ap.add_argument("--unit", default=None)
    ap.add_argument("--commit", default="unknown")
    args = ap.parse_args(argv)
    check_portvakt(args.port, args.game_port)
    from runner.control import Control
    ctl = Control(args.host, args.port)
    try:
        doc = run_deploy(
            ctl,
            manifest_path=args.manifest,
            recept_path=args.recept,
            freeze=FreezeContext.production(),
            lock_token=args.lock_token,
            lock_path=args.lock,
            qwprogs_sha256=args.qwprogs_sha256,
            mvdsv_sha256=args.mvdsv_sha256,
            ctl_port=args.port,
            game_port=args.game_port,
            unit=args.unit,
            commit=args.commit,
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
