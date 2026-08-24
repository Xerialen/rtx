#!/usr/bin/env python3
"""Reser riggens unit och bevisar att servern faktiskt lever.

Två fel som skarpvalideringen 2026-08-23 fällde, och som båda bor här:

1. **Fel basedir.** mvdsv löser `id1/` och `-game <namn>` mot sin
   ARBETSKATALOG. Kör man med `--working-directory=<gamediren>` finns
   varken `id1/` eller `<cwd>/<namn>`, och servern dör direkt med
   `SV_Error: Couldn't spawn a server`. Arbetskatalogen ska vara
   basedir — katalogen som innehåller `mvdsv` och `id1/` — och
   gamediren namnges med `-game`. Samma form som de fungerande
   mätdrivrarna använder.

2. **Falsk «klar».** `systemd-run` återvänder när uniten är *startad*,
   inte när servern *lever*. Utan en livsgrind rapporterade res.sh
   «riggen klar» rc=0 med noll lyssnare och en failad unit. En rigg som
   inte svarar är ingen rigg.

Livsgrinden väntar på MainPID i `/proc` och på en lyssnare på spelporten.
Aldrig `pgrep -f`: mönstret matchar sitt eget kommando i en ssh-kedja och
låser loopen med ett falskt positivt.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from riggvakt import Vaktfel, lyssnare  # noqa: E402

#: Unit-tillstånd som betyder att servern aldrig kom upp. Att vänta vidare
#: på ett av dem är att vänta på något som redan hänt.
DODA_TILLSTAND = frozenset({"failed", "inactive"})


def start_argv(
    unit: str, mvdsv: Path, gamedir: Path, spel: int, runtime_max_s: int
) -> list[str]:
    """Bygger startkommandot, och vägrar bygga ett som inte kan fungera."""
    # Katalogen loses upp, men INTE mvdsv-filen sjalv: `mvdsv` ar normalt en
    # symlank till en versionsstamplad binar, och foljer man den ut ur
    # katalogen blir basedir fel. Arbetskatalogen ar den katalog sokvagen
    # pekar in i — samma katalog `-game <namn>` sedan loses mot.
    mvdsv = Path(mvdsv).absolute()
    gamedir = Path(gamedir).absolute()
    basedir = mvdsv.parent.resolve()

    if gamedir.parent.resolve() != basedir:
        raise Vaktfel(
            "gamediren %s ligger inte direkt under mvdsv:s basedir %s — "
            "`-game <namn>` löses mot basedir, så servern hade inte hittat "
            "den" % (gamedir, basedir)
        )
    if not (basedir / "id1").is_dir():
        raise Vaktfel(
            "basedir %s saknar id1/ — mvdsv hittar varken pak eller "
            "maps/start.bsp och dör med «Couldn't spawn a server»" % basedir
        )

    return [
        "systemd-run",
        "--user",
        "--unit=%s" % unit,
        # Arbetskatalogen ÄR basedir. Inte gamediren. Se modulens huvud.
        "--working-directory=%s" % basedir,
        "-p",
        "RuntimeMaxSec=%d" % runtime_max_s,
        str(mvdsv),
        "-port",
        str(spel),
        "-game",
        gamedir.name,
    ]


def _visa(unit: str, egenskap: str) -> str:
    r = subprocess.run(
        ["systemctl", "--user", "show", "-p", egenskap, "--value", unit],
        capture_output=True,
        text=True,
    )
    return r.stdout.strip()


def _journal(unit: str, rader: int = 25) -> str:
    r = subprocess.run(
        ["journalctl", "--user", "-u", unit, "--no-pager", "-n", str(rader)],
        capture_output=True,
        text=True,
    )
    return r.stdout.strip()


def vanta_liv(
    unit: str,
    spel: int,
    timeout_s: int,
    *,
    las_tillstand=_visa,
    las_lyssnare=lyssnare,
    sov=time.sleep,
    nu=time.monotonic,
) -> list[str]:
    """Vägrar tills servern bevisligen lever, eller tiden går ut.

    Proberna är injicerbara så att grinden går att negativkontrollera
    utan att resa en rigg.
    """
    slut = nu() + timeout_s
    sist = "ingen avläsning hann göras"
    while True:
        tillstand = las_tillstand(unit, "ActiveState")
        if tillstand in DODA_TILLSTAND:
            raise Vaktfel(
                "unit %s är %s — servern kom aldrig upp. Journal:\n%s"
                % (unit, tillstand, _journal(unit))
            )
        pid_text = las_tillstand(unit, "MainPID")
        pid = int(pid_text) if pid_text.isdigit() else 0
        if pid <= 0:
            sist = "unit %s har ingen MainPID (tillstånd=%s)" % (unit, tillstand)
        elif not Path("/proc/%d" % pid).is_dir():
            sist = "MainPID %d finns inte i /proc — processen dog" % pid
        else:
            n = las_lyssnare(spel)
            if n:
                return [
                    "unit %s aktiv, MainPID %d lever" % (unit, pid),
                    "port %d svarar: %d lyssnare" % (spel, n),
                ]
            sist = "MainPID %d lever men port %d har noll lyssnare" % (pid, spel)

        if nu() >= slut:
            raise Vaktfel(
                "riggen kom inte upp inom %d s: %s. Journal:\n%s"
                % (timeout_s, sist, _journal(unit))
            )
        sov(1)


def stada(unit: str, gamedir: Path | None, mvdsv: Path | None) -> list[str]:
    """Städar efter en rigg som inte kom upp.

    `reset-failed` ingår: en failad transient unit med samma namn vägras
    av nästa `systemd-run`, så utan den blockerar misslyckandet nästa
    försök.
    """
    gjort = []
    for verb in ("stop", "reset-failed"):
        subprocess.run(
            ["systemctl", "--user", verb, unit], capture_output=True, text=True
        )
        gjort.append("systemctl --user %s %s" % (verb, unit))

    if gamedir is None:
        return gjort
    gamedir = Path(gamedir).absolute()
    # Ta bara bort det vi sjalva byggde. En gamedir utan vart genererade
    # marker-cfg ar inte var att radera.
    if mvdsv is not None and gamedir.parent.resolve() != Path(mvdsv).absolute().parent.resolve():
        gjort.append("lämnade %s (inte under basedir)" % gamedir)
        return gjort
    marker = list(gamedir.glob("configs/usermodes/*/default.cfg"))
    if not marker or not any(
        "GENERERAD av testsuite/rig" in p.read_text(encoding="utf-8", errors="replace")
        for p in marker
    ):
        gjort.append("lämnade %s (ingen genererad markör — inte vår)" % gamedir)
        return gjort
    shutil.rmtree(gamedir)
    gjort.append("tog bort %s" % gamedir)
    return gjort


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--unit", required=True)
    ap.add_argument("--mvdsv", required=True)
    ap.add_argument("--gamedir", required=True)
    ap.add_argument("--spel", required=True, type=int)
    ap.add_argument("--runtime-max-s", required=True, type=int)
    ap.add_argument("--timeout-s", required=True, type=int)
    ap.add_argument("--verkstall", action="store_true")
    args = ap.parse_args(argv)

    try:
        argv_start = start_argv(
            args.unit, Path(args.mvdsv), Path(args.gamedir), args.spel, args.runtime_max_s
        )
    except Vaktfel as exc:
        print("START VÄGRAD: %s" % exc, file=sys.stderr)
        return 2

    if not args.verkstall:
        print("  TORRKORNING - skulle kora: %s" % " ".join(argv_start))
        print("  (lagg till --verkstall for att resa riggen skarpt)")
        return 0

    r = subprocess.run(argv_start, capture_output=True, text=True)
    print("  %s" % (r.stdout + r.stderr).strip())
    if r.returncode != 0:
        print("START VÄGRAD: systemd-run rc=%d" % r.returncode, file=sys.stderr)
        for rad in stada(args.unit, Path(args.gamedir), Path(args.mvdsv)):
            print("  städ: %s" % rad, file=sys.stderr)
        return 3

    try:
        for rad in vanta_liv(args.unit, args.spel, args.timeout_s):
            print("  OK: %s" % rad)
    except Vaktfel as exc:
        print("RIGGEN LEVER INTE: %s" % exc, file=sys.stderr)
        for rad in stada(args.unit, Path(args.gamedir), Path(args.mvdsv)):
            print("  städ: %s" % rad, file=sys.stderr)
        return 3

    print("  riggen svarar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
