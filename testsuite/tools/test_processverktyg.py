#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
PREFLIGHT = TOOLS / "preflight_malbarhet.py"
JAV = TOOLS / "review_jav.sh"


class PreflightMalbarhetTests(unittest.TestCase):
    def run_preflight(self, text: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp) / "measure.py"
            tool.write_text(text, encoding="utf-8")
            return subprocess.run(
                [str(PREFLIGHT), str(tool)], text=True, capture_output=True, check=False
            )

    def test_allowed_tbx_targets_pass_and_are_located(self) -> None:
        result = self.run_preflight(
            'HOST = "127.0.0.1"\ncontrol_port = 27999\ngame_port = 27595\n'
            'UNIT = "tbx-d4.service"\n'
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("measure.py:2: port=27999 [ALLOWED]", result.stdout)
        self.assertIn("outside_tbx=0", result.stdout)

    def test_forbidden_rig_port_and_unit_fail(self) -> None:
        result = self.run_preflight(
            'host = "fasttrack-ra"\n"port": 27990\n"spelport": 27540\n'
            'systemctl --user restart fasttrack-main-test\n'
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("port=27990 [OUTSIDE-TBX]", result.stdout)
        self.assertIn("port=27540 [OUTSIDE-TBX]", result.stdout)
        self.assertIn("systemd-unit=fasttrack-main-test [OUTSIDE-TBX]", result.stdout)
        self.assertRegex(result.stdout, r"outside_tbx=[1-9]")


class ReviewJavTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.env = os.environ.copy()
        self.env.update(
            {
                "GIT_AUTHOR_NAME": "Shared Git Identity",
                "GIT_AUTHOR_EMAIL": "shared@example.invalid",
                "GIT_COMMITTER_NAME": "Shared Git Identity",
                "GIT_COMMITTER_EMAIL": "shared@example.invalid",
            }
        )
        self.sha = self.commit("with trailer", "Agent: terra")
        self.no_trailer_sha = self.commit("without trailer")
        self.unknown_agent_sha = self.commit("unknown trailer", "Agent: outsider")

    def commit(self, text: str, trailer: str | None = None) -> str:
        evidence = self.repo / "evidence.txt"
        with evidence.open("a", encoding="utf-8") as out:
            out.write(text + "\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "evidence.txt"], check=True)
        cmd = ["git", "-C", str(self.repo), "commit", "-q", "-m", text]
        if trailer is not None:
            cmd += ["-m", trailer]
        subprocess.run(cmd, check=True, env=self.env)
        return subprocess.check_output(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True
        ).strip()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_jav(self, reviewer: str, sha: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(JAV), sha or self.sha, reviewer], cwd=self.repo, text=True,
            capture_output=True, check=False,
        )

    def test_rejects_trailer_agent_despite_shared_git_identity(self) -> None:
        result = self.run_jav("Terra")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("JÄV:", result.stderr)
        self.assertIn("Agent: terra", result.stderr)
        self.assertIn("välj annan granskare", result.stderr)

    def test_accepts_independent_reviewer(self) -> None:
        result = self.run_jav("grok")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Agent='terra'", result.stdout)

    def test_rejects_commit_without_agent_trailer(self) -> None:
        result = self.run_jav("grok", self.no_trailer_sha)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("okänd författare — kräv Agent:-trailer", result.stderr)

    def test_rejects_unknown_agent_trailer(self) -> None:
        result = self.run_jav("grok", self.unknown_agent_sha)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("okänd Agent:", result.stderr)


if __name__ == "__main__":
    unittest.main()
