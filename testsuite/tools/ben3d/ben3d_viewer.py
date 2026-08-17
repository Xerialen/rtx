#!/usr/bin/env python3
"""ben3d_viewer.py — bygger den självbärande viewern (ben3d-viewer.html).

Läser fork-/basdump + alla 97 buntar, kompakterar till ett DATA-objekt och
injicerar i mallen. Ingen socket/Control; ~/lab endast läst. Output är den
nätverksisolerade artefakten (CSP saknas avsiktligt — inga externa resurser,
inget nätverk i viewern; se FRUSEN-badgen)."""

from __future__ import annotations
import argparse, json, sys
from pathlib import Path

KIND = {"walk":0,"step":1,"drop":2,"jump":3,"doublejump":4,"speedjump":5,
        "plat":6,"teleport":7,"hook":8,"rocketjump":9,"swim":10}


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
    ap.add_argument("--buntar", required=True)  # dir med *.bunt.json
    ap.add_argument("--template", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    geo = {"fork": compact_dump(args.fork_dump), "base": compact_dump(args.base_dump)}

    bundles = {}
    for f in sorted(Path(args.buntar).glob("*.bunt.json")):
        b = json.loads(f.read_text())
        ticks = []
        for t in b["tickserie"]["farg2_observerad_bana"]:
            ticks.append([
                float(t["t"]), float(t["x"]), float(t["y"]), float(t["z"]), int(t["cell"]),
            ])
        p = b["proveniens"]
        arm = b["geometri"]["arm"]
        bundles[b["ben_id"]] = {
            "arm": arm,
            "geo": "fork" if arm == "fork" else "base",
            "dataset": b["ben_id"].split(":")[0],
            "ticks": ticks,
            "sj_okand": p["farg1_policy"]["sj_okand"],
            "farg3_antal": b["tickserie"]["farg3"]["antal"],
            "bundle_payload_sha256": b["bundle_payload_sha256"],
            "prov": {
                "graph_stamp": p["grafdump"]["graph_stamp"],
                "graph_content_hash": p["grafdump"]["graph_content_hash"],
                "manifest_sha256": p["dataset_manifest"]["sha256"],
                "meta_sha256": p["medlemmar"]["meta_json"]["sha256"],
                "jsonl_sha256": p["medlemmar"]["ra_jsonl"]["sha256"],
                "utfall": p["dataset_manifest"].get("ben_typ", ""),
            },
        }

    data = {"geo": geo, "bundles": bundles}
    data_json = json.dumps(data, separators=(",", ":"), ensure_ascii=False)

    tpl = Path(args.template).read_text()
    assert "/*DATA*/" in tpl, "template saknar /*DATA*/"
    html = tpl.replace("/*DATA*/", data_json, 1)

    out = Path(args.out)
    out.write_text(html, encoding="utf-8")
    print(f"viewer -> {out} ({out.stat().st_size/1e6:.2f} MB, {len(bundles)} buntar)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
