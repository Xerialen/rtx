import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

import mlx_run_job


class MlxRunJobContractTests(unittest.TestCase):
    def test_demo_name_carries_cell_index_and_terminal_state(self) -> None:
        self.assertEqual(
            mlx_run_job.demo_name("20260716", "baseline-a", 7, "-12"),
            "20260716_mlx_baseline-a_dm3_match-0007_-12.mvd",
        )

    def test_resume_keeps_completed_and_replans_running(self) -> None:
        matches = {
            "match-0001": {"state": "completed"},
            "match-0002": {"state": "running"},
            "match-0003": {"state": "failed"},
            "match-0004": {"state": "planned"},
        }

        selected = mlx_run_job.select_matches(matches, mode="resume")

        self.assertEqual(selected, ["match-0002", "match-0003", "match-0004"])
        self.assertEqual(matches["match-0001"]["state"], "completed")
        self.assertEqual(matches["match-0002"]["state"], "planned")

    def test_retry_selects_only_failed(self) -> None:
        matches = {
            "match-0001": {"state": "completed"},
            "match-0002": {"state": "failed"},
            "match-0003": {"state": "planned"},
        }

        self.assertEqual(mlx_run_job.select_matches(matches, mode="retry"), ["match-0002"])

    def test_analysis_validation_reads_schema57_damage_by_player(self) -> None:
        players = [{"name": f"p{index}"} for index in range(8)]
        analysis = {
            "match": {
                "duration": 60_000,
                "players": players,
                "teams": [{"name": "mlx", "frags": 1}, {"name": "frogs", "frags": 2}],
            },
            "damage": {
                "byPlayer": {player["name"]: {"given": 0, "taken": 1} for player in players}
            },
        }

        mlx_run_job.validate_analysis(analysis)

    def test_metrics_validation_requires_every_phase_one_field(self) -> None:
        team = {
            "score": 1,
            "fragMargin": 1,
            "frags": 1,
            "deaths": 0,
            "damageGiven": 100,
            "damageTaken": 0,
            "efficiency": 1.0,
            "armorShare": 0.5,
            "healthShare": 0.5,
            "powerupShare": 0.5,
            "itemTimings": {},
            "openingDamageGiven": 100,
            "openingDamageTaken": 0,
            "openingWin": True,
            "deathsAirborne": 0,
            "deathsAirborneEvaluated": 0,
            "fightToImportantItemSamples": 1,
            "fightToImportantItemMedianMs": 1000,
            "fightToImportantItemCensored": False,
        }
        metrics = {
            "schema": "mlx.metrics.v1",
            "durationMs": 60_000,
            "openingCensored": False,
            "openingFirstFragMs": 1000,
            "deathsAirborneMethod": "heuristic",
            "fightToImportantItemDefinition": "definition",
            "teams": {"mlx": team, "frogs": dict(team)},
        }

        mlx_run_job.validate_metrics(metrics)

        del metrics["teams"]["mlx"]["armorShare"]
        with self.assertRaisesRegex(ValueError, "armorShare"):
            mlx_run_job.validate_metrics(metrics)


if __name__ == "__main__":
    unittest.main()
