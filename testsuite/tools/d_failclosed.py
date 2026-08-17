#!/usr/bin/env python3
"""Fail-closed gates 3+4+7 for the apply tool chain (tools/testsuite only).

No crates/. No rig. Tools never create or delete the freeze file —
Fable writes ~/lab/.change-freeze by hand on Xerial's order (owner + clock).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

# Default on disk. Override with D_CHANGE_FREEZE for tests (never point at the
# real file from a unit test that needs to mutate).
DEFAULT_CHANGE_FREEZE = Path.home() / "lab" / ".change-freeze"

# Base-graph live ids that a recipe may name, and only with this walk-anchor.
# Anything else (link_vid_cert 48131, RA/K2-serie ids, …) is poison.
BASE_OWN_LINK_IDS: dict[int, dict[str, Any]] = {
    10446: {"from": 1416, "to": 1459, "kind": "walk"},
    10447: {"from": 1416, "to": 1461, "kind": "walk"},
}

POISON_LINK_KEYS = frozenset({
    "link_vid_cert",
    "link_vid",
    "cert_link",
    "vid_cert",
})

_LINK_ID_KEYS = ("id", "link_id", "link")
_MUTATION_LIST_KEYS = (
    "remove_links",
    "retype_links",
    "drops",
    "ops",
    "plants",
    "links",
)
_STAMP_ID_KEYS = ("graph_stamp", "graph_content_hash")


class FailClosed(RuntimeError):
    """Refuse mutation. Callers must not talk to the engine after this."""

    def __init__(self, gate: str, message: str) -> None:
        self.gate = gate
        super().__init__(message)


def freeze_path(path: Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    env = os.environ.get("D_CHANGE_FREEZE")
    if env:
        return Path(env)
    return DEFAULT_CHANGE_FREEZE


def change_freeze_reason(path: Path | None = None) -> str | None:
    """Klartext if ~/lab/.change-freeze (or override) exists; else None."""
    p = freeze_path(path)
    try:
        if not p.is_file():
            return None
    except OSError as exc:
        return f"change-freeze oläsbar ({p}): {exc} — vägrar mutation"
    try:
        body = p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        return f"change-freeze oläsbar ({p}): {exc} — vägrar mutation"
    head = body.splitlines()[0].strip() if body else ""
    owner = head or "(tom fil — saknar ägare+klockslag)"
    return (
        f"change-freeze aktiv ({p}): {owner} — "
        f"apply/undo/plant/portvakt vägrar mutation"
    )


def check_change_freeze(path: Path | None = None) -> None:
    why = change_freeze_reason(path)
    if why:
        raise FailClosed("freeze", why)


def stamp_identity(block: dict[str, Any]) -> tuple[str, str] | None:
    """Nivå-1 FNV + nivå-2 SHA. None if the block cannot identify a graph."""
    try:
        fnv = str(block["graph_stamp"]).strip()
        sha = str(block["graph_content_hash"]).strip()
    except (KeyError, TypeError, AttributeError):
        return None
    if not fnv or not sha or sha.lower() in {"none", "null"}:
        return None
    return (fnv, sha)


def sealed_stamps(recipe: dict[str, Any]) -> list[dict[str, Any]]:
    """Förseglad mängd: bas, kända mellanbilder, slutbild."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _add(block: Any) -> None:
        if not isinstance(block, dict):
            return
        key = stamp_identity(block)
        if key is None or key in seen:
            return
        seen.add(key)
        out.append(block)

    _add(recipe.get("off"))
    for name in ("sealed_stamps", "intermediates"):
        blob = recipe.get(name) or []
        if isinstance(blob, dict):
            blob = [blob]
        for item in blob:
            _add(item)
    for op in recipe.get("ops") or []:
        if not isinstance(op, dict):
            continue
        for k in ("expected", "mellanstamp", "on_expected"):
            _add(op.get(k))
    _add(recipe.get("on_expected"))
    return out


