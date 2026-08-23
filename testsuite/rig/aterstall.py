#!/usr/bin/env python3
"""Återställningskedjan efter T3/T4 (RUNBOOK §14) som kod.

Återställning är en del av körningen, inte en artighet efteråt. Kedjan
läser den ögonblicksbild `res_t3.sh`/`res_t4.sh` skrev *innan* något rördes,
och bevisar led för led att maskinen står som den stod.

Utan ögonblicksbild finns ingen återställning att göra — bara en gissning.
Då vägrar kedjan.

Systemd-stegen körs bara med `--verkstall`. Utan flaggan rapporterar
kedjan vad den skulle göra. `enable`, `disable` och `daemon-reload` görs
aldrig, med eller utan flaggan: armerade drop-ins aktiveras retroaktivt av
en reload, och den knappen är riggsätets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from riggvakt import Vaktfel, lyssnare  # noqa: E402

#: Verb som aldrig körs härifrån, oavsett flaggor.
FORBJUDNA_SYSTEMD_VERB = frozenset({"enable", "disable", "daemon-reload", "preset", "mask"})


def _systemctl(verb: str, *args: str, verkstall: bool) -> str:
    if verb in FORBJUDNA_SYSTEMD_VERB:
        raise Vaktfel(
            "systemctl %s körs aldrig härifrån — armerade drop-ins aktiveras "
            "retroaktivt och den knappen är riggsätets" % verb
        )
    kommando = ["systemctl", "--user", verb, *args]
    if not verkstall:
        return "SKULLE KÖRA: %s" % " ".join(kommando)
    r = subprocess.run(kommando, capture_output=True, text=True)
    return "%s -> rc=%d %s" % (" ".join(kommando), r.returncode, r.stdout.strip())


def _aktiva_units() -> list[str]:
    r = subprocess.run(
        ["systemctl", "--user", "list-units", "--state=running", "--type=service", "--no-legend"],
        capture_output=True,
        text=True,
    )
    return sorted(rad.split()[0] for rad in r.stdout.splitlines() if rad.strip())


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def ta_ogonblicksbild(ut: Path, korning: str, unit: str, portar: dict, moduler: list[str]) -> dict:
    """Skrivs FÖRE riggen reses. Utan den går ingenting att återställa exakt."""
    ut.parent.mkdir(parents=True, exist_ok=True)
    bild = {
        "korning": korning,
        "unit": unit,
        "portar": portar,
        "aktiva_units": _aktiva_units(),
        "moduler": {},
        "temp_filer": [],
    }
    for m in moduler:
        p = Path(m)
        if not p.is_file():
            raise Vaktfel("modulen som ska återställas finns inte: %s" % p)
        spar = ut.parent / ("%s.before" % p.name)
        spar.write_bytes(p.read_bytes())
        bild["moduler"][str(p)] = {"sha256": _sha256(p), "kopia": str(spar)}
    ut.write_text(json.dumps(bild, indent=1, sort_keys=True), encoding="utf-8")
    return bild


def aterstall(bild: dict, verkstall: bool, lasfil: Path | None) -> tuple[list[str], list[str]]:
    """Returnerar (bevis, avvikelser). Tom avvikelselista = PASS."""
    bevis: list[str] = []
    avvik: list[str] = []

    # 1. Släck riggens egen unit och testklienterna.
    #
    #    `reset-failed` hör till kedjan, inte till felhanteringen: en failad
    #    transient unit ligger kvar under sitt namn, och nästa `systemd-run`
    #    vägrar det namnet. Utan det här steget blockerar en misslyckad
    #    körning nästa körning — och felet syns först vid starten efter.
    bevis.append(_systemctl("stop", bild["unit"], verkstall=verkstall))
    bevis.append(_systemctl("reset-failed", bild["unit"], verkstall=verkstall))

    # 2. Återställ modulbytena exakt, och bevisa likheten.
    for vag, uppg in sorted(bild.get("moduler", {}).items()):
        p, kopia = Path(vag), Path(uppg["kopia"])
        if not kopia.is_file():
            avvik.append("förkopian saknas: %s" % kopia)
            continue
        if verkstall:
            p.write_bytes(kopia.read_bytes())
        nu = _sha256(p) if p.is_file() else "(saknas)"
        if verkstall and nu != uppg["sha256"]:
            avvik.append("modul %s har sha %s, förväntat %s" % (p, nu, uppg["sha256"]))
        else:
            bevis.append("modul %s: %s" % (p, uppg["sha256"]))

    # 3. Starta om exakt de units som var igång före, och kräv att de lever.
    for unit in bild.get("aktiva_units", []):
        if unit == bild["unit"]:
            continue
        bevis.append(_systemctl("start", unit, verkstall=verkstall))
    if verkstall:
        nu_aktiva = set(_aktiva_units())
        saknas = [u for u in bild.get("aktiva_units", []) if u != bild["unit"] and u not in nu_aktiva]
        if saknas:
            avvik.append("units som var igång före är nere: %s" % ", ".join(saknas))
        else:
            bevis.append("alla %d tidigare aktiva units lever" % len(bild.get("aktiva_units", [])))

    # 4. Inga testportar kvar. En tyst port är kvittot, inte en stoppad unit.
    for vad, port in sorted(bild.get("portar", {}).items()):
        n = lyssnare(int(port))
        if n:
            avvik.append("port %s=%s har fortfarande %d lyssnare" % (vad, port, n))
        else:
            bevis.append("port %s=%s tyst" % (vad, port))

    # 5. Temporära filer bort — bara de vi själva bokförde.
    for f in bild.get("temp_filer", []):
        p = Path(f)
        if p.exists():
            if verkstall:
                p.unlink()
            bevis.append("temp bort: %s" % p)

    # 6. Rigglåset. Praxis är att tömma filen, RIGG-REGLER säger ta bort den;
    #    likriktningen är en öppen punkt, så kedjan dömer inte — den säger
    #    vad den ser och vägrar PASS så länge låset står i vårt namn.
    if lasfil is not None:
        text = lasfil.read_text(encoding="utf-8", errors="replace").strip() if lasfil.exists() else ""
        if text and ("korning=%s" % bild["korning"]) in text:
            avvik.append(
                "rigglåset står kvar i vårt namn (%s) — släpp det med bevis "
                "enligt riggsätets protokoll" % lasfil
            )
        else:
            bevis.append("rigglåset är inte vårt: %r" % (text or "(tomt/saknas)"))

    return bevis, avvik


def _kor_bild(args: argparse.Namespace) -> int:
    ut = Path(args.ut)
    if ut.exists():
        print(
            "VÄGRAD: %s finns redan. En andra ögonblicksbild över den första "
            "gör återställningen omöjlig — då är «före» i själva verket "
            "«efter». Välj en ny kvittokatalog." % ut,
            file=sys.stderr,
        )
        return 2
    try:
        bild = ta_ogonblicksbild(
            ut,
            args.korning,
            args.unit,
            {"spel": args.spel, "ctl": args.ctl, "qtv": args.qtv},
            args.modul or [],
        )
    except Vaktfel as exc:
        print("VÄGRAD: %s" % exc, file=sys.stderr)
        return 2
    print("  %d aktiva units bokförda, %d moduler sparade" % (len(bild["aktiva_units"]), len(bild["moduler"])))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="kommando", required=True)

    b = sub.add_parser("bild", help="skriv ögonblicksbilden FÖRE riggen reses")
    b.add_argument("--ut", required=True)
    b.add_argument("--korning", required=True)
    b.add_argument("--unit", required=True)
    b.add_argument("--spel", required=True, type=int)
    b.add_argument("--ctl", required=True, type=int)
    b.add_argument("--qtv", required=True, type=int)
    b.add_argument("--modul", action="append", help="modul att spara och senare återställa")

    ap_a = sub.add_parser("aterstall", help="kör hela återställningskedjan")
    ap_a.add_argument("--ogonblicksbild", required=True, help="JSON skriven före riggen restes")
    ap_a.add_argument("--lasfil")
    ap_a.add_argument("--verkstall", action="store_true", help="kör systemd-stegen på riktigt")
    ap_a.add_argument("--verdikt-ut", required=True, help="dit restore_verdict skrivs")

    args = ap.parse_args(argv)
    if args.kommando == "bild":
        return _kor_bild(args)

    bild_p = Path(args.ogonblicksbild)
    if not bild_p.is_file():
        print(
            "ÅTERSTÄLLNING VÄGRAD: ögonblicksbilden saknas (%s). Utan den finns "
            "ingen återställning, bara en gissning." % bild_p,
            file=sys.stderr,
        )
        return 2

    bild = json.loads(bild_p.read_text(encoding="utf-8"))
    try:
        bevis, avvik = aterstall(bild, args.verkstall, Path(args.lasfil) if args.lasfil else None)
    except Vaktfel as exc:
        print("ÅTERSTÄLLNING VÄGRAD: %s" % exc, file=sys.stderr)
        return 2

    for b in bevis:
        print("  %s" % b)
    verdikt = "PASS" if not avvik else "FAIL"
    for a in avvik:
        print("  AVVIKELSE: %s" % a, file=sys.stderr)

    Path(args.verdikt_ut).write_text(
        json.dumps(
            {
                "restore_verdict": verdikt,
                "korning": bild["korning"],
                "verkstalld": args.verkstall,
                "moduler": {v: u["sha256"] for v, u in sorted(bild.get("moduler", {}).items())},
                "unit_count": len(bild.get("aktiva_units", [])),
                "avvikelser": avvik,
            },
            indent=1,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print("restore_verdict=%s" % verdikt)
    return 0 if verdikt == "PASS" else 3


if __name__ == "__main__":
    sys.exit(main())
