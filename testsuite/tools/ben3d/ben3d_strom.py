#!/usr/bin/env python3
"""ben3d_strom.py — ben3d-strom/1 (D3) + CLI tail/validering.

D3-terminalgrind: abort är PERMANENT terminal (efterföljande end får ALDRIG frysa);
giltigt end är terminalt + unikt, samma stream_id, förbjudet efter abort, binder
exakt föregående seq + tickantal, och slutroten JÄMFÖRS mot den omräknade
ben3d-rot/1 ur header/ticks (inte bara 64-hex-regex). Ingen socket/rigg."""

from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path

SCHEMA = "ben3d-strom/1"


def canonical(v) -> str:
    if v is None: return "null"
    if v is True: return "true"
    if v is False: return "false"
    if isinstance(v, int): return str(v)
    if isinstance(v, float): raise ValueError("flyttal — num-as-string")
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


def stream_rot(proveniens: dict, tick_payloads: list) -> str:
    """Strömmens EGNA slutrot (R2-a): ben3d-strom-rot/1, domän = header + tickposter.
    Får ALDRIG förväxlas med buntarnas D2-rot ben3d-rot/1."""
    return sha(canonical({"schema": "ben3d-strom-rot/1", "proveniens": proveniens, "ticks": tick_payloads}))


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
        if rec.get("payload_sha256") != sha(canonical(rec["payload"])):
            raise StromError("payload_sha256 mismatch")
    else:
        if rec.get("tick_id") != "-":
            raise StromError(f"{typ} ska ha tick_id='-'")
    if typ == "end" and not isinstance(rec.get("antal_ticks"), int):
        raise StromError("end saknar antal_ticks")
    return True


class Stream:
    """Numrerad read-only-ström. Frysbar ENDAST från ett giltigt, terminalt, unikt end
    med korrekt slutrot; abort är permanent terminal (D3)."""

    def __init__(self, path):
        self.path = path
        self.records = []
        self.ticks = []
        self.seen_seq = set()
        self.last_seq = 0
        self.stream_id = None
        self.header_prov = None
        self.aborted = False
        self.ended = False
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
                typ = rec["typ"]
                if self.stream_id is not None and rec["stream_id"] != self.stream_id:
                    self.errors.append("stream_id-byte")
                self.stream_id = rec["stream_id"]
                if self.ended:
                    # varje record efter accepterat end återkallar PERMANENT frysbar status (D3)
                    self.status = "OGILTIG"
                if typ == "header":
                    if self.records:
                        self.errors.append("header ej först/unik")
                    else:
                        self.header_prov = rec.get("proveniens")
                    self.records.append(rec)
                elif typ == "tick":
                    if self.aborted or self.ended:
                        self.errors.append("tick efter terminal")
                        continue
                    s = rec["seq"]
                    if s in self.seen_seq:
                        continue  # dubblett ignoreras (räknas ej som fel)
                    self.seen_seq.add(s)
                    if s != self.last_seq + 1:
                        self.errors.append(f"gap: seq {s} != {self.last_seq + 1}")
                    self.last_seq = s
                    self.ticks.append(rec)
                elif typ == "abort":
                    if self.ended:
                        self.errors.append("abort efter end")
                        # status redan OGILTIG från topp-checken
                    else:
                        self.aborted = True
                        self.status = "LIVE/OFÖRSEGLAD STRÖM"
                    self.records.append(rec)
                elif typ == "end":
                    if self.aborted:
                        self.errors.append("end efter abort — permanent terminal, aldrig frysbar")
                    elif self.ended:
                        self.errors.append("end ej unik")
                    else:
                        rot = stream_rot(self.header_prov or {}, [t["payload"] for t in self.ticks])
                        if (rec["seq"] == self.last_seq + 1 and rec["antal_ticks"] == len(self.ticks)
                                and rec["slutrot"] == rot and not self.errors):
                            self.status = "FRUSEN"
                            self.ended = True
                        else:
                            self.errors.append(f"end binder ej seq/tickantal/slutrot (rot {rot})")
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
