#!/usr/bin/env python3
"""Deploy-runner: sealed komponat-manifest/1 via one atomic Komponat verb.

Preflight (manifest-sha, status, pin, lock, binary pin) is unchanged.
The deploy path sends the whole op-list as one structured Komponat cmd.
Per-step apply/undo and chain-top reads stay lab tools, not deploy.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from d_failclosed import (
    ALLOWED_DEPLOY_PAIRS,
    CAMPAIGN_OWNER,
    DEPLOY_OK,
    BoundStep,
    DeployContext,
    FailClosed,
    FreezeContext,
    LOCK_TS_WINDOW_S,
    PLAN_LINK_UNDO_ID,
    SEALED_MANIFEST_SHA256,
    validate_op_arter,
    sealed_manifest_for,
    SEALED_MVDSV_SHA256,
    SEALED_QWPROGS_SHA256,
    SEALED_RECEPT_SHA256,
    _issue_preflight_seal_from_files,
    check_change_freeze,
    check_deploy_status,
    clear_deploy_context,
    komponat_wire_cmd,
    mint_deploy_context,
    op_payload_sha256,
    parse_komponat_reply,
    read_engine_chain,
    sealed_binary_shas,
)
from d_kvitto import write_exclusive
from fixa import run_fixa, stamp_from_reply
from recept_lint_grind import kor_grind as kor_lintgrind

HERE = Path(__file__).resolve().parent
DEFAULT_MANIFEST = HERE / "recept" / "komponat-v296-ram.manifest.json"
DEFAULT_RECEPT = HERE / "recept" / "komponat-v296-ram.json"

# Re-export sealed pins (single source: d_failclosed).
EXPECTED_QWPROGS_SHA256 = SEALED_QWPROGS_SHA256
EXPECTED_MVDSV_SHA256 = SEALED_MVDSV_SHA256

SCHEMA_RUN = "deploy-run/1"
LOCK_REQUIRED = (
    "owner",
    "unit",
    "ctl_port",
    "game_port",
    "token",
    "qwprogs_sha256",
    "mvdsv_sha256",
    "ts",
)


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


def read_undo_chain(ctl) -> dict[str, Any]:
    """Read-only. No lock. Recovery must use this, never guess the top."""
    return read_engine_chain(ctl)


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
    # Antalet steg är komponatets, inte apparatens. Trean var v296-ram-komponatets
    # op-antal och låste runnern vid just det receptet; på/av-provets varianter är
    # ett op var och kunde därför inte köras alls. Kravet generaliseras till
    # "sammanhängande 0..N med pin först", medan den parvisa projektionskontrollen
    # nedan står orörd — det är DEN som uppfyller Sols F2 (extra/saknad/omkastad op
    # vägras), inte siffran.
    steg = list(manifest.get("steg") or [])
    if not steg:
        raise FailClosed("deploy", "manifest saknar steg")
    if [s.get("index") for s in steg] != list(range(len(steg))):
        raise FailClosed(
            "deploy",
            f"manifest kräver sammanhängande stegindex 0..{len(steg) - 1}, fick "
            f"{[s.get('index') for s in steg]}",
        )
    if str(steg[0].get("op") or "") != "pin":
        raise FailClosed("deploy", "steg 0 måste vara pin")
    mut = mutating_steg(manifest)
    ops = list(recept.get("ops") or [])
    if len(ops) < 1 or len(ops) != len(mut):
        raise FailClosed(
            "deploy",
            f"op-antalet måste vara minst 1 och lika i recept och manifest, fick "
            f"recept={len(ops)} manifest-mut={len(mut)}",
        )
    for i, (op, st) in enumerate(zip(ops, mut), start=1):
        if int(st.get("index") or -1) != i:
            raise FailClosed("deploy", f"steg {i} har index {st.get('index')!r}")
        if op_projection(op) != op_projection(st):
            raise FailClosed(
                "deploy",
                f"steg {i}: recept {op_projection(op)} ≠ manifest {op_projection(st)}",
            )


def bound_steps(recept: dict[str, Any]) -> tuple[BoundStep, ...]:
    out: list[BoundStep] = []
    for i, op in enumerate(recept.get("ops") or [], start=1):
        kind = str(op.get("op") or "")
        name = str(op.get("name") or f"op{i}")
        if kind == "plan_link":
            rid = PLAN_LINK_UNDO_ID
        elif kind == "remove_links":
            # Ingen tabellpost att namnge; komponatet är ändå EN undo-post.
            rid = "komponat"
        else:
            rid = Path(str(op.get("kalla") or name)).stem
        out.append(
            BoundStep(
                index=i,
                kind=kind,
                name=name,
                recipe_id=rid,
                payload_sha256=op_payload_sha256(op),
            )
        )
    return tuple(out)


def check_portvakt(
    ctl_port: int | None,
    game_port: int | None,
    unit: str | None = None,
) -> str:
    """Exact d1 or d3 as an atomic pair. d2/d4/0/0/mixed/RA are refused."""
    if ctl_port is None or game_port is None:
        raise FailClosed("portvakt", "ctl- och game-port krävs som par")
    pair = (int(ctl_port), int(game_port))
    if unit:
        if unit not in ALLOWED_DEPLOY_PAIRS:
            raise FailClosed(
                "portvakt",
                f"unit {unit!r} är inte tbx-d1 eller tbx-d3",
            )
        want = ALLOWED_DEPLOY_PAIRS[unit]
        if pair != want:
            raise FailClosed(
                "portvakt",
                f"{unit} kräver par {want[0]}/{want[1]}, fick {pair[0]}/{pair[1]}",
            )
        return unit
    matches = [u for u, p in ALLOWED_DEPLOY_PAIRS.items() if p == pair]
    if len(matches) != 1:
        raise FailClosed(
            "portvakt",
            f"portpar {pair[0]}/{pair[1]} är inte d1 (27996/27592) eller "
            f"d3 (27998/27594) — paret valideras atomärt",
        )
    return matches[0]


def _rig_lock_lines(body: str):
    """Mirror Rust ``str::lines()``: split on LF / CRLF only.

    Lone CR is not a line break (unlike ``str.splitlines()``). A trailing
    newline does not yield an empty last line. A lone trailing CR stays
    on the last line — later ``str.strip()`` / Rust ``trim()`` treat it as
    whitespace, same as the proto contract.
    """
    if not body:
        return
    ended_with_nl = body.endswith("\n")
    core = body[:-1] if ended_with_nl else body
    parts = core.split("\n")
    n = len(parts)
    for i, line in enumerate(parts):
        followed_by_nl = (i < n - 1) or ended_with_nl
        if followed_by_nl and line.endswith("\r"):
            line = line[:-1]
        yield line


def rig_lock_declared_token(body: str) -> str | None:
    """Mirror rtx-ctlproto::rig_lock_declared_token. None if empty or contradictory."""
    found: str | None = None
    for line in _rig_lock_lines(body):
        stripped = line.strip()
        if not stripped.startswith("token="):
            continue
        val = stripped[len("token="):].strip()
        if found is not None and found != val:
            return None
        found = val
    if found == "":
        return None
    return found


def rig_lock_accepts(body: str, token: str) -> bool:
    """Mirror rtx-ctlproto::rig_lock_accepts."""
    body, token = body.strip(), token.strip()
    if not body or not token:
        return False
    if any(line.strip().startswith("token=") for line in _rig_lock_lines(body)):
        return rig_lock_declared_token(body) == token
    first = body.split()[0] if body.split() else ""
    return token == body or token == first


def parse_deploy_lock(path: Path) -> dict[str, str]:
    """Parse campaign/bridge lock fields. Bare first line is skipped (no `=`).

    A file that names `token=` but contradicts itself or leaves it empty is
    refused here — it does not fall back to the first field.

    Bytes are decoded without universal-newline translation so a lone CR
    reaches the Rust-``lines()`` mirror the same way ``read_to_string`` does.
    """
    raw = Path(path).read_bytes().decode("utf-8", errors="replace")
    has_token_line = any(
        line.strip().startswith("token=") for line in _rig_lock_lines(raw)
    )
    declared = rig_lock_declared_token(raw) if has_token_line else None
    if has_token_line and declared is None:
        raise FailClosed(
            "lock",
            f"{path} har motsägelsefull eller tom token=-deklaration — vägran",
        )
    fields: dict[str, str] = {}
    for line in _rig_lock_lines(raw):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        fields[key.strip()] = val.strip()
    if declared is not None:
        fields["token"] = declared
    return fields


def check_rig_lock(
    lock_path: Path,
    *,
    freeze: FreezeContext | None,
    unit: str,
    ctl_port: int,
    game_port: int,
    token: str,
    qwprogs_sha256: str,
    mvdsv_sha256: str,
) -> dict[str, str]:
    check_change_freeze(freeze)
    path = Path(lock_path)
    if not path.is_file():
        raise FailClosed("lock", f"ingen rig-lock {path}")
    fields = parse_deploy_lock(path)
    missing = [k for k in LOCK_REQUIRED if not fields.get(k)]
    if missing:
        raise FailClosed(
            "lock",
            f"{path} saknar bindande fält {missing} "
            f"(unit+båda portar+kampanjtoken+båda binär-sha krävs)",
        )
    if fields["unit"] != unit:
        raise FailClosed("lock", f"lock unit {fields['unit']!r} ≠ vald {unit!r}")
    if int(fields["ctl_port"]) != int(ctl_port) or int(fields["game_port"]) != int(game_port):
        raise FailClosed(
            "lock",
            f"lock portpar {fields['ctl_port']}/{fields['game_port']} "
            f"≠ {ctl_port}/{game_port}",
        )
    if fields["owner"] != CAMPAIGN_OWNER:
        raise FailClosed(
            "lock",
            f"lock owner {fields['owner']!r} ≠ kampanjidentitet {CAMPAIGN_OWNER!r}",
        )
    try:
        lock_ts = datetime.strptime(fields["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise FailClosed(
            "lock",
            f"lock ts {fields['ts']!r} är inte ISO-8601 Z — vägran",
        ) from exc
    age = abs((datetime.now(timezone.utc) - lock_ts).total_seconds())
    if age > LOCK_TS_WINDOW_S:
        raise FailClosed(
            "lock",
            f"lock ts {fields['ts']} utanför ±{LOCK_TS_WINDOW_S // 3600}h-fönster",
        )
    body = path.read_text(encoding="utf-8", errors="replace")
    if not rig_lock_accepts(body, token):
        raise FailClosed("lock", "lock kampanjtoken matchar inte runnerns token")
    if fields["qwprogs_sha256"].lower() != qwprogs_sha256.lower():
        raise FailClosed("lock", "lock qwprogs-sha ≠ hashad staged/live-fil")
    if fields["mvdsv_sha256"].lower() != mvdsv_sha256.lower():
        raise FailClosed("lock", "lock mvdsv-sha ≠ hashad staged/live-fil")
    return fields


def check_binary_pin(
    qwprogs_path: Path | str | None,
    mvdsv_path: Path | str | None,
) -> tuple[str, str]:
    """Hash staged/live files against sealed (or injected test-pin) constants."""
    if not qwprogs_path or not Path(qwprogs_path).is_file():
        raise FailClosed(
            "binary",
            "qwprogs-fil saknas — binärpin hashar staged/live-fil, inte callersträng",
        )
    if not mvdsv_path or not Path(mvdsv_path).is_file():
        raise FailClosed(
            "binary",
            "mvdsv-fil saknas — binärpin hashar staged/live-fil, inte callersträng",
        )
    got_q = file_sha256(Path(qwprogs_path))
    got_m = file_sha256(Path(mvdsv_path))
    want_q, want_m = sealed_binary_shas()
    if got_q != want_q:
        raise FailClosed("binary", f"qwprogs-fil SHA-256 {got_q} ≠ förväntad {want_q}")
    if got_m != want_m:
        raise FailClosed("binary", f"mvdsv-fil SHA-256 {got_m} ≠ förväntad {want_m}")
    return got_q, got_m


def _reserve_outdir(outdir: Path, names: list[str]) -> dict[str, Path]:
    """O_EXCL-create every receipt path before the first mutation."""
    outdir.mkdir(parents=True, exist_ok=True)
    reserved: dict[str, Path] = {}
    for name in names:
        p = outdir / name
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError as exc:
            raise FailClosed("kvitto", f"output {p} finns redan — reservera före mutation") from exc
        os.close(fd)
        reserved[name] = p
    return reserved


def _write_reserved(path: Path, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def preflight(
    *,
    manifest_path: Path,
    recept_path: Path,
    ctl,
    freeze: FreezeContext | None,
    lock_path: Path | None,
    lock_token: str | None,
    qwprogs_path: Path | str | None,
    mvdsv_path: Path | str | None,
    ctl_port: int | None,
    game_port: int | None,
    unit: str | None,
    dumpregister: Path | None = None,
):
    """SHA+recept+status+bind+portpar+binärfiler+lock+pin+enväg. Issues the mint seal."""
    manifest_path = Path(manifest_path)
    recept_path = Path(recept_path)
    man_sha = file_sha256(manifest_path)
    rec_sha = file_sha256(recept_path)
    # Receptet slås upp i den förseglade mängden; manifestet måste vara dess bundna
    # motpart. Ett giltigt recept med ett annat giltigt manifest är lika förbjudet
    # som ett okänt recept — parbindningen ÄR förseglingen.
    want_man = sealed_manifest_for(rec_sha, gate="crash-detector")
    if man_sha != want_man:
        raise FailClosed(
            "crash-detector",
            f"manifest SHA-256 {man_sha} ≠ {want_man} (manifestet som är bundet till "
            f"recept {rec_sha}) — okänd identitet vägras oavsett filnamn",
        )
    man = load_manifest(manifest_path)
    rec = load_recept(recept_path)
    # Nattens läxa, som en grind: receptets op-arter mot verbets ordförråd, före
    # allt annat. Upptäcktes den vid apply kostade den ett helt varv.
    validate_op_arter(rec)
    check_deploy_status(man, deploy=True)
    check_deploy_status(rec, deploy=True)
    bind_ops(rec, man)
    unit_id = check_portvakt(ctl_port, game_port, unit)
    qw_sha, mv_sha = check_binary_pin(qwprogs_path, mvdsv_path)
    if lock_path is None:
        raise FailClosed("lock", "rig-lock krävs i deploy-preflight")
    if not lock_token:
        raise FailClosed("lock", "kampanjtoken krävs och måste bindas i låset")
    lock_fields = check_rig_lock(
        Path(lock_path),
        freeze=freeze,
        unit=unit_id,
        ctl_port=int(ctl_port),
        game_port=int(game_port),
        token=lock_token,
        qwprogs_sha256=qw_sha,
        mvdsv_sha256=mv_sha,
    )
    check_change_freeze(freeze)
    live = live_identity(ctl)
    pin = motor_ident(man["steg"][0].get("identitet") or man["steg"][0])
    why = same_identity(live, pin)
    if why:
        raise FailClosed("crash-detector", f"pin=bas misslyckades: {why}")
    # Envägsgrinden. Först HÄR är live bevisad lika med manifestets pin, så dumpen
    # kan bindas till en identitet som är verifierad i stället för påstådd. Linten
    # fanns redan och hittade F-fällan; den kördes av den som kom ihåg den, och ett
    # verktyg som måste kommas ihåg är en vana, inte en kontroll.
    lint_block = kor_lintgrind(rec, live, dumpregister=dumpregister)
    seal = _issue_preflight_seal_from_files(
        manifest_path=manifest_path,
        recept_path=recept_path,
        qwprogs_path=qwprogs_path,
        mvdsv_path=mvdsv_path,
    )
    # seal ligger SIST: `*_, seal = preflight(...)` är etablerad idiom hos
    # anroparna, och ett nytt fält i slutet hade tyst gjort lint_block till seal.
    return (
        man, rec, live, man_sha, rec_sha, lock_fields,
        qw_sha, mv_sha, unit_id, lint_block, seal,
    )


def _write_run(path: Path, doc: dict[str, Any]) -> None:
    body = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    try:
        if path.exists():
            _write_reserved(path, body)
        else:
            write_exclusive(path, body)
    except Exception:
        alt = path.with_name(path.stem + "-recovery.json")
        try:
            if alt.exists():
                _write_reserved(alt, body)
            else:
                write_exclusive(alt, body)
            doc["run_kvitto_recovery"] = str(alt)
        except Exception:
            doc["run_kvitto_write_failed"] = True


def run_deploy(
    ctl,
    *,
    manifest_path: Path | None = None,
    recept_path: Path | None = None,
    freeze: FreezeContext | None = None,
    lock_token: str | None = None,
    lock_path: Path | None = None,
    qwprogs_path: Path | str | None = None,
    mvdsv_path: Path | str | None = None,
    ctl_port: int | None = None,
    game_port: int | None = None,
    unit: str | None = None,
    commit: str | None = None,
    outdir: Path | None = None,
    dumpregister: Path | None = None,
) -> dict[str, Any]:
    manifest_path = Path(manifest_path or DEFAULT_MANIFEST)
    recept_path = Path(recept_path or DEFAULT_RECEPT)
    ctx = freeze if freeze is not None else FreezeContext.production()
    started = _utc()
    out = Path(outdir or Path("."))
    out.mkdir(parents=True, exist_ok=True)
    run_path = out / "deploy-run.json"
    deploy_ctx: DeployContext | None = None
    abort_reason: str | None = None
    leftover: str | None = None
    man: dict[str, Any] = {}
    live: dict[str, Any] | None = None
    man_sha = ""
    rec_sha = ""
    lock_owner = ""
    qw_sha = ""
    mv_sha = ""
    unit_id = unit
    pin: dict[str, Any] = {}
    slut: dict[str, Any] = {}
    token = lock_token or ""
    receipt_ops: list[dict[str, Any]] = []
    motor_outcome: str | None = None
    stamp_before: str | None = None
    stamp_after: str | None = None
    komponat_kvitto: dict[str, Any] | None = None
    lint_block: dict[str, Any] | None = None

    def _run_doc(outcome: str) -> dict[str, Any]:
        steps = list((komponat_kvitto or {}).get("steps") or receipt_ops)
        applied_names = (
            [o.get("name") for o in steps if o.get("outcome") == "ok"]
            if motor_outcome == "applied"
            else []
        )
        observed = (komponat_kvitto or {}).get("observed_final")
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
            "qwprogs_sha256": qw_sha or None,
            "mvdsv_sha256": mv_sha or None,
            "qwprogs_path": str(qwprogs_path) if qwprogs_path else None,
            "mvdsv_path": str(mvdsv_path) if mvdsv_path else None,
            "unit": unit_id,
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
            "komponat": komponat_kvitto,
            # Lintutfallet skrivs ALLTID, även PASS: ett tyst godkännande går inte
            # att skilja från en grind som aldrig kördes.
            "recept_lint": lint_block,
            "ops": steps,
            "undo_name": (komponat_kvitto or {}).get("undo_name"),
            "observed_final": observed,
            "motor_outcome": motor_outcome,
            "stamp_before": stamp_before,
            "stamp_after": stamp_after,
            "applied": applied_names,
            "freeze": ctx.as_kvitto(),
            "n_ops": len(steps),
        }

    try:
        (
            man, recept, live, man_sha, rec_sha, lock_fields,
            qw_sha, mv_sha, unit_id, lint_block, seal,
        ) = preflight(
            manifest_path=manifest_path,
            recept_path=recept_path,
            ctl=ctl,
            freeze=ctx,
            lock_path=lock_path,
            lock_token=lock_token,
            qwprogs_path=qwprogs_path,
            mvdsv_path=mvdsv_path,
            ctl_port=ctl_port,
            game_port=game_port,
            unit=unit,
            dumpregister=dumpregister,
        )
        lock_owner = lock_fields.get("owner") or ""
        pin = motor_ident(man["steg"][0].get("identitet") or man["steg"][0])
        slut = motor_ident(man["slut"])
        ops = list(recept.get("ops") or [])
        _reserve_outdir(out, ["deploy-run.json"])

        deploy_ctx = mint_deploy_context(seal, bound_steps(recept))
        token = lock_token or lock_fields.get("token") or ""
        stamp_before = pin.get("graph_stamp")
        wire = komponat_wire_cmd(recept, man, lock_token=token)
        verb_err: str | None = None
        parsed: dict[str, Any] = {}
        try:
            reply = ctl.request(wire)
            parsed = parse_komponat_reply(reply.get("data"))
            komponat_kvitto = parsed
            motor_outcome = parsed.get("outcome")
            observed = parsed.get("observed_final") or {}
            if isinstance(observed, dict):
                stamp_after = observed.get("graph_stamp")
            receipt_ops = list(parsed.get("steps") or [])
            if motor_outcome != "applied":
                verb_err = (
                    f"komponat outcome {motor_outcome!r}"
                    + (f": {parsed.get('reason')}" if parsed.get("reason") else "")
                )
        except Exception as exc:
            verb_err = str(exc)
        live = live_identity(ctl)
        if verb_err:
            why_pin = same_identity(live, pin)
            if why_pin:
                leftover = (
                    f"motorbugg: komponat icke-OK men live ≠ utgångsstamp: {why_pin}"
                )
                abort_reason = f"{verb_err} | {leftover}"
            else:
                abort_reason = verb_err
        else:
            why_slut = same_identity(live, slut)
            if why_slut:
                abort_reason = f"slutverifiering: {why_slut}"
    except Exception as exc:
        abort_reason = abort_reason or str(exc)
        doc = _run_doc("aborted")
        _write_run(run_path, doc)
        doc["run_kvitto"] = str(run_path)
        if leftover:
            raise FailClosed("crash-detector", leftover) from exc
        if isinstance(exc, FailClosed):
            raise
        raise FailClosed("crash-detector", abort_reason) from exc
    finally:
        if deploy_ctx is not None:
            clear_deploy_context(deploy_ctx.token)

    outcome = "aborted" if abort_reason else "applied"
    if leftover:
        outcome = "aborted"
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
    ap.add_argument("--game-port", type=int, required=True)
    ap.add_argument("--lock", type=Path, required=True)
    ap.add_argument("--lock-token", required=True)
    ap.add_argument("--qwprogs", type=Path, required=True, help="staged/live qwprogs file to hash")
    ap.add_argument("--mvdsv", type=Path, required=True, help="staged/live mvdsv file to hash")
    ap.add_argument("--unit", required=True, choices=sorted(ALLOWED_DEPLOY_PAIRS))
    ap.add_argument("--commit", required=True)
    args = ap.parse_args(argv)
    check_portvakt(args.port, args.game_port, args.unit)
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
            qwprogs_path=args.qwprogs,
            mvdsv_path=args.mvdsv,
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
