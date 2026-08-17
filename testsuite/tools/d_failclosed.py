#!/usr/bin/env python3
"""Fail-closed gates 3+4+7 for the apply tool chain (tools/testsuite only).

Flaggväg: pwd.getpwuid(uid).pw_dir/lab/.change-freeze — aldrig HOME/env.
Test injicerar ENDAST FreezeContext(path=..., injected=True), loggas i kvitto.
"""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import secrets
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from d_recipe import FIXTURE_SHA256, REGISTERED_IDS, REQUIRED_OPS, SealedRecipe

CHANGE_FREEZE = Path("lab") / ".change-freeze"


@dataclass(frozen=True)
class FreezeContext:
    """Production path from passwd home. Tests pass injected=True + path."""

    path: Path
    injected: bool = False

    def __post_init__(self) -> None:
        if not self.injected:
            real = Path(pwd.getpwuid(os.getuid()).pw_dir) / CHANGE_FREEZE
            object.__setattr__(self, "path", real)

    @staticmethod
    def production() -> "FreezeContext":
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        return FreezeContext(path=home / CHANGE_FREEZE, injected=False)

    @staticmethod
    def for_test(path: Path | str) -> "FreezeContext":
        return FreezeContext(path=Path(path), injected=True)

    def as_kvitto(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "injected": self.injected,
            "lookup": "constructor" if self.injected else "pwd.getpwuid",
        }

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


# 5c66a6d registered_recipe_names() — equality, not contains (Sol villkor 6).
ENGINE_REGISTERED_RECIPES = (
    "west-shelf",
    "ram-rail",
    "ram-rail-v2",
    "ram-prevent",
    "haz1462-k1",
    "haz1462-k2",
    "haz1462-k3",
)
# undoable_name = PLANT_HANDLES + registered. Handles are undo-only, never apply.
ENGINE_UNDO_HANDLES = ("plan-cell", "plan-drop", "plan-link")
ENGINE_UNDOABLE = ENGINE_UNDO_HANDLES + ENGINE_REGISTERED_RECIPES

# Compose children. Generic LABB apply of these is a silent-deploy surface.
COMPOSE_CHILD_IDS = frozenset({"ram-rail-v2", "ram-prevent"})
PLAN_LINK_UNDO_ID = "plan-link"
V296_STEP_NAME = "v296-vasthoppet"
# Canonical payload of sealed v296-vasthoppet (recept e327251e…).
SEALED_V296_PAYLOAD_SHA256 = (
    "aa96a55f33a8d73005f4e8438f5cf3b51b927f5eb37801bd58c49e58c0995bf3"
)

# Facit §3: exactly one dedicated pair. d2/d4/RA/0/0 are not pairs.
ALLOWED_DEPLOY_PAIRS: dict[str, tuple[int, int]] = {
    "tbx-d1": (27996, 27592),
    "tbx-d3": (27998, 27594),
}


@dataclass(frozen=True)
class BoundStep:
    """One sealed compose mutation. Consumed in order, once."""

    index: int
    kind: str
    name: str
    recipe_id: str
    payload_sha256: str


@dataclass(frozen=True)
class PreflightSeal:
    """Single-use ticket. Only issue_preflight_seal after a real preflight."""

    _secret: str
    manifest_sha256: str
    recept_sha256: str


@dataclass(frozen=True)
class DeployContext:
    """Capability minted from a preflight seal. Not self-activatable.

    Carries the exact ordered op projection + payload hashes. apply/undo
    consume one expected step; extra/reordered/reused/borrowed is refused
    before ctl-send.
    """

    token: str
    manifest_sha256: str
    recept_sha256: str
    steps: tuple[BoundStep, ...] = field(default_factory=tuple)


@dataclass
class _DeploySession:
    ctx: DeployContext
    next_index: int = 0
    stack: list[BoundStep] = field(default_factory=list)


_active_session: _DeploySession | None = None
_preflight_ticket: str | None = None


def active_deploy_context() -> DeployContext | None:
    return None if _active_session is None else _active_session.ctx


def issue_preflight_seal(manifest_sha256: str, recept_sha256: str) -> PreflightSeal:
    """Runner-only. Called at the end of a successful sealed preflight."""
    global _preflight_ticket
    if not manifest_sha256 or not recept_sha256:
        raise FailClosed("deploy-context", "preflight-sigill kräver båda SHA")
    _preflight_ticket = secrets.token_hex(16)
    return PreflightSeal(
        _secret=_preflight_ticket,
        manifest_sha256=manifest_sha256,
        recept_sha256=recept_sha256,
    )


