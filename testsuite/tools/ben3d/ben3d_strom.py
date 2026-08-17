#!/usr/bin/env python3
"""ben3d_strom.py — ben3d-strom/1 (D3): header|tick|end|abort + CLI tail/validering.

En källa, flera läsare (B4): producenten skriver en numrerad read-only-jsonl-ström;
CLI-läsaren och 3D-läsarna konsumerar SAMMA serialiserade post (payload_sha256
byteidentisk). Monoton seq, dubblett = ignoreras+räknas, lucka = markeras (aldrig
omräknas, G7), partiell sista rad = väntar, EOF/reconnect = replay från numrerat seq.
Frysning får ENDAST ske från ett giltigt `end` (D3). Ingen socket/rigg; hermetisk."""

from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path

SCHEMA = "ben3d-strom/1"


def canonical(v) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        raise ValueError("flyttal i ström — ben3d-num-as-string/1 gäller")
    if isinstance(v, str):
        out = ['"']
        for c in v:
            o = ord(c)
            if c == '"':
                out.append('\\"')
            elif c == "\\":
                out.append("\\\\")
            elif o == 0x08:
                out.append("\\b")
            elif o == 0x09:
                out.append("\\t")
            elif o == 0x0A:
                out.append("\\n")
            elif o == 0x0C:
                out.append("\\f")
            elif o == 0x0D:
                out.append("\\r")
            elif o < 0x20:
                out.append("\\u%04x" % o)
            else:
                out.append(c)
        out.append('"')
        return "".join(out)
    if isinstance(v, list):
        return "[" + ",".join(canonical(e) for e in v) + "]"
    if isinstance(v, dict):
        keys = sorted(v.keys())
        return "{" + ",".join(canonical(k) + ":" + canonical(v[k]) for k in keys) + "}"
    raise ValueError("okänd typ: %r" % type(v))


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def make_header(stream_id: str, proveniens: dict | None = None) -> dict:
    p = dict(proveniens or {})
    p.setdefault("manifest_sha256", None)  # preliminär under live (D3)
    return {"schema": SCHEMA, "typ": "header", "stream_id": stream_id,
            "seq": 0, "tick_id": "-", "proveniens": p}


def make_tick(stream_id: str, ben_id: str, tick_id: str, seq: int, payload: dict) -> dict:
    return {"schema": SCHEMA, "typ": "tick", "stream_id": stream_id,
            "ben_id": ben_id, "tick_id": tick_id, "seq": seq,
            "payload": payload, "payload_sha256": sha(canonical(payload))}


def make_end(stream_id: str, seq: int, antal_ticks: int, slutrot: str) -> dict:
    return {"schema": SCHEMA, "typ": "end", "stream_id": stream_id, "seq": seq,
            "tick_id": "-", "antal_ticks": antal_ticks, "slutrot": slutrot}


def make_abort(stream_id: str, seq: int, skal: str) -> dict:
    return {"schema": SCHEMA, "typ": "abort", "stream_id": stream_id, "seq": seq,
            "tick_id": "-", "skal": skal}


class StromError(Exception):
    pass


def validate_record(rec: dict) -> None:
    """Schema-/hashvalidering, fail-closed."""
    if rec.get("schema") != SCHEMA:
        raise StromError(f"schema {rec.get('schema')!r} != {SCHEMA}")
    typ = rec.get("typ")
    if typ not in ("header", "tick", "end", "abort"):
        raise StromError(f"okänd typ {typ!r}")
    if "stream_id" not in rec or "seq" not in rec:
        raise StromError("saknar stream_id/seq")
    if not isinstance(rec["seq"], int):
        raise StromError("seq måste vara heltal")
    if typ == "tick":
        if rec.get("tick_id") == "-" or not rec.get("ben_id"):
            raise StromError("tick saknar tick_id/ben_id")
        want = sha(canonical(rec["payload"]))
        if rec.get("payload_sha256") != want:
            raise StromError(f"payload_sha256 {rec.get('payload_sha256')} != {want}")
    else:
        if rec.get("tick_id") != "-":
            raise StromError(f"{typ} ska ha tick_id='-'")


def read_stream(path: str, wait_partial: bool = True):
    """Yield (record, status) för varje rad. Status: ok|dup|gap|partial|bad."""
    seen_seq = set()
    last_seq = 0
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                yield None, "partial" if wait_partial else "bad"
                continue
            try:
                validate_record(rec)
            except StromError as e:
                yield rec, "bad"
                continue
            s = rec["seq"]
            if rec["typ"] == "tick":
                if s in seen_seq:
                    yield rec, "dup"
                else:
                    seen_seq.add(s)
                    if s != last_seq + 1:
                        yield rec, "gap" if s > last_seq else "dup"
                    else:
                        yield rec, "ok"
                    last_seq = max(last_seq, s)
            else:
                yield rec, "ok"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["tail"])
    ap.add_argument("stream", nargs="+")
    args = ap.parse_args()
    if args.cmd == "tail":
        for p in args.stream:
            n_ok = n_dup = n_gap = n_bad = n_partial = 0
            print(f"== {p}")
            for rec, status in read_stream(p):
                if status == "ok":
                    n_ok += 1
                elif status == "dup":
                    n_dup += 1
                elif status == "gap":
                    n_gap += 1
                elif status == "partial":
                    n_partial += 1
                else:
                    n_bad += 1
                    print(f"  FAIL-CLOSED rad: {rec!r}")
            print(f"  ok={n_ok} dup={n_dup} gap={n_gap} partial={n_partial} bad={n_bad}")
            if n_bad:
                return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
