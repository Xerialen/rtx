"""Prove the dashboard shows the envelopes' numbers, field by field.

Reads the RUNS JSON back out of the built HTML — the artifact the browser
renders — and compares it against the raw envelopes on disk. Field names below
are the embedded structure's own, read from a built page, not guessed. Complete
tiers are checked field by field; failed and aborted tiers are checked as state
and error and are never interpreted as measurements.

Zones are deliberately out of scope: the map view draws them from snapshot
files, not from the embedded JSON, so the page carries no zone numbers to
compare. Everything the page states as a number is covered.

The index names the runner's LAST attempt per tier, which is not always the
envelope the page selected: when that attempt failed, the builder shows an
earlier complete envelope from the same group. That is not a mismatch to crash
on. The failed run is then proven to be listed as an attempt with its own
status, and the envelope the page DID select is checked field by field against
its own file. Both facts get proven, and no index file is ever rewritten to make
the check pass.

Run: python3 dashboard/verify_against_evidence.py <index-file>
where index-file lines are "t0 evidence/t0-....json" for one chain.
"""
import json
import re
import sys
from pathlib import Path

# Resolved from the repo layout, so the check runs wherever the suite lives.
BASE = Path(__file__).resolve().parent.parent
DASHBOARD = BASE / "dashboard" / "dashboard.html"

failures: list[str] = []
checked = 0


def check(what: str, shown, truth) -> None:
    global checked
    checked += 1
    same = (
        abs(shown - truth) < 1e-6
        if isinstance(shown, (int, float)) and isinstance(truth, (int, float))
        and not isinstance(shown, bool) and not isinstance(truth, bool)
        else shown == truth
    )
    if not same:
        failures.append(f"{what}: dashboard={shown!r} envelope={truth!r}")


