#!/usr/bin/env python3
"""K1-orkester: kör båda armarna parallellt, fas för fas, med FASBARRIÄR
(grok krav 4): ingen arm startar fas k+1 förrän båda är klara med fas k.

Per fas och arm: färsk serverstart -> vänta kontrollport -> replant (endast
arm A) -> ra_kanon.py under taskset. Manifest per arm skrivs FÖRE första
fasen (plan v2 §5/§9). Ett riglock med båda armarnas info.

Användning:
  python3 k1_orkester.py --dry      # torrkörning: N=1 per rutt, egen katalog
  python3 k1_orkester.py            # skarpt: N ur PLANEN
"""
import argparse, hashlib, json, os, socket, subprocess, sys, time

HEM = os.path.expanduser("~")
VERKTYG = HEM + "/rtx-tools"
FASER = ["ut_ring", "ut_tunnel", "ut_vast", "in_ring", "in_tunnel", "in_vast"]
N_SKARP = {"ut_ring": 12, "ut_tunnel": 12, "ut_vast": 12,
           "in_ring": 10, "in_tunnel": 10, "in_vast": 10}
BUDGET = {"ut_ring": 150, "ut_tunnel": 150, "ut_vast": 150,
          "in_ring": 340, "in_tunnel": 340, "in_vast": 340}
# IN-budget höjd K1b: N=10 ska hinnas ÄVEN med idel värstafalls-försök
# (7 s anflygning + 25 s fönster) — terra-k1-resultatreview krav 3

