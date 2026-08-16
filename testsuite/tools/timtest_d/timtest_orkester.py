#!/usr/bin/env python3
"""T1h TIMTEST — orkestern.

En timme, båda armarna parallellt, KEDJAD regim (timtest_ben.py), INGA
fasomstartar, INGA systemctl-anrop efter T0 (REVISION 1, grok 1–3).

T0-omstart av BÅDA unitarna (färsk RuntimeMaxSec-klocka; ingen ärvd
process) → ActiveEnterTimestamp + RuntimeMaxUSec-koll ≥70 min kvar (annars
ABORT) → df-koll ≥1 G → replant ENDAST arm A (replant_kanon) → taskset
A srv/harn 2/3, B 4/5 (pgrep med [klammer]) → manifest v2 FÖRE start
→ kör båda armarna 60 min parallellt under en EN riglock.

Fönsterregeln :25–:45 GÄLLER INTE som skydd: :17-UTC-cronen träffar alltid
en gång under timmen (start :25 CEST ⇒ T+~54 min); tic-vakten är domare
(omnoteras i manifestet).

Användning:
  python3 timtest_orkester.py --dry      # torrkörning: 1 cykel/arm
  python3 timtest_orkester.py            # skarpt: 60 min/arm
"""
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time

HEM = os.path.expanduser("~")
VERKTYG = HEM + "/rtx-tools"
RA_ROOM = HEM + "/rtx-cost-exp/reference/ra-room"
GRANSK = RA_ROOM + "/granskriterier.py"
HERE = os.path.dirname(os.path.abspath(__file__))
# v296-plantens sha (K2-läxan; REVISION 1, grok 5) — verifieras mot disken
V296_SHA = "00da2859"
RUNTIME_MIN_S = 60 * 60 * 1.166  # ≥70 min kvar
DF_MIN_GB = 1.0
MINUTER = 60.0

ARMAR = {
    "A": {"unit": "fasttrack-ra", "port": 27990, "spelport": 27540,
          "replant": True,
          "runtime": HEM + "/.local/share/qw-fasttrack/runtime2",
          "pgrep": "runtime[2]/mvdsv", "server_cpu": "2", "harness_cpu": "3"},
    "B": {"unit": "fasttrack-main-test", "port": 27993, "spelport": 27570,
          "replant": False,
          "runtime": HEM + "/.local/share/qw-fasttrack/runtime3",
          "pgrep": "runtime[3]/mvdsv", "server_cpu": "4", "harness_cpu": "5"},
}


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)


def sha256(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def fil(p):
    return {"path": p, "sha256": sha256(p)}


def vanta_port(port, tmo=120):
    t0 = time.monotonic()
    while time.monotonic() - t0 < tmo:
        try:
            socket.create_connection(("127.0.0.1", port), 2).close()
            return True
        except OSError:
            time.sleep(2)
    return False


def vanta_redo(port, tmo=180):
    """Vänta tills navmesh är byggd OCH boten spawnad (annars 'no such bot 1')."""
    sys.path.insert(0, VERKTYG)
    from labctl import Lab
    t0 = time.monotonic()
    while time.monotonic() - t0 < tmo:
        try:
            s = Lab(port=port).status()
            if s.get("cells") and any(b["ent"] == 1 and b["alive"]
                                      for b in s.get("bots", [])):
                return True
        except Exception:
            pass
        time.sleep(3)
    return False


def parse_tidsspann_us(v):
    """Parsa en tidsspann-sträng till µs. (AKUT-FIX, v255-systemctl)
    - "infinity" -> None (INGEN gräns)
    - rena siffror -> µs direkt
    - valfri kombination av Nh/Nmin/Ns/Nus: "3h", "2h 30min", "45min",
      "100s", "500us", "1h 2min 3s 4us".
    Oidentifierbar -> None."""
    v = (v or "").strip()
    if not v:
        return None
    if v.lower() in ("infinity", "∞", "inf"):
        return None
    if v.isdigit():
        return int(v)
    total = 0.0
    ok = False
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(h|min|s|us)\b", v):
        val, unit = float(m.group(1)), m.group(2)
        total += val * {"h": 3600e6, "min": 60e6, "s": 1e6, "us": 1.0}[unit]
        ok = True
    return int(total) if ok else None


def nu_monotonic_us():
    """Nuvarande tid på MONOTONISK klocka (µs) från /proc/upp.
    (Samma klocka som ActiveEnterTimestampMonotonic använder.)"""
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0]) * 1e6
    except (OSError, IndexError, ValueError):
        return None