def main(index_path: str) -> None:
    text = DASHBOARD.read_text(encoding="utf-8")
    runs = json.loads(re.search(r"const RUNS = (\[.*?\]);\n", text, re.S).group(1))

    envelopes = {}
    paths: dict[str, Path] = {}
    for line in Path(index_path).read_text().splitlines():
        tier, rel = line.split()
        paths[tier] = BASE / rel
        envelopes[tier] = json.loads(paths[tier].read_text())

    # The dashboard level whose runId matches each envelope, wherever the
    # builder grouped it.
    def level_for(tier: str, run_id: str) -> dict | None:
        for group in runs:
            level = (group.get("levels") or {}).get(tier)
            if level and level.get("runId") == run_id:
                return level
        return None

    def attempt_for(run_id: str) -> dict | None:
        """The run as the page lists it among a group's attempts."""
        for group in runs:
            for attempt in group.get("attempts") or []:
                if attempt.get("runId") == run_id:
                    return attempt
        return None

    def displayed_level(tier: str) -> dict | None:
        for group in runs:
            level = (group.get("levels") or {}).get(tier)
            if level and level.get("runId"):
                return level
        return None

    # (tier, envelope, level) to check field by field. Normally one per index
    # line. When the index names a run the page did not select, the pair is
    # rebuilt so that BOTH facts get proven — see below.
    work: list[tuple[str, dict, dict]] = []
    for tier, envelope in sorted(envelopes.items()):
        level = level_for(tier, envelope["run_id"])
        if level is not None:
            work.append((tier, envelope, level))
            continue
        # The index names the runner's LAST attempt per tier. When that attempt
        # failed, the builder deliberately shows an earlier complete envelope
        # from the same group instead — status-aware selection. The contract
        # says failed and aborted runs are checked as state and error and are
        # never interpreted as measurements, so a failed run that is absent as a
        # LEVEL is correct. Two things must still hold, and skipping either
        # would turn a passing run into a thinner one:
        #   1. the page did not drop the failed run silently — it appears as an
        #      attempt, carrying its own status;
        #   2. whatever the page DID select for that tier is itself checked
        #      field by field against its own envelope on disk.
        if envelope["status"] == "complete":
            failures.append(
                f"{tier}.runId: complete envelope {envelope['run_id']} "
                "is not shown on the dashboard"
            )
            continue
        attempt = attempt_for(envelope["run_id"])
        if attempt is None:
            failures.append(
                f"{tier}.attempt: {envelope['run_id']} is neither a level nor a "
                "listed attempt — the page lost the run"
            )
            continue
        check(f"{tier}.attempt.status", attempt.get("status"), envelope["status"])
        shown = displayed_level(tier)
        if shown is None:
            print(
                f"not: {tier} {envelope['run_id']} ({envelope['status']}) visas som "
                "försök; ingen nivå vald för tiern"
            )
            continue
        shown_path = BASE / "evidence" / f"{shown['runId']}.json"
        print(
            f"not: {tier} {envelope['run_id']} ({envelope['status']}) visas som "
            f"försök — statusmedvetet kuvertval; vald nivå {shown['runId']} "
            "kontrolleras i stället"
        )
        work.append((tier, json.loads(shown_path.read_text()), shown))

    for tier, envelope, level in work:
        check(f"{tier}.status", level.get("status"), envelope["status"])
        if envelope["status"] != "complete":
            check(f"{tier}.error", level.get("error"), envelope.get("error"))
            check(f"{tier}.verdict", level.get("verdict"), envelope["status"].upper())
            continue
        payload = envelope["payload"]

        if tier == "t0":
            check("t0.total", level["total"], payload["total"])
            check("t0.verdict", level["verdict"], payload["verdict"])
            check("t0.qualityFloors", level["qualityFloors"], payload["quality_floors"])
            check(
                "t0.modules",
                {m["name"]: (m["tests"], m["passed"]) for m in level["modules"]},
                {m["name"]: (m["tests"], m["passed"]) for m in payload["modules"]},
            )
        elif tier == "t1":
            data = level["data"]
            check("t1.verdict", level["verdict"], payload["verdict"])
            nav = data["nav"]
            for html_key, env_key in (
                ("cells", "cells"), ("links", "links"),
                ("rjLinks", "rj_links"), ("waitedS", "waited_s"),
                ("map", "map"), ("state", "state"),
            ):
                check(f"t1.nav.{env_key}", nav[html_key], envelope["nav"][env_key])
            drills = {d["name"]: d for d in data["drills"]}
            for scenario in payload["scenarios"]:
                name = scenario["name"]
                drill = drills.get(name)
                if drill is None:
                    failures.append(f"t1.{name}: missing from dashboard")
                    continue
                check(f"t1.{name}.verdict", drill["verdict"], scenario.get("verdict"))
                check(f"t1.{name}.bestTime", drill["bestTime"], scenario.get("best_time_s"))
                check(f"t1.{name}.arrived", drill["arrived"], scenario.get("arrived"))
                check(
                    f"t1.{name}.statuses",
                    [r["status"] for r in drill["results"]],
                    [a["status"] for a in scenario.get("attempts") or []],
                )
            check("t1.dash.verdict", data["dash"]["verdict"], payload["dash"].get("verdict"))
        elif tier == "t2":
            for key in (
                "stall_firings", "speed_1s", "speed_100ms", "still_s_per_bot",
                "quad_takes", "pent_takes", "quad_lay_avg", "pent_lay_avg",
                "polls", "bots",
            ):
                check(f"t2.{key}", level["stats"][key], payload["stats"][key])
            # En sidokontrollpost kan ha domt kuvertet utan att rora det. Da ar
            # sidans avvikande verdikt DET RATTA, och verifieraren maste lasa
            # samma evidens som byggaren — annars flaggar den varje gang en
            # oberoende grind sagt emot en sjalvdeklaration, vilket ar precis
            # nar sidan har mest ratt.
            envelope_path = paths[tier]
            control_path = envelope_path.with_name(
                envelope_path.name.replace(".json", "-control.json")
            )
            override = None
            if control_path.is_file():
                control = json.loads(control_path.read_text(encoding="utf-8"))
                if str(control.get("result", "")).upper() != "PASS":
                    override = str(control.get("verdict_override") or "FORSOK")
            if override is not None:
                check(
                    "t2.verdict(kontrollpost)",
                    "FORSOK" if level["verdict"] == "FÖRSÖK" else level["verdict"],
                    override,
                )
                check("t2.gateNote finns", bool(level.get("gateNote")), True)
            else:
                # The page renders verdicts in Swedish; the translation is the
                # only transform accepted, and only this exact pair.
                check(
                    "t2.verdict",
                    "MEASURED" if level["verdict"] == "MÄTT" else level["verdict"],
                    payload["verdict"],
                )
            check("t2.sources", level["metricSources"], payload.get("sources", {}))
            for html_key, env_key in (("cells", "cells"), ("rjLinks", "rj_links")):
                check(f"t2.nav.{env_key}", level["nav"][html_key], envelope["nav"][env_key])
        elif tier == "t3":
            check("t3.winner", level["result"]["winner"], payload["result"]["winner"])
            check("t3.diff", level["result"]["diff"], payload["result"]["diff"])
            check("t3.kind", level["kind"], "pipeline")
            check("t3.verdict", level.get("verdict"), payload["verdict"])
            # The panel's score comes from the scoreboard oracle's team totals;
            # winner+diff above are the claim the verdict rests on, and the
            # internal consistency is its own check.
            score = level.get("score") or {}
            if score:
                check(
                    "t3.score-consistency",
                    abs(score.get("branch", 0) - score.get("main", 0)),
                    payload["result"]["diff"],
                )
        elif tier == "t4":
            check("t4.reached", level["reached"], payload["reached"])
            check(
                "t4.rungs",
                # The ladder pads unplayed rungs for display; the envelope
                # only records what was climbed. Played rungs compare strictly.
                [
                    (r["skill"], r["for"], r["against"], r["state"])
                    for r in level["rungs"]
                    if r["state"] != "unplayed"
                ],
                [
                    (
                        r["skill"], r["frags_for"], r["frags_against"],
                        "won" if r["win"] else "lost",
                    )
                    for r in payload["ladder"]
                ],
            )

    print(f"kontrollerade fält: {checked}")
    if failures:
        print(f"AVVIKELSER: {len(failures)}")
        for failure in failures:
            print("  ", failure)
        sys.exit(1)
    print("dashboarden återger kuverten exakt — 0 avvikelser")


if __name__ == "__main__":
    main(sys.argv[1])
