#!/usr/bin/env python3
"""timtest_d — enarmad T1h-mätare mot tbx-d (väg a, DOM M1-EFTER).

Originalen timtest_orkester.py / timtest_ben.py rörs inte. Benlogik och
klipp importeras oförändrade ur den frysta kopian av timtest_ben.py +
granskriterier.py.

Skillnad mot orkestern (ärligt):
  * --host/--port/--game-port, EN arm, ingen A/B-tabell
  * --duration N (minuter, default 60; T20m = --duration 20). --minuter är alias.
    --dry/--mock tvingar en cykel oavsett fönster.
  * ingen systemctl-restart, ingen replant, ingen taskset, ingen RA-riglock
  * portvakt fail-closed rc=2
  * demo via ctl RunCmd sv_demorecord / sv_demostop
  * kluster.json (fall/stall per cell) efter körning
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, os.path.expanduser("~/rtx-tools"))

from timtest_d_ports import EXIT_REFUSED, port_fel  # noqa: E402
from timtest_d_kluster import skriv_kluster  # noqa: E402

# Importera BEN-API:t ur den frysta kopian — ingen omskrivning av loopen.
import timtest_ben as ben  # noqa: E402


DEMO_CMD_RECORD = "sv_demorecord"
DEMO_CMD_STOP = "sv_demostop"


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def in_predikat_hash() -> str:
    src = (HERE / "timtest_ben.py").read_text(encoding="utf-8")
    lo = src.index("IN_TOPP_CENTRUM = ")
    hi = src.index("def fall_peak_drop_150")
    return hashlib.sha256(src[lo:hi].encode()).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


class FakeLab:
    """Socketlös Lab-dubbel för --mock. Ingen nätverkskontakt."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.origin = list(ben.TOPP)
        self.goal = None
        self.cmds: list = []

    def set(self, name, value):
        self.cmds.append(("set", name, value))
        return {}

    def teleport(self, bot, pos, vel=(0.0, 0.0, 0.0)):
        self.origin = [float(x) for x in pos]
        self.cmds.append(("teleport", pos))
        return {}

    def goto(self, bot, pos):
        self.goal = [float(x) for x in pos]
        self.cmds.append(("goto", pos))
        return {}

    def stop(self, bot):
        self.goal = None
        self.cmds.append(("stop",))
        return {}

    def request(self, cmd, timeout=8.0):
        self.cmds.append(("request", cmd))
        if cmd == "Status":
            # Hoppa mot mål och räkna game-tid i hela sekunder så cap=25
            # tar ~25 anrop. Kort sleep så originalets 0,3 s-svans inte
            # spolar hundra tusen rader (benlogiken är orörd).
            if self.goal is not None:
                self.origin = list(self.goal)
            self.t += 1.0
            time.sleep(0.012)
            return {
                "Status": {
                    "time": self.t,
                    "map": "dm3",
                    "cells": 0,
                    "links": 0,
                    "bots": [{
                        "ent": ben.BOT,
                        "alive": True,
                        "origin": list(self.origin),
                        "on_ground": True,
                    }],
                }
            }
        if isinstance(cmd, dict) and "RunCmd" in cmd:
            return {"Queued": True}
        if isinstance(cmd, dict) and "Cell" in cmd:
            return {"Cell": {"cell": None}}
        if isinstance(cmd, dict) and "Set" in cmd:
            return {}
        raise RuntimeError("FakeLab: okänt cmd %r" % (cmd,))

    def cell(self, pos):
        return {"Cell": {"cell": None}}

    def close(self):
        return None


def start_demo(lab, stem: str) -> str:
    lab.set("sv_demoDir", "demos")
    lab.request({"RunCmd": {"raw": "%s %s" % (DEMO_CMD_RECORD, stem)}})
    return "qw/demos/%s.mvd" % stem


def stop_demo(lab) -> None:
    try:
        lab.request({"RunCmd": {"raw": DEMO_CMD_STOP}})
    except Exception:
        pass


def skriv_manifest(outdir: Path, *, host, port, game_port, dry, mock,
                   duration, minuter, demo_file):
    man = {
        "schema": "t1h-d-manifest-v1",
        "arm": "D",
        "host": host,
        "port": {"kontroll": port, "spel": game_port},
        "start_utc": _utc(),
        "duration": duration,
        "minuter": minuter,
        "torrkorning": dry,
        "mock": mock,
        "ingen_systemctl": True,
        "ingen_replant": True,
        "ingen_ab_tabell": True,
        "regim": "kedjad (samma CYKEL/BEN som timtest_ben.py)",
        "IN_predikat_kodhash": in_predikat_hash(),
        "demo_file": demo_file,
        "python": platform.python_version(),
        "verktyg": {
            "granskriterier": {
                "path": str(HERE / "granskriterier.py"),
                "sha256": sha256_file(HERE / "granskriterier.py"),
            },
            "timtest_ben": {
                "path": str(HERE / "timtest_ben.py"),
                "sha256": sha256_file(HERE / "timtest_ben.py"),
            },
            "timtest_d": {
                "path": str(Path(__file__).resolve()),
            },
        },
    }
    (outdir / "manifest.json").write_text(
        json.dumps(man, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return man


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True, help="ctl-port (27996–27999)")
    ap.add_argument("--game-port", type=int, required=True, dest="game_port",
                    help="spelport (27592–27595)")
    ap.add_argument("--out", required=True, help="utdatakatalog")
    ap.add_argument(
        "--duration", "--minuter", type=float, default=60.0, dest="duration",
        help="tidsfönster i minuter (default 60). T20m = --duration 20. "
             "--minuter är alias. --dry/--mock tvingar en cykel.",
    )
    ap.add_argument("--dry", action="store_true", help="en cykel (samma som originalet)")
    ap.add_argument("--mock", action="store_true",
                    help="ingen socket; FakeLab. Bara tester.")
    ap.add_argument("--no-demo", action="store_true")
    ap.add_argument("--demo-stem", default=None)
    args = ap.parse_args(argv)

    fel = port_fel(args.port, args.game_port)
    if fel:
        sys.stderr.write("VÄGRAR: %s\n" % fel)
        return EXIT_REFUSED
    if args.duration <= 0:
        sys.stderr.write("VÄGRAR: --duration måste vara > 0 (fick %s)\n" % args.duration)
        return EXIT_REFUSED

    outdir = Path(os.path.expanduser(args.out)).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    demo_file = None
    lab = None
    try:
        if args.mock:
            lab = FakeLab()
        else:
            from labctl import Lab
            lab = Lab(host=args.host, port=int(args.port))
        lab.set("rtx_telemetry", "1")
        if not args.no_demo and not args.mock:
            stem = args.demo_stem or ("t1hd_%s" % time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
            demo_file = start_demo(lab, stem)
        effektiv = 1 if (args.dry or args.mock) else args.duration
        skriv_manifest(
            outdir, host=args.host, port=args.port, game_port=args.game_port,
            dry=args.dry, mock=args.mock,
            duration=args.duration,
            minuter=effektiv,
            demo_file=demo_file,
        )
        lab.teleport(ben.BOT, ben.TOPP)
        time.sleep(0.0 if args.mock else 0.6)
        n = ben.koda_arm(
            lab, "D", str(outdir),
            effektiv,
            dry=bool(args.dry or args.mock),
        )
        skriv_kluster(outdir)
        print("timtest_d klar: %d cykler out=%s demo=%s" % (n, outdir, demo_file),
              flush=True)
        return 0
    finally:
        if lab is not None:
            if not args.no_demo and not args.mock:
                stop_demo(lab)
            try:
                lab.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
