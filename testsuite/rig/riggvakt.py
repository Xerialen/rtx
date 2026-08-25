#!/usr/bin/env python3
"""Förvillkorskontroll för T3/T4-riggarna. Vägrar hellre än reser.

Vakten mäter — den citerar inte. Varje led är ett eget svar med sitt eget
skäl, så att en vägran säger vad som fattas i stället för "preflight
failed".

Vakten tar inte rigglåset. Låsprotokollet är riggsätets, och ett andra
protokoll bredvid det första är hur två sanningar uppstår. Vakten kräver
att låset redan är taget i körningens namn, och vägrar annars.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from portar import Portfel, Rad, krav_tillaten, las_tabell  # noqa: E402


class Vaktfel(Exception):
    """Ett led fattas. Alltid ett stopp, aldrig en varning."""


def lyssnare(port: int) -> int:
    """Antal lyssnare på porten. Kräver inte rot — ägaren syns bara med rot."""
    try:
        r = subprocess.run(
            ["ss", "-tulnH", "( sport = :%d )" % port],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Vaktfel("kunde inte läsa portläget för %d: %s" % (port, exc)) from exc
    return len([r for r in r.stdout.splitlines() if r.strip()])


def kolla_las(lasfil: Path, korning: str) -> str:
    """Rigglåset ska vara taget i körningens namn — av riggsätet, inte av oss.

    Praxis skiljer sig: RIGG-REGLER säger ta bort filen, körkvittona tömmer
    den. Båda betyder ledig. Vakten läser därför tom fil och saknad fil som
    samma sak, och kräver ett innehåll som namnger körningen.
    """
    if not lasfil.exists():
        raise Vaktfel(
            "rigglåset %s finns inte. Ta det i körningens namn före rigg "
            "(korning=%s), enligt riggsätets protokoll." % (lasfil, korning)
        )
    text = lasfil.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        raise Vaktfel(
            "rigglåset %s är tomt = ledigt. Ta det i körningens namn "
            "(korning=%s) före rigg." % (lasfil, korning)
        )
    if ("korning=%s" % korning) not in text:
        raise Vaktfel(
            "rigglåset hålls av någon annan körning: %r (vi är korning=%s). "
            "Vänta — riggen har en ägare i taget." % (text, korning)
        )
    return text


def kolla_portar(tabell: dict[int, Rad], valda: dict[str, int]) -> list[str]:
    """Varje vald port måste vara både tillåten enligt tabellen och tyst nu."""
    bevis = []
    sedda: dict[int, str] = {}
    for vad, port in sorted(valda.items()):
        if port in sedda:
            raise Vaktfel(
                "port %d vald både som %s och %s — halv trio är hur rätt "
                "rigg hamnar på fel portar" % (port, sedda[port], vad)
            )
        sedda[port] = vad
        try:
            rad = krav_tillaten(tabell, port, vad)
        except Portfel as exc:
            raise Vaktfel(str(exc)) from exc
        n = lyssnare(port)
        if n:
            raise Vaktfel(
                "port %d (%s) är upptagen — %d lyssnare. En upptagen port "
                "är någon annans körning, inte en ledig plats." % (port, vad, n)
            )
        bevis.append("%s=%d tillåten (%s/%s) och tyst" % (vad, port, rad.grupp, rad.roll))
    return bevis


def kolla_ororda(tabell: dict[int, Rad], valda: dict[str, int]) -> list[str]:
    """De pid-ägda portarna ska leva och inte vara våra.

    KTX-paret och qtv-syskonen är någon annans processer. Att de lever är
    ett friskhetstecken; att någon av dem står bland våra val är ett stopp.
    """
    bevis = []
    vara = set(valda.values())
    for rad in sorted(tabell.values(), key=lambda r: r.port):
        if rad.klass != "orord":
            continue
        if rad.port in vara:
            raise Vaktfel(
                "port %d är pid-ägd av någon annan (%s) och står bland våra "
                "val" % (rad.port, rad.belagg)
            )
        n = lyssnare(rad.port)
        bevis.append("%d orörd: %d lyssnare (%s)" % (rad.port, n, rad.belagg))
    return bevis


def kolla_frogbotdata(bots: Path, manifest: Path) -> list[str]:
    """T4:s bots/-data. Pekare + hash i repot; datat självt stannar utanför."""
    if not bots.is_dir():
        raise Vaktfel("frogbot-datat saknas: %s" % bots)
    if not manifest.is_file():
        raise Vaktfel("frogbot-manifestet saknas: %s" % manifest)
    r = subprocess.run(
        ["sha256sum", "-c", "--quiet", str(manifest.resolve())],
        cwd=str(bots),
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        rader = [x for x in (r.stdout + r.stderr).splitlines() if x.strip()]
        raise Vaktfel(
            "frogbot-datat stämmer inte med manifestet (%d avvikelser): %s"
            % (len(rader), "; ".join(rader[:5]))
        )
    n = len(manifest.read_text(encoding="utf-8").strip().splitlines())
    return ["frogbot-data: %d filer, alla sha256 stämmer" % n]


def kolla_qw_analyze(sokvag: Path, vantad_sha: str | None) -> list[str]:
    """`[tools].qw_analyze` är en pekare. Vi löser upp den och säger vad den blev."""
    if not sokvag.exists():
        raise Vaktfel("qw_analyze pekar på något som inte finns: %s" % sokvag)
    riktig = sokvag.resolve()
    if not os.access(riktig, os.X_OK):
        raise Vaktfel("qw_analyze är inte körbar: %s" % riktig)
    sha = hashlib.sha256(riktig.read_bytes()).hexdigest()
    if vantad_sha and sha != vantad_sha:
        raise Vaktfel(
            "qw_analyze har fel sha256: %s (vantad %s) — kombattlåset skulle "
            "mätas med en annan binär än den bokförda" % (sha, vantad_sha)
        )
    return ["qw_analyze: %s sha256=%s" % (riktig, sha)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # Inga defaults. En bekväm default läser fel fil tyst.
    ap.add_argument("--portlista", required=True)
    ap.add_argument("--korning", required=True, help="körningens namn, som i rigglåset")
    ap.add_argument("--lasfil", required=True, help="rigglåsets sökväg")
    ap.add_argument("--spel", required=True, type=int)
    ap.add_argument("--ctl", required=True, type=int)
    ap.add_argument("--qtv", required=True, type=int)
    ap.add_argument("--bots", help="KTX bots/ (krävs för t4)")
    ap.add_argument("--bots-manifest")
    ap.add_argument("--qw-analyze")
    ap.add_argument("--qw-analyze-sha256")
    args = ap.parse_args(argv)

    bevis: list[str] = []
    try:
        try:
            tabell = las_tabell(args.portlista)
        except Portfel as exc:
            raise Vaktfel(str(exc)) from exc
        bevis.append("portlista: %d rader ur %s" % (len(tabell), args.portlista))

        valda = {"spel": args.spel, "ctl": args.ctl, "qtv": args.qtv}
        grupper = {tabell[p].grupp for p in valda.values() if p in tabell}
        if len(grupper) > 1:
            raise Vaktfel(
                "portarna kommer ur olika grupper (%s) — en trio är alltid "
                "hela trion ur samma grupp" % ", ".join(sorted(grupper))
            )

        bevis.append(kolla_las(Path(args.lasfil), args.korning))
        bevis += kolla_portar(tabell, valda)
        bevis += kolla_ororda(tabell, valda)
        if args.bots:
            if not args.bots_manifest:
                raise Vaktfel("--bots utan --bots-manifest: data utan hash är ingen pekare")
            bevis += kolla_frogbotdata(Path(args.bots), Path(args.bots_manifest))
        if args.qw_analyze:
            bevis += kolla_qw_analyze(Path(args.qw_analyze), args.qw_analyze_sha256)
    except Vaktfel as exc:
        for b in bevis:
            print("  OK: %s" % b)
        print("VAKTEN VÄGRAR: %s" % exc, file=sys.stderr)
        return 2

    for b in bevis:
        print("  OK: %s" % b)
    print("förvillkoren håller")
    return 0


if __name__ == "__main__":
    sys.exit(main())
