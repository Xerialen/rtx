#!/usr/bin/env python3
"""Hopptraning: agarens tre ring2quad-hopp, ett hopp i taget, 10 forsok per varv.

Facit (agarens ord): ett forsok ar LYCKAT nar boten nar malet utan att falla och
utan att fastna. Tider ar sekundara och bokfors bara som matvarde.

Klasser (obduktionsvokabular):
  lyckad · fall · stall · timeout · fel_mal · start_blockerad

Varje forsok spelas in med egen 20 Hz-tape (origin, mark, fart, rutt, faser) sa
att misslyckanden gar att obducera aven nar ingen terminal handelse kommer.

Riggen: fasttrack-server (spel 27530, kontroll 27980). Ingen annan rigg rors.
"""
from __future__ import annotations

import argparse
import json
import math
import socket
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/xerial/rtx-testsuite/testsuite")
from runner import mpwire  # noqa: E402

HOST = "127.0.0.1"
PORT = 27980
BOT = 1

PREP_HEALTH = 100.0
PREP_ROCKETS = 100.0
RESPAWN_POLL_TIMEOUT = 10.0
RESPAWN_POLL_INTERVAL = 0.15
BLOCK_RADIUS = 40.0
BLOCK_TIMEOUT = 6.0
BLOCK_INTERVAL = 0.3
TAPE_HZ = 20.0

# fall_z: dyker banan under detta z ar forsoket ett FALL. Kalibrerat mot agarens
# egna demon (ring2quad2/3: min_z = 56.0 rakt igenom, dvs. han gar aldrig under
# ringens/quadens plan). Inga avsedda drop finns forregistrerade i dessa hopp.
HOPP = {
    "1": {
        "namn": "ringkanten over gapet till quad",
        "starter": [
            ("syd", [478.0, -515.0, 56.0]),   # ring2quad2, agarens exakta start
            ("nord", [193.0, -45.0, 56.0]),   # ring2quad3, agarens exakta start
        ],
        "mal": [946.0, 334.0, 56.0],          # quad
        "ankomst_r": 56.0,
        "ankomst_dz": 12.0,
        "fall_z": 48.0,
        "budget_s": 12.0,
    },
    "2": {
        "namn": "RA-spawnen ut genom teleporten",
        "starter": [("rarox", [-632.0, -680.0, -16.0])],
        "mal": [224.0, -320.0, 75.0],         # teleporten ut
        "ankomst_r": 64.0,
        "ankomst_dz": 40.0,
        "fall_z": -260.0,
        "budget_s": 15.0,
    },
    "3": {
        "namn": "ringspawnen till quad",
        "starter": [("ringspawn", [224.0, -320.0, 75.0])],
        "mal": [946.0, 334.0, 56.0],
        "ankomst_r": 56.0,
        "ankomst_dz": 12.0,
        "fall_z": 48.0,
        "budget_s": 14.0,
    },
}


class Lab:
    """Framad msgpack-klient mot kontrollkanalen (samma codec som labctl.py)."""

    def __init__(self, host=HOST, port=PORT, timeout=30.0):
        self.sock = socket.create_connection((host, port), timeout)
        self.buf = b""
        self.rid = 0
        self.events = []

    def _send(self, cmd):
        self.rid += 1
        body = mpwire.packb({"id": self.rid, "cmd": cmd})
        self.sock.sendall(struct.pack("<I", len(body)) + body)
        return self.rid

    def _messages(self, deadline):
        while True:
            while len(self.buf) >= 4:
                n = struct.unpack("<I", self.buf[:4])[0]
                if len(self.buf) < 4 + n:
                    break
                msg = mpwire.unpackb(self.buf[4:4 + n])
                self.buf = self.buf[4 + n:]
                yield msg
            left = deadline - time.monotonic()
            if left <= 0:
                return
            self.sock.settimeout(max(left, 0.05))
            try:
                chunk = self.sock.recv(1 << 20)
            except (TimeoutError, socket.timeout):
                return
            if not chunk:
                raise ConnectionError("kontrollkanalen stangde")
            self.buf += chunk

    def request(self, cmd, timeout=10.0):
        rid = self._send(cmd)
        deadline = time.monotonic() + timeout
        for msg in self._messages(deadline):
            if not isinstance(msg, dict):
                continue
            if "Event" in msg:
                self.events.append(msg["Event"])
                continue
            if "Reply" in msg and msg["Reply"].get("id") == rid:
                res = msg["Reply"]["result"]
                if "Ok" in res:
                    ok = res["Ok"]
                    if isinstance(ok, dict) and len(ok) == 1:
                        return next(iter(ok.values()))
                    return ok
                raise RuntimeError(str(res))
        raise TimeoutError(f"inget svar pa {cmd!r} inom {timeout}s")

    def status(self):
        return self.request("Status")

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def hdist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def bot_row(lab, bot):
    for b in lab.status().get("bots", []):
        if b.get("ent") == bot:
            return b
    return None