ARMAR = {
    "A": {"unit": "fasttrack-ra", "port": 27990, "spelport": 27540, "replant": True,
          "runtime": HEM + "/.local/share/qw-fasttrack/runtime2",
          "pgrep": "runtime[2]/mvdsv", "server_cpu": "2", "harness_cpu": "3"},
    "B": {"unit": "fasttrack-main-test", "port": 27993, "spelport": 27570, "replant": False,
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
    """Vänta tills navmesh är byggd OCH boten spawnat (annars 'no such bot 1')."""
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

def restart_arm(arm):
    a = ARMAR[arm]
    sh("systemctl --user restart %s" % a["unit"])
    if not vanta_port(a["port"]):
        raise RuntimeError("arm %s: port %d kom aldrig upp" % (arm, a["port"]))
    if not vanta_redo(a["port"]):
        raise RuntimeError("arm %s: navmesh/bot blev aldrig redo" % arm)
    # pinna serverprocessen (mvdsv i armens runtime)
    # [2]-klammern hindrar pgrep från att matcha sitt eget kommandorad (GOTCHA)
    r = sh("pgrep -f 'runtime[%s]/mvdsv'" % ("2" if arm == "A" else "3"))
    for pid in r.stdout.split():
        sh("taskset -cp %s %s" % (a["server_cpu"], pid))

def replant_arm(arm, logf):
    a = ARMAR[arm]
    for i in range(24):
        r = sh("RTX_PORT=%d python3 %s/replant_kanon.py" % (a["port"], VERKTYG))
        logf.write(r.stderr)
        if r.returncode == 0:
            return json.loads(r.stdout.strip().splitlines()[-1])
        time.sleep(5)  # navmesh inte redo än — kända retryloopen
    raise RuntimeError("arm %s: replant gav aldrig grönt" % arm)

def kor_fas(arm, fas, n, outdir):
    a = ARMAR[arm]
    return subprocess.Popen(
        "taskset -c %s env RTX_PORT=%d python3 %s/ra_kanon.py --phase %s --n %d "
        "--budget %d --out %s" % (a["harness_cpu"], a["port"], VERKTYG, fas, n,
                                  BUDGET[fas], outdir),
        shell=True, stdout=open("%s/%s_%s.log" % (outdir, arm, fas), "w"),
        stderr=subprocess.STDOUT)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--tag", default=None)
    args = ap.parse_args()
    tag = args.tag or ("k1dry" if args.dry else "k1")
    bas = HEM + "/lab/" + tag
    os.makedirs(bas, exist_ok=True)
    N = {f: 1 for f in FASER} if args.dry else N_SKARP

    lock = HEM + "/lab/.rig-lock"
    if os.path.exists(lock):
        sys.exit("RIGG LÅST: " + open(lock).read())
    with open(lock, "w") as f:
        f.write("fabian-%s %d (armA+armB)" % (tag, os.getpid()))
    os.environ["RIG_PARENT_LOCK"] = "1"

    try:
        # SJÄLVARKIVERING (kimi-k1-resultatreview A3/villkor 1): exakta
        # körtidsversioner av verktyg + plantfacit kopieras in i körkatalogen
        # så manifesthasharna alltid pekar på bevarade filer.
        import shutil
        ark = os.path.join(bas, "verktyg")
        os.makedirs(ark, exist_ok=True)
        for p in [VERKTYG + "/ra_kanon.py", VERKTYG + "/replant_kanon.py",
                  os.path.abspath(__file__),
                  HEM + "/rtx-cost-exp/reference/ra-room/granskriterier.py",
                  os.path.expanduser("~/lab/ra_climb_planted.json"),
                  os.path.expanduser("~/lab/ra_mesh_planted.json"),
                  os.path.expanduser("~/lab/p1_56_planted.json")]:
            shutil.copy2(p, ark)
        stamp = sh("TZ=Europe/Stockholm date '+%Y-%m-%d %H:%M:%S %Z'").stdout.strip()
        stamp_utc = sh("date -u '+%Y-%m-%dT%H:%M:%SZ'").stdout.strip()
        sys.path.insert(0, VERKTYG)
        import platform
        from ra_kanon import PHASES, PARK, TOPP
        git = {"repo": HEM + "/rtx-cost-exp",
               "head_full": sh("cd %s/rtx-cost-exp && git rev-parse HEAD" % HEM).stdout.strip(),
               "dirty": sh("cd %s/rtx-cost-exp && git status --short" % HEM).stdout.strip() or "ren",
               "byggkommando": "cargo build --release -p rtx-game (librtx.so -> qwprogs.so)"}
        for arm, a in ARMAR.items():
            so = a["runtime"] + "/qw/qwprogs.so"
            cfg = a["runtime"] + "/qw/fasttrack.cfg"
            man = {"arm": arm, "start_cest": stamp, "start_utc": stamp_utc, "tag": tag,
                   "unit": a["unit"],
                   "port": {"spel": a["spelport"], "kontroll": a["port"]},
                   "qwprogs": dict(fil(so), algoritm="sha256"),
                   "kalla": (dict(git, kommentar="HEAD kodidentisk med d321f5a; "
                                  "91a6e34/3f039ce är docs+mätverktyg")
                             if arm == "A" else
                             {"byte_kalla": HEM + "/.local/share/qw-fasttrack/runtime-main/qw/qwprogs.so",
                              "beskrivning": "byteidentisk kopia av 27550:s (main) qwprogs.so; "
                                             "git-hash okänd för deployen, identitet = sha256"}),
                   "cvars": {"fil": fil(cfg),
                             "innehall": open(cfg).read()},
                   "taskset": {"server": a["server_cpu"], "harness": a["harness_cpu"]},
                   "N": N, "budget_s": BUDGET,
                   "fall_def": "peak_drop_150 (Δz>150 från löpande peak, inget golv)",
                   "tic_grans_pct": 1.0,
                   "koordinater": {"phases": {k: {"rikt": v["rikt"], "grans": v["grans"],
                                                  "start": v["start"], "mal": v["mal"],
                                                  "klipp_cap_s": v["cap"],
                                                  "approach_max_s": v.get("approach")}
                                              for k, v in PHASES.items()},
                                   "cap_semantik": "cap = kanonens 25 s KLIPPFÖNSTER; för IN "
                                                   "räknas det dynamiskt från första gräns-"
                                                   "passagen, anflygningen har egen maxtid",
                                   "parkering": PARK, "topp": TOPP,
                                   "predikat": "granskriterier.py (hash nedan) = kanonens klippkriterier"},
                   "verktyg": {"klippmodul": fil(HEM + "/rtx-cost-exp/reference/ra-room/granskriterier.py"),
                               "harness": fil(VERKTYG + "/ra_kanon.py"),
                               "replant": fil(VERKTYG + "/replant_kanon.py") if a["replant"]
                                          else "ingen — stock nav (main mäts oplanterad)",
                               "orkester": fil(os.path.abspath(__file__)),
                               "python": platform.python_version()},
                   "plant_files": ({k: fil(os.path.expanduser(v)) for k, v in
                                    {"climb": "~/lab/ra_climb_planted.json",
                                     "mesh": "~/lab/ra_mesh_planted.json",
                                     "p156": "~/lab/p1_56_planted.json"}.items()}
                                   if a["replant"] else "inga"),
                   "state_bevis": "per fas: fas_state_<fas>.json (restart-tid, PID, "
                                  "navmesh-stämpel, plantfrag, barriärtider)"}
            os.makedirs("%s/%s" % (bas, arm), exist_ok=True)
            with open("%s/%s/manifest.json" % (bas, arm), "w") as f:
                json.dump(man, f, ensure_ascii=False, indent=1)

        from labctl import Lab
        t_start = time.monotonic()
        for fas in FASER:
            print("=== FAS %s (%s) ===" % (fas, sh("date +%H:%M:%S").stdout.strip()), flush=True)
            # färsk restart båda armarna, parallellt
            restart_t = sh("date -u '+%Y-%m-%dT%H:%M:%SZ'").stdout.strip()
            for arm in ARMAR:
                restart_arm(arm)
            frags = {}
            for arm, a in ARMAR.items():
                if a["replant"]:
                    with open("%s/%s/replant_%s.log" % (bas, arm, fas), "a") as lf:
                        frags[arm] = replant_arm(arm, lf)
                else:
                    frags[arm] = "stock nav — ingen plantering (main-armen)"
            # statebevis per arm (terra-k1manifest-review p1/7/8)
            barr_start = sh("date -u '+%Y-%m-%dT%H:%M:%SZ'").stdout.strip()
            for arm, a in ARMAR.items():
                s = Lab(port=a["port"]).status()
                pid = sh("pgrep -f '%s'" % a["pgrep"]).stdout.strip()
                state = {"fas": fas, "restart_utc": restart_t, "pid": pid,
                         "port": {"spel": a["spelport"], "kontroll": a["port"]},
                         "navmesh_stamp": {"map": s["map"], "cells": s["cells"],
                                           "links": s["links"],
                                           "rj_links": s.get("rj_links")},
                         "bots": [(b["ent"], b["alive"]) for b in s.get("bots", [])],
                         "plantfrag": frags[arm],
                         "fas_start_utc": barr_start,
                         "fasordning_hittills": FASER[:FASER.index(fas) + 1]}
                with open("%s/%s/fas_state_%s.json" % (bas, arm, fas), "w") as f:
                    json.dump(state, f, ensure_ascii=False, indent=1)
            procs = {arm: kor_fas(arm, fas, N[fas], "%s/%s" % (bas, arm))
                     for arm in ARMAR}
            for arm, p in procs.items():   # FASBARRIÄR
                rc = p.wait()
                print("  arm %s klar (rc=%d)" % (arm, rc), flush=True)
            barr_end = sh("date -u '+%Y-%m-%dT%H:%M:%SZ'").stdout.strip()
            for arm in ARMAR:
                sf = "%s/%s/fas_state_%s.json" % (bas, arm, fas)
                st = json.load(open(sf)); st["fas_slut_utc"] = barr_end
                json.dump(st, open(sf, "w"), ensure_ascii=False, indent=1)
            print("  förflutet: %.1f min" % ((time.monotonic() - t_start) / 60), flush=True)
        print("=== ALLA FASER KLARA %s — total %.1f min ===" % (
            sh("date +%H:%M:%S").stdout.strip(), (time.monotonic() - t_start) / 60), flush=True)
    finally:
        os.remove(lock)

if __name__ == "__main__":
    main()
