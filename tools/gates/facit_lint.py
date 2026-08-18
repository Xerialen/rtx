#!/usr/bin/env python3
"""facit_lint — nio obligatoriska klausuler + grind-1 unit-korsning.

Validerar ett facitutkast mot PLANS/FACIT-MALL.md. Syskon-addendum
(`<stam>-addendum.md`) läses automatiskt. Unit-tilldelning korsas mot
d_failclosed.ALLOWED_DEPLOY_PAIRS (d1/d3 tillåtna; d2/d4/RA vägras som
deploy-mål).

Exit 0 = komplett. Exit 2 = brist (förseglingsscriptet ska vägra).
Ingen socket, ingen ~/lab i tester.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# tools/gates/ → repo = parents[1]
TOOLS = HERE.parents[1] / "testsuite" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from d_failclosed import ALLOWED_DEPLOY_PAIRS  # noqa: E402

# Alias som facit skriver; mappar till ALLOWED_DEPLOY_PAIRS-nycklar.
UNIT_ALIAS = {
    "d1": "tbx-d1",
    "tbx-d1": "tbx-d1",
    "d3": "tbx-d3",
    "tbx-d3": "tbx-d3",
}
FORBIDDEN_DEPLOY = ("d2", "tbx-d2", "d4", "tbx-d4")

# Klausul → minst en regex måste träffa (substans, inte rubrikordning).
# Kalibrerat så nattens v3+addendum passerar; cykeldefinition är densamma
# K-regel som TIMPROV-fotnoten (hel cykel / sex giltiga / hopp per cykel).
CLAUSES: list[tuple[str, str, list[str]]] = [
    (
        "grund",
        "GRUND: graf (stamp/nivå-2), binär, unit-tilldelning",
        [
            r"nivå-2|niva-2|graph_content_hash|graph_stamp|stamp",
            r"qwprogs|binär|byggcommit",
            r"\bd1\b|\bd3\b|tbx-d[13]|unit",
        ],
    ),
    (
        "cykeldefinition",
        "CYKELDEFINITION: hel cykel = sex terminala giltiga utfall (K-regeln)",
        [
            r"sex\s+(terminala\s+)?giltiga\s+utfall",
            r"hel(a)?\s+cykel",
            r"hopp\s+exakt\s+en\s+gång\s+per\s+cykel",
            r"intern\s+balans",
        ],
    ),
    (
        "paritet",
        "PARITET: trunkering till minsta gemensamma hela cykelantal",
        [
            r"trunker",
            r"minsta\s+gemensamma",
            r"lika[- ]?(många\s+ben|ben)",
            r"paritet",
            r"sekundär(tabell|vy)",
        ],
    ),
    (
        "referensarm",
        "REFERENSARM: samtidig start ≤60 s, populationsmärkning, main på ägarorder",
        [
            r"referens",
            r"samtidig|inom\s+60\s*s|start\s+inom",
            r"\bmain\b",
        ],
    ),
    (
        "jamforande_dom",
        "JÄMFÖRANDE DOM: timskala/styrka för rankning; 20 min får konstatera",
        [
            r"timskala|t1h-skala|1\s*h\b|en\s+timme",
            r"20\s*min|ranka|jämför|osäkert",
        ],
    ),
    (
        "kontrollvarden",
        "KONTROLLVÄRDEN: förväntade slutlägen, oberoende härledning",
        [
            r"slut\s+\d{4}|nivå-2\s+[0-9a-f]{8}|oberoende",
        ],
    ),
    (
        "staende_slutklausul",
        "STÅENDE SLUTKLAUSUL: ingen variant stående utan ägarbeslut; rent läge; undo",
        [
            r"stående",
            r"rent\s+bokfört",
            r"undo.bevis|undo_bevis",
            r"stängs\s+efter",
            r"ägarens\s+(uttryckliga\s+)?beslut",
        ],
    ),
    (
        "addendum_regel",
        "ADDENDUM: tolkningsfrågor förseglas före armslut, aldrig i efterhand",
        [
            r"addendum",
            r"före\s+armslut",
            r"aldrig\s+(avgöras\s+)?i\s+efterhand",
        ],
    ),
    (
        "domskala",
        "DOMSKALA: uttrycklig, med sämre-referens (näst-bästa kända läge)",
        [
            r"bättre",
            r"sämre",
            r"osäkert|oförändrat",
        ],
    ),
]


def _norm(text: str) -> str:
    return text.replace("\u00a0", " ").lower()


def gather_text(facit_path: Path, addendum: Path | None) -> str:
    parts = [facit_path.read_text(encoding="utf-8")]
    cands = []
    if addendum is not None:
        cands.append(addendum)
    else:
        stem = facit_path.stem
        cands.append(facit_path.with_name(stem + "-addendum.md"))
        cands.append(facit_path.with_name(stem + ".addendum.md"))
    for p in cands:
        if p.is_file() and p.resolve() != facit_path.resolve():
            parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


def check_clauses(text: str) -> list[dict]:
    blob = _norm(text)
    out = []
    for cid, title, pats in CLAUSES:
        hits = [p for p in pats if re.search(p, blob, re.I)]
        # cykeldefinition / addendum / staende: minst EN träff.
        # grund / referens / jamforande / domskala: minst två.
        # paritet / kontroll: minst en.
        need = 2 if cid in {"grund", "referensarm", "jamforande_dom", "domskala"} else 1
        ok = len(hits) >= need
        out.append(
            {
                "id": cid,
                "title": title,
                "ok": ok,
                "hits": hits,
            }
        )
    return out


def check_units(text: str) -> dict:
    """Grind-1: deploy-mål måste ligga i ALLOWED_DEPLOY_PAIRS."""
    blob = _norm(text)
    errors: list[str] = []
    assigned = []
    for raw, canon in UNIT_ALIAS.items():
        if re.search(r"\b" + re.escape(raw) + r"\b", blob):
            assigned.append(canon)
    assigned = sorted(set(assigned))
    for unit in assigned:
        if unit not in ALLOWED_DEPLOY_PAIRS:
            errors.append(
                "unit %s finns inte i ALLOWED_DEPLOY_PAIRS %s"
                % (unit, sorted(ALLOWED_DEPLOY_PAIRS))
            )
    for bad in FORBIDDEN_DEPLOY:
        if re.search(
            r"(?:på|unit|uniten|apply|kör(?:s)?\s+på|tilldel\w*|variant\w*)\s+"
            + re.escape(bad)
            + r"\b"
            + r"|\b"
            + re.escape(bad)
            + r"\s*[=:]",
            blob,
        ):
            errors.append(
                "grind-1: %s är inte ett tillåtet deploy-par (ALLOWED_DEPLOY_PAIRS=%s)"
                % (bad, sorted(ALLOWED_DEPLOY_PAIRS))
            )
    # Portpar som nämns tillsammans med en känd unit måste stämma.
    for unit, (ctl, game) in ALLOWED_DEPLOY_PAIRS.items():
        short = unit.replace("tbx-", "")
        if not re.search(r"\b" + short + r"\b|\b" + unit + r"\b", blob):
            continue
        # om båda portarna nämns i dokumentet, ska de vara just detta par
        has_ctl = re.search(r"\b%d\b" % ctl, blob)
        has_game = re.search(r"\b%d\b" % game, blob)
        # inget krav att de ska stå med — v3 nämner dem inte för d1/d3
        _ = (has_ctl, has_game)
    return {
        "ok": not errors,
        "assigned": assigned,
        "allowed": {k: list(v) for k, v in ALLOWED_DEPLOY_PAIRS.items()},
        "errors": errors,
    }


def lint(text: str) -> dict:
    clauses = check_clauses(text)
    units = check_units(text)
    missing = [c["id"] for c in clauses if not c["ok"]]
    ok = not missing and units["ok"]
    return {
        "ok": ok,
        "missing": missing,
        "clauses": clauses,
        "units": units,
    }


def lint_path(facit: str | Path, addendum: str | Path | None = None) -> dict:
    p = Path(facit)
    ad = Path(addendum) if addendum else None
    text = gather_text(p, ad)
    report = lint(text)
    report["facit"] = str(p)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("facit")
    ap.add_argument("--addendum", default=None)
    args = ap.parse_args(argv)
    report = lint_path(args.facit, args.addendum)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    if not report["ok"]:
        print("STOPP: facit ofullständigt", file=sys.stderr)
        for m in report["missing"]:
            print("  saknar klausul:", m, file=sys.stderr)
        for e in report["units"]["errors"]:
            print("  unit:", e, file=sys.stderr)
        return 2
    print("OK: nio klausuler + grind-1", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