def ensure_ready(lab, bot):
    deadline = time.monotonic() + RESPAWN_POLL_TIMEOUT
    while True:
        b = bot_row(lab, bot)
        if b and b.get("alive") and (b.get("health") or 0.0) > 0.0:
            break
        if time.monotonic() > deadline:
            raise RuntimeError(f"bot {bot} respawnade inte inom {RESPAWN_POLL_TIMEOUT}s")
        time.sleep(RESPAWN_POLL_INTERVAL)
    lab.request({"Prep": {"bot": bot, "health": PREP_HEALTH, "rockets": PREP_ROCKETS}})


def wait_clear(lab, bot, pos):
    """Vanta tills ingen ANNAN bot star pa startpunkten (de kilar fast puppeten)."""
    deadline = time.monotonic() + BLOCK_TIMEOUT
    while True:
        blocked = False
        for b in lab.status().get("bots", []):
            if b.get("ent") == bot:
                continue
            o = b.get("origin") or pos
            if hdist(o, pos) <= BLOCK_RADIUS and abs(o[2] - pos[2]) <= BLOCK_RADIUS:
                blocked = True
        if not blocked:
            return False
        if time.monotonic() > deadline:
            return True
        time.sleep(BLOCK_INTERVAL)


def hamta_rutt(lab, bot):
    try:
        r = lab.request({"Route": {"bot": bot}}, timeout=3.0)
        return {
            "route_pos": r.get("route_pos"),
            "legs": [{"i": g.get("i"), "link": g.get("link"), "kind": g.get("kind"),
                      "src_cell": g.get("src_cell"), "tgt_cell": g.get("tgt_cell"),
                      "src": [round(v, 1) for v in (g.get("src") or [])],
                      "tgt": [round(v, 1) for v in (g.get("tgt") or [])]}
                     for g in r.get("legs", [])],
        }
    except Exception as exc:
        return {"fel": str(exc)}