def live_in_sealed(live: dict[str, Any] | None, recipe: dict[str, Any]) -> str | None:
    """None if live ∈ sealed set. Counts-only is not enough (gate 3 / 5)."""
    if not isinstance(live, dict):
        return "live stamp saknas — kraschdetektor vägrar mutation"
    live_key = stamp_identity(live)
    if live_key is None:
        return (
            "live stamp saknar nivå-1 FNV eller nivå-2 hash — "
            "kraschdetektor vägrar mutation"
        )
    sealed = sealed_stamps(recipe)
    if not sealed:
        return "receptet har ingen förseglad stamp-mängd — kraschdetektor vägrar mutation"
    keys = {stamp_identity(s) for s in sealed}
    keys.discard(None)
    if live_key not in keys:
        known = []
        for s in sealed:
            ident = stamp_identity(s)
            if ident is None:
                continue
            known.append(
                f"{s.get('cells')}/{s.get('links')} FNV={ident[0][:8]}… "
                f"sha={ident[1][:12]}…"
            )
        return (
            f"live stamp ∉ förseglad mängd "
            f"(FNV={live_key[0]} nivå-2={live_key[1][:16]}…; "
            f"kända: {', '.join(known) or '—'}) — kraschdetektor vägrar mutation"
        )
    return None


def check_live_sealed(live: dict[str, Any] | None, recipe: dict[str, Any]) -> None:
    why = live_in_sealed(live, recipe)
    if why:
        raise FailClosed("crash-detector", why)


def _origin_ok(origin: Any) -> bool:
    if not isinstance(origin, (list, tuple)) or len(origin) < 3:
        return False
    try:
        float(origin[0])
        float(origin[1])
        float(origin[2])
    except (TypeError, ValueError):
        return False
    return True


