"""Run the same tiers against several builds, one build at a time.

Comparing branches means running each of them through identical work on the
same rig, and that is a procedure, not a judgement call: swap the library in,
restart the server, run the tiers, move on, and put the baseline back at the
end whatever happened. Written down here it repeats exactly; improvised in a
shell it does not, and the ways it fails are quiet ones — a commit landing
mid-sweep splits a column across two build ids, an edited working tree stamps
half the evidence `dirty`, a leftover library from an aborted attempt makes the
next column measure the wrong binary.

So the sweep refuses to start unless the ground is stable, records what it
actually did, and restores the rig on its way out.

One thing it cannot establish, and does not claim to: that the restarted server
loaded the library that was just copied in. It writes the bytes and runs the
configured restart command, and if that command restarts a unit pointing
somewhere else, or a unit that keeps its already-mapped library, the column
would name a build it did not measure. Closing that needs the server to report
a digest of what it is running; the current control protocol reports none. Until
it does, the deploy path and the restart command have to agree with each other,
and `[sweep].deploy_to` is the place that has to be right.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from . import t1, t2
from .runlib import (
    ConfigError,
    RunAborted,
    build_identity,
    config_path,
    load_config,
    utc_now,
    utc_text,
)

# What a sweep can vary is the server library it deploys, so it can only run the
# tiers that measure one. T0 has no live phase at all — it imports a cargo
# summary. T3 and T4 measure the *client* binary against a stock KTX server
# (`t3ktx/qwprogs.so` is KTX's own), so deploying a library would change nothing
# they observe while stamping their evidence with that library's commit: a
# column that names a build it never measured.
RUNNABLE = ("t1", "t2")
CLIENT_TIERS = ("t3", "t4")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # Silence here would read as "clean checkout" and let a sweep record a
        # provenance it never established.
        raise ConfigError(
            f"{repo}: git {' '.join(args)} failed: {result.stderr.strip() or 'no output'}"
        )
    return result.stdout.strip()


def _scenario_digest(directory: Path) -> str:
    """One digest over every scenario file, so columns can be shown to match.

    Two builds are only comparable if they answered the same questions. This is
    the cheapest proof of that, and it is recorded rather than assumed.
    """
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.toml")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()[:16]}"


def _targets(config: dict[str, Any], source: str) -> list[dict[str, Any]]:
    sweep = config.get("sweep")
    if not isinstance(sweep, dict):
        raise ConfigError(f"{source}: no [sweep] section")
    targets = sweep.get("target")
    if not isinstance(targets, list) or not targets:
        raise ConfigError(f"{source}: [sweep] needs at least one [[sweep.target]]")
    labels = [target.get("label") for target in targets]
    if len(set(labels)) != len(labels):
        raise ConfigError(f"{source}: sweep target labels must be unique")
    for target in targets:
        for field in ("label", "config", "library"):
            if not isinstance(target.get(field), str) or not target[field]:
                raise ConfigError(f"{source}: sweep target needs a {field}")
        tiers = target.get("tiers", sweep.get("tiers", ["t1", "t2"]))
        # T0 imports a summary someone else produced, so it has nothing to run
        # here; a target with no tiers would deploy a build and measure nothing.
        if not isinstance(tiers, list) or not tiers:
            raise ConfigError(f"{source}: {target['label']}: needs at least one tier")
        for tier in tiers:
            if tier in CLIENT_TIERS:
                raise ConfigError(
                    f"{source}: {target['label']}: {tier} measures the client "
                    "binary against a stock KTX server, so a sweep that swaps "
                    "the server library would stamp it with a build it did not "
                    "measure — run it directly with the config for that client"
                )
            if tier not in RUNNABLE:
                raise ConfigError(
                    f"{source}: {target['label']}: tiers must be from {RUNNABLE}"
                )
        target["tiers"] = list(tiers)
    return targets


def _deploy(sweep: dict[str, Any], library: Path, source: str) -> None:
    destination = sweep.get("deploy_to")
    if not isinstance(destination, str) or not destination:
        raise ConfigError(f"{source}: [sweep].deploy_to is required")
    if not library.is_file():
        raise ConfigError(f"{library}: sweep library is missing — build it first")
    Path(destination).write_bytes(library.read_bytes())
    restart = sweep.get("restart_cmd")
    if isinstance(restart, str) and restart:
        result = subprocess.run(restart, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"sweep restart_cmd failed ({result.returncode}): {result.stderr.strip()}"
            )
    time.sleep(float(sweep.get("boot_wait_s", 20)))


def _digest(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()[:8] if path.is_file() else "?"


def _run_tier(tier: str, config: dict[str, Any], scenarios: str, quick: bool) -> Path:
    if tier == "t1":
        return t1.run(config, scenarios, quick=quick)
    if tier == "t2":
        return t2.run(config)
    raise ConfigError(f"{tier}: not runnable from a sweep")


def run(
    config_file: str,
    scenarios: str,
    *,
    quick: bool = False,
    allow_dirty: bool = False,
) -> Path:
    """Run every sweep target in order and write one manifest describing it."""
    config = load_config(config_file)
    targets = _targets(config, config_file)
    sweep = config["sweep"]
    scenario_dir = Path(scenarios)
    digest = _scenario_digest(scenario_dir) if scenario_dir.is_dir() else None

    # Every target's checkout has to be settled before the first measurement,
    # not discovered halfway through: build identity is captured when a run
    # starts, so a tree that moves underneath a sweep silently splits its own
    # column in two.
    prepared = []
    for target in targets:
        target_config = load_config(config_path(config, target["config"]))
        repo = config_path(target_config, target_config["build"]["repo_dir"]).resolve()
        dirty = bool(_git(repo, "status", "--porcelain"))
        if dirty and not allow_dirty:
            raise ConfigError(
                f"{target['label']}: {repo} has uncommitted changes — commit them or "
                "pass --allow-dirty to stamp the evidence dirty on purpose"
            )
        prepared.append((
            target,
            target_config,
            repo,
            dirty,
            _git(repo, "rev-parse", "HEAD"),
            bool(_git(repo, "status", "--porcelain", "-uno")),
        ))

    restore = sweep.get("restore")
    restore_library = None
    if isinstance(restore, str) and restore:
        match = [target for target in targets if target["label"] == restore]
        if not match:
            raise ConfigError(f"[sweep].restore names an unknown target: {restore}")
        restore_library = config_path(config, match[0]["library"])

    began = utc_now()
    columns: list[dict[str, Any]] = []
    restored: str | None = None
    restore_error: str | None = None
    try:
        for target, target_config, repo, dirty, commit, tracked_dirty in prepared:
            # Preflight settled the ground before the first measurement; by the
            # time this column's turn comes, minutes of other columns have
            # passed. A tree that moved in between would split this column
            # across two build ids while the manifest names only one, so the
            # question is asked again immediately before the deploy. This
            # narrows the window rather than closing it — a commit landing
            # between this check and the run itself would still slip through,
            # and nothing short of holding a lock on someone else's checkout
            # can prevent that.
            if _git(repo, "rev-parse", "HEAD") != commit:
                raise ConfigError(
                    f"{target['label']}: {repo} moved from {commit[:8]} during the "
                    "sweep — a column cannot span two commits"
                )
            # Tracked changes only. A sweep writes its evidence into the
            # checkout it is measuring, so an earlier column's envelopes show
            # up here as new files; treating those as the tree moving would
            # make a two-target sweep abort on its own output. What matters for
            # build identity is what is committed and what is modified, and
            # neither of those is an untracked file.
            if bool(_git(repo, "status", "--porcelain", "-uno")) != tracked_dirty:
                raise ConfigError(
                    f"{target['label']}: {repo} changed its working tree during "
                    "the sweep — the evidence would be stamped from preflight"
                )
            library = config_path(config, target["library"])
            _deploy(sweep, library, config_file)
            column: dict[str, Any] = {
                "label": target["label"],
                "branch": _git(repo, "branch", "--show-current") or "detached",
                "commit": _git(repo, "rev-parse", "HEAD"),
                "dirty": dirty,
                "library_md5": _digest(library),
                "runs": [],
            }
            for tier in target["tiers"]:
                try:
                    path = _run_tier(tier, target_config, scenarios, quick)
                    document = json.loads(path.read_text(encoding="utf-8"))
                    column["runs"].append(
                        {
                            "tier": tier.upper(),
                            "run_id": document.get("run_id"),
                            "status": document.get("status"),
                            "verdict": (document.get("payload") or {}).get("verdict"),
                        }
                    )
                    # A column that could not be asked about something is not
                    # comparable to one that could, so the manifest says so at
                    # the level a reader compares at.
                    capabilities = document.get("capabilities")
                    if isinstance(capabilities, dict):
                        column["capabilities"] = capabilities
                except RunAborted:
                    raise
                except Exception as exc:  # one tier failing must not cost the sweep
                    # The tier writes its envelope before re-raising, so there
                    # is evidence on disk; a manifest saying `run_id: null`
                    # would send the reader looking for a file it already has.
                    column["runs"].append(
                        {"tier": tier.upper(),
                         "run_id": getattr(exc, "run_id", None),
                         "status": "failed",
                         "error": str(exc)[:200]}
                    )
            columns.append(column)
    finally:
        if restore_library is not None:
            try:
                _deploy(sweep, restore_library, config_file)
                restored = restore
            except Exception as exc:  # must not mask the real error, must not lie
                restored = None
                restore_error = str(exc)[:200]

    manifest = {
        "schema": "rtx-sweep/1",
        "started_utc": utc_text(began),
        "ended_utc": utc_text(utc_now()),
        "scenarios": str(scenario_dir),
        "scenario_digest": digest,
        "regime": "quick" if quick else "full",
        # What the rig actually ended up on, not what was asked for: a failed
        # restore leaves the next run measuring the wrong binary, and a manifest
        # that claimed success would hide exactly that.
        "restored": restored,
        "restore_error": restore_error,
        "columns": columns,
    }
    evidence = config_path(config, config["paths"]["evidence_dir"])
    evidence.mkdir(parents=True, exist_ok=True)
    destination = evidence / f"sweep-{began.strftime('%Y%m%dT%H%M%SZ')}.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    temporary.replace(destination)
    return destination
