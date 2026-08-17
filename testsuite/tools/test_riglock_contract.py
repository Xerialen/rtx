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
    return (FIXTURES / name).read_text(encoding="utf-8")


class RiglockContractTests(unittest.TestCase):
    def test_fixtures_exist(self):
        for name in (
            "kampanj-atta-falt.lock",
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
