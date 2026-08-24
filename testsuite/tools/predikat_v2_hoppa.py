#!/usr/bin/env python3
"""Hopptraning PREDIKAT-V2: agarens tre ring2quad-hopp, ett hopp i taget.

V2 av predikatet i `hoppa.py` (v1 sha 44a815ad1d485020ccd64277b3e0d899e57778155317bbcf31f946a0755c84d2).
Bygger pa order WORK_LOGS/2026-08-24-order-kodaren-predikat-v2.md och
adjudiceringen A3/A8/A12/A13/F16. De sju andringarna, var och en markt
[V2-n] i koden:

  [V2-1] Markmask: FL_ONGROUND=512 var en dod konstant (satt i 266 795 av
         266 795 registratorbilder). Markbiten ar bit 2. Markkravet galler
         den DOMDA bilden.
  [V2-2] Malidentitet + startidentitet + obligatorisk passagepunkt (avfarten).
  [V2-3] Kontinuitetsvakt: max MAX_STEG_U per registratorbild.
  [V2-4] Bindande hojdklausul: den domda bilden ar landningsbilden vid disken
         (landning_dz), inte vilken bild som helst inom hojdtoleransen.
  [V2-5] Egen `dod`-klass, skild fran `fall`.
  [V2-6] Facit-sida fastnadpredikat (instangning inom FASTNAD_R over
         FASTNAD_N_S), provat FORE `timeout`. GotoStall/BotStall degraderas
         till bekraftelse och klassar inte langre.
  [V2-7] PREP_ROCKETS=0 for r2q; band dar rj_phase lamnar Idle -> `fel_fraga`.

Klasser (obduktionsvokabular v2):
  lyckad · fall · dod · stall · timeout · fel_mal · fel_fraga · ogiltig ·
  start_blockerad

Domen ar EN ren funktion, `dom_band`, som kors identiskt live och offline.
Den live-loopen gor ar att spela in bandet och avgora NAR forsoket slutar.

Riggen: fasttrack-server (spel 27530, kontroll 27980). Ingen annan rigg rors.
"""
from __future__ import annotations

import argparse
import hashlib
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
# [V2-7] r2q ar inget raketjump-hopp. 0 raketer som forval; hopp 2 (RA-spawnen)
# behaller sitt gamla varde via cfg["prep_rockets"] sa dess beteende inte andras.
PREP_ROCKETS = 0.0
RESPAWN_POLL_TIMEOUT = 10.0
RESPAWN_POLL_INTERVAL = 0.15
BLOCK_RADIUS = 40.0
BLOCK_TIMEOUT = 6.0
BLOCK_INTERVAL = 0.3
TAPE_HZ = 20.0

# [V2-1] Empirisk markbit. Uppmatt over hela r2q-korpusen (266 795
# registratorbilder, 1216 band): P(vz==0 | bit2) = 131724/133418 = 0,987 och
# P(vz==0 | ej bit2) = 674/133377 = 0,005. FL_ONGROUND=512 ar satt i ALLA
# 266 795 bilder och kan darfor inte falla nagot. `leg` duger inte som
# markindikator (P(vz==0 | leg=="Walk") = 70580/128453 = 0,55) och `air` ar en
# raknare, aldrig 0 (0 av 266 795 bilder). Darfor bit 2, inte leg/air.
FL_MARK = 2
FL_ONGROUND_DOD = 512   # bevaras bara som dokumentation av v1:s doda konstant

# [V2-3] Kontinuitetstak per registratorbild (monster: ra_kanon.py:157,185-186).
# Uppmatt storsta forflyttning mellan tva pa varandra foljande bilder FORE
# domen over hela korpusen: 7,506 u (1216 band). 300 u ar 40x marginal och
# ligger under det enda verkliga hoppet i materialet (657,2 u = parkera()-
# teleporten tillbaka till start EFTER en falldom, dom_i=382/389).
MAX_STEG_U = 300.0

# [V2-2] Avfarten over klyftan. Radie i paritet med ankomstdisken (56 u) och
# hojdtolerans i paritet med ankomst_dz (12 u). Uppmatt minsta dxy till
# avfarten: OVER 0,14-9,38 u, TILLBAKA 30,56-32,76 u; 1216 av 1216 band
# passerar med bade dxy<=56 och |dz|<=12. Marginal mot varsta band: 1,71x.
AVFART = [454.7, 153.3, 56.0]
AVFART_R = 56.0
AVFART_DZ = 12.0

