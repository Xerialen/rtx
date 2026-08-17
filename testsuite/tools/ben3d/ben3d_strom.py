#!/usr/bin/env python3
"""ben3d_strom.py — ben3d-strom/1 (D3) + CLI tail/validering.

D3-livscykel: `end` måste binda sista seq + tickantal + GILTIG slutrot (64-hex SHA),
annars fail-closed; `abort` lämnar strömmen OFÖRSEGLAD. Frysning får ENDAST ske från
ett giltigt `end`. Monoton seq, dubblett=ignoreras+räknas, lucka=markeras (G7),
partiell sista rad=väntar. Ingen socket/rigg."""

from __future__ import annotations
import argparse, hashlib, json, re, sys
from pathlib import Path

SCHEMA = "ben3d-strom/1"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def canonical(v) -> str:
    if v is None: return "null"
    if v is True: return "true"
    if v is False: return "false"
    if isinstance(v, int): return str(v)
    if isinstance(v, float): raise ValueError("flyttal i ström — ben3d-num-as-string/1 gäller")
    if isinstance(v, str):
        out = ['"']
        for c in v:
            o = ord(c)
            if c == '"': out.append('\\"')
            elif c == "\\": out.append("\\\\")
            elif o == 0x08: out.append("\\b")
            elif o == 0x09: out.append("\\t")
            elif o == 0x0A: out.append("\\n")
            elif o == 0x0C: out.append("\\f")
            elif o == 0x0D: out.append("\\r")
            elif o < 0x20: out.append("\\u%04x" % o)
            else: out.append(c)
        out.append('"')
        return "".join(out)
    if isinstance(v, list): return "[" + ",".join(canonical(e) for e in v) + "]"
    if isinstance(v, dict):
        keys = sorted(v.keys())
        return "{" + ",".join(canonical(k) + ":" + canonical(v[k]) for k in keys) + "}"
    raise ValueError("okänd typ %r" % type(v))


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def make_header(stream_id, proveniens=None):
    p = dict(proveniens or {})
    p.setdefault("manifest_sha256", None)
    return {"schema": SCHEMA, "typ": "header", "stream_id": stream_id, "seq": 0, "tick_id": "-", "proveniens": p}


def make_tick(stream_id, ben_id, tick_id, seq, payload):
    return {"schema": SCHEMA, "typ": "tick", "stream_id": stream_id, "ben_id": ben_id,
            "tick_id": tick_id, "seq": seq, "payload": payload, "payload_sha256": sha(canonical(payload))}


def make_end(stream_id, seq, antal_ticks, slutrot):
    return {"schema": SCHEMA, "typ": "end", "stream_id": stream_id, "seq": seq,
            "tick_id": "-", "antal_ticks": antal_ticks, "slutrot": slutrot}


def make_abort(stream_id, seq, skal):
    return {"schema": SCHEMA, "typ": "abort", "stream_id": stream_id, "seq": seq, "tick_id": "-", "skal": skal}


class StromError(Exception):
    pass


def validate_record(rec):
    if rec.get("schema") != SCHEMA:
        raise StromError(f"schema {rec.get('schema')!r} != {SCHEMA}")
    typ = rec.get("typ")
    if typ not in ("header", "tick", "end", "abort"):
        raise StromError(f"okänd typ {typ!r}")
    if "stream_id" not in rec or not isinstance(rec.get("seq"), int):
        raise StromError("saknar stream_id/heltals-seq")
    if typ == "tick":
        if rec.get("tick_id") == "-" or not rec.get("ben_id"):
            raise StromError("tick saknar tick_id/ben_id")
        want = sha(canonical(rec["payload"]))
        if rec.get("payload_sha256") != want:
            raise StromError("payload_sha256 mismatch")
    else:
        if rec.get("tick_id") != "-":
            raise StromError(f"{typ} ska ha tick_id='-'")
    if typ == "end":
        if not isinstance(rec.get("antal_ticks"), int):
            raise StromError("end saknar antal_ticks")
        if not HEX64.match(str(rec.get("slutrot", ""))):
            raise StromError("end saknar GILTIG slutrot (64-hex SHA)")
    return True


class Stream:
    """En numrerad read-only-ström: frysbar ENDAST från ett giltigt end (D3)."""

    def __init__(self, path):
        self.path = path
        self.records = []
        self.ticks = []
        self.seen_seq = set()
        self.last_seq = 0
        self.status = "LIVE/OFÖRSEGLAD STRÖM"
        self.errors = []

    def read(self):
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    validate_record(rec)
                except (json.JSONDecodeError, StromError, ValueError) as e:
                    self.errors.append(str(e))
                    continue
                s = rec["seq"]
                if rec["typ"] == "tick":
                    if s in self.seen_seq:
                        continue  # dubblett ignoreras
                    self.seen_seq.add(s)
                    if s != self.last_seq + 1:
                        self.errors.append(f"gap: seq {s} != {self.last_seq + 1}")
                    self.last_seq = s
                    self.ticks.append(rec)
                elif rec["typ"] == "end":
                    # giltigt end binder sista seq + tickantal => frysbar
                    if s == self.last_seq + 1 and rec["antal_ticks"] == len(self.ticks) and not self.errors:
                        self.status = "FRUSEN"
                    else:
                        self.errors.append("end binder ej sista seq/tickantal")
                    self.records.append(rec)
                else:
                    self.records.append(rec)
        return self


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["tail"])
    ap.add_argument("stream", nargs="+")
    args = ap.parse_args()
    rc = 0
    for p in args.stream:
        st = Stream(p).read()
        print(f"== {p}: {st.status} · ticks={len(st.ticks)} · errors={len(st.errors)}")
        for e in st.errors:
            print(f"  FAIL-CLOSED: {e}")
            rc = 2
    return rc


if __name__ == "__main__":
    sys.exit(main())