def mint_deploy_context(seal: PreflightSeal, steps: tuple[BoundStep, ...] | list[BoundStep]) -> DeployContext:
    """Activate from a one-shot preflight seal. Public activate is closed."""
    global _preflight_ticket, _active_session
    if not isinstance(seal, PreflightSeal) or not seal._secret:
        raise FailClosed("deploy-context", "ogiltigt preflight-sigill")
    if _preflight_ticket is None or seal._secret != _preflight_ticket:
        raise FailClosed(
            "deploy-context",
            "DeployContext är inte självaktiverbar — saknar runner-preflight",
        )
    if _active_session is not None:
        raise FailClosed("deploy-context", "deploy-kontext redan aktiv")
    bound = tuple(steps)
    if len(bound) != 3:
        raise FailClosed("deploy-context", "kontext kräver exakt de tre komponatstegen")
    _preflight_ticket = None
    ctx = DeployContext(
        token=secrets.token_hex(16),
        manifest_sha256=seal.manifest_sha256,
        recept_sha256=seal.recept_sha256,
        steps=bound,
    )
    _active_session = _DeploySession(ctx=ctx)
    return ctx


def activate_deploy_context(ctx: DeployContext) -> None:
    """Closed. Sol C1: public setter is not a grind."""
    raise FailClosed(
        "deploy-context",
        "activate_deploy_context är stängd — kontext mintas av runnern "
        "efter förseglad preflight (icke-självaktiverbar)",
    )


def reset_deploy_state() -> None:
    """Test/runner recovery: drop ambient session and unused preflight ticket."""
    global _active_session, _preflight_ticket
    _active_session = None
    _preflight_ticket = None


def clear_deploy_context(token: str) -> None:
    global _active_session, _preflight_ticket
    if _active_session is None:
        _preflight_ticket = None
        return
    if token != _active_session.ctx.token:
        raise FailClosed("deploy-context", "clear_deploy_context: fel token")
    _active_session = None
    _preflight_ticket = None


def require_deploy_context(ctx: DeployContext | None = None) -> DeployContext:
    sess = _active_session
    if sess is None:
        raise FailClosed(
            "deploy-context",
            "ingen deploy-kontext — komponat-barn och V296-PlanLink kräver "
            "att runnern mintat kontexten efter förseglad preflight",
        )
    if ctx is not None and ctx.token != sess.ctx.token:
        raise FailClosed(
            "deploy-context",
            "lånad/förfalskad deploy-kontext — token mismatch",
        )
    return sess.ctx


def _session(ctx: DeployContext | None = None) -> _DeploySession:
    require_deploy_context(ctx)
    assert _active_session is not None
    return _active_session


def consume_deploy_apply(
    *,
    kind: str,
    name: str,
    payload_sha256: str,
    ctx: DeployContext | None = None,
) -> BoundStep:
    """Refuse extra/reordered/reused/wrong-payload before ctl-send."""
    sess = _session(ctx)
    if sess.next_index >= len(sess.ctx.steps):
        raise FailClosed(
            "deploy-context",
            "extra steg — kontexten har inga fler apply (förbrukad)",
        )
    want = sess.ctx.steps[sess.next_index]
    got = (str(kind), str(name), str(payload_sha256))
    exp = (want.kind, want.name, want.payload_sha256)
    if got != exp:
        raise FailClosed(
            "deploy-context",
            f"omordnat/lånat/fel payload: förväntade steg {want.index} "
            f"{exp[0]}/{exp[1]}/{exp[2][:16]}… fick {got[0]}/{got[1]}/{got[2][:16]}…",
        )
    sess.stack.append(want)
    sess.next_index += 1
    return want


def consume_deploy_undo(
    *,
    recipe_id: str,
    ctx: DeployContext | None = None,
) -> BoundStep:
    sess = _session(ctx)
    if not sess.stack:
        raise FailClosed(
            "deploy-context",
            "återanvänd/tom undo — ingen applicerad ram att poppa",
        )
    top = sess.stack[-1]
    if top.recipe_id != recipe_id:
        raise FailClosed(
            "deploy-context",
            f"undo {recipe_id!r} ≠ stacktop {top.recipe_id!r}",
        )
    sess.stack.pop()
    sess.next_index -= 1
    return top


