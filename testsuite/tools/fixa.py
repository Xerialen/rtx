#!/usr/bin/env python3
"""Lådkommando `fixa` — dry-run / apply / undo of the west-shelf recipe via apply_one.

Talks ctlproto `Fixa`. Apply and undo require ~/lab/.rig-lock. Never invents ON
expected from an observed stamp (facit §1). No new recipes.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from d_kvitto import WEST_SHELF_RECIPE, astar_path, make_kvitto, write_kvitto  # noqa: E402
from d_failclosed import FailClosed, FreezeContext, change_freeze_reason, guard_mutation  # noqa: E402
from d_recipe import load_recipe, on_expected, recipe_path  # noqa: E402
from d_strata import FORBIDDEN_CTL, FORBIDDEN_GAME  # noqa: E402
from verify_d_kvitto import verify  # noqa: E402

RIG_LOCK = Path.home() / "lab" / ".rig-lock"


def require_lock(
    port: int, lock_path: Path = RIG_LOCK, *, freeze: FreezeContext | None = None
) -> str:
    if port in FORBIDDEN_CTL:
        raise SystemExit(f"port {port} is RA/main — dedicated D instance only")
    frozen = change_freeze_reason(freeze or FreezeContext.production())
    if frozen:
        raise SystemExit(frozen)
    if not lock_path.is_file():
        raise SystemExit(f"no {lock_path} — hold the lock before fixa --apply/--undo")
    body = lock_path.read_text(encoding="utf-8", errors="replace").strip()
    if not body:
        raise SystemExit(f"{lock_path} is empty")
    return body.split()[0]


def parse_fixa_reply(data: dict) -> dict:
    if not isinstance(data, dict):
        raise SystemExit(f"unexpected fixa reply: {data!r}")
    return data


def stamp_from_reply(data: dict) -> dict:
    return {
        "cells": int(data["cells"]),
        "links": int(data["links"]),
        "rj_links": int(data["rj_links"]),
        "graph_stamp": str(data["stamp"]),
        "graph_content_hash": str(data["content_hash"]),
    }


def path_from_fixa(block: dict | None) -> dict:
    if not block:
        return astar_path(found=False)
    return astar_path(
        found=bool(block.get("found")),
        cells=block.get("cells") or [],
        links=block.get("links") or [],
        cost=block.get("cost"),
        mask_links=block.get("mask_links") or [],
    )


def lock_token_from_file(lock_path: Path) -> str:
    body = lock_path.read_text(encoding="utf-8", errors="replace").strip()
    if not body:
        raise SystemExit(f"{lock_path} is empty")
    return body.split()[0]


def _send_fixa(
    ctl,
    *,
    recipe_id: str,
    mode: str,
    from_cell: int | None,
    to_cell: int | None,
    lock_token: str | None = None,
    recipe=None,
    freeze=None,
    deploy: bool = False,
    deploy_ctx=None,
) -> dict:
    """ENDA muterande ctl-ingången. Frys + stampgrind bor här, inte hos anroparen."""
    from d_failclosed import (
        COMPOSE_CHILD_IDS,
        KOMPONAT_SCHEMAN,
        FreezeContext,
        consume_deploy_apply,
        consume_deploy_undo,
        guard_mutation,
        guard_plant,
        require_deploy_context,
        revert_last_apply,
        revert_last_undo,
        shelf_payload_sha256,
    )

    mode_l = (mode or "").strip().lower()

    def _ctl(mode_now: str, token: str | None) -> dict:
        cmd = f"fixa {recipe_id} {mode_now}"
        if from_cell is not None and to_cell is not None:
            cmd += f" {from_cell} {to_cell}"
        if token:
            cmd += f" lock {token}"
        return parse_fixa_reply(ctl.request(cmd)["data"])

    if mode_l in {"apply", "undo", "plant"}:
        rec = recipe if recipe is not None else load_recipe(recipe_path(recipe_id))
        ctx = freeze if freeze is not None else FreezeContext.production()
        schema = str(rec.get("schema") or "") if hasattr(rec, "get") else ""
        child = recipe_id if recipe_id in COMPOSE_CHILD_IDS else None
        want_deploy = bool(deploy or deploy_ctx is not None or schema in KOMPONAT_SCHEMAN or child)
        consumed = None
        if want_deploy or child:
            require_deploy_context(deploy_ctx)
        if child and mode_l == "apply":
            consume_deploy_apply(
                kind="shelf_patch",
                name=child,
                payload_sha256=shelf_payload_sha256(child, child),
                ctx=deploy_ctx,
            )
            consumed = "apply"
        elif want_deploy and mode_l == "undo":
            consume_deploy_undo(recipe_id=recipe_id, ctx=deploy_ctx)
            consumed = "undo"
        try:
            if mode_l == "plant":
                guard_plant(rec, freeze=ctx, deploy=want_deploy)
            else:
                ident = _ctl("dry-run", None)
                live = stamp_from_reply(ident)
                guard_mutation(
                    mode_l, recipe=rec, live=live, freeze=ctx, deploy=want_deploy
                )
            return _ctl(mode_l, lock_token)
        except Exception:
            if consumed == "apply":
                revert_last_apply()
            elif consumed == "undo":
                revert_last_undo()
            raise
    return _ctl(mode, lock_token)


def run_fixa(
    ctl,
    *,
    recipe_id: str,
    mode: str,
    from_cell: int | None,
    to_cell: int | None,
    lock_token: str | None = None,
    recipe=None,
    freeze=None,
    deploy: bool = False,
    deploy_ctx=None,
) -> dict:
    """Alias till _send_fixa — ingen ogrindad sändväg."""
    return _send_fixa(
        ctl,
        recipe_id=recipe_id,
        mode=mode,
        from_cell=from_cell,
        to_cell=to_cell,
        lock_token=lock_token,
        recipe=recipe,
        freeze=freeze,
        deploy=deploy,
        deploy_ctx=deploy_ctx,
    )


def write_apply_kvitto(
    *,
    path: Path,
    recipe: dict,
    reply: dict,
    lock_owner: str,
    lock_path: Path,
    issued_at: str,
    started_at: str,
    ended_at: str,
    host: str,
    ctl_port: int,
    game_port: int,
    commit: str,
    binary_sha256: str,
    seed: int,
    stratum: dict,
    raw_pointer: str,
    freeze_record: dict | None = None,
) -> dict:
    off = dict(recipe["off"])
    on = on_expected(recipe)
    observed = stamp_from_reply(reply)
    doc = make_kvitto(
        riglock_owner=lock_owner,
        riglock_issued_at=issued_at,
        riglock_valid_from=issued_at,
        riglock_valid_to=ended_at,
        riglock_path=str(lock_path),
        run_started_at=started_at,
        run_ended_at=ended_at,
        endpoint_host=host,
        endpoint_ctl_port=ctl_port,
        endpoint_game_port=game_port,
        map_name=reply.get("map") or recipe.get("map") or "dm3",
        binary_sha256=binary_sha256,
        commit=commit,
        stamps_off_expected=off,
        stamps_off_observed=off,
        stamps_on_expected=on,
        stamps_on_observed=observed,
        stamps_undo_expected=off,
        stamps_undo_observed=off,
        recipe=WEST_SHELF_RECIPE if recipe.get("id") == "west-shelf" else recipe,
        seed=seed,
        stratum=stratum,
        raw_pointer=raw_pointer,
        astar_before=path_from_fixa(reply.get("astar_before")),
        astar_after=path_from_fixa(reply.get("astar_after")),
        astar_next_best=path_from_fixa(reply.get("astar_next_best")),
        freeze_record=freeze_record,
    )
    write_kvitto(path, doc)
    errors = verify(doc)
    if errors:
        raise SystemExit("kvitto verify failed:\n  " + "\n  ".join(errors))
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--recept", default="west-shelf")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--undo", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--game-port", type=int, default=0)
    ap.add_argument("--from-cell", type=int)
    ap.add_argument("--to-cell", type=int)
    ap.add_argument("--fixture", type=Path)
    ap.add_argument("--kvitto", type=Path)
    ap.add_argument("--lock", type=Path, default=RIG_LOCK)
    ap.add_argument("--commit", default="unknown")
    ap.add_argument("--binary-sha256", default="00" * 32)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    from d_recipe import REGISTERED_IDS

    if args.recept not in REGISTERED_IDS:
        print(
            f"unknown recipe {args.recept!r} — registered: {sorted(REGISTERED_IDS)}",
            file=sys.stderr,
        )
        return 2
    if args.game_port in FORBIDDEN_GAME:
        print(f"game port {args.game_port} is RA/main", file=sys.stderr)
        return 2

    fixture = args.fixture
    if fixture is None and args.recept != "west-shelf":
        fixture = Path(__file__).resolve().parent / "recept" / f"{args.recept}.json"
    recipe = load_recipe(fixture)
    if recipe.get("id") != args.recept:
        print(
            f"fixture id {recipe.get('id')!r} != --recept {args.recept!r}",
            file=sys.stderr,
        )
        return 2
    mode_s = "dry-run" if args.dry_run else "apply" if args.apply else "undo"

    if mode_s in {"apply", "undo"}:
        owner = require_lock(args.port, args.lock)
        if mode_s == "apply":
            try:
                on_expected(recipe)
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 2
    else:
        if args.port in FORBIDDEN_CTL:
            print(f"port {args.port} is RA/main", file=sys.stderr)
            return 2
        owner = "dry-run"

    from runner.control import Control  # local import so unit tests can skip it

    started = datetime.now(timezone.utc).isoformat()
    token = None
    if mode_s in {"apply", "undo"}:
        token = lock_token_from_file(args.lock)
    ctl = Control(args.host, args.port)
    try:
        try:
            reply = run_fixa(
                ctl,
                recipe_id=args.recept,
                mode=mode_s,
                from_cell=args.from_cell,
                to_cell=args.to_cell,
                lock_token=token,
                recipe=recipe,
            )
        except FailClosed as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except (KeyError, TypeError, ValueError) as exc:
            print(f"live stamp oläsbar före {mode_s}: {exc}", file=sys.stderr)
            return 2
    finally:
        ctl.close()
    ended = datetime.now(timezone.utc).isoformat()
    print(json.dumps({k: reply.get(k) for k in (
        "recipe", "mode", "outcome", "reason", "stamp", "content_hash",
        "cells", "links", "audit",
    )}, indent=2))

    if mode_s == "apply" and args.kvitto:
        issued = started
        write_apply_kvitto(
            path=args.kvitto,
            recipe=recipe,
            reply=reply,
            lock_owner=owner,
            lock_path=args.lock,
            issued_at=issued,
            started_at=started,
            ended_at=ended,
            host=args.host,
            ctl_port=args.port,
            game_port=args.game_port or 0,
            commit=args.commit,
            binary_sha256=args.binary_sha256,
            seed=args.seed,
            stratum={"id": "fixa-apply"},
            raw_pointer=str(args.kvitto),
            freeze_record=FreezeContext.production().as_kvitto(),
        )
    if reply.get("outcome") == "failed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
