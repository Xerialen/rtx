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

Mått-identitet (punkt 8, rev 2 efter grok2-review-p8.md): varje rå-kvitto —
per-ben `cNNN/*_meta.json`, varje klusterbucket och aggregatet — stämplas med
`measure_id` = klassarens namn+version. Jämförelseverktyg VÄGRAR kvoter där
täljare och nämnare bär olika measure_id, och VÄGRAR kvitton som SAKNAR
fältet (annars blir None==None en tyst kvot — just −36 %-klassen). Här är
klassaren `klassa_utfall` (UTFALL); den ANDRA klassen i läxan är
fall-EPISODER (`fall_peak_drop_150`, falls-räknaren) — aldrig jämförbar med
denna.
"""
from __future__ import annotations

import json
import os
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
        "measure_id": MEASURE_ID,
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


def stampa_ra_meta(outdir: Path) -> int:
    """Stämpla varje per-ben `cNNN/*_meta.json` med measure_id (punkt 8 rev 2).

    Den frysta benmätaren (`timtest_ben.py`) skriver meta utan fält — det här
    är poststeget som lägger fältet på VARJE rå-kvitto, inte bara aggregatet.
    Skrivningen är atomisk (temp + `os.replace`); ett befintligt measure_id som
    redan är rätt lämnas orört. Returnerar antalet stämplade filer.
    """
    n = 0
    for meta_p in sorted(outdir.glob("c*/*_meta.json")):
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("measure_id") == MEASURE_ID:
            n += 1
            continue
        meta["measure_id"] = MEASURE_ID
        tmp = meta_p.with_name(meta_p.name + ".tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
        os.replace(tmp, meta_p)
        n += 1
    return n


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


def _write_exclusive(path: Path, text: str) -> Path:
    """Samma O_CREAT|O_EXCL-väg som d_kvitto.write_exclusive: vägrar att
    skriva över ett befintligt kvitto (3785da5-klassen, punkt 8 rev 2)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(path, flags, 0o644)
    except FileExistsError:
        raise FileExistsError(f"refuse overwrite of existing kvitto {path}") from None
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def skriv_kluster(outdir: Path, path: Path | None = None) -> dict:
    stampa_ra_meta(outdir)
    doc = samla_kluster(outdir)
    dest = path or (outdir / "kluster.json")
    _write_exclusive(dest, json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    return doc


def _saknat_measure(m: str | None) -> bool:
    return m is None or not str(m).strip()


def kvot_krav_samma_measure(
    taljare: int | float,
    namnare: int | float,
    taljare_measure: str | None,
    namnare_measure: str | None,
    *,
    etikett: str | None = None,
    tillat_omarkta: bool = False,
) -> float | None:
    """Jämförelsevägran (punkt 8 ii, rev 2): beräkna kvot ENDAST om täljare och
    nämnare bär samma measure_id.

    Blandmått (fall-EPISODER ställt mot fall-UTFALL) är −36 %-läxan och vägras.
    Saknat/tomt measure_id vägras också — två omärkta kvitton får inte bli en
    tyst kvot (None==None är just historiska −36 %-klassen). Enda undantaget är
    `tillat_omarkta=True` (namngivet, medvetet val av anroparen).
    """
    if _saknat_measure(taljare_measure) or _saknat_measure(namnare_measure):
        if tillat_omarkta:
            if _saknat_measure(taljare_measure):
                taljare_measure = None
            if _saknat_measure(namnare_measure):
                namnare_measure = None
            if taljare_measure == namnare_measure:
                if not namnare:
                    return None
                return taljare / namnare
        raise ValueError(
            "vägrar kvot%s: measure_id saknas på %s — märk kvittot eller ge "
            "explicit --tillat-omarkta (punkt 8)"
            % ((" %s" % etikett) if etikett else "",
               "täljare och/eller nämnare")
        )
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
    measure_id: str | None,
    *,
    tillat_omarkta: bool = False,
) -> dict:
    """Rapportrad med measure_id (punkt 8 iii, rev 2): varje kvotrad bär sitt
    mått-id. Saknat mått-id vägras — en rad utan id får inte publicera ett
    procenttal."""
    if _saknat_measure(measure_id) and not tillat_omarkta:
        raise ValueError(
            "rapport_rad %r: measure_id saknas — märk kvittot eller ge "
            "explicit --tillat-omarkta (punkt 8)" % etikett
        )
    return {
        "etikett": etikett,
        "measure_id": measure_id,
        "taljare": taljare,
        "namnare": namnare,
        "pct": round(100.0 * taljare / namnare, 1) if namnare else None,
    }
