#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Materialize one compressed MLX Phase 1 summary row per completed match."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


TEAM_NAMES = ("mlx", "frogs")
ITEM_KINDS = ("ga", "ya", "ra", "h15", "h25", "mh", "quad", "pent", "ring", "rl", "lg")


def compact_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def nullable(value: object, reason: str) -> tuple[object, str]:
    return value, "" if value is not None else reason


def validation_confidence(precision: object) -> str:
    if precision is None:
        return "unvalidated"
    return "low" if float(precision) < 0.8 else "validated-high"


def build_row(
    sidecar: dict[str, object],
    analysis: dict[str, object],
    metrics: dict[str, object],
    validation: dict[str, object],
) -> dict[str, object]:
    match = analysis["match"]
    damage_by_player = (analysis.get("damage") or {}).get("byPlayer") or {}
    players = match["players"]
    row: dict[str, object] = {
        "schema": "mlx.phase1-summary.v1",
        "job_id": sidecar["experimentId"],
        "match_id": sidecar["match"],
        "demo_file": sidecar["demoFile"],
        "demo_sha256": sidecar["sha256"],
        "map": metrics["map"],
        "duration_ms": metrics["durationMs"],
        "benchmark_cell": sidecar["benchmarkCell"],
        "bhop_enabled": bool((sidecar.get("serverConfig") or {}).get("rtx_bot_bhop", 0)),
        "rex_commit": sidecar["rexCommit"],
        "analyzer_commit": sidecar["analyzerCommit"],
        "opening_censored": metrics["openingCensored"],
        "deaths_airborne_method": metrics["deathsAirborneMethod"],
        "fight_to_item_definition": metrics["fightToImportantItemDefinition"],
        "player_frags_json": compact_json({player["name"]: player.get("frags", 0) for player in players}),
        "player_deaths_json": compact_json({player["name"]: player.get("deaths", 0) for player in players}),
        "player_damage_given_json": compact_json(
            {player["name"]: (damage_by_player.get(player["name"]) or {}).get("given") for player in players}
        ),
        "player_damage_taken_json": compact_json(
            {player["name"]: (damage_by_player.get(player["name"]) or {}).get("taken") for player in players}
        ),
    }
    airborne_precision, airborne_reason = nullable(
        validation.get("airbornePrecision"), "manual_sample_not_supplied"
    )
    fight_precision, fight_reason = nullable(
        validation.get("fightToItemPrecision"), "manual_sample_not_supplied"
    )
    row["deaths_airborne_validation_precision"] = airborne_precision
    row["deaths_airborne_validation_precision_na_reason"] = airborne_reason
    row["deaths_airborne_confidence"] = validation_confidence(airborne_precision)
    row["fight_to_item_validation_precision"] = fight_precision
    row["fight_to_item_validation_precision_na_reason"] = fight_reason
    row["fight_to_item_confidence"] = validation_confidence(fight_precision)
    opening_first, opening_reason = nullable(metrics.get("openingFirstFragMs"), "no_frag_within_60s")
    row["opening_first_frag_ms"] = opening_first
    row["opening_first_frag_ms_na_reason"] = opening_reason

    for team_name in TEAM_NAMES:
        team = metrics["teams"][team_name]
        prefix = f"{team_name}_"
        for source, target in (
            ("score", "score"),
            ("fragMargin", "frag_margin"),
            ("frags", "frags"),
            ("deaths", "deaths"),
            ("damageGiven", "damage_given"),
            ("damageTaken", "damage_taken"),
            ("deathsAirborne", "deaths_airborne"),
            ("deathsAirborneEvaluated", "deaths_airborne_evaluated"),
            ("fightToImportantItemSamples", "fight_to_item_samples"),
            ("fightToImportantItemCensored", "fight_to_item_censored"),
        ):
            row[prefix + target] = team[source]
        for source, target, reason in (
            ("efficiency", "efficiency", "zero_frags_plus_deaths"),
            ("armorShare", "armor_share", "no_armor_pickups"),
            ("healthShare", "health_share", "no_health_pickups"),
            ("powerupShare", "powerup_share", "no_powerup_pickups"),
            ("openingDamageGiven", "opening_damage_given", "no_frag_within_60s"),
            ("openingDamageTaken", "opening_damage_taken", "no_frag_within_60s"),
            ("openingWin", "opening_win", "no_frag_within_60s"),
            ("fightToImportantItemMedianMs", "fight_to_item_median_ms", "no_qualifying_sequence"),
        ):
            value, na_reason = nullable(team[source], reason)
            row[prefix + target] = value
            row[prefix + target + "_na_reason"] = na_reason
        timings = team["itemTimings"]
        for kind in ITEM_KINDS:
            timing = timings.get(kind)
            row[f"{prefix}item_{kind}_pickups"] = int(timing["pickups"]) if timing else 0
            median = timing["medianTakeMs"] if timing and timing["pickups"] else None
            value, na_reason = nullable(median, "no_pickups")
            row[f"{prefix}item_{kind}_median_take_ms"] = value
            row[f"{prefix}item_{kind}_median_take_ms_na_reason"] = na_reason
    return row


