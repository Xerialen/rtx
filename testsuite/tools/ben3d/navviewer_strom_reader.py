#!/usr/bin/env python3
"""navviewer_strom_reader.py — formatläsare för navviewer-sidan (offline).

En ANDRA, oberoende läsare av ben3d-strom/1 (ej samma kod som CLI-läsaren):
konsumerar den numrerade strömmen och returnerar det navviewern behöver — header
(preliminär proveniens), per-tick-records med payload_sha256, samt status
LIVE/OFÖRSEGLAD STRÖM tills ett giltigt end binder slutroten (B6/A6d)."""

from __future__ import annotations
import hashlib, json, re
from pathlib import Path

SCHEMA = "ben3d-strom/1"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _canon(v):
    if v is None: return "null"
    if v is True: return "true"
    if v is False: return "false"
    if isinstance(v, int): return str(v)
    if isinstance(v, float): raise ValueError("flyttal — num-as-string")
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list): return "[" + ",".join(_canon(e) for e in v) + "]"
    if isinstance(v, dict):
        return "{" + ",".join(_canon(k) + ":" + _canon(v[k]) for k in sorted(v)) + "}"
    raise ValueError("okänd typ")


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def consume(path: str) -> dict:
    """Navviewer-konsumtion av strömmen: {stream_id, status, ticks, proveniens}."""
    out = {"stream_id": None, "status": "LIVE/OFÖRSEGLAD STRÖM", "proveniens": None,
           "ticks": [], "slutrot": None, "fel": []}
    last_seq = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                out["fel"].append("partiell sista rad")
                continue
            if rec.get("schema") != SCHEMA:
                out["fel"].append("schema")
                continue
            typ = rec.get("typ")
            if typ == "header":
                out["stream_id"] = rec["stream_id"]
                out["proveniens"] = rec.get("proveniens")
            elif typ == "tick":
                if rec.get("payload_sha256") != _sha(_canon(rec["payload"])):
                    out["fel"].append("payload_sha256")
                    continue
                if rec["seq"] != last_seq + 1:
                    out["fel"].append(f"gap seq {rec['seq']}")
                last_seq = rec["seq"]
                out["ticks"].append({"tick_id": rec["tick_id"], "seq": rec["seq"],
                                     "payload_sha256": rec["payload_sha256"]})
            elif typ == "end":
                if rec["seq"] == last_seq + 1 and rec["antal_ticks"] == len(out["ticks"]) \
                   and HEX64.match(str(rec.get("slutrot", ""))) and not out["fel"]:
                    out["status"] = "FRUSEN"
                    out["slutrot"] = rec["slutrot"]
                else:
                    out["fel"].append("end binder ej sista seq/tickantal/slutrot")
            elif typ == "abort":
                out["status"] = "LIVE/OFÖRSEGLAD STRÖM"  # abort => aldrig frysbar
    return out
