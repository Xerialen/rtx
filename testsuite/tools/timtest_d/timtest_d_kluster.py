"""Fall/stall-kluster ur timtest-JSONL + _meta — samma form som M1-underlaget.

Läser originalets per-ben JSONL (`players[].origin`) och `_meta.json`
(`utfall`, `falls`). Klustrar per cell om `cell`-fält finns, annars per
avrundad origin. Ändrar inte ben-/klipplogik.

Klassning (timtest_ben.py:255–261, låst):
  * fall              → fall-bucket (peak_drop_150 till härden)
  * fall_plus_fastnad → BÅDE fall- och fastnad-bucket (M1 per-event:
                        peak_drop_150→fall, bot_stall→fastnad)
  * fall_efter_framme → INTE fall. Egen miss-räknare. Originalet:
                        «miss (täljarens nämnare, ej täljare)».
                        M1:s fall = fall till härden, inte miss efter ankomst.
  * fastnad           → fastnad-bucket

Mått-identitet (punkt 8): varje rå-kvitto stämplas med `measure_id` =
klassarens namn+version. Jämförelseverktyg VÄGRAR kvoter där täljare och
nämnare bär olika measure_id (−36 %-läxan: fall-EPISODER ställdes mot
fall-UTFALL). Här är klassaren `klassa_utfall` (UTFALL); den ANDRA klassen i
läxan är fall-EPISODER (`fall_peak_drop_150`, falls-räknaren) — aldrig
jämförbar med denna.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Klassarens namn+version (låst mot timtest_ben.py:255–261, grok 8).
MEASURE_ID = "klassa_utfall@r6"
# Den andra måttklassen i −36 %-läxan — INTE denna bucket, aldrig jämförbar.
MEASURE_ID_FALL_EPISODER = "fall_peak_drop_150@r6"


def _origin(row: dict) -> list[float] | None:
    for p in row.get("players") or []:
        o = p.get("origin")
        if o and len(o) >= 3:
            return [float(o[0]), float(o[1]), float(o[2])]
    return None


def _cell(row: dict) -> Any:
    if "cell" in row and row["cell"] is not None:
        return row["cell"]
    for p in row.get("players") or []:
        if p.get("cell") is not None:
            return p["cell"]
    return None


def _last_row(jsonl: Path) -> dict | None:
    last = None
    if not jsonl.is_file():
        return None
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            last = json.loads(line)
        except json.JSONDecodeError:
            continue
    return last


def klassa_utfall(utfall: str) -> tuple[bool, bool, bool]:
    """(is_fall, is_fastnad, is_miss) — se modul-docstring."""
    u = str(utfall or "")
    is_miss = u == "fall_efter_framme"
    is_fall = u == "fall" or u == "fall_plus_fastnad"
    is_fastnad = "fastnad" in u
    return is_fall, is_fastnad, is_miss


def _bucket_key(typ: str, cell: Any, origin: list[float] | None):
    return (typ, cell if cell is not None else (
        tuple(round(x, 0) for x in origin) if origin else None
    ))


def _add(buckets: dict, typ: str, cell: Any, origin: list[float] | None,
         meta: dict, utfall: str) -> None:
    key = _bucket_key(typ, cell, origin)
    rec = buckets.setdefault(key, {
        "typ": typ,
        "cell": cell,
        "n": 0,
        "origins": [],
        "ben": [],
        "cykler": [],
        "utfall": [],
    })
    rec["n"] += 1
    if origin:
        rec["origins"].append([round(x, 2) for x in origin])
    rec["ben"].append(meta.get("ben"))
    rec["cykler"].append(meta.get("cykel"))
    rec["utfall"].append(utfall)


def samla_kluster(outdir: Path) -> dict:
    """Gå cNNN/*_meta.json. Två oberoende if: fall_plus_fastnad ger rad
    i båda bucketarna. fall_efter_framme är miss, inte fall."""
    buckets: dict[tuple, dict] = {}
    n_fall = n_stall = n_miss = n_ben = 0
    for meta_p in sorted(outdir.glob("c*/*_meta.json")):
        n_ben += 1
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        utfall = str(meta.get("utfall") or "")
        is_fall, is_fastnad, is_miss = klassa_utfall(utfall)
        if not (is_fall or is_fastnad or is_miss):
            continue
        raw = meta_p.with_name(meta_p.name.replace("_meta.json", ".jsonl"))
        last = _last_row(raw)
        cell = _cell(last) if last else None
        origin = _origin(last) if last else None
        if is_fall:
            n_fall += 1
            _add(buckets, "fall", cell, origin, meta, utfall)
        if is_fastnad:
            n_stall += 1
            _add(buckets, "fastnad", cell, origin, meta, utfall)
        if is_miss:
            n_miss += 1
            _add(buckets, "miss", cell, origin, meta, utfall)
    kluster = sorted(buckets.values(), key=lambda r: (-r["n"], str(r["cell"])))
    for rec in kluster:
        if rec["origins"]:
            n = len(rec["origins"])
            rec["centroid"] = [
                round(sum(o[i] for o in rec["origins"]) / n, 1) for i in range(3)
            ]
        else:
            rec["centroid"] = None
    return {
        "schema": "timtest-d/kluster/1",
        "measure_id": MEASURE_ID,
        "n_ben": n_ben,
        "n_fall": n_fall,
        "n_fastnad": n_stall,
        "n_miss": n_miss,
        "miss_not": (
            "fall_efter_framme = miss efter ankomst (timtest_ben.py:257, "
            "ej täljare). Inte M1-fall (fall till härden)."
        ),
        "kluster": kluster,
    }


def skriv_kluster(outdir: Path, path: Path | None = None) -> dict:
    doc = samla_kluster(outdir)
    dest = path or (outdir / "kluster.json")
    dest.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    return doc


def kvot_krav_samma_measure(
    taljare: int | float,
    namnare: int | float,
    taljare_measure: str,
    namnare_measure: str,
    *,
    etikett: str | None = None,
) -> float | None:
    """Jämförelsevägran (punkt 8 ii): beräkna kvot ENDAST om täljare och
    nämnare bär samma measure_id. Blandmått (fall-EPISODER ställt mot
    fall-UTFALL) är −36 %-läxan och vägras med ValueError."""
    if taljare_measure != namnare_measure:
        raise ValueError(
            "vägrar kvot%s: täljare measure_id=%r != nämnare measure_id=%r "
            "(blandmått förbjudet, punkt 8)"
            % ((" %s" % etikett) if etikett else "",
               taljare_measure, namnare_measure)
        )
    if not namnare:
        return None
    return taljare / namnare


def rapport_rad(
    etikett: str,
    taljare: int,
    namnare: int,
    measure_id: str,
) -> dict:
    """Rapportrad med measure_id (punkt 8 iii): varje kvotrad bär sitt
    mått-id så en granskare ser vad som jämfördes."""
    return {
        "etikett": etikett,
        "measure_id": measure_id,
        "taljare": taljare,
        "namnare": namnare,
        "pct": round(100.0 * taljare / namnare, 1) if namnare else None,
    }