def forsok(lab, bot, start, mal, cfg, i, namn):
    lab.events = []
    ensure_ready(lab, bot)
    if wait_clear(lab, bot, start):
        return {"i": i, "start_namn": namn, "start": start, "klass": "start_blockerad", "tape": []}
    lab.request({"Teleport": {"bot": bot, "pos": list(start), "vel": [0.0, 0.0, 0.0]}})
    lab.request({"Goto": {"bot": bot, "pos": list(mal)}})
    rutt0 = hamta_rutt(lab, bot)

    tape = []
    t0 = time.monotonic()
    klass = None
    sista = None
    nasta = t0
    while True:
        nu = time.monotonic() - t0
        if nu >= cfg["budget_s"]:
            klass = "timeout"
            break
        b = bot_row(lab, bot)
        if b is None:
            time.sleep(0.05)
            continue
        sista = b
        o = b.get("origin") or [0, 0, 0]
        tape.append([round(nu, 3), round(o[0], 2), round(o[1], 2), round(o[2], 2),
                     bool(b.get("on_ground")), round(float(b.get("speed") or 0.0), 1),
                     b.get("rj_phase"), b.get("bhop"), (b.get("route") or {}).get("pos"),
                     b.get("order"), round(float(b.get("health") or 0.0), 0)])
        if not b.get("alive"):
            klass = "fall"          # dodsfall i denna geometri = nedslag i grytan
            break
        if o[2] < cfg["fall_z"]:
            klass = "fall"
            break
        if (b.get("on_ground") and nu > 0.4
                and hdist(o, mal) <= cfg["ankomst_r"] and abs(o[2] - mal[2]) <= cfg["ankomst_dz"]):
            klass = "lyckad"
            break
        nasta += 1.0 / TAPE_HZ
        sov = nasta - time.monotonic()
        if sov > 0:
            time.sleep(sov)
        else:
            nasta = time.monotonic()

    tid = round(time.monotonic() - t0, 2)
    stalls = [e["BotStall"] for e in lab.events
              if isinstance(e, dict) and "BotStall" in e and e["BotStall"].get("bot") == bot]
    goto_stall = [e["GotoStall"] for e in lab.events
                  if isinstance(e, dict) and "GotoStall" in e and e["GotoStall"].get("bot") == bot]
    if klass == "timeout" and goto_stall:
        klass = "stall"

    zs = [r[3] for r in tape] or [start[2]]
    farter = [r[5] for r in tape] or [0.0]
    rad = {
        "i": i,
        "start_namn": namn,
        "start": start,
        "mal": mal,
        "klass": klass,
        "tid_s": tid,
        "slut_pos": [round(v, 1) for v in (sista or {}).get("origin", start)],
        "min_z": round(min(zs), 1),
        "max_z": round(max(zs), 1),
        "max_fart": round(max(farter), 1),
        "n_tape": len(tape),
        "rutt0": rutt0,
        "rutt_slut": hamta_rutt(lab, bot),
        "stall_handelser": [{k: s.get(k) for k in ("t", "reason", "cell", "goal_cell", "link", "kind", "speed")}
                            for s in stalls],
        "goto_stall": [{k: s.get(k) for k in ("reason", "cell", "link")} for s in goto_stall],
        "tape": tape,
    }
    try:
        lab.request({"Stop": {"bot": bot}})
    except Exception:
        pass
    return rad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hopp", choices=sorted(HOPP))
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--varv", default="v1")
    ap.add_argument("--ut", default="/home/xerial/hopptraning")
    ap.add_argument("--bot", type=int, default=BOT)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--anteckning", default="")
    a = ap.parse_args()

    cfg = HOPP[a.hopp]
    lab = Lab(port=a.port)
    st = lab.status()
    ident = {k: st.get(k) for k in ("map", "cells", "links", "rj_links")}
    print("graf:", json.dumps(ident), flush=True)

    utd = Path(a.ut) / f"hopp{a.hopp}" / a.varv
    utd.mkdir(parents=True, exist_ok=True)

    rader = []
    starter = cfg["starter"]
    for i in range(a.n):
        namn, start = starter[i % len(starter)]
        rad = forsok(lab, a.bot, start, cfg["mal"], cfg, i + 1, namn)
        rader.append(rad)
        print(f"{i+1:2d}/{a.n} {namn:9s} {rad['klass']:15s} "
              f"tid={rad.get('tid_s')} min_z={rad.get('min_z')} slut={rad.get('slut_pos')} "
              f"stall={sorted({s.get('reason') for s in rad.get('stall_handelser', [])})}", flush=True)

    lyckade = sum(1 for r in rader if r["klass"] == "lyckad")
    klasser = {}
    for r in rader:
        klasser[r["klass"]] = klasser.get(r["klass"], 0) + 1
    per_start = {}
    for r in rader:
        d = per_start.setdefault(r["start_namn"], {"n": 0, "lyckad": 0})
        d["n"] += 1
        d["lyckad"] += int(r["klass"] == "lyckad")

    sammanfattning = {
        "hopp": a.hopp,
        "namn": cfg["namn"],
        "varv": a.varv,
        "anteckning": a.anteckning,
        "graf": ident,
        "facit": {k: cfg[k] for k in ("mal", "ankomst_r", "ankomst_dz", "fall_z", "budget_s")},
        "starter": {n: p for n, p in cfg["starter"]},
        "n": a.n,
        "lyckade": lyckade,
        "klasser": klasser,
        "per_start": per_start,
        "tider_lyckade": sorted(r["tid_s"] for r in rader if r["klass"] == "lyckad"),
    }
    (utd / "forsok.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rader))
    (utd / "sammanfattning.json").write_text(json.dumps(sammanfattning, indent=1) + "\n")
    print("SUMMA:", json.dumps(sammanfattning, ensure_ascii=False), flush=True)
    try:
        lab.request({"Stop": {"bot": a.bot}})
    except Exception:
        pass
    lab.close()


if __name__ == "__main__":
    main()
