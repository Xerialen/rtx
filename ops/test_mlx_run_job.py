import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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

    def test_timeout_is_locked_to_one_and_a_half_match_lengths(self) -> None:
        self.assertEqual(
            mlx_run_job.match_timeout_seconds({"timelimit": 1, "matchTimeoutSeconds": 90}),
            90,
        )
        with self.assertRaisesRegex(ValueError, "1.5"):
            mlx_run_job.match_timeout_seconds({"timelimit": 1, "matchTimeoutSeconds": 180})

    def test_orphan_sweep_refuses_a_recycled_lease_pid(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            job_dir = Path(raw_directory)
            ports = job_dir / "ports"
            ports.mkdir()
            (ports / "28600.lease").write_text(
                json.dumps(
                    {
                        "runnerRunId": "old",
                        "pid": 123,
                        "pgid": 123,
                        "startTimeTicks": "111",
                    }
                ),
                encoding="utf-8",
            )
            members = [(123, f"python mlx_server.py --session-dir {job_dir}")]
            with (
                mock.patch.object(
                    mlx_run_job, "lease_process_matches", return_value=False, create=True
                ),
                mock.patch.object(
                    mlx_run_job,
                    "process_group_members",
                    side_effect=[members, [], [], []],
                ),
                mock.patch.object(mlx_run_job.os, "killpg", create=True) as killpg,
            ):
                with self.assertRaisesRegex(RuntimeError, "nonce"):
                    mlx_run_job.orphan_sweep(job_dir, "new", lambda _message: None)
                killpg.assert_not_called()

    def test_lease_identity_requires_the_recorded_start_nonce(self) -> None:
        lease = {"pid": 123, "pgid": 123, "startTimeTicks": "111"}
        with mock.patch.object(mlx_run_job, "proc_identity", return_value=(123, 123, "222")):
            self.assertFalse(mlx_run_job.lease_process_matches(lease))

    def test_publish_recovers_an_unready_directory_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            root = Path(raw_directory)
            outbox = root / "outbox"
            attempt = root / "attempt"
            attempt.mkdir()
            (attempt / "demo.mvd").write_bytes(b"new demo")
            (attempt / "analysis.json").write_text("{}\n", encoding="utf-8")
            (attempt / "metrics.json").write_text("{}\n", encoding="utf-8")
            (attempt / "match-result.json").write_text(
                json.dumps({"demoSha256": "a" * 64}), encoding="utf-8"
            )

            runner = mlx_run_job.JobRunner.__new__(mlx_run_job.JobRunner)
            runner.runner_run_id = "new-run"
            runner.job_dir = root / "job"
            runner.job_dir.mkdir()
            runner.spec = {
                "startDate": "2026-07-16",
                "cell": "test",
                "outboxDir": str(outbox),
                "jobId": "job",
                "rexCommit": "r",
                "analyzerCommit": "a",
                "mlxVersion": "v",
                "serverConfig": {},
                "botSkill": 7,
                "hubDestination": "hub",
            }
            name = "20260716_mlx_test_dm3_match-0001_2"
            incomplete = outbox / name
            incomplete.mkdir(parents=True)
            (incomplete / f"{name}.mvd.json").write_text(
                json.dumps({"experimentId": "job", "match": "match-0001"}),
                encoding="utf-8",
            )
            (incomplete / f"{name}.mvd").write_bytes(b"preserve me")

            publication, _sidecar = runner.publish(
                "match-0001",
                1,
                attempt,
                {
                    "match": {
                        "teams": [
                            {"name": "mlx", "frags": 3},
                            {"name": "frogs", "frags": 1},
                        ]
                    }
                },
            )

            self.assertTrue((publication / ".ready").is_file())
            recovered = list((runner.job_dir / "incomplete-publications").iterdir())
            self.assertEqual(len(recovered), 1)
            self.assertEqual((recovered[0] / f"{name}.mvd").read_bytes(), b"preserve me")

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
