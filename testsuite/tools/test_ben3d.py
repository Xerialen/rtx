"""Fixturtester för ben3d-extraktorn (etapp 2a: h-index). Hermetisk tmp, ingen
socket, ingen ~/lab-åtkomst. Kör den byggda binären mot syntetiska manifest."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = HERE.parent.parent / "target" / "debug" / "ben3d"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _meta(utfall: str) -> bytes:
    return json.dumps({"utfall": utfall, "ben": "in_vast", "cykel": 1}).encode()


_MANIFEST_COUNTER = [0]


def _skriv_manifest(td: Path, medlemmar: list[tuple[str, bytes]]) -> tuple[Path, str]:
    """Skriv filer + sha256sum-manifest. Returnerar (manifest-path, manifest-sha)."""
    rader = []
    for rel, content in medlemmar:
        p = td / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        rader.append(f"{_sha(content)}  {rel}\n")
    man = td / ("m-%d.sha256" % _MANIFEST_COUNTER[0])
    _MANIFEST_COUNTER[0] += 1
    man.write_text("".join(rader), encoding="utf-8")
    return man, _sha(man.read_bytes())


class HIndexTests(unittest.TestCase):
    def _run(self, t1h: Path, t20m: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(BIN), "h-index", str(t1h), str(t20m)],
            capture_output=True, text=True,
        )

    def test_97_rakning_och_stopp(self):
        if not BIN.is_file():
            self.skipTest("ben3d-binär ej byggd (cargo build -p ben3d)")
        td = Path(tempfile.mkdtemp(prefix="ben3d-"))
        # t1h: 1 fork-H (fall) + 1 main-H (fall_efter_framme) + 1 framme (ej H)
        #       + 1 ogiltig_tic (nämnare-exkl) + 1 kasserad (nämnare-exkl)
        t1h, t1h_sha = _skriv_manifest(td, [
            ("t1h-d1-on/c001/in_vast_meta.json", _meta("fall")),
            ("t1h-main-ref/c001/in_vast_meta.json", _meta("fall_efter_framme")),
            ("t1h-d1-on/c001/in_tunnel_meta.json", _meta("framme")),
            ("t1h-d1-on/c001/ut_vast_meta.json", _meta("ogiltig_tic")),
            ("t1h-d1-on/c001/ut_ring_meta.json", _meta("kasserad")),
        ])
        t20m, t20m_sha = _skriv_manifest(td, [
            ("t20m-d1-on/c001/in_vast_meta.json", _meta("fall_plus_fastnad")),
            ("t20m-main-ref/c001/in_vast_meta.json", _meta("fastnad")),
        ])
        r = self._run(t1h, t20m)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["n_h"], 4)
        self.assertEqual(doc["konton"][f"{Path(t1h).name}:fork"], 1)
        self.assertEqual(doc["konton"][f"{Path(t1h).name}:main"], 1)
        self.assertEqual(doc["konton"][f"{Path(t20m).name}:fork"], 1)
        self.assertEqual(doc["konton"][f"{Path(t20m).name}:main"], 1)

    def test_sha_miss_stoppar(self):
        if not BIN.is_file():
            self.skipTest("ben3d-binär ej byggd")
        td = Path(tempfile.mkdtemp(prefix="ben3d-"))
        t1h, _ = _skriv_manifest(td, [("t1h-d1-on/c001/in_vast_meta.json", _meta("fall"))])
        t20m, _ = _skriv_manifest(td, [])
        # korrupta manifestet: byt ut en sha
        body = t1h.read_text()
        body = body.replace(_sha(_meta("fall")), "0" * 64)
        t1h.write_text(body)
        r = self._run(t1h, t20m)
        self.assertEqual(r.returncode, 2)
        self.assertIn("SHA-miss", r.stderr)

    def test_okant_utfall_stoppar(self):
        if not BIN.is_file():
            self.skipTest("ben3d-binär ej byggd")
        td = Path(tempfile.mkdtemp(prefix="ben3d-"))
        t1h, _ = _skriv_manifest(td, [("t1h-d1-on/c001/in_vast_meta.json", _meta("OVÄNTAT"))])
        t20m, _ = _skriv_manifest(td, [])
        r = self._run(t1h, t20m)
        self.assertEqual(r.returncode, 2)
        self.assertIn("okänt utfall", r.stderr)

    def test_saknad_fil_stoppar(self):
        if not BIN.is_file():
            self.skipTest("ben3d-binär ej byggd")
        td = Path(tempfile.mkdtemp(prefix="ben3d-"))
        t1h, _ = _skriv_manifest(td, [("t1h-d1-on/c001/in_vast_meta.json", _meta("fall"))])
        # ta bort filen
        (td / "t1h-d1-on/c001/in_vast_meta.json").unlink()
        t20m, _ = _skriv_manifest(td, [])
        r = self._run(t1h, t20m)
        self.assertEqual(r.returncode, 2)
        self.assertIn("saknas", r.stderr)


if __name__ == "__main__":
    unittest.main()
