#!/usr/bin/env python3
"""facts2_lint — vägra dashboard vars facts2-block ljuger om hårda tal.

Vägrar om:
  (a) detail-kategoriernas tal inte summerar till value-radens hard-antal
      (antingen numeratörn eller N−numeratörn, så både H/N och success/N går),
  (b) banner-summan inte matchar radsumman (täljare och nämnare),
  (c) ordet "raw" förekommer i detail eller value.

Körs före varje publicering. Ingen ~/lab, ingen socket.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

RAW_RE = re.compile(r"\braw\b", re.I)
FRAC_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
NUM_RE = re.compile(r"\d+")
NOFAIL_RE = re.compile(
    r"^\s*(no failures|inga fel|inga hårda|none|—|-)?\s*$", re.I
)


def extract_facts2(text: str) -> dict:
    m = re.search(
        r'<script[^>]*\bid=["\']facts2["\'][^>]*>(.*?)</script>',
        text,
        re.S | re.I,
    )
    if m:
        blob = m.group(1).strip()
        return json.loads(blob)
    return json.loads(text)


def _stat_blocks(doc: dict):
    if not isinstance(doc, dict):
        raise ValueError("facts2 är inte ett objekt")
    blocks = []
    for key in ("ours_column", "main_column"):
        col = doc.get(key)
        if isinstance(col, dict) and (col.get("stats") or col.get("rows")):
            blocks.append(
                (
                    key,
                    col.get("stats") or col.get("rows") or [],
                    col.get("banner"),
                )
            )
    if "arms" in doc and isinstance(doc["arms"], dict):
        for name, col in doc["arms"].items():
            if isinstance(col, dict):
                blocks.append(
                    (
                        "arms.%s" % name,
                        col.get("stats") or col.get("rows") or [],
                        col.get("banner"),
                    )
                )
    if "stats" in doc:
        blocks.append(("stats", doc.get("stats") or [], doc.get("banner")))
    if "rows" in doc:
        blocks.append(("rows", doc.get("rows") or [], doc.get("banner")))
    if not blocks:
        raise ValueError("facts2 saknar stats/rows/kolumner")
    return blocks


def _detail_nums(detail) -> list[int]:
    if detail is None:
        return []
    if isinstance(detail, dict):
        out = []
        for v in detail.values():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                out.append(int(v))
            elif isinstance(v, str):
                out.extend(int(x) for x in NUM_RE.findall(v))
        return out
    s = str(detail)
    if NOFAIL_RE.match(s):
        return []
    return [int(x) for x in NUM_RE.findall(s)]


def _value_frac(value) -> tuple[int, int] | None:
    if value is None:
        return None
    if isinstance(value, dict) and "hard" in value and "n" in value:
        return int(value["hard"]), int(value["n"])
    m = FRAC_RE.search(str(value))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def lint(doc: dict) -> dict:
    errors: list[str] = []
    checked = 0
    for bname, rows, banner in _stat_blocks(doc):
        row_num = row_den = 0
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errors.append("%s[%d]: rad är inte objekt" % (bname, i))
                continue
            value = row.get("value")
            detail = row.get("detail")
            for field, text in (("value", value), ("detail", detail)):
                if isinstance(text, str) and RAW_RE.search(text):
                    errors.append(
                        "%s[%d].%s innehåller ordet raw: %r"
                        % (bname, i, field, text)
                    )
                elif isinstance(text, dict):
                    blob = json.dumps(text, ensure_ascii=False)
                    if RAW_RE.search(blob):
                        errors.append(
                            "%s[%d].%s innehåller ordet raw" % (bname, i, field)
                        )
            frac = _value_frac(value)
            if frac is None:
                errors.append(
                    "%s[%d].value saknar A/B (hard eller success/N): %r"
                    % (bname, i, value)
                )
                continue
            a, n = frac
            row_num += a
            row_den += n
            nums = _detail_nums(detail)
            total = sum(nums)
            hard = a if total == a else (n - a if total == (n - a) else None)
            checked += 1
            if hard is None:
                errors.append(
                    "%s[%d]: detail-summa %d ≠ value-hard %d och ≠ N-success %d "
                    "(value=%r detail=%r)"
                    % (bname, i, total, a, n - a, value, detail)
                )
        if banner is not None:
            bf = _value_frac(banner)
            if bf is None:
                errors.append("%s.banner saknar A/B: %r" % (bname, banner))
            else:
                ba, bn = bf
                if ba != row_num or bn != row_den:
                    errors.append(
                        "%s.banner %d/%d ≠ radsumma %d/%d"
                        % (bname, ba, bn, row_num, row_den)
                    )
                checked += 1
    return {"ok": not errors, "errors": errors, "checked": checked}


def lint_path(path: str | Path) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    try:
        doc = extract_facts2(text)
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "errors": ["facts2 parsar inte: %s" % e],
            "checked": 0,
        }
    except ValueError as e:
        return {"ok": False, "errors": [str(e)], "checked": 0}
    return lint(doc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("dashboard")
    args = ap.parse_args(argv)
    report = lint_path(args.dashboard)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    if not report["ok"]:
        print("STOPP: facts2 underkänd", file=sys.stderr)
        for e in report["errors"]:
            print(" ", e, file=sys.stderr)
        return 2
    print("OK: facts2 %d kontroller" % report["checked"], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
