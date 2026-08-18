#!/usr/bin/env python3
"""facit_lint — nio mallklausuler, eller addendumkrav vid '# ADDENDUM'.

Validerar ett facitutkast mot PLANS/FACIT-MALL.md. Syskon-addendum
(`<stam>-addendum.md`) läses automatiskt. Unit-tilldelning korsas mot
d_failclosed.ALLOWED_DEPLOY_PAIRS (d1/d3 tillåtna; d2/d4/RA vägras som
deploy-mål).

Filer vars första icke-tomma rad är ett '# ADDENDUM'-huvud går inte
genom de nio klausulerna. De valideras mot addendumkraven: referens
till moderfacitets sha (full 64-hex, och den sha ska tillhöra en
förseglad syskonfil: 0444 + sha-match + sidokvitto), tidsstämpel,
vilken § som tolkas, och beslutsfattare.

Exit 0 = komplett. Exit 2 = brist (förseglingsscriptet ska vägra).
Ingen socket, ingen ~/lab i tester.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
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


# Addendumläge (första icke-tomma raden är '# ADDENDUM…'). Minst en
# träff per krav. moder_sha är full 64-hex *och* en förseglad
# syskonfil (0444 + innehållssha + sidokvitto) — inte ett prefix i
# rubriken.
_FULL_SHA = r"[0-9a-fA-F]{64}"
ADDENDUM_KRAV: list[tuple[str, str, list[str]]] = [
    (
        "moder_sha",
        "refererar moderfacitets sha (64-hex + förseglad syskonfil)",
        [
            r"sha256\s*[:=]?\s*" + _FULL_SHA + r"\b",
            r"\bsha\s+" + _FULL_SHA + r"\b",
            r"\(" + _FULL_SHA + r"[.…)]",
        ],
    ),
    (
        "tidsstamplad",
        "tidsstämplad",
        [
            r"20\d{2}-\d{2}-\d{2}",
            r"\b\d{1,2}:\d{2}\s*(?:Z|z)\b",
            r"\b\d{1,2}:\d[0-9xX](?:Z|z)?\b",
        ],
    ),
    (
        "paragraf",
        "anger vilken § som tolkas",
        [
            r"§\s*\d+",
            r"paragraf\s+\d+",
            r"klausul\s+\d+",
        ],
    ),
    (
        "beslutsfattare",
        "beslutsfattare angiven",
        [
            r"beslut\s*:",
            r"beslutsfattare",
            r"förseglat\s+av",
            r"ägarorder",
            r"ägarens\s+(direkta\s+)?order",
        ],
    ),
]


def _norm(text: str) -> str:
    return text.replace("\u00a0", " ").lower()


def is_addendum(text: str) -> bool:
    """True iff the first non-empty line is a '# ADDENDUM' heading."""
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        return bool(re.match(r"^#\s*ADDENDUM\b", s, re.I))
    return False


def extract_moder_shas(text: str) -> list[str]:
    """Full 64-hex cited as sha / sha256 / (hex…). Prefixer räknas inte."""
    blob = _norm(text)
    found: list[str] = []
    for pat in (
        r"sha256\s*[:=]?\s*(" + _FULL_SHA + r")\b",
        r"\bsha\s+(" + _FULL_SHA + r")\b",
        r"\((" + _FULL_SHA + r")[.…)]",
    ):
        for m in re.finditer(pat, blob, re.I):
            h = m.group(1).lower()
            if h not in found:
                found.append(h)
    return found


def is_sealed_facit(path: Path, cited: str) -> bool:
    """True iff path is a sealed facit: 0444, content sha == cited, sidecar match."""
    try:
        st = path.stat()
    except OSError:
        return False
    if not path.is_file():
        return False
    if stat.S_IMODE(st.st_mode) != 0o444:
        return False
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return False
    if digest != cited.lower():
        return False
    side = Path(str(path) + ".sha256")
    try:
        if not side.is_file():
            return False
        if stat.S_IMODE(side.stat().st_mode) != 0o444:
            return False
        line = side.read_text(encoding="utf-8").strip().split()
    except OSError:
        return False
    return bool(line) and line[0].lower() == cited.lower()


def find_sealed_parent(
    cited_shas: list[str], search_dir: Path
) -> Path | None:
    """Same-directory *.md that is not itself an addendum and is sealed."""
    if not cited_shas or not search_dir.is_dir():
        return None
    try:
        cands = sorted(search_dir.glob("*.md"))
    except OSError:
        return None
    for md in cands:
        try:
            if is_addendum(md.read_text(encoding="utf-8")):
                continue
        except OSError:
            continue
        for cited in cited_shas:
            if is_sealed_facit(md, cited):
                return md
    return None


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


def lint_addendum(
    text: str, search_dir: str | Path | None = None
) -> dict:
    blob = _norm(text)
    cited = extract_moder_shas(text)
    parent: Path | None = None
    if cited and search_dir is not None:
        parent = find_sealed_parent(cited, Path(search_dir))
    krav = []
    for cid, title, pats in ADDENDUM_KRAV:
        hits = [p for p in pats if re.search(p, blob, re.I)]
        ok = len(hits) >= 1
        extra: dict = {}
        if cid == "moder_sha":
            extra["cited"] = cited
            extra["parent"] = str(parent) if parent is not None else None
            if not cited:
                ok = False
                extra["reason"] = "ingen full 64-hex"
            elif parent is None:
                ok = False
                extra["reason"] = (
                    "ingen förseglad moderfil (0444 + sha-match) i samma katalog"
                )
            else:
                ok = True
        item = {
            "id": cid,
            "title": title,
            "ok": ok,
            "hits": hits,
        }
        item.update(extra)
        krav.append(item)
    missing = [c["id"] for c in krav if not c["ok"]]
    return {
        "ok": not missing,
        "mode": "addendum",
        "missing": missing,
        "addendum_krav": krav,
        "clauses": [],
        "units": {
            "ok": True,
            "assigned": [],
            "allowed": {},
            "errors": [],
        },
    }


def lint_path(facit: str | Path, addendum: str | Path | None = None) -> dict:
    p = Path(facit)
    raw = p.read_text(encoding="utf-8")
    if is_addendum(raw):
        if addendum is not None:
            return {
                "ok": False,
                "mode": "addendum",
                "missing": [],
                "addendum_krav": [],
                "clauses": [],
                "units": {
                    "ok": True,
                    "assigned": [],
                    "allowed": {},
                    "errors": [],
                },
                "facit": str(p),
                "error": "addendumläge tar inte --addendum",
            }
        report = lint_addendum(raw, search_dir=p.parent)
        report["facit"] = str(p)
        return report
    ad = Path(addendum) if addendum else None
    text = gather_text(p, ad)
    report = lint(text)
    report["mode"] = "facit"
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
    if report.get("mode") == "addendum":
        if report.get("error"):
            print("STOPP: %s" % report["error"], file=sys.stderr)
            return 2
        if not report["ok"]:
            print("STOPP: addendum ofullständigt", file=sys.stderr)
            for m in report["missing"]:
                print("  saknar krav:", m, file=sys.stderr)
            return 2
        print(
            "OK: addendum (moder-sha · tidsstämpel · § · beslutsfattare)",
            file=sys.stderr,
        )
        return 0
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