def validate_row(row: dict[str, object]) -> None:
    for key, value in row.items():
        if value is not None:
            continue
        reason = row.get(key + "_na_reason")
        if not isinstance(reason, str) or not reason:
            raise ValueError(f"summary column {key} is null without an n/a reason")
    for key, value in row.items():
        if key.endswith("_na_reason") and not isinstance(value, str):
            raise ValueError(f"summary n/a reason {key} is not a string")


def resolve_publication(path: Path) -> Path:
    if path.is_dir():
        return path
    if path.parent.name == "outbox":
        synced = path.parent.parent / "synced" / path.name
        if synced.is_dir():
            return synced
    raise FileNotFoundError(f"publication not found in outbox or synced: {path}")


def load_rows(job_dir: Path) -> list[dict[str, object]]:
    status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
    config = json.loads((job_dir / "config.json").read_text(encoding="utf-8"))
    validation = config.get("metricValidation") or {}
    rows: list[dict[str, object]] = []
    for match_id, match in sorted(status["matches"].items()):
        if match["state"] != "completed":
            continue
        publication = resolve_publication(Path(match["publication"]))
        sidecars = list(publication.glob("*.mvd.json"))
        if len(sidecars) != 1:
            raise ValueError(f"{publication} has {len(sidecars)} demo sidecars")
        sidecar = json.loads(sidecars[0].read_text(encoding="utf-8"))
        analysis = json.loads((publication / "analysis.json").read_text(encoding="utf-8"))
        metrics = json.loads((publication / "metrics.json").read_text(encoding="utf-8"))
        if sidecar["match"] != match_id:
            raise ValueError(f"sidecar match mismatch in {publication}")
        row = build_row(sidecar, analysis, metrics, validation)
        validate_row(row)
        rows.append(row)
    return rows


def write_summary(rows: list[dict[str, object]], output_dir: Path) -> None:
    if not rows:
        raise ValueError("no completed match rows to summarize")
    output_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    for row in rows[1:]:
        if list(row) != fieldnames:
            raise ValueError("summary rows have inconsistent columns")
    temporary_csv = output_dir / "summary.csv.tmp"
    with temporary_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary_csv.replace(output_dir / "summary.csv")

    import pyarrow as pa
    import pyarrow.parquet as pq

    temporary_parquet = output_dir / "summary.parquet.tmp"
    pq.write_table(pa.Table.from_pylist(rows), temporary_parquet, compression="zstd")
    temporary_parquet.replace(output_dir / "summary.parquet")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = load_rows(args.job_dir.resolve())
    write_summary(rows, args.output_dir.resolve())
    print(f"summary_rows={len(rows)} summary_columns={len(rows[0])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
