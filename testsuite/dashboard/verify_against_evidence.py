"""Prove the dashboard shows the envelopes' numbers, field by field.

Reads the RUNS JSON back out of the built HTML — the artifact the browser
renders — and compares it against the raw envelopes on disk. Field names below
are the embedded structure's own, read from a built page, not guessed.

Zones are deliberately out of scope: the map view draws them from snapshot
files, not from the embedded JSON, so the page carries no zone numbers to
compare. Everything the page states as a number is covered.

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
    for line in Path(index_path).read_text().splitlines():
        tier, rel = line.split()
        envelopes[tier] = json.loads((BASE / rel).read_text())

    # The dashboard level whose runId matches each envelope, wherever the
    # builder grouped it.
    def level_for(tier: str, run_id: str) -> dict:
        for group in runs:
            level = (group.get("levels") or {}).get(tier)
            if level and level.get("runId") == run_id:
                return level
        raise AssertionError(f"{tier}: {run_id} not on the dashboard")

    for tier, envelope in sorted(envelopes.items()):
        level = level_for(tier, envelope["run_id"])
        check(f"{tier}.status", level.get("status"), envelope["status"])
        payload = envelope["payload"]

        if tier == "t0":
            check("t0.total", level["total"], payload["total"])
            check("t0.verdict", level["verdict"], payload["verdict"])
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
            check("t1.dash.verdict", data["dash"]["verdict"], payload["dash"]["verdict"])
        elif tier == "t2":
            for key in (
                "stall_firings", "speed_1s", "speed_100ms", "still_s_per_bot",
                "quad_takes", "pent_takes", "quad_lay_avg", "pent_lay_avg",
                "polls", "bots",
            ):
                check(f"t2.{key}", level["stats"][key], payload["stats"][key])
            # The page renders verdicts in Swedish; the translation is the
            # only transform accepted, and only this exact pair.
            check(
                "t2.verdict",
                "MEASURED" if level["verdict"] == "MÄTT" else level["verdict"],
                payload["verdict"],
            )
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