def active_enter_usec(unit):
    """ActiveEnterTimestampMonotonic (µs, MONOTONISK klocka) ur systemctl.
    (v255-systemctl har inget ActiveEnterTimestampUSec — Monotonic är det
    som finns, heltal µs.) None om inte aktivt."""
    r = sh("systemctl --user show %s -p ActiveEnterTimestampMonotonic --value" % unit)
    v = r.stdout.strip()
    if v.isdigit():
        return int(v)
    return None


def runtime_max_usec(unit):
    """RuntimeMaxUSec -> µs (duration). v255-systemctl skriver människoräslbart
    ("3h", "infinity"). "infinity" -> None (INGEN gräns). Annars parsa
    tidsspann (Nh/Nmin/Ns/Nus) till µs; rena siffror = µs direkt."""
    r = sh("systemctl --user show %s -p RuntimeMaxUSec --value" % unit)
    return parse_tidsspann_us(r.stdout.strip())


def df_free_gb(path=HEM + "/lab"):
    st = os.statvfs(path)
    return (st.f_bavail * st.f_frsize) / (1024 ** 3)


def pin_server(arm):
    """taskset -cp på serverprocessen (mvdsv i armens runtime). [klammern]
    hindrar pgrep från att matcha sitt eget kommandorad (GOTCHA)."""
    a = ARMAR[arm]
    r = sh("pgrep -f '%s'" % a["pgrep"])
    for pid in r.stdout.split():
        sh("taskset -cp %s %s" % (a["server_cpu"], pid))
    return r.stdout.split()


def server_pid(arm):
    a = ARMAR[arm]
    r = sh("pgrep -f '%s'" % a["pgrep"])
    pids = [int(p) for p in r.stdout.split() if p.isdigit()]
    return pids[0] if pids else None


def server_pids():
    return {arm: server_pid(arm) for arm in ARMAR}


def restart_arm(arm):
    """T0-omstart ENA unit (endast här — inga restartar efteråt)."""
    a = ARMAR[arm]
    sh("systemctl --user restart %s" % a["unit"])
    if not vanta_port(a["port"]):
        raise RuntimeError("arm %s: port %d kom aldrig upp" % (arm, a["port"]))
    if not vanta_redo(a["port"]):
        raise RuntimeError("arm %s: navmesh/bot blev aldrig redo" % arm)


def replant_arm(arm, logf):
    a = ARMAR[arm]
    for i in range(24):
        r = sh("RTX_PORT=%d python3 %s/replant_kanon.py" % (a["port"], VERKTYG))
        logf.write(r.stderr)
        if r.returncode == 0:
            return json.loads(r.stdout.strip().splitlines()[-1])
        time.sleep(5)
    raise RuntimeError("arm %s: replant gav aldrig grönt" % arm)


def in_predikat_hash():
    """Hash av koden som utvärderar nya IN-predikatet (REVISION 1, grok 5).
    Däckar in in_topp_vid + in_topp_vid_stadig + konstanterna."""
    src = open(HERE + "/timtest_ben.py").read()
    # dra ut just IN-predikatets kodblock (deterministiskt ur källan)
    lo = src.index("IN_TOPP_CENTRUM = ")
    hi = src.index("def fall_peak_drop_150")
    return hashlib.sha256(src[lo:hi].encode()).hexdigest()


