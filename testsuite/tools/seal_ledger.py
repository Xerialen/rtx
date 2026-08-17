#!/usr/bin/env python3
"""Förseglingsliggarens radformat: bygg, skriv, läs, verifiera.

Liggaren är två saker som håller varandra:

* **ett kvitto per försegling**, `<ledger>/seals/<seal_id>.json`, skapat med
  `O_EXCL`. Finns filen redan är facitet förseglat och verktyget vägrar. Det är
  den riktiga mutexen: en försegling kan inte skrivas två gånger och inte skrivas
  över.
* **ett append-only index**, `<ledger>/forsegling.jsonl`, en rad per försegling,
  kedjad: varje rad bär `prev` = föregående rads `line_sha256`. En bortklippt
  eller ändrad rad bryter kedjan och syns.

Radformatet är maskinformat med flit. Ledgern i `WORK_LOGS/terra-d-facit-forsegling.md`
är prosa och skriven för hand; den är läsbar men går inte att verifiera. Den här
filen ersätter inte prosan — den gör att det finns något att jämföra prosan MOT.

`line_sha256` hashar radens kanoniska JSON **utan** `line_sha256`-fältet, så
verifieringen är entydig: plocka bort fältet, räkna om, jämför. Ingen tvetydighet
om radslut eller nyckelordning.

Ingen riggkontakt. Rör bara de sökvägar den får som argument.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

SCHEMA = "forsegling/1"

#: Fält som ingår i radens hash, i den ordning `sort_keys` ger dem. `line_sha256`
#: är per definition undantaget — den ÄR hashen.
HASH_EXCLUDED = ("line_sha256",)

#: Kedjans början. Sextiofyra nollor är inte en giltig sha256 av något vi skriver,
#: så "första raden" går att skilja från "raden pekar på något".
GENESIS = "0" * 64

INDEX_NAME = "forsegling.jsonl"
SEALS_DIR = "seals"


class Vagran(Exception):
    """Verktyget vägrar hellre än gissar."""


def kanonisk(row: dict) -> str:
    """Radens kanoniska form: sorterade nycklar, inga blanksteg."""
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def line_hash(row: dict) -> str:
    """sha256 över den kanoniska raden utan `line_sha256`."""
    utan = {k: v for k, v in row.items() if k not in HASH_EXCLUDED}
    return hashlib.sha256(kanonisk(utan).encode("utf-8")).hexdigest()


def file_sha256(p: Path) -> tuple[str, int]:
    data = p.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def index_path(ledger: Path) -> Path:
    return ledger / INDEX_NAME


def seal_path(ledger: Path, seal_id: str) -> Path:
    return ledger / SEALS_DIR / f"{seal_id}.json"


def read_index(ledger: Path) -> list[dict]:
    p = index_path(ledger)
    if not p.is_file():
        return []
    rows = []
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise Vagran(f"{p}:{n} är inte JSON: {exc}") from exc
    return rows


def seal_id_for(facit: Path, facit_sha256: str) -> str:
    """`<stam>-<sha12>`. Samma bytes under samma namn ger samma id, alltså en krock
    i `O_EXCL`-steget — vilket är precis rätt svar på "försegla det här igen"."""
    stem = facit.name
    for suffix in (".md", ".json", ".jsonl", ".txt"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    trygg = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in stem)
    return f"{trygg}-{facit_sha256[:12]}"


def build_row(
    *,
    facit: Path,
    facit_sha256: str,
    facit_bytes: int,
    head: str,
    head_subject: str,
    code_paths: list[str],
    sealed_at: str,
    sealed_by: str,
    prev: str,
    seal_id: str,
    extra: dict | None = None,
) -> dict:
    row = {
        "schema": SCHEMA,
        "seal_id": seal_id,
        "facit": str(facit),
        "facit_basename": facit.name,
        "facit_sha256": facit_sha256,
        "facit_bytes": facit_bytes,
        "head": head,
        "head_subject": head_subject,
        "code_paths": sorted(code_paths),
        "sealed_at": sealed_at,
        "sealed_by": sealed_by,
        "prev": prev,
    }
    if extra:
        # Additivt: senare punkter (kontrasignaturen) lägger till fält utan att
        # bumpa schemat. Konsumenter ska ignorera fält de inte känner igen.
        row.update(extra)
    row["line_sha256"] = line_hash(row)
    return row


def append_row(ledger: Path, row: dict) -> Path:
    """Skriv kvittot med `O_EXCL` och appenda indexraden. Kvittot först: finns det
    redan är facitet förseglat, och då ska ingenting läggas till i indexet."""
    (ledger / SEALS_DIR).mkdir(parents=True, exist_ok=True)
    kvitto = seal_path(ledger, row["seal_id"])
    blob = (kanonisk(row) + "\n").encode("utf-8")
    try:
        fd = os.open(kvitto, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError as exc:
        raise Vagran(
            f"redan förseglat: {kvitto} finns. En försegling skrivs en gång och "
            f"skrivs aldrig över — ändra facitet eller läs den befintliga raden."
        ) from exc
    with os.fdopen(fd, "wb") as f:
        f.write(blob)
    with open(index_path(ledger), "ab") as f:
        f.write(blob)
    return kvitto


def verify_chain(rows: list[dict]) -> list[str]:
    """Vad som är fel i kedjan. Tom lista = hel."""
    fel: list[str] = []
    vantad_prev = GENESIS
    sedda: set[str] = set()
    for i, row in enumerate(rows, start=1):
        if row.get("schema") != SCHEMA:
            fel.append(f"rad {i}: schema {row.get('schema')!r} != {SCHEMA}")
            continue
        if row.get("line_sha256") != line_hash(row):
            fel.append(f"rad {i} ({row.get('seal_id')}): line_sha256 stämmer inte — raden är ändrad")
        if row.get("prev") != vantad_prev:
            fel.append(
                f"rad {i} ({row.get('seal_id')}): prev {row.get('prev')} != {vantad_prev} — "
                f"kedjan är bruten (rad bortklippt eller omordnad)"
            )
        sid = row.get("seal_id")
        if sid in sedda:
            fel.append(f"rad {i}: seal_id {sid} förekommer två gånger")
        sedda.add(sid)
        vantad_prev = row.get("line_sha256", GENESIS)
    return fel


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="seal_ledger.py",
        description="Förseglingsliggarens radformat. Anropas av seal.sh; --verify går att köra fristående.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("append", help="bygg och skriv en förseglingsrad")
    a.add_argument("--ledger", required=True)
    a.add_argument("--facit", required=True)
    a.add_argument("--head", required=True)
    a.add_argument("--head-subject", default="")
    a.add_argument("--code-path", action="append", default=[])
    a.add_argument("--sealed-at", required=True)
    a.add_argument("--sealed-by", required=True)

    v = sub.add_parser("verify", help="kontrollera hela kedjan")
    v.add_argument("--ledger", required=True)

    args = p.parse_args(argv)
    ledger = Path(args.ledger)

    try:
        if args.cmd == "verify":
            rows = read_index(ledger)
            fel = verify_chain(rows)
            for f in fel:
                print(f"KEDJEFEL: {f}", file=sys.stderr)
            print(f"{len(rows)} förseglingar, {'HEL' if not fel else str(len(fel)) + ' fel'}")
            return 1 if fel else 0

        facit = Path(args.facit)
        if not facit.is_file():
            raise Vagran(f"facit är ingen fil: {facit}")
        sha, n = file_sha256(facit)
        rows = read_index(ledger)
        fel = verify_chain(rows)
        if fel:
            raise Vagran(
                "liggaren är trasig innan vi ens börjat: " + "; ".join(fel) + ". Lägg inte till på en bruten kedja."
            )
        row = build_row(
            facit=facit,
            facit_sha256=sha,
            facit_bytes=n,
            head=args.head,
            head_subject=args.head_subject,
            code_paths=args.code_path,
            sealed_at=args.sealed_at,
            sealed_by=args.sealed_by,
            prev=rows[-1]["line_sha256"] if rows else GENESIS,
            seal_id=seal_id_for(facit, sha),
        )
        kvitto = append_row(ledger, row)
        print(kanonisk(row))
        print(f"kvitto: {kvitto}", file=sys.stderr)
        return 0
    except Vagran as exc:
        print(f"VÄGRAR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
