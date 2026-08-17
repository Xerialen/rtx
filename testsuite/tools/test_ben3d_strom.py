"""Hermetiskt syntetiskt broadcasttest (A6d) + D3-terminalgrind-regression."""

from __future__ import annotations
import json, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "ben3d"))
import ben3d_strom as S  # noqa: E402
import navviewer_strom_reader as N  # noqa: E402


def _payload(i):
    return {"t": f"{i}.0", "x": f"{i*10}.0", "y": "0.0", "z": "0.0", "cell": i}


class StromTests(unittest.TestCase):
    def _header_ticks(self, sid="syn-1", n=5):
        header = S.make_header(sid, {"dataset": "t1h", "arm": "fork"})
        ticks = [S.make_tick(sid, "t1h:fork:c001:in_vast", str(i), i + 1, _payload(i)) for i in range(n)]
        return header, ticks

    def _stream(self, td: Path, n=5):
        header, ticks = self._header_ticks(n=n)
        rot = S.stream_rot(header["proveniens"], [t["payload"] for t in ticks])
        lines = [header] + ticks + [S.make_end("syn-1", n + 1, n, rot)]
        p = td / "stream.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in lines) + "\n")
        return p, rot

    def test_broadcast_tva_olika_lasare(self):
        td = Path(tempfile.mkdtemp(prefix="ben3d-strom-"))
        p, rot = self._stream(td)
        cli = subprocess.run([sys.executable, str(HERE / "ben3d" / "ben3d_strom.py"), "tail", str(p)],
                             capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertIn("FRUSEN", cli.stdout)
        nv = N.consume(str(p))
        self.assertEqual(nv["status"], "FRUSEN")
        self.assertEqual(nv["slutrot"], rot)
        self.assertEqual(len(nv["ticks"]), 5)

    def test_abort_ar_permanent_terminal_i_bada_lasarna(self):
        td = Path(tempfile.mkdtemp(prefix="ben3d-strom-"))
        header, ticks = self._header_ticks(n=2)
        rot = S.stream_rot(header["proveniens"], [t["payload"] for t in ticks])
        # header(0) -> abort(1) -> end(1, antal=0, korrekt rot för 2 ticks) — end får ALDRIG frysa
        lines = [header, S.make_abort("syn-1", 1, "krasch"),
                 S.make_end("syn-1", 1, 0, S.stream_rot(header["proveniens"], []))]
        p = td / "abort-end.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in lines) + "\n")
        # CLI-läsaren
        st = S.Stream(str(p)).read()
        self.assertNotEqual(st.status, "FRUSEN")
        self.assertTrue(any("efter abort" in e for e in st.errors))
        # navviewer-läsaren
        nv = N.consume(str(p))
        self.assertNotEqual(nv["status"], "FRUSEN")
        self.assertTrue(any("efter abort" in e for e in nv["fel"]))

    def test_slutrot_maste_matcha_omraknad_rot(self):
        td = Path(tempfile.mkdtemp(prefix="ben3d-strom-"))
        header, ticks = self._header_ticks(n=2)
        # slutrot "a"*64 (regex-giltig men INTE omräknad rot) ska vägras
        lines = [header] + ticks + [S.make_end("syn-1", 3, 2, "a" * 64)]
        p = td / "badrot.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in lines) + "\n")
        self.assertNotEqual(N.consume(str(p))["status"], "FRUSEN")
        st = S.Stream(str(p)).read()
        self.assertNotEqual(st.status, "FRUSEN")

    def test_gap_ger_exit2(self):
        td = Path(tempfile.mkdtemp(prefix="ben3d-strom-"))
        p, _ = self._stream(td, n=3)
        body = [l for l in p.read_text().splitlines() if json.loads(l).get("typ") != "tick" or json.loads(l)["seq"] != 2]
        p.write_text("\n".join(body) + "\n")
        cli = subprocess.run([sys.executable, str(HERE / "ben3d" / "ben3d_strom.py"), "tail", str(p)],
                             capture_output=True, text=True)
        self.assertNotEqual(cli.returncode, 0)

    def test_payload_sha_valideras(self):
        rec = S.make_tick("s", "b", "0", 1, {"x": "1.0"})
        rec["payload_sha256"] = "0" * 64
        with self.assertRaises(S.StromError):
            S.validate_record(rec)


if __name__ == "__main__":
    unittest.main()