def revert_last_apply() -> None:
    if _active_session is None or not _active_session.stack:
        return
    _active_session.stack.pop()
    _active_session.next_index -= 1


def revert_last_undo() -> None:
    """Undo send failed after consume — put the frame back."""
    sess = _active_session
    if sess is None or sess.next_index >= len(sess.ctx.steps):
        return
    # next_index was decremented by consume_deploy_undo; the step lives in ctx.steps
    nxt = sess.ctx.steps[sess.next_index]
    if nxt not in sess.stack:
        sess.stack.append(nxt)
        sess.next_index += 1


def planlink_payload_sha256(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
    canon = {
        "from": payload.get("from"),
        "takeoff": payload.get("takeoff"),
        "tgt": payload.get("tgt"),
        "v_req": float(payload["v_req"]) if payload.get("v_req") is not None else None,
        "gain": float(payload["gain"]) if payload.get("gain") is not None else None,
        "carried": bool(payload.get("carried") or False),
    }
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def shelf_payload_sha256(name: str, kalla: str | None = None) -> str:
    stem = Path(str(kalla or name)).stem
    blob = json.dumps(
        {"op": "shelf_patch", "name": name, "kalla": stem},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def op_payload_sha256(op: Mapping[str, Any]) -> str:
    kind = str(op.get("op") or "")
    if kind == "plan_link":
        return planlink_payload_sha256(op)
    return shelf_payload_sha256(str(op.get("name") or ""), str(op.get("kalla") or "") or None)


def compose_child_id(recipe_id: str | None, recipe: Any = None) -> str | None:
    if recipe_id in COMPOSE_CHILD_IDS:
        return str(recipe_id)
    if recipe is not None and hasattr(recipe, "get"):
        rid = recipe.get("id")
        if rid in COMPOSE_CHILD_IDS:
            return str(rid)
    return None


def change_freeze_path(ctx: FreezeContext | None = None) -> Path:
    return (ctx or FreezeContext.production()).path


def _assert_injected_write_path(path: Path) -> None:
    """Injected writer may only touch a path under tmp, never ~/lab."""
    abs_p = Path(os.path.abspath(path))
    tmp_root = Path(os.path.abspath(tempfile.gettempdir()))
    try:
        abs_p.relative_to(tmp_root)
    except ValueError as exc:
        raise FailClosed(
            "freeze",
            f"injicerad freeze-väg {abs_p} ligger inte under tmp {tmp_root}",
        ) from exc
    lab = Path(os.path.abspath(Path(pwd.getpwuid(os.getuid()).pw_dir) / "lab"))
    if abs_p == lab / ".change-freeze" or abs_p == lab or str(abs_p).startswith(str(lab) + os.sep):
        raise FailClosed(
            "freeze",
            f"injicerad freeze-väg {abs_p} pekar på riktiga ~/lab",
        )


def write_change_freeze(
    owner: str,
    note: str = "",
    *,
    freeze: FreezeContext,
    allow_production: bool = False,
) -> Path:
    """Fable-writer. Kräver explicit FreezeContext.

    Test: FreezeContext.for_test(tmp-väg). Produktion: production()
    + allow_production=True. Utan det skrivs aldrig passwd-hemmet.
    """
    if not isinstance(freeze, FreezeContext):
        raise FailClosed("freeze", "write_change_freeze kräver explicit FreezeContext")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", owner or ""):
        raise FailClosed("freeze", f"ogiltig freeze-ägare {owner!r}")
    if freeze.injected:
        _assert_injected_write_path(freeze.path)
        path = freeze.path
    elif allow_production:
        path = FreezeContext.production().path
    else:
        raise FailClosed(
            "freeze",
            "write_change_freeze vägrar passwd-hem utan allow_production=True",
        )
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{owner} {ts}"
    if note:
        line += f" {note.strip()}"
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


def change_freeze_reason(ctx: FreezeContext | None = None) -> str | None:
    """Klartext if the passwd-home flag exists. ctx is the only injection."""
    p = change_freeze_path(ctx)
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


def check_change_freeze(ctx: FreezeContext | None = None) -> None:
    why = change_freeze_reason(ctx)
    if why:
        raise FailClosed("freeze", why)


def stamp_identity(block: dict[str, Any]) -> tuple[str, str] | None:
    """Nivå-1 FNV + nivå-2 SHA. None if the block cannot identify a graph."""
    if not isinstance(block, Mapping):
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
    if isinstance(blob, Mapping) and not isinstance(blob, (str, bytes)):
        return [blob]
    if isinstance(blob, (list, tuple)):
        return list(blob)
    raise FailClosed(
        "crash-detector",
        f"{name} har korrupt form {type(blob).__name__} — FailClosed, inte TypeError",
    )


def _extract_stamp_identities(recipe: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(block: Any) -> None:
        key = stamp_identity(block) if isinstance(block, Mapping) else None
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


def verify_fixture_seal(recipe: Any) -> None:
    """Registered recipes must be the frozen SealedRecipe from load_recipe."""
    if isinstance(recipe, SealedRecipe):
        rid = recipe.get("id")
        want = FIXTURE_SHA256.get(rid)
        if rid in REGISTERED_IDS and recipe.fixture_sha256 != want:
            raise FailClosed(
                "crash-detector",
                f"recept {rid} fixture-SHA {recipe.fixture_sha256!r} ≠ pin {want!r}",
            )
        return
    if not isinstance(recipe, Mapping):
        raise FailClosed("crash-detector", "recept saknas — kraschdetektor vägrar")
    rid = recipe.get("id")
    if rid in REGISTERED_IDS:
        raise FailClosed(
            "crash-detector",
            f"recept {rid} måste vara SealedRecipe från load_recipe — "
            f"lösa dictar kan förfalska stampmängden",
        )


def sealed_identities(recipe: Any) -> list[tuple[str, str]]:
    """Förseglad mängd. För registrerade id:n endast ur frozen SealedRecipe."""
    verify_fixture_seal(recipe)
    if isinstance(recipe, SealedRecipe):
        return list(recipe.sealed_identities)
    return _extract_stamp_identities(recipe)


def sealed_stamps(recipe: dict[str, Any]) -> list[dict[str, Any]]:
    """Back-compat view: identity tuples as dicts. Prefer sealed_identities."""
    return [
        {"graph_stamp": fnv, "graph_content_hash": sha}
        for fnv, sha in sealed_identities(recipe)
    ]


def live_in_sealed(live: Mapping | None, recipe: Any) -> str | None:
    """None if live ∈ sealed set. Counts-only is not enough."""
    if not isinstance(live, Mapping):
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


def _sources(recipe: Any) -> list:
    src = recipe.get("source") if hasattr(recipe, "get") else None
    if isinstance(src, Mapping) and "cell" in src:
        return [src]
    if isinstance(src, (list, tuple)):
        return [x for x in src if isinstance(x, Mapping)]
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
    if isinstance(obj, Mapping) and not isinstance(obj, (str, bytes)):
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
        if isinstance(blob, Mapping) and not isinstance(blob, (str, bytes)):
            blob = [blob]
        if not isinstance(blob, (list, tuple)):
            raise FailClosed(
                "anchor",
                f"{key} har korrupt form {type(blob).__name__}",
            )
        for i, op in enumerate(blob):
            if not isinstance(op, Mapping):
                raise FailClosed("anchor", f"{key}[{i}] är inte ett objekt")
            yield f"{key}[{i}]", op
            if key == "ops":
                for inner_key in _MUTATION_LIST_KEYS:
                    if inner_key == "ops" or inner_key not in op:
                        continue
                    inner = op.get(inner_key)
                    if inner is None:
                        continue
                    if isinstance(inner, Mapping) and not isinstance(inner, (str, bytes)):
                        inner = [inner]
                    if not isinstance(inner, (list, tuple)):
                        raise FailClosed(
                            "anchor",
                            f"{key}[{i}].{inner_key} har korrupt form",
                        )
                    for j, child in enumerate(inner):
                        if isinstance(child, Mapping):
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
        if not isinstance(have, (list, tuple)):
            return f"{rid}: {key} har korrupt form {type(have).__name__}"
        if isinstance(want, int):
            if len(have) != want:
                return f"{rid}: {key} har {len(have)} poster, kräver {want}"
            continue
        if isinstance(want, list):
            have_sigs = {_link_sig(op) for op in have if isinstance(op, Mapping)}
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
    if not isinstance(recipe, Mapping):
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


#: Enda driftstatusen som får nå en deploy. Allt annat vägras på deploy-vägen.
DEPLOY_OK = "DEPLOY-KANDIDAT"

#: Får köras i labb — torrkörning, mock, tbx-dömda mätningar — men aldrig
#: deployas. Deploy sker endast via komponat-manifestet.
LABB_OK = "LABB"

#: Hela ordförrådet. Ett värde utanför det är ett stavfel, och ett stavfel får
#: aldrig tolkas som ett godkännande.
KANDA_STATUSAR = (DEPLOY_OK, LABB_OK, "EJ-DEPLOY", "OKAND")

#: Artefaktklasser som ÄR deploy-vägen: en komponat-op-lista (och dess manifest)
#: är den enda vägen till drift, så den kräver `DEPLOY-KANDIDAT` oavsett hur den
#: anropas. `LABB` på ett komponat är en självmotsägelse och vägras.
KOMPONAT_SCHEMAN = ("komponat/1", "komponat-manifest/1")


def deploy_status(recipe: Any) -> str | None:
    """Receptets driftstatus, eller `None` när fältet saknas."""
    if recipe is None or not hasattr(recipe, "get"):
        return None
    status = recipe.get("status")
    return None if status is None else str(status).strip().upper()


def check_deploy_status(recipe: Any, *, deploy: bool = False) -> None:
    """Gate: varje recept måste bära en status, och bara rätt status får köra.

    Märkningen fanns (opus5 0c8554b) men ingen konsumerade den, så "aldrig
    deploybar" var en konvention och inte en grind (deepseeks trio-review, flagga
    ii). Nu är den hård, och sedan Fables disposition också **villkorslös**: en
    artefakt utan status vägras, punkt. Alla recept i `recept/` är märkta, så
    tystnad betyder numera att någon lagt till en fil utan att ta ställning — och
    det ska stoppa, inte glida igenom.

    Två vägar, med olika krav:

    * **Deploy** — `DEPLOY-KANDIDAT` och inget annat. Vägen känns igen på
      artefakten (ett komponat ÄR deploy-vägen) eller pekas ut av anroparen med
      `deploy=True`. `LABB` räcker inte här; det är hela skillnaden mellan de två.
    * **Labb** — torrkörning, mock, tbx-dömda mätningar. `LABB` och
      `DEPLOY-KANDIDAT` går båda igenom; en deploy-kandidat får förstås mätas.

    `EJ-DEPLOY` och `OKAND` vägras överallt. `EJ-DEPLOY` bär domens skäl vidare i
    klartext, så den som blir stoppad slipper leta reda på varför.
    """
    if recipe is None or not hasattr(recipe, "get"):
        if deploy:
            raise FailClosed(
                "deploy-status",
                "deployläge kräver förseglat recept/manifest — recipe=None släpps inte igenom",
            )
        return
    status = deploy_status(recipe)
    schema = str(recipe.get("schema") or "")
    rid = recipe.get("id") or recipe.get("recept_id") or "receptet"
    ar_deploy = deploy or schema in KOMPONAT_SCHEMAN

    if status is None:
        raise FailClosed(
            "deploy-status",
            f"{rid} saknar status — varje recept måste ta ställning "
            f"({'/'.join(KANDA_STATUSAR)}). Tystnad är inte ett godkännande.",
        )
    if status not in KANDA_STATUSAR:
        raise FailClosed(
            "deploy-status",
            f"{rid} har okänd status {status!r} — kända är {'/'.join(KANDA_STATUSAR)}. "
            f"Ett värde ingen känner igen godkänns inte.",
        )

    skal = recipe.get("status_skal")
    svans = f" Skäl: {skal}" if skal else ""
    if status in ("EJ-DEPLOY", "OKAND"):
        raise FailClosed(
            "deploy-status", f"{rid} har status {status} — vägrar köra.{svans}"
        )
    if ar_deploy and status != DEPLOY_OK:
        vad = f"{schema}-artefakt" if schema else "deploy-körning"
        raise FailClosed(
            "deploy-status",
            f"{rid} har status {status}, men en {vad} kräver {DEPLOY_OK}. "
            f"{LABB_OK} får mätas, aldrig deployas.{svans}",
        )


def guard_mutation(
    action: str,
    *,
    recipe: Any = None,
    live: Mapping | None = None,
    require_live: bool = True,
    freeze: FreezeContext | None = None,
    deploy: bool = False,
) -> None:
    """Gate 7 always for mutating verbs; 4 if recipe given; 3 if live required.

    `deploy=True` säger att det här är en driftkörning och inte en labbmätning.
    Default är labb därför att en fristående fixtur-apply ÄR en labbkörning —
    deploy sker endast via komponat-manifestet, och det manifestet känns igen på
    sitt schema utan att någon behöver komma ihåg flaggan. Flaggan finns för en
    framtida deploy-runner som vill kräva `DEPLOY-KANDIDAT` även av något som
    kallar sig labb.
    """
    action = (action or "").strip().lower()
    mutating = {"apply", "undo", "plant", "portvakt"}
    if action not in mutating:
        raise FailClosed("action", f"okänd mutationsverb {action!r}")
    check_change_freeze(freeze)
    if action == "portvakt":
        return
    if recipe is None:
        if action in {"apply", "undo"}:
            raise FailClosed("anchor", f"{action} kräver recept för ankarvalidering")
        if action == "plant" and deploy:
            raise FailClosed(
                "deploy-status",
                "plant i deployläge kräver recept/sigill — recipe=None är stängt",
            )
        return
    rid = recipe.get("id") if hasattr(recipe, "get") else None
    if isinstance(recipe, SealedRecipe) or rid in REGISTERED_IDS:
        verify_fixture_seal(recipe)
    # Före ankarvalideringen: ett recept som inte får köras ska vägras på den
    # grunden, inte på ett ankarfel som råkar hittas först.
    check_deploy_status(recipe, deploy=deploy)
    check_anchors(recipe)
    if action in {"apply", "undo"} and require_live:
        check_live_sealed(live, recipe)


def guard_plant(
    recipe: Any = None, *, freeze: FreezeContext | None = None, deploy: bool = False
) -> None:
    """PlanLink / plant / replant. Always freeze; anchors if a recipe is given."""
    guard_mutation("plant", recipe=recipe, require_live=False, freeze=freeze, deploy=deploy)


def guard_portvakt(*, freeze: FreezeContext | None = None) -> None:
    guard_mutation("portvakt", freeze=freeze)


def send_plan_link(
    ctl: Any,
    payload: Any,
    recipe: Any = None,
    *,
    freeze: FreezeContext | None = None,
    deploy: bool = False,
    deploy_ctx: DeployContext | None = None,
) -> Any:
    """Production PlanLink entry. Sealed V296 + deployläge require minted context."""
    payload_sha = planlink_payload_sha256(payload)
    is_v296 = payload_sha == SEALED_V296_PAYLOAD_SHA256
    schema = ""
    if recipe is not None and hasattr(recipe, "get"):
        schema = str(recipe.get("schema") or "")
    need_campaign = bool(
        deploy
        or deploy_ctx is not None
        or active_deploy_context() is not None
        or is_v296
        or schema in KOMPONAT_SCHEMAN
    )
    consumed = False
    if need_campaign:
        require_deploy_context(deploy_ctx)
        if recipe is None or not hasattr(recipe, "get"):
            raise FailClosed(
                "deploy-status",
                "PlanLink i deployläge kräver förseglat recept — recipe=None är stängt",
            )
        guard_plant(recipe, freeze=freeze, deploy=True)
        consume_deploy_apply(
            kind="plan_link",
            name=V296_STEP_NAME,
            payload_sha256=payload_sha,
            ctx=deploy_ctx,
        )
        consumed = True
    else:
        if recipe is None:
            raise FailClosed(
                "deploy-status",
                "PlanLink kräver recept/sigill — recipe=None når inte live "
                "(labbväg är inte tyst deployersättning för V296)",
            )
        if schema in KOMPONAT_SCHEMAN and active_deploy_context() is None:
            raise FailClosed(
                "deploy-context",
                "labbväg + komponat-ops vägras — deploy sker bara via runnern",
            )
        guard_plant(recipe, freeze=freeze, deploy=False)
    if not hasattr(ctl, "request"):
        if consumed:
            revert_last_apply()
        raise FailClosed("plant", "ctl saknar request — plant avbruten")
    try:
        return ctl.request({"PlanLink": payload} if not isinstance(payload, str) else payload)
    except Exception:
        if consumed:
            revert_last_apply()
        raise


def is_plant_command(cmd: Any) -> bool:
    if isinstance(cmd, dict):
        return any(str(k).lower() == "planlink" for k in cmd)
    if isinstance(cmd, str):
        low = cmd.strip().lower()
        return low.startswith("planlink") or " planlink " in f" {low} "
    return False
