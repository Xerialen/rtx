#!/usr/bin/env python3
"""WIP-lint. Fixture tables only — no rig."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import test_lab_guard  # noqa: F401
import wip_lint as wl

OK = """# Kanban
Format: `| säte | punkt | status |` — status ∈ PÅGÅR / KÖ / KLAR / GRANSKAS.
Tak: WIP ≤2/säte (PÅGÅR), ködjup ≤2 (KÖ).

| säte | punkt | status |
|---|---|---|
| grok-sätet | U2 | PÅGÅR |
| grok-sätet | U3 | KÖ |
| opus5 | U5 | PÅGÅR |
| deepseek | motgranskning U2 | KÖ |
"""

HOT = """| säte | punkt | status |
|---|---|---|
| grok-sätet | a | PÅGÅR |
| grok-sätet | b | PÅGÅR |
| grok-sätet | c | PÅGÅR |
| grok-sätet | d | KÖ |
| grok-sätet | e | KÖ |
| grok-sätet | f | KÖ |
| Fable | U4 | PÅGÅR |
"""


class WipLintTests(unittest.TestCase):
    def test_ok_table_no_alarm(self):
        doc = wl.lint(wl.parse_kanban(OK))
        self.assertTrue(doc["ok"])
        self.assertEqual(doc["alarms"], [])
        self.assertEqual(doc["wip"]["grok-sätet"], 1)

    def test_wip_and_queue_alarms(self):
        doc = wl.lint(wl.parse_kanban(HOT))
        self.assertFalse(doc["ok"])
        kinds = {(a["kind"], a["sate"], a["n"]) for a in doc["alarms"]}
        self.assertIn(("WIP", "grok-sätet", 3), kinds)
        self.assertIn(("KÖ", "grok-sätet", 3), kinds)
        self.assertFalse(any(a["sate"] == "Fable" for a in doc["alarms"]))

    def test_cli_never_blocks(self):
        td = tempfile.TemporaryDirectory()
        p = Path(td.name) / "K.md"
        p.write_text(HOT, encoding="utf-8")
        self.assertEqual(wl.main(["--kanban", str(p)]), 0)
        p.write_text(OK, encoding="utf-8")
        self.assertEqual(wl.main(["--kanban", str(p)]), 0)
        td.cleanup()


if __name__ == "__main__":
    unittest.main()
