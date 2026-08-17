#!/usr/bin/env python3
"""navviewer_strom_reader.py — formatläsare för navviewer-sidan (offline).

Oberoende läsare av ben3d-strom/1 (egen konsumtionsloop, inte samma kod som
CLI-läsaren). Samma D3-terminalgrind: abort permanent terminal, end terminalt +
unikt + förbjudet efter abort + binder exakt seq/tickantal + slutrot JÄMFÖRS mot
omräknad ben3d-rot/1 ur header/ticks (LIVE/OFÖRSEGLAD STRÖM tills giltigt end)."""

from __future__ import annotations
import json
from pathlib import Path

from ben3d_strom import SCHEMA, canonical, sha, stream_rot


def consume(path: str) -> dict:
    out = {"stream_id": None, "status": "LIVE/OFÖRSEGLAD STRÖM", "proveniens": None,
           "ticks": [], "slutrot": None, "fel": []}
    last_seq = 0
    aborted = False
    ended = False
    seen = set()
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
            if out["stream_id"] is not None and rec.get("stream_id") != out["stream_id"]:
                out["fel"].append("stream_id-byte")
            out["stream_id"] = rec.get("stream_id")
            typ = rec.get("typ")
            if typ == "header":
                if out["proveniens"] is not None or out["ticks"]:
                    out["fel"].append("header ej först/unik")
                out["proveniens"] = rec.get("proveniens")
            elif typ == "tick":
                if aborted or ended:
                    out["fel"].append("tick efter terminal")
                    continue
                if rec.get("payload_sha256") != sha(canonical(rec["payload"])):
                    out["fel"].append("payload_sha256")
                    continue
                if rec["seq"] in seen:
                    continue
                seen.add(rec["seq"])
                if rec["seq"] != last_seq + 1:
                    out["fel"].append(f"gap seq {rec['seq']}")
                last_seq = rec["seq"]
                out["ticks"].append({"tick_id": rec["tick_id"], "seq": rec["seq"],
                                     "payload_sha256": rec["payload_sha256"], "payload": rec["payload"]})
            elif typ == "abort":
                aborted = True
                out["status"] = "LIVE/OFÖRSEGLAD STRÖM"
            elif typ == "end":
                if aborted:
                    out["fel"].append("end efter abort — permanent terminal, aldrig frysbar")
                elif ended:
                    out["fel"].append("end ej unik")
                else:
                    rot = stream_rot(out["proveniens"] or {}, [t["payload"] for t in out["ticks"]])
                    if (rec["seq"] == last_seq + 1 and rec["antal_ticks"] == len(out["ticks"])
                            and rec.get("slutrot") == rot and not out["fel"]):
                        out["status"] = "FRUSEN"
                        out["slutrot"] = rot
                        ended = True
                    else:
                        out["fel"].append(f"end binder ej seq/tickantal/slutrot (rot {rot})")
    return out
