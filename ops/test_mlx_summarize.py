import unittest

import mlx_summarize


class MlxSummarizeTests(unittest.TestCase):
    def test_build_row_materializes_phase_one_metrics_and_na_reasons(self) -> None:
        sidecar = {
            "demoFile": "demo.mvd",
            "sha256": "a" * 64,
            "experimentId": "job-1",
            "rexCommit": "b" * 40,
            "analyzerCommit": "c" * 40,
            "benchmarkCell": "baseline-a",
            "serverConfig": {"rtx_bot_bhop": 0},
            "match": "match-0001",
        }
        analysis = {
            "match": {
                "players": [
                    {"name": "m1", "team": "mlx", "frags": 1, "deaths": 0},
                    {"name": "f1", "team": "frogs", "frags": 0, "deaths": 1},
                ]
            },
            "damage": {"byPlayer": {"m1": {"given": 100, "taken": 0}, "f1": {"given": 0, "taken": 100}}},
        }
        team = {
            "score": 1,
            "fragMargin": 1,
            "frags": 1,
            "deaths": 0,
            "damageGiven": 100,
            "damageTaken": 0,
            "efficiency": 1.0,
            "armorShare": None,
            "healthShare": None,
            "powerupShare": None,
            "itemTimings": {},
            "openingDamageGiven": 100,
            "openingDamageTaken": 0,
            "openingWin": True,
            "deathsAirborne": 0,
            "deathsAirborneEvaluated": 1,
            "fightToImportantItemSamples": 0,
            "fightToImportantItemMedianMs": None,
            "fightToImportantItemCensored": True,
        }
        metrics = {
            "schema": "mlx.metrics.v1",
            "map": "dm3",
            "durationMs": 60_000,
            "openingCensored": False,
            "openingFirstFragMs": 1000,
            "deathsAirborneMethod": "heuristic",
            "fightToImportantItemDefinition": "definition",
            "teams": {"mlx": team, "frogs": dict(team, score=0, fragMargin=-1)},
        }

        row = mlx_summarize.build_row(
            sidecar,
            analysis,
            metrics,
            {"airbornePrecision": 0.95, "fightToItemPrecision": 0.95},
        )

        self.assertEqual(row["mlx_score"], 1)
        self.assertEqual(row["mlx_armor_share_na_reason"], "no_armor_pickups")
        self.assertEqual(row["mlx_fight_to_item_median_ms_na_reason"], "no_qualifying_sequence")
        self.assertEqual(row["deaths_airborne_confidence"], "validated-high")
        self.assertEqual(row["fight_to_item_confidence"], "validated-high")
        self.assertEqual(row["player_frags_json"], '{"f1":0,"m1":1}')
        mlx_summarize.validate_row(row)

    def test_missing_manual_validation_has_explicit_na_reasons(self) -> None:
        sidecar = {
            "demoFile": "demo.mvd",
            "sha256": "a" * 64,
            "experimentId": "job-1",
            "rexCommit": "b" * 40,
            "analyzerCommit": "c" * 40,
            "benchmarkCell": "baseline-a",
            "serverConfig": {},
            "match": "match-0001",
        }
        analysis = {
            "match": {"players": []},
            "damage": {"byPlayer": {}},
        }
        team = {
            "score": 0,
            "fragMargin": 0,
            "frags": 0,
            "deaths": 0,
            "damageGiven": 0,
            "damageTaken": 0,
            "efficiency": None,
            "armorShare": None,
            "healthShare": None,
            "powerupShare": None,
            "itemTimings": {},
            "openingDamageGiven": None,
            "openingDamageTaken": None,
            "openingWin": None,
            "deathsAirborne": 0,
            "deathsAirborneEvaluated": 0,
            "fightToImportantItemSamples": 0,
            "fightToImportantItemMedianMs": None,
            "fightToImportantItemCensored": True,
        }
        metrics = {
            "map": "dm3",
            "durationMs": 60_000,
            "openingCensored": True,
            "openingFirstFragMs": None,
            "deathsAirborneMethod": "heuristic",
            "fightToImportantItemDefinition": "definition",
            "teams": {"mlx": team, "frogs": dict(team)},
        }

        row = mlx_summarize.build_row(sidecar, analysis, metrics, {})

        self.assertEqual(row["deaths_airborne_validation_precision_na_reason"], "manual_sample_not_supplied")
        self.assertEqual(row["fight_to_item_validation_precision_na_reason"], "manual_sample_not_supplied")
        self.assertEqual(row["fight_to_item_confidence"], "unvalidated")
        mlx_summarize.validate_row(row)


if __name__ == "__main__":
    unittest.main()
