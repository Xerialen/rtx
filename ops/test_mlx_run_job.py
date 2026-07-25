import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent))

import mlx_run_job


class MlxRunJobContractTests(unittest.TestCase):
    def test_skipped_analysis_uses_ktxstats_without_analyzer(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            attempt = Path(raw_directory)
            demo = b"ten-minute-mvd"
            (attempt / "demo.mvd").write_bytes(demo)
            (attempt / "match-result.json").write_text(
                json.dumps(
                    {
                        "schema": "mlx.match-result.v1",
                        "ok": True,
                        "portsReleased": True,
                        "demoBytes": len(demo),
                        "demoSha256": hashlib.sha256(demo).hexdigest(),
                        "matchStartedAt": "2026-07-17T10:00:00Z",
                        "matchEndedAt": "2026-07-17T10:10:01Z",
                    }
                ),
                encoding="utf-8",
            )
            players = [
                {"name": f"mlx{index}", "team": "mlx", "stats": {"frags": index}}
                for index in range(1, 5)
            ] + [
                {
                    "name": f"frog{index}",
                    "team": "frogs",
                    "stats": {"frags": index + 2},
                    "bot": {"skill": 20},
                }
                for index in range(1, 5)
            ]
            (attempt / "demo.txt").write_text(
                json.dumps(
                    {
                        "version": 3,
                        "map": "dm3",
                        "tl": 10,
                        "duration": 600,
                        "teams": ["mlx", "frogs"],
                        "players": players,
                    }
                ),
                encoding="utf-8",
            )

            analysis = mlx_run_job.build_skipped_analysis(
                attempt,
                {"timelimit": 10, "matchTag": "dm3mlx-4aa9433-vs-fbot-skill20"},
            )

            self.assertEqual(analysis["schema"], "mlx.analysis-skipped.v1")
            self.assertEqual(analysis["status"], "skipped-by-owner-directive")
            self.assertEqual(analysis["consistency"]["teamScores"], {"mlx": 10, "frogs": 18})
            self.assertEqual(analysis["consistency"]["margin"], 8)
            self.assertEqual(analysis["consistency"]["durationSeconds"], 600)
            self.assertEqual(analysis["consistency"]["mvdBytes"], len(demo))
            self.assertEqual(analysis["consistency"]["roster"], {"mlx": 4, "frogs": 4})
            self.assertEqual(
                analysis["matchTag"], "dm3mlx-4aa9433-vs-fbot-skill20"
            )

    def test_skipped_analysis_rejects_bad_roster_and_frogbot_skill(self) -> None:
        def write_attempt(attempt: Path, players: list[dict[str, object]]) -> None:
            demo = b"mvd"
            (attempt / "demo.mvd").write_bytes(demo)
            (attempt / "match-result.json").write_text(
                json.dumps(
                    {
                        "schema": "mlx.match-result.v1",
                        "ok": True,
                        "portsReleased": True,
                        "demoBytes": len(demo),
                        "demoSha256": hashlib.sha256(demo).hexdigest(),
                        "matchStartedAt": "2026-07-17T10:00:00Z",
                        "matchEndedAt": "2026-07-17T10:10:00Z",
                    }
                ),
                encoding="utf-8",
            )
            (attempt / "demo.txt").write_text(
                json.dumps(
                    {
                        "version": 3,
                        "map": "dm3",
                        "tl": 10,
                        "duration": 600,
                        "teams": ["mlx", "frogs"],
                        "players": players,
                    }
                ),
                encoding="utf-8",
            )

        spec = {"timelimit": 10, "matchTag": "dm3mlx-4aa9433-vs-fbot-skill20"}
        with tempfile.TemporaryDirectory() as raw_directory:
            attempt = Path(raw_directory)
            players = [
                {"name": f"mlx{index}", "team": "mlx", "stats": {"frags": 0}}
                for index in range(5)
            ] + [
                {
                    "name": f"frog{index}",
                    "team": "frogs",
                    "stats": {"frags": 0},
                    "bot": {"skill": 20},
                }
                for index in range(3)
            ]
            write_attempt(attempt, players)
            with self.assertRaisesRegex(ValueError, "four players per team"):
                mlx_run_job.build_skipped_analysis(attempt, spec)

        with tempfile.TemporaryDirectory() as raw_directory:
            attempt = Path(raw_directory)
            players = [
                {"name": f"mlx{index}", "team": "mlx", "stats": {"frags": 0}}
                for index in range(4)
            ] + [
                {
                    "name": f"frog{index}",
                    "team": "frogs",
                    "stats": {"frags": 0},
                    "bot": {"skill": 19 if index == 0 else 20},
                }
                for index in range(4)
            ]
            write_attempt(attempt, players)
            with self.assertRaisesRegex(ValueError, "skill 20"):
                mlx_run_job.build_skipped_analysis(attempt, spec)

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

    def test_skipped_mode_does_not_require_full_analysis_commands(self) -> None:
        skipped = {
            "matches": 32,
            "parallel": 32,
            "basePort": 28600,
            "timelimit": 10,
            "matchTimeoutSeconds": 900,
            "analysisMode": "skipped-by-owner-directive",
            "matchTag": "dm3mlx-4aa9433-vs-fbot-skill20",
        }
        mlx_run_job.validate_job_spec(skipped)

        full = dict(skipped, analysisMode="full")
        with self.assertRaisesRegex(ValueError, "analysisCommand"):
            mlx_run_job.validate_job_spec(full)

        with self.assertRaisesRegex(ValueError, "matchTag"):
            mlx_run_job.validate_job_spec(dict(skipped, matchTag=""))

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

    def test_publish_marks_skipped_analysis_and_omits_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            root = Path(raw_directory)
            attempt = root / "attempt"
            attempt.mkdir()
            (attempt / "demo.mvd").write_bytes(b"demo")
            (attempt / "demo.txt").write_text('{"duration": 600}\n', encoding="utf-8")
            skipped = {
                "schema": "mlx.analysis-skipped.v1",
                "status": "skipped-by-owner-directive",
                "consistency": {
                    "teamScores": {"mlx": 12, "frogs": 20},
                    "margin": 8,
                },
            }
            (attempt / "analysis.json").write_text(json.dumps(skipped), encoding="utf-8")
            (attempt / "match-result.json").write_text(
                json.dumps({"demoSha256": "a" * 64}), encoding="utf-8"
            )

            runner = mlx_run_job.JobRunner.__new__(mlx_run_job.JobRunner)
            runner.runner_run_id = "run"
            runner.job_dir = root / "job"
            runner.job_dir.mkdir()
            runner.spec = {
                "startDate": "2026-07-17",
                "cell": "baseline10",
                "outboxDir": str(root / "outbox"),
                "jobId": "20260717-baseline10-dm3-32",
                "rexCommit": "4aa9433",
                "analyzerCommit": "73bafd8",
                "mlxVersion": "baseline10-rev3",
                "serverConfig": {},
                "botSkill": 7,
                "hubDestination": "non-games/lab/MLX/dm3/",
                "analysisMode": "skipped-by-owner-directive",
                "matchTag": "dm3mlx-4aa9433-vs-fbot-skill20",
            }

            publication, sidecar_path = runner.publish(
                "match-0001", 1, attempt, skipped
            )

            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            self.assertEqual(sidecar["analysis"], "skipped-by-owner-directive")
            self.assertEqual(sidecar["matchTag"], "dm3mlx-4aa9433-vs-fbot-skill20")
            self.assertEqual(sidecar["matchResult"]["teams"], {"mlx": 12, "frogs": 20})
            self.assertTrue((publication / "ktxstats.json").is_file())
            self.assertFalse((publication / "metrics.json").exists())
            self.assertTrue((publication / ".ready").is_file())

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

    def test_prepare_skipped_analysis_does_not_invoke_full_analyzer(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            attempt = Path(raw_directory)
            skipped = {
                "schema": "mlx.analysis-skipped.v1",
                "status": "skipped-by-owner-directive",
                "consistency": {"teamScores": {"mlx": 1, "frogs": 2}, "margin": 1},
            }
            runner = mlx_run_job.JobRunner.__new__(mlx_run_job.JobRunner)
            runner.spec = {"analysisMode": "skipped-by-owner-directive"}
            with (
                mock.patch.object(
                    mlx_run_job, "build_skipped_analysis", return_value=skipped
                ) as build,
                mock.patch.object(runner, "analyze") as analyze,
                mock.patch.object(runner, "derive_metrics") as derive_metrics,
            ):
                result = runner.prepare_analysis(attempt)

            self.assertEqual(result, skipped)
            build.assert_called_once_with(attempt, runner.spec)
            analyze.assert_not_called()
            derive_metrics.assert_not_called()
            self.assertEqual(
                json.loads((attempt / "analysis.json").read_text(encoding="utf-8")),
                skipped,
            )
            self.assertFalse((attempt / "metrics.json").exists())

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