# [V2-2] Startidentitet: bandets forsta registratorbild maste ligga vid en av
# hoppets egna startpunkter. Uppmatt avstand over korpusen: 0,03-0,57 u.
START_R = 56.0

# [V2-6] Fastnadpredikat. "Fastnad" = boten holl sig inom FASTNAD_R fran sin
# egen position vid fonstrets borjan i FASTNAD_N_S sekunder i strack.
# FASTNAD_R = 64 u ur ordern/A12. FASTNAD_N_S = 3,0 s: langsta LEGITIMA
# instangning inom 64 u i hela korpusen ar 1,227 s (over/fall) respektive
# 1,121 s (over/lyckad) och 0,242 s (tillbaka/lyckad), matt i full
# registratortakt. 3,0 s ar 2,4x den siffran och samtidigt 4x under
# budget_s=12 s, sa predikatet hinner falla ett verkligt stillastaende band
# med ~9 s marginal innan budgeten tar slut.
#
# UNDERKANT och OVERKANT — bada ar AVSIKTLIGA och ska last ihop (QA-dom
# 2026-08-24 §4.2, villkor 4.2b). Konstanterna definierar TVA granser:
#
#   underkant  FASTNAD_N_S = 3,0 s   : ett band far vara instangt i upp till
#                                      3,0 s utan att kallas fastnat. Under
#                                      1,3 s hade legitima band fallts
#                                      (korpusens max ar 1,227 s).
#   OVERKANT   FASTNAD_R/FASTNAD_N_S : = 64/3,0 = ~21,3 u/s. VARJE band vars
#                                      framdrift understiger ~21,3 u/s i
#                                      genomsnitt doms `stall` — aven en bot
#                                      som ror sig stadigt men langsamt.
#                                      En bot i 12 u/s doms fastnad; en bot i
#                                      30 u/s gor det inte.
#
# Overkanten ar ALLTSA en definition av vad "fastnad" betyder i det har
# facitet: "ror sig langsammare an ~21,3 u/s i 3 s i strack" — inte enbart
# "star helt still". Det ar avsiktligt: agarkriteriet ar "inte falla, inte
# fastna", och en bot som kryper 12 u/s mot ett mal 500 u bort hinner aldrig
# fram inom budget_s=12 s. Korpusens verkliga band gar ~500 u pa ~4,8 s
# ~ 104 u/s, dvs. ~5x over overkanten. Vill man tillata langsam framdrift ar
# det FASTNAD_R som ska sankas, inte FASTNAD_N_S som ska hojas — N styr hur
# lange man far vara instangd, R styr hur langsamt man far ga.
FASTNAD_R = 64.0
FASTNAD_N_S = 3.0

# [V2-live] Efterspelning fore registratorn lases av. Loopens brytvillkor
# ligger pa avlasarens grova disk (v1:s), men v2:s dom ligger senare i bandet:
# uppmatt over 1188 band ar skillnaden median 0,100 s och som mest 0,170 s
# (OVER). Utan efterspelning ligger v2:s dom pa bandets ALLRA SISTA bild i
# 388 av 1188 band (32,7 %), och i 661 band (55,6 %) finns hogst en bild kvar.
# En bild kortare i en levande korning hade gjort de 388 banden `fel_mal`.
# (QA-dom 2026-08-24 §7, villkor 7.1.)
#
# DIMENSIONERING (matt, inte antagen). Svansen i dag ar enbart RPC-latensen i
# Audit-anropet: uppmatt svans_bilder ar 0-6 bilder, VARSTA FALL 0. En paus pa
# EFTERSPELNING_S fore avlasningen lagger till de bilder registratorn hinner
# skriva under pausen. Hur manga det ar i VARSTA fall ar mott over hela
# korpusen (265 579 mellanrum, varje position i varje band, bara fonster som
# ryms helt i bandet):
#
#     fonster   varsta antal bilder   medel
#      0,20 s          13              14,6
#      0,23 s          15              16,7
#      0,30 s          20              22,1
#      0,40 s          28              29,7      <- valt
#      0,50 s          35              37,1
#
# Registratorn gar i median 96,4 Hz (p50-mellanrum 0,0104 s), inte 75 Hz.
# 0,40 s ger alltsa >= 28 bilder efter den domda bilden aven i korpusens
# varsta fonster — 1,9x kravet SVANS_MINST = 15 (paritet med ra_kanons
# ">= 15 bilder i strack"). 0,23 s hade racht men med noll marginal.
#
# OBS — rakna INTE marginalen ur percentiler pa enskilda mellanrum. Det ger
# 10,5 bilder vid p99 och ser ut att underkanna 0,40 s; den rakningen antar
# att de 15 varsta mellanrummen kommer i rad, vilket de aldrig gor. Fonster-
# matningen ovan ar den ratta grunden (`fonstermatning.py`).
#
# KOSTNAD, utskriven for agarbeslut: 0,40 s per LYCKAT forsok. Entimmen gjorde
# 1136 forsok pa 3600 s (~3,17 s/forsok); pausen lagger till ~454 s ~ 7,6 min,
# dvs. ~13 % farre forsok per timme. Vill man ha fler forsok ar 0,23 s
# nedre gransen som fortfarande klarar 15 bilder — men utan marginal.
# Pausen gors INTE efter fall: dar ar boten redan parkerad och bandet kapat.
EFTERSPELNING_S = 0.40
SVANS_MINST = 15