def _as_cell(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _raw_link_id(op: dict[str, Any]) -> int | None:
    """Numeric link-id on an op, if present. xyz triples are not ids."""
    for key in _LINK_ID_KEYS:
        if key not in op:
            continue
        val = op[key]
        if isinstance(val, bool):
            continue
        if isinstance(val, int):
            return int(val)
        if isinstance(val, str) and val.isdigit():
            return int(val)
    return None


def _kind_of(op: dict[str, Any]) -> str:
    for key in ("kind", "old_kind"):
        raw = op.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip().lower()
    return ""


def _sources(recipe: dict[str, Any]) -> list[dict[str, Any]]:
    src = recipe.get("source")
    if isinstance(src, dict):
        return [src]
    if isinstance(src, list):
        return [x for x in src if isinstance(x, dict)]
    return []


def _origin_for_from(recipe: dict[str, Any], op: dict[str, Any], from_cell: int | None) -> Any:
    if _origin_ok(op.get("origin")):
        return op["origin"]
    if from_cell is None:
        return None
    for src in _sources(recipe):
        if src.get("cell") == from_cell and _origin_ok(src.get("origin")):
            return src["origin"]
    return None


def _walk_poison(obj: Any, path: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = f"{path}.{k}"
            if k in POISON_LINK_KEYS:
                hits.append(here)
            hits.extend(_walk_poison(v, here))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(_walk_poison(v, f"{path}[{i}]"))
    return hits


def _iter_ops(recipe: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    for key in _MUTATION_LIST_KEYS:
        blob = recipe.get(key) or []
        if isinstance(blob, dict):
            blob = [blob]
        if not isinstance(blob, list):
            continue
        for i, op in enumerate(blob):
            if not isinstance(op, dict):
                continue
            yield f"{key}[{i}]", op
            # one nesting level: composed ops wrapping the same shapes
            if key == "ops":
                for inner_key in _MUTATION_LIST_KEYS:
                    if inner_key == "ops":
                        continue
                    inner = op.get(inner_key) or []
                    if isinstance(inner, dict):
                        inner = [inner]
                    if not isinstance(inner, list):
                        continue
                    for j, child in enumerate(inner):
                        if isinstance(child, dict):
                            yield f"{key}[{i}].{inner_key}[{j}]", child


def _is_xyz(value: Any) -> bool:
    return _origin_ok(value)


def _validate_link_op(recipe: dict[str, Any], path: str, op: dict[str, Any]) -> str | None:
    frm = _as_cell(op.get("from"))
    to = _as_cell(op.get("to"))
    kind = _kind_of(op)
    if frm is None or to is None or not kind:
        return (
            f"{path}: op måste bära from/to/kind som ankare "
            f"(from={op.get('from')!r} to={op.get('to')!r} kind={kind or op.get('kind')!r})"
        )
    if not _origin_ok(_origin_for_from(recipe, op, frm)):
        return f"{path}: op måste bära origin (på op:en eller recipe.source för from={frm})"
    lid = _raw_link_id(op)
    if lid is None:
        return None
    want = BASE_OWN_LINK_IDS.get(lid)
    if want is None:
        return (
            f"{path}: rått länk-id {lid} är inte basens 10446/10447 — "
            f"avvisat (link_vid_cert-klassen)"
        )
    if frm != want["from"] or to != want["to"] or kind != want["kind"]:
        return (
            f"{path}: länk-id {lid} får bara användas tillsammans med "
            f"walk-ankaret {want['from']}→{want['to']} {want['kind']} "
            f"(fick {frm}→{to} {kind})"
        )
    return None


def _validate_drop_op(path: str, op: dict[str, Any]) -> str | None:
    if not _kind_of(op):
        return f"{path}: drop saknar kind"
    if not _is_xyz(op.get("from")):
        return f"{path}: drop.from måste vara origin [x,y,z]"
    if not (_is_xyz(op.get("to")) or isinstance(op.get("to_cell"), int)):
        return f"{path}: drop saknar to-origin eller to_cell"
    lid = _raw_link_id(op)
    if lid is not None:
        return f"{path}: drop får inte bära rått länk-id {lid}"
    return None


def validate_anchors(recipe: dict[str, Any]) -> str | None:
    """None if every mutation op is origin+from/to/kind anchored."""
    if not isinstance(recipe, dict):
        return "recept saknas — ankarvalidering vägrar"
    poison = _walk_poison(recipe)
    if poison:
        return (
            f"{poison[0]}: link_vid_cert-klassen är gift — "
            f"råa länk-id från annan graf avvisas"
        )
    for path, op in _iter_ops(recipe):
        if _is_xyz(op.get("from")) and _as_cell(op.get("from")) is None:
            why = _validate_drop_op(path, op)
        else:
            why = _validate_link_op(recipe, path, op)
        if why:
            return why
    # carve-only recipes (west-shelf) have no link ops — allowed.
    return None


def check_anchors(recipe: dict[str, Any]) -> None:
    why = validate_anchors(recipe)
    if why:
        raise FailClosed("anchor", why)


def guard_mutation(
    action: str,
    *,
    recipe: dict[str, Any] | None = None,
    live: dict[str, Any] | None = None,
    freeze_path_override: Path | None = None,
    require_live: bool = True,
) -> None:
    """Gate 7 always for mutating verbs; 4 if recipe given; 3 if live required.

    dry-run / status are not mutations and must not call this.
    """
    action = (action or "").strip().lower()
    mutating = {"apply", "undo", "plant", "portvakt"}
    if action not in mutating:
        raise FailClosed("action", f"okänd mutationsverb {action!r}")
    check_change_freeze(freeze_path_override)
    if action == "portvakt":
        return
    if recipe is None:
        if action in {"apply", "undo", "plant"}:
            raise FailClosed("anchor", f"{action} kräver recept för ankarvalidering")
        return
    check_anchors(recipe)
    if action in {"apply", "undo"} and require_live:
        check_live_sealed(live, recipe)


def guard_plant(
    recipe: dict[str, Any] | None = None,
    *,
    live: dict[str, Any] | None = None,
    freeze_path_override: Path | None = None,
    require_live: bool = False,
) -> None:
    """PlanLink / plant entry. Freeze + anchors; live if the caller has it."""
    guard_mutation(
        "plant",
        recipe=recipe,
        live=live,
        freeze_path_override=freeze_path_override,
        require_live=require_live,
    )


def guard_portvakt(freeze_path_override: Path | None = None) -> None:
    guard_mutation("portvakt", freeze_path_override=freeze_path_override)
