#!/usr/bin/env python3
"""Låsfilsgenerator. No rig. Token always caller-supplied."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import test_lab_guard  # noqa: F401
from d_deploy import parse_deploy_lock, rig_lock_accepts, rig_lock_declared_token
import lasfil


TOKEN = "fixtur-kampanj-0000-0000"
QW = "00" * 32
MV = "11" * 32


class LasfilTests(unittest.TestCase):
    def test_token_is_authoritative_and_verbatim(self):
        body = lasfil.generate_lock(
            token=TOKEN, unit="tbx-d1", qwprogs_sha256=QW, mvdsv_sha256=MV,
            ts="2026-08-17T12:00:00Z",
        )
        self.assertIn(f"token={TOKEN}\n", body)
        self.assertFalse(body.startswith(TOKEN))  # campaign form, not invented bare line
        self.assertEqual(rig_lock_declared_token(body), TOKEN)
        self.assertTrue(rig_lock_accepts(body, TOKEN))
        self.assertFalse(rig_lock_accepts(body, "fable"))

    def test_refuses_to_author_a_token(self):
        with self.assertRaises(ValueError) as cm:
            lasfil.generate_lock(
                token="", unit="tbx-d1", qwprogs_sha256=QW, mvdsv_sha256=MV,
            )
        self.assertIn("författar aldrig", str(cm.exception))
        with self.assertRaises(SystemExit):
            lasfil.main(["--unit", "tbx-d1", "--qwprogs-sha256", QW, "--mvdsv-sha256", MV])

    def test_parse_deploy_lock_reads_generated_bytes(self):
        td = tempfile.TemporaryDirectory()
        p = Path(td.name) / "lock"
        body = lasfil.generate_lock(
            token=TOKEN, unit="tbx-d3", qwprogs_sha256=QW, mvdsv_sha256=MV,
            ts="2026-08-17T12:00:00Z",
        )
        p.write_text(body, encoding="utf-8")
        fields = parse_deploy_lock(p)
        self.assertEqual(fields["token"], TOKEN)
        self.assertEqual(fields["owner"], "fable")
        self.assertEqual(fields["unit"], "tbx-d3")
        self.assertEqual(fields["ctl_port"], "27998")
        td.cleanup()

    def test_bridge_form_still_uses_caller_token(self):
        body = lasfil.generate_lock(
            token=TOKEN, unit="tbx-d1", qwprogs_sha256=QW, mvdsv_sha256=MV,
            ts="2026-08-17T12:00:00Z", bridge=True,
        )
        self.assertTrue(body.startswith(TOKEN + "\n"))
        self.assertEqual(body.count(f"token={TOKEN}"), 1)
        self.assertEqual(rig_lock_declared_token(body), TOKEN)

    def test_cli_writes_out(self):
        td = tempfile.TemporaryDirectory()
        p = Path(td.name) / "l.lock"
        rc = lasfil.main([
            "--token", TOKEN, "--unit", "tbx-d1",
            "--qwprogs-sha256", QW, "--mvdsv-sha256", MV,
            "--ts", "2026-08-17T12:00:00Z", "--out", str(p),
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(rig_lock_declared_token(p.read_text(encoding="utf-8")), TOKEN)
        td.cleanup()

    def test_refuses_newline_in_every_field(self):
        base = dict(token=TOKEN, unit="tbx-d1", qwprogs_sha256=QW, mvdsv_sha256=MV,
                    ts="2026-08-17T12:00:00Z")
        cases = (
            ("token", "tok\nen"),
            ("token", "tok\ren"),
            ("owner", "fable\ninjected"),
            ("owner", "fable\rinjected"),
            ("ts", "2026-08-17T12:00:00Z\nowner=evil"),
            ("ts", "2026-08-17T12:00:00Z\rowner=evil"),
            ("qwprogs_sha256", QW[:32] + "\n" + QW[32:]),
            ("mvdsv_sha256", MV[:32] + "\r" + MV[32:]),
        )
        for field, value in cases:
            with self.subTest(field=field, nl=repr(value)):
                kw = dict(base)
                kw[field] = value
                with self.assertRaises(ValueError) as cm:
                    lasfil.generate_lock(**kw)
                self.assertIn("radbrytning", str(cm.exception))
                self.assertIn(field, str(cm.exception))

    def test_cli_refuses_newline_owner_and_ts(self):
        rc_owner = lasfil.main([
            "--token", TOKEN, "--unit", "tbx-d1",
            "--qwprogs-sha256", QW, "--mvdsv-sha256", MV,
            "--owner", "fable\ninjected",
        ])
        self.assertEqual(rc_owner, 2)
        rc_ts = lasfil.main([
            "--token", TOKEN, "--unit", "tbx-d1",
            "--qwprogs-sha256", QW, "--mvdsv-sha256", MV,
            "--ts", "2026-08-17T12:00:00Z\rowner=x",
        ])
        self.assertEqual(rc_ts, 2)


if __name__ == "__main__":
    unittest.main()

