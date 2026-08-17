#!/usr/bin/env python3
"""Fail-closed gates 3+4+7 for the apply tool chain (tools/testsuite only).

Flaggväg: Path.home()/lab/.change-freeze — hårdkodad, ingen env-override.
Fable skriver flaggan via write_change_freeze (ägare + UTC). Apply-vägar
läser och validerar bara. Felformad flagga = frys ändå.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from d_recipe import FIXTURE_SHA256, REGISTERED_IDS, REQUIRED_OPS

CHANGE_FREEZE = Path("lab") / ".change-freeze"

# Base-graph live ids that a recipe may name, and only with this walk-anchor.
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

# owner 2026-08-17T07:15:00Z optional-note
_FREEZE_RE = re.compile(
    r"^(?P<owner>[A-Za-z0-9._-]+)\s+"
    r"(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"
    r"(?:\s+(?P<note>\S.*))?$"
)


class FailClosed(RuntimeError):
    """Refuse mutation. Callers must not talk to the engine after this."""

    def __init__(self, gate: str, message: str) -> None:
        self.gate = gate
        super().__init__(message)


def change_freeze_path() -> Path:
    """Hardcoded ~/lab/.change-freeze. Tests isolate via tmp HOME."""
    return Path.home() / CHANGE_FREEZE


def write_change_freeze(owner: str, note: str = "") -> Path:
    """Fable-writer: one line `owner 2026-08-17T07:15:00Z [note]`.

    Apply/undo/plant/portvakt never call this.
    """
    if not re.fullmatch(r"[A-Za-z0-9._-]+", owner or ""):
        raise FailClosed("freeze", f"ogiltig freeze-ägare {owner!r}")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{owner} {ts}"
    if note:
        line += f" {note.strip()}"
    path = change_freeze_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(line + "\n", encoding="utf-8")
    return path


def parse_freeze_line(head: str) -> tuple[bool, str]:
    """Return (well_formed, display). Empty/malformed is not well-formed."""
    text = (head or "").strip()
    if not text:
        return False, "(tom fil — saknar ägare+klockslag)"
    m = _FREEZE_RE.match(text)
    if not m:
        return False, f"felformad ({text!r})"
    note = (m.group("note") or "").strip()
    display = f"{m.group('owner')} {m.group('ts')}"
    if note:
        display += f" {note}"
    return True, display


def change_freeze_reason() -> str | None:
    """Klartext if the hardcoded flag exists; else None. No path override."""
    p = change_freeze_path()
    try:
        if not p.is_file():
            return None
    except OSError as exc:
        return f"change-freeze oläsbar ({p}): {exc} — vägrar mutation"
    try:
        body = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"change-freeze oläsbar ({p}): {exc} — vägrar mutation"
    head = body.splitlines()[0] if body.splitlines() else ""
    ok, display = parse_freeze_line(head)
    if ok:
        return (
            f"change-freeze aktiv ({p}): {display} — "
            f"apply/undo/plant/portvakt vägrar mutation"
        )
    return (
        f"change-freeze aktiv ({p}): {display} — "
        f"felformad flagga = frys ändå — "
        f"apply/undo/plant/portvakt vägrar mutation"
    )


def check_change_freeze() -> None:
    why = change_freeze_reason()
    if why:
        raise FailClosed("freeze", why)


def stamp_identity(block: dict[str, Any]) -> tuple[str, str] | None:
    """Nivå-1 FNV + nivå-2 SHA. None if the block cannot identify a graph."""
    if not isinstance(block, dict):
        return None
    try:
        fnv = str(block["graph_stamp"]).strip()
        sha = str(block["graph_content_hash"]).strip()
    except (KeyError, TypeError, AttributeError):
        return None
    if not fnv or not sha or sha.lower() in {"none", "null"}:
        return None
    return (fnv, sha)


def _as_stamp_list(blob: Any, name: str) -> list[Any]:
    if blob is None:
        return []
    if isinstance(blob, dict):
        return [blob]
    if isinstance(blob, list):
        return blob
    raise FailClosed(
        "crash-detector",
        f"{name} har korrupt form {type(blob).__name__} — FailClosed, inte TypeError",
    )


def _extract_stamp_identities(recipe: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(block: Any) -> None:
        key = stamp_identity(block) if isinstance(block, dict) else None
        if key is None or key in seen:
            return
        seen.add(key)
        out.append(key)

    _add(recipe.get("off"))
    for name in ("sealed_stamps", "intermediates"):
        for item in _as_stamp_list(recipe.get(name), name):
            _add(item)
    for i, op in enumerate(_as_stamp_list(recipe.get("ops"), "ops")):
        if not isinstance(op, dict):
            raise FailClosed(
                "crash-detector",
                f"ops[{i}] har korrupt form {type(op).__name__}",
            )
        for k in ("expected", "mellanstamp", "on_expected"):
            _add(op.get(k))
    _add(recipe.get("on_expected"))
    return out


def verify_fixture_seal(recipe: dict[str, Any]) -> None:
    """Registered recipes must carry the pinned fixture SHA from load_recipe."""
    if not isinstance(recipe, dict):
        raise FailClosed("crash-detector", "recept saknas — kraschdetektor vägrar")
    rid = recipe.get("id")
    if rid not in REGISTERED_IDS:
        return
    want = FIXTURE_SHA256.get(rid)
    got = recipe.get("_fixture_sha256")
    if not want or got != want:
        raise FailClosed(
            "crash-detector",
            f"recept {rid} är inte bundet till förseglad fixture-SHA "
            f"(fick {got!r}, ville {want!r})",
        )


def sealed_identities(recipe: dict[str, Any]) -> list[tuple[str, str]]:
    """Förseglad mängd, bunden till fixture-SHA för registrerade id:n."""
    verify_fixture_seal(recipe)
    pinned = recipe.get("_sealed_identities")
    if pinned is not None:
        if not isinstance(pinned, (list, tuple, set, frozenset)):
            raise FailClosed(
                "crash-detector",
                f"_sealed_identities har korrupt form {type(pinned).__name__}",
            )
        out: list[tuple[str, str]] = []
        for item in pinned:
            if (
                isinstance(item, (list, tuple))
                and len(item) == 2
                and all(isinstance(x, str) and x for x in item)
            ):
                out.append((item[0], item[1]))
            else:
                raise FailClosed(
                    "crash-detector",
                    "_sealed_identities-rad är inte (fnv, sha)",
                )
        return out
    return _extract_stamp_identities(recipe)


def sealed_stamps(recipe: dict[str, Any]) -> list[dict[str, Any]]:
    """Back-compat view: identity tuples as dicts. Prefer sealed_identities."""
    return [
        {"graph_stamp": fnv, "graph_content_hash": sha}
        for fnv, sha in sealed_identities(recipe)
    ]


def live_in_sealed(live: dict[str, Any] | None, recipe: dict[str, Any]) -> str | None:
    """None if live ∈ sealed set. Counts-only is not enough."""
    if not isinstance(live, dict):
        return "live stamp saknas — kraschdetektor vägrar mutation"
    live_key = stamp_identity(live)
    if live_key is None:
        return (
            "live stamp saknar nivå-1 FNV eller nivå-2 hash — "
            "kraschdetektor vägrar mutation"
        )
    try:
        keys = set(sealed_identities(recipe))
    except FailClosed as exc:
        return str(exc)
    if not keys:
        return "receptet har ingen förseglad stamp-mängd — kraschdetektor vägrar mutation"
    if live_key not in keys:
        known = [f"FNV={fnv[:8]}… sha={sha[:12]}…" for fnv, sha in keys]
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
            if str(k).startswith("_"):
                continue
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
        blob = recipe.get(key)
        if blob is None:
            continue
        if isinstance(blob, dict):
            blob = [blob]
        if not isinstance(blob, list):
            raise FailClosed(
                "anchor",
                f"{key} har korrupt form {type(blob).__name__}",
            )
        for i, op in enumerate(blob):
            if not isinstance(op, dict):
                raise FailClosed("anchor", f"{key}[{i}] är inte ett objekt")
            yield f"{key}[{i}]", op
            if key == "ops":
                for inner_key in _MUTATION_LIST_KEYS:
                    if inner_key == "ops" or inner_key not in op:
                        continue
                    inner = op.get(inner_key)
                    if inner is None:
                        continue
                    if isinstance(inner, dict):
                        inner = [inner]
                    if not isinstance(inner, list):
                        raise FailClosed(
                            "anchor",
                            f"{key}[{i}].{inner_key} har korrupt form",
                        )
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


def _link_sig(op: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _raw_link_id(op),
        _as_cell(op.get("from")),
        _as_cell(op.get("to")),
        _kind_of(op),
        str(op.get("new_kind") or "").lower(),
    )


def _required_ops_present(recipe: dict[str, Any]) -> str | None:
    """Registered id must describe the mutation the engine will apply."""
    rid = recipe.get("id")
    spec = REQUIRED_OPS.get(rid) if rid in REGISTERED_IDS else None
    if spec is None:
        return None
    if spec.get("engine_patch"):
        # SHA-bind happens in verify_fixture_seal on apply/undo.
        return None
    for key, want in spec.items():
        if key == "engine_patch":
            continue
        have = recipe.get(key)
        if not have:
            return (
                f"{rid}: tom {key}-lista — grinden måste validera den "
                f"mutation recept-id:t faktiskt utför"
            )
        if not isinstance(have, list):
            return f"{rid}: {key} har korrupt form {type(have).__name__}"
        if isinstance(want, int):
            if len(have) != want:
                return f"{rid}: {key} har {len(have)} poster, kräver {want}"
            continue
        if isinstance(want, list):
            have_sigs = {_link_sig(op) for op in have if isinstance(op, dict)}
            want_sigs = set()
            for w in want:
                want_sigs.add((
                    w.get("id"),
                    w.get("from"),
                    w.get("to"),
                    str(w.get("kind") or w.get("old_kind") or "").lower(),
                    str(w.get("new_kind") or "").lower(),
                ))
            if have_sigs != want_sigs:
                return (
                    f"{rid}: {key} matchar inte den receptspecifika opmängden"
                )
    return None


def validate_anchors(recipe: dict[str, Any]) -> str | None:
    """None if the fixture describes the mutation that will actually run."""
    if not isinstance(recipe, dict):
        return "recept saknas — ankarvalidering vägrar"
    poison = _walk_poison(recipe)
    if poison:
        return (
            f"{poison[0]}: link_vid_cert-klassen är gift — "
            f"råa länk-id från annan graf avvisas"
        )
    missing = _required_ops_present(recipe)
    if missing:
        return missing
    found = False
    for path, op in _iter_ops(recipe):
        found = True
        if _is_xyz(op.get("from")) and _as_cell(op.get("from")) is None:
            why = _validate_drop_op(path, op)
        else:
            why = _validate_link_op(recipe, path, op)
        if why:
            return why
    rid = recipe.get("id")
    if rid in REGISTERED_IDS and rid in REQUIRED_OPS and not REQUIRED_OPS[rid].get("engine_patch"):
        if not found:
            return (
                f"{rid}: tom op-lista — grinden måste validera den "
                f"mutation recept-id:t faktiskt utför"
            )
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
    require_live: bool = True,
) -> None:
    """Gate 7 always for mutating verbs; 4 if recipe given; 3 if live required."""
    action = (action or "").strip().lower()
    mutating = {"apply", "undo", "plant", "portvakt"}
    if action not in mutating:
        raise FailClosed("action", f"okänd mutationsverb {action!r}")
    check_change_freeze()
    if action == "portvakt":
        return
    if recipe is None:
        if action in {"apply", "undo"}:
            raise FailClosed("anchor", f"{action} kräver recept för ankarvalidering")
        return
    check_anchors(recipe)
    if action in {"apply", "undo"} and require_live:
        check_live_sealed(live, recipe)


def guard_plant(recipe: dict[str, Any] | None = None) -> None:
    """PlanLink / plant / replant. Always freeze; anchors if a recipe is given."""
    guard_mutation("plant", recipe=recipe, require_live=False)


def guard_portvakt() -> None:
    guard_mutation("portvakt")


def send_plan_link(ctl: Any, payload: dict[str, Any], recipe: dict[str, Any] | None = None) -> Any:
    """Production PlanLink entry: freeze (+anchors) then send."""
    guard_plant(recipe)
    if hasattr(ctl, "request"):
        return ctl.request({"PlanLink": payload} if not isinstance(payload, str) else payload)
    raise FailClosed("plant", "ctl saknar request — plant avbruten")


def is_plant_command(cmd: Any) -> bool:
    if isinstance(cmd, dict):
        return any(str(k).lower() == "planlink" for k in cmd)
    if isinstance(cmd, str):
        low = cmd.strip().lower()
        return low.startswith("planlink") or " planlink " in f" {low} "
    return False