def skriv_manifest(bas, tag, dry):
    """Manifest v2 FÖRE start (REVISION 1, grok 5) — per arm."""
    stamp = sh("TZ=Europe/Stockholm date '+%Y-%m-%d %H:%M:%S %Z'").stdout.strip()
    stamp_utc = sh("date -u '+%Y-%m-%dT%H:%M:%SZ'").stdout.strip()
    git = {"repo": HEM + "/rtx-cost-exp",
           "head_full": sh("cd %s/rtx-cost-exp && git rev-parse HEAD" % HEM).stdout.strip(),
           "dirty": sh("cd %s/rtx-cost-exp && git status --short" % HEM).stdout.strip() or "ren",
           "byggkommando": "cargo build --release -p rtx-game (librtx.so -> qwprogs.so)"}
    ben_hash = in_predikat_hash()
    for arm, a in ARMAR.items():
        so = a["runtime"] + "/qw/qwprogs.so"
        cfg = a["runtime"] + "/qw/fasttrack.cfg"
        enter = active_enter_usec(a["unit"])
        maxu = runtime_max_usec(a["unit"])
        # AKUT-FIX (v255): monotonisk klocka; maxu None = infinity = ingen gräns.
        _now_mono = nu_monotonic_us()
        kvar_min = ((maxu - (_now_mono - enter)) / 6e7
                    if (maxu is not None and enter and _now_mono is not None) else None)
        man = {
            "schema": "t1h-manifest-v2",
            "arm": arm,
            "tag": tag,
            "start_cest": stamp,
            "start_utc": stamp_utc,
            "unit": a["unit"],
            "port": {"spel": a["spelport"], "kontroll": a["port"]},
            "qwprogs": dict(fil(so), algoritm="sha256"),
            "kalla": (dict(git, kommentar="K2-recept: climb+mesh+p156+v296; "
                                           "HEAD kodidentisk, 91a6e34/3f039ce docs+mätverktyg")
                      if arm == "A" else
                      {"byte_kalla": HEM + "/.local/share/qw-fasttrack/runtime-main/qw/qwprogs.so",
                       "beskrivning": "byteidentisk kopia av 27550:s (main) qwprogs.so; git-hash okänd, identitet=sha256"}),
            "cvars": {"fil": fil(cfg), "innehall": open(cfg).read()},
            "runtime_guard": {
                "ActiveEnterTimestampMonotonic_usec": enter,
                "RuntimeMaxUSec": ("infinity (inget tak)" if maxu is None else maxu),
                "kvar_vid_start_min": ("inget tak" if maxu is None
                                       else (round(kvar_min, 1) if kvar_min is not None else None)),
                "krav_min": round(RUNTIME_MIN_S / 60, 0),
                # infinity (maxu None) => ingen gräns => ok; annars >=70 min.
                "ok": (maxu is None) or bool(kvar_min and kvar_min >= RUNTIME_MIN_S / 60),
            },
            "df": {"ledigt_gb": round(df_free_gb(), 3), "krav_gb": DF_MIN_GB,
                   "ok": df_free_gb() >= DF_MIN_GB},
            "taskset": {"server": a["server_cpu"], "harness": a["harness_cpu"]},
            "regim": "kedjad (ingen teleport på friska ben); cykelordning ut_ring,in_ring,ut_tunnel,in_tunnel,ut_vast,in_vast",
            "mal": {
                "IN_nytt": "z≥320 OCH dxy([250,-703])≤130, kvarvaro ≥15 konsekutiva ticks (~0,3s @ ~49Hz); kodhash nedan",
                "IN_predikat_kodhash": ben_hash,
                "UT": "kanongränser via granskriterier.py (ÅTERANVÄNDS, ej duplicerad)",
                "radie_130_befog": "REVISION 1 ds 1b: västkantens vilofläck dxy≈100, max konsekutiv kvarvaro 83s ⇒ 130+0,3s STÅR",
            },
            "cap_semantik": "cap 25 s utan mål = fastnad (i nämnaren); UT räknas från t_game0; IN räknas OCKSÅ från t_game0 ÄVEN om gränsen aldrig passeras (F4: ingen evig loop) + sekundärt från gränsen",
            "fall_def": "peak_drop_150 (Δz>150 från löpande peak); IN-felsignal; UT-avsedda nedhopp undantas",
            "tic_vakt": "game-dt vs wall-dt per ben; drift_pct i ben-metadata; >1 % => benet = ogiltig_tic, exkluderat ur täljare OCH nämnare (rapporten redovisar antalet). Tic-vakten är domare på :17-cronen.",
            "n_regel": "nämnare n = alla GILTIGA försök per rutt (ogiltig_tic/kasserade exkluderade); täljare = framme UTAN fall OCH UTAN fastnad; fastnad_tot inkluderar fall_plus_fastnad; median = diskret (samma val som klippvalet: jämnt n => senare av mittparet), ej linjär interpolation; median/IQR bara över täljaren, skrivs 'median (täljare/nämnare)'; n=cykler efter kap",
            "stratifiering": "varje ben start=kedjad|teleport_efter_fel; huvudtal % = ALLA försök; kedjad-only-% = egen kolumn (REVISION 1 ds 4 + grok 7)",
            "trunkering": "FÖRSTA min(N_A,N_B) hela cykler från T0; pågående cykel vid 60 min kastas hos båda; råa cykeltal + n_kastade per arm rapporteras",
            "tidsgrans_not": "60-min-gränsen testas ENDAST MELLAN cykler (gate vid cykelstart). En cykel som startar t.ex. T=59,9 min körs färdig (upp till 6×25 s) och räknas som hel — väggtiden kan bli ~62 min. Kap till min(N) räddar lika n. (F9)",
            "ut_tidsnolla": "UT-tid = t_gräns − t_game0 (T1h-semantik: tid från bens start, EJ kanonklippets sista-topp-tick). IN-tid = första tick av bekräftade 15-strängen − t_game0.",
            "cron_not": "T+~54 min: :17-UTC-cronen (minimain-backup+clipshot) träffar en gång; tic-vakten är domare (se tic_vakt); inget onsdan; start :25 CEST",
            "pids": server_pids(),
            "n_prognos": {"beskrivning": "prognos fylls av torrkörningen: cykler/60min per arm vid 1 cykel", "cykler_per_arm": None},
            "minuter": MINUTER,
            "torrkorning": dry,
            "inget_systemctl_efter_T0": True,
            "verktyg": {
                "klippmodul": fil(GRANSK),
                "benmeter": fil(HERE + "/timtest_ben.py"),
                "orkester": fil(os.path.abspath(__file__)),
                "replant": fil(VERKTYG + "/replant_kanon.py") if a["replant"] else "ingen — stock nav (main-armen)",
                "python": platform.python_version(),
            },
            "plant_files": ({k: fil(os.path.expanduser(v)) for k, v in
                             {"climb": "~/lab/ra_climb_planted.json",
                              "mesh": "~/lab/ra_mesh_planted.json",
                              "p156": "~/lab/p1_56_planted.json",
                              "v296": "~/lab/vast_296_planted.json"}.items()}
                            if a["replant"] else "inga"),
            "selfarkiv": "verktyg/ (exakta körtidsversioner, nedan)",
        }
        if arm == "A":
            v296 = os.path.expanduser("~/lab/vast_296_planted.json")
            if os.path.exists(v296):
                h = sha256(v296)
                man["plant_files"]["v296"]["sha_prefill_00da2859"] = (
                    "MATCH" if h.startswith(V296_SHA) else
                    "MISMATCH: %s" % h[:8])
        os.makedirs("%s/%s" % (bas, arm), exist_ok=True)
        with open("%s/%s/manifest.json" % (bas, arm), "w") as f:
            json.dump(man, f, ensure_ascii=False, indent=1)
    return ben_hash


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--tag", default=None)
    args = ap.parse_args()
    tag = args.tag or ("t1hdry" if args.dry else "t1h")
    bas = HEM + "/lab/" + tag
    os.makedirs(bas, exist_ok=True)
    sys.path.insert(0, VERKTYG)
    sys.path.insert(0, RA_ROOM)

    lock = HEM + "/lab/.rig-lock"
    if os.path.exists(lock):
        sys.exit("RIGG LÅST: " + open(lock).read())
    with open(lock, "w") as f:
        f.write("fabian-%s %d (armA+armB, T1h kedjad)" % (tag, os.getpid()))

    try:
        # --- SJÄLVARKIVERING: exakta körtidsversioner in i körkatalogen ---
        ark = os.path.join(bas, "verktyg")
        os.makedirs(ark, exist_ok=True)
        for p in [HERE + "/timtest_ben.py", HERE + "/timtest_orkester.py",
                  HERE + "/timtest_rapport.py",
                  VERKTYG + "/replant_kanon.py",
                  GRANSK,
                  os.path.expanduser("~/lab/ra_climb_planted.json"),
                  os.path.expanduser("~/lab/ra_mesh_planted.json"),
                  os.path.expanduser("~/lab/p1_56_planted.json"),
                  os.path.expanduser("~/lab/vast_296_planted.json")]:
            if os.path.exists(p):
                shutil.copy2(p, ark)

        # --- T0: omstart av BÅDA unitarna (färsk RuntimeMaxSec-klocka) ---
        print("T0: restartarm A+B ...", flush=True)
        for arm in ARMAR:
            restart_arm(arm)

        # --- RuntimeMaxSec-koll ≥70 min kvar (annars ABORT) ---
        # AKUT-FIX (v255): monotonisk klocka (ActiveEnterTimestampMonotonic +
        # /proc/uptime), RuntimeMaxUSec människoläsbart ("3h"/"infinity").
        now_mono = nu_monotonic_us()
        for arm, a in ARMAR.items():
            enter = active_enter_usec(a["unit"])
            maxu = runtime_max_usec(a["unit"])
            if maxu is None:
                # infinity -> INGEN gräns; kollen passerar.
                print("  arm %s: RuntimeMaxUSec=infinity (inget tak) -> ok" % arm, flush=True)
                continue
            kvar = ((maxu - (now_mono - enter)) / 6e7
                    if (enter and now_mono is not None) else -1)
            if kvar < RUNTIME_MIN_S / 60:
                sys.exit("ABORT arm %s: RuntimeMaxSec-kvar %.1f min < 70 min "
                         "(enter=%s max=%s)" % (arm, kvar, enter, maxu))
            print("  arm %s: kvar %.1f min (>=70 ok)" % (arm, kvar), flush=True)

        # --- df-koll ≥1 G ---
        free = df_free_gb()
        if free < DF_MIN_GB:
            sys.exit("ABORT: diskledigt %.2f G < 1 G" % free)
        print("  disk: %.2f G ledigt (>=1 G ok)" % free, flush=True)

        # --- replant ENDAST arm A ---
        frag = {}
        for arm, a in ARMAR.items():
            os.makedirs("%s/%s" % (bas, arm), exist_ok=True)  # Fable-fix: armdir före replant.log (skapades tidigare först i skriv_manifest)
            if a["replant"]:
                with open("%s/%s/replant.log" % (bas, arm), "a") as lf:
                    frag[arm] = replant_arm(arm, lf)
            else:
                frag[arm] = "stock nav — ingen plantering (main-armen)"
        print("  replant: A=%s" % (
            "ok" if isinstance(frag.get("A"), dict) else "FAIL"), flush=True)

        # --- taskset (A srv/harn 2/3, B 4/5) — låst i manifestet ---
        for arm in ARMAR:
            pin_server(arm)

        # --- Manifest v2 FÖRE start ---
        ben_hash = skriv_manifest(bas, tag, dry=args.dry)
        print("  manifest v2 skriven (IN-predikat kodhash %s...)" % ben_hash[:12],
              flush=True)

        # --- kör båda armarna parallellt 60 min (eller 1 cykel i --dry) ---
        minute = 1 if args.dry else MINUTER
        procs = {}
        for arm, a in ARMAR.items():
            outdir = "%s/%s" % (bas, arm)
            cmd = ("taskset -c %s env RTX_PORT=%d python3 %s/timtest_ben.py "
                   "--arm %s --minuter %d --out %s"
                   % (a["harness_cpu"], a["port"], HERE, arm, minute, bas))
            if args.dry:
                cmd += " --dry"
            procs[arm] = subprocess.Popen(
                cmd, shell=True,
                stdout=open("%s/%s_koda.log" % (bas, arm), "w"),
                stderr=subprocess.STDOUT)
        print("  kodar parallellt %d min/arm ..." % minute, flush=True)
        for arm, p in procs.items():
            rc = p.wait()
            print("  arm %s klar (rc=%d)" % (arm, rc), flush=True)

        # --- torrkörning: logga cykelns totala VÄGGTID (F11-fix) ---
        # INGEN manifestbackfill, INGEN prognosformel — Fable räknar
        # prognosen själv. Skriver {bas}/{arm}/cykeltid_dry.json per arm:
        # summa wall_dt_s över de 6 benen i c001 (väggklocka, ej game-tid).
        if args.dry:
            import glob as _g
            for arm in ARMAR:
                mfiler = _g.glob("%s/%s/c001/*_meta.json" % (bas, arm))
                total_vagg = sum(
                    (json.load(open(m)).get("wall_dt_s") or 0) for m in mfiler)
                out = {"arm": arm, "cykel": 1, "total_vagg_s": round(total_vagg, 2),
                       "n_ben": len(mfiler),
                       "not": "summa wall_dt_s i c001 (väggklocka); prognos räknas av beställaren"}
                with open("%s/%s/cykeltid_dry.json" % (bas, arm), "w") as f:
                    json.dump(out, f, ensure_ascii=False, indent=1)
            print("  cykeltid_dry skriven (per arm, total_vagg_s i c001)", flush=True)

        # --- statebevis ---
        from labctl import Lab
        for arm, a in ARMAR.items():
            s = Lab(port=a["port"]).status()
            pid = sh("pgrep -f '%s'" % a["pgrep"]).stdout.strip()
            state = {"arm": arm, "restart_arm": a["unit"], "pid": pid,
                     "port": {"spel": a["spelport"], "kontroll": a["port"]},
                     "navmesh_stamp": {"map": s.get("map"), "cells": s.get("cells"),
                                       "links": s.get("links")},
                     "bots": [(b["ent"], b["alive"]) for b in s.get("bots", [])],
                     "plantfrag": frag[arm],
                     "slut_utc": sh("date -u '+%Y-%m-%dT%H:%M:%SZ'").stdout.strip()}
            with open("%s/%s/state.json" % (bas, arm), "w") as f:
                json.dump(state, f, ensure_ascii=False, indent=1)

        print("=== T1h KLARA %s — kör %s/timtest_rapport.py för analys ===" % (
            sh("date +%H:%M:%S").stdout.strip(), HERE), flush=True)
    finally:
        os.remove(lock)


if __name__ == "__main__":
    main()
