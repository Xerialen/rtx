#!/usr/bin/env python3
"""parse_deploy_lock mot de delade fixturerna i testsuite/fixtures/riglock/.

Samma byte som motorns riglock_*-tester. Ingen rigg.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import test_lab_guard  # noqa: F401
import d_failclosed as fc
from d_deploy import (
    parse_deploy_lock,
    rig_lock_accepts,
    rig_lock_declared_token,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "riglock"
FIXTUR_TOKEN = "fixtur-kampanj-0000-0000"


def _body(name: str) -> str:
    return (FIXTURES / name).read_bytes().decode("utf-8")


class RiglockContractTests(unittest.TestCase):
    def test_fixtures_exist(self):
        for name in (
            "kampanj-atta-falt.lock",
            "kampanj-crlf.lock",
            "brygga-bar-forsta-rad.lock",
            "arv-enrad.lock",
            "motsagelsefull-tva-token.lock",
        ):
            self.assertTrue((FIXTURES / name).is_file(), name)

    def test_kampanj_atta_falt_parses_declared_token(self):
        fields = parse_deploy_lock(FIXTURES / "kampanj-atta-falt.lock")
        self.assertEqual(fields["token"], FIXTUR_TOKEN)
        self.assertEqual(fields["owner"], "fable")
        self.assertEqual(fields["unit"], "tbx-d1")
        self.assertEqual(fields["ctl_port"], "27996")
        self.assertEqual(fields["game_port"], "27592")
        body = _body("kampanj-atta-falt.lock")
        self.assertEqual(rig_lock_declared_token(body), FIXTUR_TOKEN)
        self.assertTrue(rig_lock_accepts(body, FIXTUR_TOKEN))
        self.assertFalse(rig_lock_accepts(body, "fable"))
        self.assertFalse(rig_lock_accepts(body, "owner=fable"))

    def test_kampanj_crlf_is_crlf_bytes_and_same_token(self):
        raw = (FIXTURES / "kampanj-crlf.lock").read_bytes()
        self.assertIn(b"\r\n", raw, "fixturen måste vara CRLF-bytes (git-normalisering?)")
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""), "inga bara LF i crlf-fixturen")
        fields = parse_deploy_lock(FIXTURES / "kampanj-crlf.lock")
        self.assertEqual(fields["token"], FIXTUR_TOKEN)
        self.assertEqual(fields["owner"], "fable")
        self.assertEqual(fields["unit"], "tbx-d1")
        lf = parse_deploy_lock(FIXTURES / "kampanj-atta-falt.lock")
        self.assertEqual(fields, lf)
        body = _body("kampanj-crlf.lock")
        self.assertIn("\r\n", body)
        self.assertEqual(rig_lock_declared_token(body), FIXTUR_TOKEN)
        self.assertTrue(rig_lock_accepts(body, FIXTUR_TOKEN))
        self.assertFalse(rig_lock_accepts(body, "fable"))
        self.assertFalse(rig_lock_accepts(body, "owner=fable"))

    def test_lone_cr_is_not_a_line_break(self):
        # Rust str::lines() keeps lone CR. splitlines() would split and diverge.
        self.assertEqual(rig_lock_declared_token("token=a\rb"), "a\rb")
        self.assertEqual(rig_lock_declared_token("token=a\rtoken=b"), "a\rtoken=b")
        self.assertTrue(rig_lock_accepts("token=a\rb", "a\rb"))
        self.assertFalse(rig_lock_accepts("token=a\rb", "a"))
        self.assertTrue(rig_lock_accepts("token=a\rtoken=b", "a\rtoken=b"))
        self.assertFalse(rig_lock_accepts("token=a\rtoken=b", "a"))
        self.assertFalse(rig_lock_accepts("token=a\rtoken=b", "b"))

    def test_trailing_whitespace_matches_rust_trim(self):
        self.assertEqual(rig_lock_declared_token("token=abc  \r\nowner=fable\r\n"), "abc")
        self.assertTrue(rig_lock_accepts("token=abc\r\n", "abc"))
        self.assertTrue(rig_lock_accepts("token=abc\r\n", "abc  "))
        self.assertEqual(rig_lock_declared_token("  token=abc\n"), "abc")

    def test_brygga_skips_bare_first_line(self):
        fields = parse_deploy_lock(FIXTURES / "brygga-bar-forsta-rad.lock")
        self.assertEqual(fields["token"], FIXTUR_TOKEN)
        self.assertEqual(fields["owner"], "fable")
        body = _body("brygga-bar-forsta-rad.lock")
        self.assertTrue(body.splitlines()[0] == FIXTUR_TOKEN)
        self.assertEqual(rig_lock_declared_token(body), FIXTUR_TOKEN)
        self.assertTrue(rig_lock_accepts(body, FIXTUR_TOKEN))

    def test_arv_enrad_has_no_campaign_fields(self):
        fields = parse_deploy_lock(FIXTURES / "arv-enrad.lock")
        self.assertNotIn("token", fields)
        self.assertNotIn("owner", fields)
        body = _body("arv-enrad.lock")
        self.assertIsNone(rig_lock_declared_token(body))
        self.assertTrue(rig_lock_accepts(body, "fable 1"))
        self.assertTrue(rig_lock_accepts(body, "fable"))
        self.assertFalse(rig_lock_accepts(body, "1"))

    def test_motsagelsefull_refused_entirely(self):
        with self.assertRaises(fc.FailClosed) as cm:
            parse_deploy_lock(FIXTURES / "motsagelsefull-tva-token.lock")
        self.assertEqual(cm.exception.gate, "lock")
        self.assertIn("motsägelsefull", str(cm.exception))
        body = _body("motsagelsefull-tva-token.lock")
        self.assertIsNone(rig_lock_declared_token(body))
        self.assertFalse(rig_lock_accepts(body, FIXTUR_TOKEN))
        self.assertFalse(rig_lock_accepts(body, "nagot-annat-0000"))
        self.assertFalse(rig_lock_accepts(body, "fable"))

    def test_empty_token_declaration_opens_nothing(self):
        self.assertIsNone(rig_lock_declared_token("owner=fable\ntoken=\n"))
        self.assertFalse(rig_lock_accepts("owner=fable\ntoken=", "owner=fable"))
        self.assertFalse(rig_lock_accepts("owner=fable\ntoken=", "fable"))


if __name__ == "__main__":
    unittest.main()