# fall_z: dyker banan under detta z ar forsoket ett FALL. Kalibrerat mot agarens
# egna demon (ring2quad2/3: min_z = 56.0 rakt igenom, dvs. han gar aldrig under
# ringens/quadens plan). Inga avsedda drop finns forregistrerade i dessa hopp.
#
# [V2-2] mal_id: malets identitet. Ett band far bara domas mot sitt EGET mal.
# [V2-4] landning_dz: den domda bilden maste ligga pa malets markplan.
#        Uppmatt |z - mal_z| pa den domda bilden under bit 2: 0,03 u i
#        1188 av 1188 lyckade band. 2,0 u ar 66x marginal. None = av
#        (hopp 2 ar en teleportankomst, ingen landning).
HOPP = {
    "1": {
        "namn": "ringkanten over gapet till quad",
        "starter": [
            ("syd", [478.0, -515.0, 56.0]),   # ring2quad2, agarens exakta start
            ("nord", [193.0, -45.0, 56.0]),   # ring2quad3, agarens exakta start
        ],
        "mal": [946.0, 334.0, 56.0],          # quad
        "mal_id": "quad",
        "ankomst_r": 56.0,
        "ankomst_dz": 12.0,
        "landning_dz": 2.0,
        "avfart": AVFART,
        "fall_z": 48.0,
        "budget_s": 12.0,
        "prep_rockets": 0.0,
        "krav_rj_idle": True,
    },
    "1f": {
        "namn": "ring2quad, tre etablerade vinklar (F-serien)",
        "starter": [
            ("syd", [478.0, -515.0, 56.0]),        # ring2quad2
            ("nord", [193.0, -45.0, 56.0]),        # ring2quad3
            ("ringitemet", [240.0, -32.0, 56.0]),  # Ring of Shadows
        ],
        "mal": [946.0, 334.0, 56.0],
        "mal_id": "quad",
        "ankomst_r": 56.0,
        "ankomst_dz": 12.0,
        "landning_dz": 2.0,
        "avfart": AVFART,
        "fall_z": 48.0,
        "budget_s": 12.0,
        "prep_rockets": 0.0,
        "krav_rj_idle": True,
    },
    "4": {
        "namn": "TILLBAKAHOPPET: quad over klyftan till ringen",
        "starter": [("quad", [946.0, 334.0, 56.0])],
        "mal": [438.0, 142.0, 56.0],
        "mal_id": "ringkanten",
        "ankomst_r": 56.0,
        "ankomst_dz": 12.0,
        "landning_dz": 2.0,
        "avfart": AVFART,
        "fall_z": 48.0,
        "budget_s": 12.0,
        "prep_rockets": 0.0,
        "krav_rj_idle": True,
    },
    "2": {
        "namn": "RA-spawnen ut genom teleporten",
        "starter": [("rarox", [-632.0, -680.0, -16.0])],
        "mal": [224.0, -320.0, 75.0],         # teleporten ut
        "mal_id": "teleporten_ut",
        "ankomst_r": 64.0,
        "ankomst_dz": 40.0,
        "landning_dz": None,     # teleportankomst, ingen landning att binda
        "avfart": None,          # inget gap att passera
        "fall_z": -260.0,
        "budget_s": 15.0,
        "prep_rockets": 100.0,   # ororda forutsattningar for icke-r2q-hoppet
        "krav_rj_idle": False,
    },
    "3": {
        "namn": "ringspawnen (Ring of Shadows) till quad",
        # Agarens ord: ringspawnen = RING-ITEMETS plats, inte teleportavslappet.
        # dm3.bsp entitetslump: item_artifact_invisibility origin "240 -32 56".
        # Narmaste stabara cell [224,-32,56]; samma lump ger quaditemet [952,296,56].
        "starter": [("ringitemet", [240.0, -32.0, 56.0])],
        "mal": [946.0, 334.0, 56.0],
        "mal_id": "quad",
        "ankomst_r": 56.0,
        "ankomst_dz": 12.0,
        "landning_dz": 2.0,
        "avfart": AVFART,
        "fall_z": 48.0,
        "budget_s": 14.0,
        "prep_rockets": 0.0,
        "krav_rj_idle": True,
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


def ensure_ready(lab, bot, cfg=None):
    deadline = time.monotonic() + RESPAWN_POLL_TIMEOUT
    while True:
        b = bot_row(lab, bot)
        if b and b.get("alive") and (b.get("health") or 0.0) > 0.0:
            break
        if time.monotonic() > deadline:
            raise RuntimeError(f"bot {bot} respawnade inte inom {RESPAWN_POLL_TIMEOUT}s")
        time.sleep(RESPAWN_POLL_INTERVAL)
    # [V2-7] raketer per hopp, forval 0
    rockets = PREP_ROCKETS if cfg is None else cfg.get("prep_rockets", PREP_ROCKETS)
    lab.request({"Prep": {"bot": bot, "health": PREP_HEALTH, "rockets": float(rockets)}})


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


AUDIT_FALT = ("t", "origin", "vel", "speed", "peak", "bhop", "hops", "leg", "runup", "wp", "lip",
              "cell", "target", "route_goal", "route_len", "route_pos", "band", "frozen",
              "air", "posture", "gate", "goal_cell", "commit", "flags")


# ---------------------------------------------------------------- predikat v2

def pa_mark(f):
    """[V2-1] Markkontakt i en registratorbild.

    Bit 2 om flaggfaltet finns, annars vz == 0. v1 anvande bit 512, som ar satt
    i varenda bild i hela korpusen och darfor aldrig kunde falla nagot.
    """
    fl = f.get("flags")
    if isinstance(fl, int):
        return bool(fl & FL_MARK)
    v = f.get("vel") or [0.0, 0.0, 1.0]
    return abs(v[2]) < 1e-6


def mal_identitet_ok(mal, cfg, tol=0.5):
    """[V2-2] Bandet far bara domas mot hoppets EGET mal.

    v1 godkande alla 576 OVER-band mot TILLBAKA-malet [438,142,56], eftersom
    OVER-banan passerar ringkanten pa vag ut. Predikatet kande inte igen malet.
    """
    m = cfg.get("mal")
    if not m or not mal or len(mal) < 3:
        return False
    return math.dist(list(mal)[:3], list(m)[:3]) <= tol


def start_identitet_ok(audit, cfg, r=START_R):
    """[V2-2] Bandets forsta registratorbild maste ligga vid en av hoppets
    egna startpunkter. Binder bandet till riktningen oberoende av vilket mal
    anroparen skickar in."""
    starter = [p for _n, p in (cfg.get("starter") or [])]
    if not starter:
        return True
    for f in audit:
        o = f.get("origin") or []
        if len(o) >= 3:
            return min(math.dist(o, s) for s in starter) <= r
    return False


def _bedom(audit, mal, cfg):
    """Som `bedom_ur_registrator`, men returnerar ocksa den DOMDA BILDENS
    index i `audit` (None nar ingen bild bar domen). Indexet behovs for att
    mata svansen — se EFTERSPELNING_S."""
    if not audit:
        return None, None, None
    # [V2-2] identitetsgrindar fore allt annat
    if not mal_identitet_ok(mal, cfg):
        return None, None, None
    if not start_identitet_ok(audit, cfg):
        return None, None, None

    t0 = audit[0]["t"]
    forra = None
    av = cfg.get("avfart")
    passerat_avfart = av is None          # inget gap att passera -> uppfyllt
    landning_dz = cfg.get("landning_dz")
    for i, f in enumerate(audit):
        o = f.get("origin") or []
        if len(o) < 3:
            continue
        dt = f["t"] - t0
        # [V2-3] kontinuitetsvakt
        if forra is not None and math.dist(o, forra) > MAX_STEG_U:
            return "ogiltig", round(dt, 2), i
        forra = o
        if o[2] < cfg["fall_z"]:
            return "fall", round(dt, 2), i
        # [V2-2] obligatorisk passagepunkt
        if not passerat_avfart:
            if (hdist(o, av) <= cfg.get("avfart_r", AVFART_R)
                    and abs(o[2] - av[2]) <= cfg.get("avfart_dz", AVFART_DZ)):
                passerat_avfart = True
        # dt > 0.4: bilderna precis efter teleporten till startpunkten far
        # inte bara domen. Utan den kan ett hopp vars mal ligger vid
        # startpunkten domas `lyckad` pa bild noll, innan boten rort sig.
        # (NK12 halller grinden levande; QA-dom 2026-08-24 villkor 3.3.)
        if (dt > 0.4
                and pa_mark(f)                       # [V2-1] mark pa DOMD bild
                and passerat_avfart                  # [V2-2]
                and hdist(o, mal) <= cfg["ankomst_r"]
                and abs(o[2] - mal[2]) <= cfg["ankomst_dz"]
                # [V2-4] landningsbindning: pa malets markplan, inte ett svep
                and (landning_dz is None or abs(o[2] - mal[2]) <= landning_dz)):
            return "lyckad", round(dt, 2), i
    return None, None, None


def bedom_ur_registrator(audit, mal, cfg):
    """Facitets predikat v2, last pa motorns egna bilder.

    Returnerar (klass, tid) dar klass ar "lyckad", "fall", "ogiltig" eller None.
    Fallet doms fore ankomsten. Bilderna gas igenom i ordning, sa en terminal
    dom (fall) som intraffar fore en diskontinuitet returneras fore den.
    """
    klass, tid, _i = _bedom(audit, mal, cfg)
    return klass, tid


def punkter_ur_audit(audit):
    ut = []
    if not audit:
        return ut
    t0 = audit[0].get("t", 0.0)
    for f in audit:
        o = f.get("origin") or []
        if len(o) >= 3:
            ut.append((f.get("t", 0.0) - t0, o[0], o[1], o[2]))
    return ut


def punkter_ur_tape(tape):
    ut = []
    for t in tape or ():
        if isinstance(t, (list, tuple)) and len(t) >= 4:
            ut.append((t[0], t[1], t[2], t[3]))
    return ut


def fastnad_tid(punkter, n_s=FASTNAD_N_S, r=FASTNAD_R):
    """[V2-6] Facit-sida fastnadpredikat.

    Returnerar tiden da boten forst har hallit sig inom `r` fran sin egen
    position vid fonstrets borjan i `n_s` sekunder i strack — annars None.
    Matt pa registratorns bilder, inte pa motorns egna stall-handelser.
    """
    n = len(punkter)
    for i in range(n):
        k = i
        while k + 1 < n and math.dist(punkter[k + 1][1:], punkter[i][1:]) <= r:
            k += 1
            if punkter[k][0] - punkter[i][0] >= n_s:
                return round(punkter[k][0], 2)
    return None


def dom_ur_tape(tape, mal, cfg):
    """Avlasarens (20 Hz) dom. For grov for att avgora OM malet natts — den far
    bara avgora NAR forsoket slutar. [V2-5] skiljer `dod` fran `fall`."""
    sista_nu = 0.0
    for t in tape or ():
        if not isinstance(t, (list, tuple)) or len(t) < 5:
            continue
        nu = t[0]
        sista_nu = nu
        o = [t[1], t[2], t[3]]
        on_ground = bool(t[4])
        halsa = t[10] if len(t) > 10 else None
        if nu >= cfg["budget_s"]:
            return "timeout", round(nu, 2)
        # [V2-5] dod skiljs fran fall
        if halsa is not None and float(halsa) <= 0.0:
            return "dod", round(nu, 2)
        if o[2] < cfg["fall_z"]:
            return "fall", round(nu, 2)
        if (on_ground and nu > 0.4
                and hdist(o, mal) <= cfg["ankomst_r"]
                and abs(o[2] - mal[2]) <= cfg["ankomst_dz"]):
            return "lyckad", round(nu, 2)
    return "timeout", round(sista_nu, 2)


def dom_band(tape, audit, mal, cfg, goto_stall=(), stall_handelser=()):
    """DOMEN. Ren funktion — identisk live och offline. Returnerar en dict."""
    ut = {
        "klass": None,
        "tid_s": None,
        "fastnad_vid_s": None,
        "stall_bekraftad_av_motorn": bool(goto_stall) or bool(stall_handelser),
        # svans_bilder: antal registratorbilder EFTER den domda bilden.
        # Hopparens levande provrunda ska visa svans_bilder >= SVANS_MINST.
        "svans_bilder": None,
        "svans_ok": None,
        "predikat": "v2",
    }
    if not tape and not audit:
        ut["klass"] = "start_blockerad"
        return ut
    # [V2-7] raketfas ar fel fraga for r2q
    if cfg.get("krav_rj_idle", True):
        for t in tape or ():
            if isinstance(t, (list, tuple)) and len(t) > 6 and t[6] not in (None, "Idle"):
                ut["klass"] = "fel_fraga"
                ut["rj_phase"] = t[6]
                return ut

    klass, tid = dom_ur_tape(tape, mal, cfg)
    reg_klass, reg_tid, reg_i = _bedom(audit, mal, cfg)
    if reg_i is not None:
        ut["svans_bilder"] = len(audit) - 1 - reg_i
        ut["svans_ok"] = ut["svans_bilder"] >= SVANS_MINST
    if reg_klass is not None and klass != reg_klass:
        klass, tid = reg_klass, reg_tid
    elif reg_klass is None and klass == "lyckad":
        # Avlasaren sag en ankomst registratorn inte bekraftar — registratorn galler.
        klass = "fel_mal"

    # [V2-6] fastnad provas FORE timeout far sta kvar. GotoStall klassar inte.
    if klass == "timeout":
        pkt = punkter_ur_audit(audit) or punkter_ur_tape(tape)
        f_t = fastnad_tid(pkt, FASTNAD_N_S, FASTNAD_R)
        ut["fastnad_vid_s"] = f_t
        if f_t is not None:
            klass, tid = "stall", f_t
    ut["klass"] = klass
    ut["tid_s"] = tid
    return ut


# ------------------------------------------------------------------- live-del

def audit_nu(lab, bot):
    """Serverklockan just nu, last ur flygregistratorn (ingen egen klocka)."""
    try:
        fr = lab.request({"Audit": {"bot": bot, "lines": 1}}, timeout=5.0).get("frames") or []
        return fr[-1]["t"] if fr else None
    except Exception:
        return None


def audit_slice(lab, bot, t_fran, t_till=None):
    """Hela forsokets flygregistrator: motorns EGNA celler, aldrig xyz->cell.

    [V2-live] `t_till` kapar bandet vid FORSOKETS SLUT. Utan kapning slapar
    bilder som inte tillhor forsoket med in — framfor allt `parkera()`-
    teleporten tillbaka till startpunkten efter ett fall, som i A/B-20 ger ett
    steg pa 657,176 u (bildindex 385 av 389 i A/over/attempt_01). I dag baras
    det av att falldomen kommer fore diskontinuiteten i sekvensen, vilket
    galler i 28 av 28 fall-band — men det ar en ordningsberoende garanti, inte
    en konstruktion. Kapningen tar bort beroendet. (QA-dom 2026-08-24 §5,
    villkor 5.1.)
    """
    try:
        fr = lab.request({"Audit": {"bot": bot, "lines": 2000}}, timeout=20.0).get("frames") or []
    except Exception:
        return []
    if t_fran is not None:
        fr = [f for f in fr if f.get("t", 0.0) >= t_fran]
    if t_till is not None:
        fr = [f for f in fr if f.get("t", 0.0) <= t_till]
    ut = []
    for f in fr:
        r = {k: f.get(k) for k in AUDIT_FALT}
        r["origin"] = [round(v, 2) for v in (f.get("origin") or [])]
        r["vel"] = [round(v, 1) for v in (f.get("vel") or [])]
        for k in ("speed", "peak", "runup", "wp", "lip", "air"):
            if isinstance(r.get(k), float):
                r[k] = round(r[k], 2)
        ut.append(r)
    return ut


def parkera(lab, bot, pos):
    """Stall boten pa `pos` och HALL den dar. Aldrig fritt strov nar riggen visas."""
    try:
        lab.request({"Teleport": {"bot": bot, "pos": [float(v) for v in pos], "vel": [0.0, 0.0, 0.0]}})
        lab.request({"Hold": {"bot": bot}})
    except Exception:
        pass


def forsok(lab, bot, start, mal, cfg, i, namn):
    lab.events = []
    ensure_ready(lab, bot, cfg)
    if wait_clear(lab, bot, start):
        return {"i": i, "start_namn": namn, "start": start, "klass": "start_blockerad", "tape": []}
    lab.request({"Teleport": {"bot": bot, "pos": list(start), "vel": [0.0, 0.0, 0.0]}})
    lab.request({"Goto": {"bot": bot, "pos": list(mal)}})
    # Fonstret till flygregistratorn oppnas EFTER teleporten, annars slapar
    # foregaende forsoks slut med in i bandet.
    t_audit0 = audit_nu(lab, bot)
    rutt0 = hamta_rutt(lab, bot)

    tape = []
    t0 = time.monotonic()
    sista = None
    nasta = t0
    brutet = None
    t_slut = None
    while True:
        nu = time.monotonic() - t0
        if nu >= cfg["budget_s"]:
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
        if brutet is None and (not b.get("alive") or o[2] < cfg["fall_z"]):
            # AGARREGEL: ramlar boten ner ar forsoket FAIL DIREKT. Bryt pa stallet,
            # resetta, nytt forsok. Ingen aterklattring, ingen vantan pa timeout.
            # [V2-5] doden och fallet skiljs at i domen, inte har.
            brutet = "fall_eller_dod"
            # [V2-live] serverklockan las FORE parkera(), sa att teleporten
            # tillbaka till start kan kapas bort ur bandet. Agarregeln star
            # kvar: parkeringen sker fortfarande omedelbart.
            t_slut = audit_nu(lab, bot)
            parkera(lab, bot, start)   # FAIL direkt: tillbaka till startpunkten pa stallet
            break
        if brutet is None and (b.get("on_ground") and nu > 0.4
                               and hdist(o, mal) <= cfg["ankomst_r"]
                               and abs(o[2] - mal[2]) <= cfg["ankomst_dz"]):
            brutet = "ankomst"
            break
        nasta += 1.0 / TAPE_HZ
        sov = nasta - time.monotonic()
        if sov > 0:
            time.sleep(sov)
        else:
            nasta = time.monotonic()

    # [V2-live] Efterspelning: loopen bryter pa avlasarens grova disk, men v2:s
    # dom ligger upp till 0,170 s senare i registratorbandet. Utan den har
    # pausen ligger domen pa bandets sista bild i 32,7 % av de lyckade banden.
    # Pausen behovs INTE efter ett fall — dar ar boten redan parkerad och
    # bandet kapat vid t_slut.
    if brutet == "ankomst":
        time.sleep(EFTERSPELNING_S)
    audit = audit_slice(lab, bot, t_audit0, t_slut)
    stalls = [e["BotStall"] for e in lab.events
              if isinstance(e, dict) and "BotStall" in e and e["BotStall"].get("bot") == bot]
    goto_stall = [e["GotoStall"] for e in lab.events
                  if isinstance(e, dict) and "GotoStall" in e and e["GotoStall"].get("bot") == bot]

    # DOMEN — samma rena funktion som offline-omraakningen anvander.
    dom = dom_band(tape, audit, mal, cfg, goto_stall, stalls)
    klass = dom["klass"]
    tid = dom["tid_s"]
    if tid is None:
        tid = round(time.monotonic() - t0, 2)

    zs = [r[3] for r in tape] or [start[2]]
    farter = [r[5] for r in tape] or [0.0]
    rad = {
        "i": i,
        "start_namn": namn,
        "start": start,
        "mal": mal,
        "mal_id": cfg.get("mal_id"),
        "klass": klass,
        "tid_s": tid,
        "predikat": "v2",
        "fastnad_vid_s": dom.get("fastnad_vid_s"),
        "stall_bekraftad_av_motorn": dom.get("stall_bekraftad_av_motorn"),
        "svans_bilder": dom.get("svans_bilder"),
        "svans_ok": dom.get("svans_ok"),
        "efterspelning_s": EFTERSPELNING_S if brutet == "ankomst" else 0.0,
        "band_kapat_vid": t_slut,
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
        "n_audit": len(audit),
        "tape": tape,
        "audit": audit,
    }
    parkera(lab, bot, start)       # mellan forsok: stillastaende, aldrig fritt strov
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
    ap.add_argument("--mvd", action="store_true", default=True)
    ap.add_argument("--ingen-mvd", dest="mvd", action="store_false")
    ap.add_argument("--demodir", default="/home/xerial/.local/share/qw-fasttrack/runtime/qw/demos")
    a = ap.parse_args()

    cfg = HOPP[a.hopp]
    lab = Lab(port=a.port)
    st = lab.status()
    ident = {k: st.get(k) for k in ("map", "cells", "links", "rj_links")}
    print("graf:", json.dumps(ident), flush=True)

    utd = Path(a.ut) / f"hopp{a.hopp}" / a.varv
    utd.mkdir(parents=True, exist_ok=True)

    mvd_namn = f"hopp{a.hopp}-{a.varv}"
    if a.mvd:
        lab.request({"RunCmd": {"raw": f"record {mvd_namn}"}})
        time.sleep(0.5)

    rader = []
    starter = cfg["starter"]
    for i in range(a.n):
        namn, start = starter[i % len(starter)]
        rad = forsok(lab, a.bot, start, cfg["mal"], cfg, i + 1, namn)
        rader.append(rad)
        print(f"{i+1:2d}/{a.n} {namn:9s} {rad['klass']:15s} "
              f"tid={rad.get('tid_s')} min_z={rad.get('min_z')} slut={rad.get('slut_pos')} "
              f"stall={sorted({s.get('reason') for s in rad.get('stall_handelser', [])})}", flush=True)

    mvd_info = None
    if a.mvd:
        time.sleep(0.5)
        lab.request({"RunCmd": {"raw": "stop"}})
        time.sleep(1.0)
        mvd_info = []
        for suff in (".mvd", ".txt"):
            src = Path(a.demodir) / (mvd_namn + suff)
            if src.exists():
                dst = utd / src.name
                dst.write_bytes(src.read_bytes())
                mvd_info.append({"fil": dst.name, "bytes": dst.stat().st_size,
                                 "sha256": hashlib.sha256(dst.read_bytes()).hexdigest()})

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
        "predikat": "v2",
        "graf": ident,
        "facit": {k: cfg[k] for k in ("mal", "mal_id", "ankomst_r", "ankomst_dz",
                                      "landning_dz", "avfart", "fall_z", "budget_s",
                                      "prep_rockets", "krav_rj_idle")},
        "facit_konstanter": {"FL_MARK": FL_MARK, "MAX_STEG_U": MAX_STEG_U,
                             "AVFART_R": AVFART_R, "AVFART_DZ": AVFART_DZ,
                             "START_R": START_R, "FASTNAD_R": FASTNAD_R,
                             "FASTNAD_N_S": FASTNAD_N_S,
                             "fastnad_overkant_u_per_s": round(FASTNAD_R / FASTNAD_N_S, 1),
                             "EFTERSPELNING_S": EFTERSPELNING_S,
                             "SVANS_MINST": SVANS_MINST},
        "starter": {n: p for n, p in cfg["starter"]},
        "n": a.n,
        "lyckade": lyckade,
        "klasser": klasser,
        "per_start": per_start,
        "mvd": mvd_info,
        "tider_lyckade": sorted(r["tid_s"] for r in rader if r["klass"] == "lyckad"),
    }
    (utd / "forsok.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rader))
    (utd / "sammanfattning.json").write_text(json.dumps(sammanfattning, indent=1) + "\n")
    print("SUMMA:", json.dumps(sammanfattning, ensure_ascii=False), flush=True)
    parkera(lab, a.bot, cfg["starter"][0][1])   # varvet slut: boten star kvar pa startpunkten
    print("boten parkerad pa %s" % (cfg["starter"][0][1],))
    lab.close()


if __name__ == "__main__":
    main()
