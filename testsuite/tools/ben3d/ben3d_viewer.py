#!/usr/bin/env python3
"""ben3d_viewer.py — bygger den självbärande viewern (ben3d-viewer.html).

Läser fork-/basdump + alla buntar, kompakterar till DATA + injicerar i mallen.
Viewern ritar ENDAST extraktorns färdiga per-tick-listor (G2). CSP i mallen.
Ingen socket/Control; ~/lab endast läst."""

from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path

KIND = {"walk":0,"step":1,"drop":2,"jump":3,"doublejump":4,"speedjump":5,
        "plat":6,"teleport":7,"hook":8,"rocketjump":9,"swim":10}
KLASS = {"räckhåll":0, "okänd":1, "blockerad":2}


def git_head() -> str:
    r = subprocess.run(["git","rev-parse","HEAD"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else "OKÄND"


def compact_dump(path: str) -> dict:
    d = json.load(open(path))
    cells = []
    for c in d["cells"]:
        cells.extend([c[0], c[1], c[2]])
    links = []
    for l in d["links"]:
        links.extend([l["from"], l["to_cell"], KIND[l["kind"]], l.get("T", 1)])
    return {"cells": cells, "links": links}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fork-dump", required=True)
    ap.add_argument("--base-dump", required=True)
    ap.add_argument("--buntar", required=True)
    ap.add_argument("--template", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    geo = {"fork": compact_dump(args.fork_dump), "base": compact_dump(args.base_dump)}
    viewer_commit = git_head()

    bundles = {}
    for f in sorted(Path(args.buntar).glob("*.bunt.json")):
        b = json.loads(f.read_text())
        arm = b["geometri"]["arm"]
        ticks = []
        for t in b["tickserie"]["farg2_observerad_bana"]:
            ticks.append([float(t["t"]), float(t["x"]), float(t["y"]), float(t["z"]), int(t["cell"])])
        # farg1: per cell → flat [link_id, klass_code, ...]
        farg1 = {}
        raw = b["tickserie"].get("farg1")
        if raw:
            for cellblock in raw:
                lst = []
                for p in cellblock["lankar"]:
                    lst.extend([int(p["link_id"]), KLASS.get(p["klass"], 1)])
                farg1[int(cellblock["cell"])] = lst
        p = b["proveniens"]
        bundles[b["ben_id"]] = {
            "arm": arm,
            "geo": "fork" if arm == "fork" else "base",
            "dataset": b["ben_id"].split(":")[0],
            "ticks": ticks,
            "farg1": farg1 if farg1 else None,
            "sj_okand": p["farg1_policy"]["sj_okand"],
            "farg3_antal": b["tickserie"]["farg3"]["antal"] if b["tickserie"].get("farg3") else 0,
            "bundle_payload_sha256": b["bundle_payload_sha256"],
            "prov": {
                "graph_stamp": p["grafdump"]["graph_stamp"],
                "graph_content_hash": p["grafdump"]["graph_content_hash"],
            },
            "provFull": p,
        }

    data = {"geo": geo, "bundles": bundles}
    data_json = json.dumps(data, separators=(",", ":"), ensure_ascii=False)

    tpl = Path(args.template).read_text()
    assert "/*DATA*/" in tpl and "/*VIEWER_COMMIT*/" in tpl
    html = tpl.replace("/*VIEWER_COMMIT*/", json.dumps(viewer_commit), 1)
    html = html.replace("/*DATA*/", data_json, 1)

    out = Path(args.out)
    out.write_text(html, encoding="utf-8")
    print(f"viewer -> {out} ({out.stat().st_size/1e6:.2f} MB, {len(bundles)} buntar, viewer-commit {viewer_commit[:8]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
