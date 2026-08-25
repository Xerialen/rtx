#!/usr/bin/env python3
"""Läser repots enda portlista, `docs/PORTAR.md`.

Riggskripten hårdkodar inga portnummer. De läser den här filen, som läser
samma bytes som en människa läser. Det är hela poängen: en kopierad
portlista är samma fälla som ett kopierat kontrollvärde.

Parsern är fail-closed. En rad som inte följer formatet tolkas inte
välvilligt — den fäller läsningen.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

#: Tabellens kontrakt. Ändras rubriken i `docs/PORTAR.md` ska den här
#: parsern falla, inte gissa vilken kolumn som blev vilken.
RUBRIK = ["port", "klass", "roll", "grupp", "ägare / belägg"]

#: Slutna ordförråd. En fri sträng hade låtit den som skriver tabellen
#: uppfinna en klass som ingen grind känner igen.
KLASSER = frozenset({"forbjuden", "orord", "deploy", "lab", "ra-kontroll"})
ROLLER = frozenset({"spel", "ctl", "qtv"})

#: Vad ett skript får göra med en port av varje klass. Mappningen står i
#: kod och inte i tabellen, så att tabellen inte kan säga emot sig själv.
ATKOMST = {
    "forbjuden": "neka",
    "orord": "neka",
    "deploy": "neka",
    "lab": "tillat",
    "ra-kontroll": "order",
}
assert set(ATKOMST) == set(KLASSER)


class Portfel(Exception):
    """Portlistan gick inte att lita på. Alltid ett stopp, aldrig en varning."""


@dataclass(frozen=True)
class Rad:
    port: int
    klass: str
    roll: str
    grupp: str
    belagg: str

    @property
    def atkomst(self) -> str:
        return ATKOMST[self.klass]


def _celler(rad: str) -> list[str]:
    if not rad.startswith("|") or not rad.rstrip().endswith("|"):
        raise Portfel("tabellrad utan kantpipe: %r" % rad)
    return [c.strip() for c in rad.strip().strip("|").split("|")]


def las_tabell(path: str | Path) -> dict[int, Rad]:
    """Returnerar {port: Rad}. Kastar Portfel vid minsta oklarhet."""
    p = Path(path)
    if not p.is_file():
        raise Portfel("portlistan saknas: %s" % p)
    rader = p.read_text(encoding="utf-8").splitlines()

    start = None
    for i, rad in enumerate(rader):
        if rad.startswith("|") and _celler(rad) == RUBRIK:
            start = i
            break
    if start is None:
        raise Portfel(
            "hittade ingen tabell med rubriken %s i %s — formatet är kontrakt"
            % (" | ".join(RUBRIK), p)
        )

    if start + 1 >= len(rader) or set(rader[start + 1].replace("|", "").strip()) - {
        "-",
        " ",
    }:
        raise Portfel("rubriken följs inte av en avgränsarrad i %s" % p)

    ut: dict[int, Rad] = {}
    for rad in rader[start + 2 :]:
        if not rad.startswith("|"):
            break
        c = _celler(rad)
        if len(c) != len(RUBRIK):
            raise Portfel("rad med %d kolumner, vill ha %d: %r" % (len(c), len(RUBRIK), rad))
        port_text, klass, roll, grupp, belagg = c
        if not port_text.isdigit():
            raise Portfel("port är inte ett tal: %r" % port_text)
        port = int(port_text)
        if not 1 <= port <= 65535:
            raise Portfel("port utanför intervallet: %d" % port)
        if klass not in KLASSER:
            raise Portfel(
                "okänd klass %r för port %d — tillåtna: %s"
                % (klass, port, ", ".join(sorted(KLASSER)))
            )
        if roll not in ROLLER:
            raise Portfel(
                "okänd roll %r för port %d — tillåtna: %s"
                % (roll, port, ", ".join(sorted(ROLLER)))
            )
        if not grupp:
            raise Portfel("port %d saknar grupp" % port)
        if port in ut:
            raise Portfel("port %d står två gånger" % port)
        ut[port] = Rad(port, klass, roll, grupp, belagg)

    if not ut:
        raise Portfel("portlistan är tom i %s" % p)
    return ut


def trior(tabell: dict[int, Rad], klass: str = "lab") -> dict[str, dict[str, int]]:
    """Grupperar portar per grupp. En trio är spel+ctl+qtv ur samma grupp.

    Halva trior returneras inte: de är den enda sortens misstag som annars
    hade kunnat resa rätt rigg på fel portar.
    """
    per_grupp: dict[str, dict[str, int]] = {}
    for rad in tabell.values():
        if rad.klass != klass:
            continue
        g = per_grupp.setdefault(rad.grupp, {})
        if rad.roll in g:
            raise Portfel("grupp %s har två portar med rollen %s" % (rad.grupp, rad.roll))
        g[rad.roll] = rad.port
    return {g: v for g, v in per_grupp.items() if set(v) == ROLLER}


def krav_tillaten(tabell: dict[int, Rad], port: int, vad: str) -> Rad:
    """Vägrar om porten inte får resas. Okänd port vägras också.

    En port som inte står i tabellen är inte "ledig" — den är oredovisad.
    Det är precis så de pid-ägda qtv-portarna kunde se lediga ut för varje
    säte som läste portläget utan rot: porten syntes, ägaren inte.
    """
    rad = tabell.get(port)
    if rad is None:
        raise Portfel(
            "port %d (%s) står inte i portlistan. Oredovisad port är inte "
            "ledig port — för in den i docs/PORTAR.md först." % (port, vad)
        )
    if rad.atkomst == "tillat":
        return rad
    if rad.atkomst == "order":
        raise Portfel(
            "port %d (%s) är %s och rörs endast på uttrycklig order: %s"
            % (port, vad, rad.klass, rad.belagg)
        )
    raise Portfel(
        "port %d (%s) är %s och får inte resas: %s" % (port, vad, rad.klass, rad.belagg)
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # Ingen default: en bekväm default skriver mot fel fil tyst.
    ap.add_argument("--portlista", required=True, help="sökväg till docs/PORTAR.md")
    ap.add_argument("--trior", action="store_true", help="visa hela lab-trior")
    ap.add_argument(
        "--grupp",
        help="skriv ut SPEL/CTL/QTV för en lab-grupp, för `eval` i ett skript",
    )
    ap.add_argument("--port", type=int, action="append", help="pröva en port; kan upprepas")
    args = ap.parse_args(argv)

    try:
        tabell = las_tabell(args.portlista)
    except Portfel as exc:
        print("PORTLISTA VÄGRAD: %s" % exc, file=sys.stderr)
        return 2

    if args.trior:
        print(json.dumps(trior(tabell), indent=1, sort_keys=True, ensure_ascii=False))

    if args.grupp:
        alla = trior(tabell)
        if args.grupp not in alla:
            print(
                "PORTLISTA VÄGRAD: ingen hel lab-trio heter %r. Hela trior: %s"
                % (args.grupp, ", ".join(sorted(alla)) or "(inga)"),
                file=sys.stderr,
            )
            return 2
        t = alla[args.grupp]
        print("SPEL=%d\nCTL=%d\nQTV=%d" % (t["spel"], t["ctl"], t["qtv"]))

    rc = 0
    for port in args.port or []:
        try:
            rad = krav_tillaten(tabell, port, "prövad")
        except Portfel as exc:
            print("NEKAD %d: %s" % (port, exc))
            rc = 2
        else:
            print("TILLÅTEN %d: %s/%s (%s)" % (rad.port, rad.grupp, rad.roll, rad.belagg))
    return rc


if __name__ == "__main__":
    sys.exit(main())
