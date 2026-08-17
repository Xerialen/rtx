#!/usr/bin/env python3
"""ben3d_buntar.py — genererar en bunt per H-ben (etapp 2c), deterministiskt.

Kör extraktorn (crates/ben3d) per H-ben ur de två förseglade manifesten.
Efterproduktion fyller P1-proveniensen med källhärledda SHA (binary, cargo_lock,
cli_config, kvitto) och skriver h-index.json (P3: 97 identiteter + meta-/raw-SHA).
Ingen socket/Control; ~/lab endast läst."""

from __future__ import annotations
import argparse, hashlib, json, subprocess, sys
from pathlib import Path


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git_head() -> str:
    r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


PREFIX = {
    ("t1h", "fork"): "t1h-d1-on",
    ("t1h", "main"): "t1h-main-ref",
    ("t20m", "fork"): "t20m-d1-on",
    ("t20m", "main"): "t20m-main-ref",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--t1h", required=True)
    ap.add_argument("--t20m", required=True)
    ap.add_argument("--fork-dump", required=True)
    ap.add_argument("--fork-dump-id", default="dm3-fork-v296-ram")
    ap.add_argument("--base-dump", required=True)
    ap.add_argument("--base-dump-id", default="dm3-base")
    ap.add_argument("--kvitto", default=str(Path.home() / "lab" / "rokdeploy-kvitto-20260817.json"))
    ap.add_argument("--cargo-lock", default=str(Path(__file__).resolve().parent.parent.parent.parent / "Cargo.lock"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--bin", default="target/debug/ben3d")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    r = subprocess.run([args.bin, "h-index", args.t1h, args.t20m], capture_output=True, text=True)
    if r.returncode != 0:
        print("STOPP: h-index misslyckades:", r.stderr, file=sys.stderr)
        return 2
    doc = json.loads(r.stdout)
    if doc["n_h"] != 97:
        print(f"STOPP: n_h={doc['n_h']} != 97", file=sys.stderr)
        return 2

    manifest_path = {
        "t1h-dataset-manifest-20260817T1536Z.sha256": Path(args.t1h),
        "t20m-dataset-manifest-20260817T1339Z.sha256": Path(args.t20m),
    }
    binary_sha = sha(Path(args.bin).read_bytes())
    cargo_lock_sha = sha(Path(args.cargo_lock).read_bytes())
    kvitto_sha = sha(Path(args.kvitto).read_bytes()) if Path(args.kvitto).is_file() else "OKÄND"

    index_rows = []
    for row in doc["ben"]:
        dataset_name = row["dataset"]
        mpath = manifest_path.get(dataset_name)
        if mpath is None:
            print(f"STOPP: okänt dataset {dataset_name}", file=sys.stderr)
            return 2
        ds_label = "t1h" if "t1h" in dataset_name else "t20m"
        arm = row["arm"]
        prefix = PREFIX[(ds_label, arm)]
        mdir = mpath.parent
        rel = f"{prefix}/{row['cycle_id']}/{row['ben']}_meta.json"
        meta = mdir / rel
        jsonl = mdir / f"{prefix}/{row['cycle_id']}/{row['ben']}.jsonl"
        if not meta.is_file() or not jsonl.is_file():
            print(f"STOPP: saknad fil {meta} / {jsonl}", file=sys.stderr)
            return 2
        jsonl_sha = sha(jsonl.read_bytes())
        dump = args.fork_dump if arm == "fork" else args.base_dump
        dump_id = args.fork_dump_id if arm == "fork" else args.base_dump_id
        bunt_out = out / f"{ds_label}-{arm}-{row['cycle_id']}-{row['ben']}.bunt.json"
        cli_sha = sha(json.dumps([binary_sha, "bunt", dump_id, arm, ds_label], ensure_ascii=False).encode())
        cli = [args.bin, "bunt", dump, dump_id, str(meta), str(jsonl), str(mpath), arm, ds_label, str(bunt_out)]
        rr = subprocess.run(cli, capture_output=True, text=True)
        if rr.returncode != 0:
            print(f"STOPP: bunt misslyckades för {rel}: {rr.stderr}", file=sys.stderr)
            return 2
        b = json.loads(bunt_out.read_text())
        # P1-efterproduktion: fyll källhärledda SHA (proveniens ej i payload ⇒ payload-sha oförändrad)
        p = b["proveniens"]
        p["extractor"]["binary_sha256"] = binary_sha
        p["extractor"]["cargo_lock_sha256"] = cargo_lock_sha
        p["extractor"]["cli_config_sha256"] = cli_sha
        if p["kvitto"]["sha256"] == "OKÄND":  # fork: fyll kvitto-SHA; main: extraktorn band till basdumpen
            p["kvitto"]["sha256"] = kvitto_sha
        bunt_out.write_text(json.dumps(b, ensure_ascii=False, separators=(",", ":")))
        index_rows.append({
            "ben_id": b["ben_id"],
            "bunt_rel": bunt_out.name,
            "bunt_sha256": sha(bunt_out.read_bytes()),
            "bundle_payload_sha256": b["bundle_payload_sha256"],
            "meta_sha256": row["meta_sha256"],
            "ra_jsonl_sha256": jsonl_sha,
            "arm": arm,
            "dataset": ds_label,
            "utfall": row["utfall"],
        })

    index_rows.sort(key=lambda r: r["ben_id"])
    index_doc = {
        "schema": "ben3d-h-index/1",
        "build_revision": git_head(),
        "n_h": len(index_rows),
        "konton": {},
        "ben": index_rows,
    }
    from collections import Counter
    index_doc["konton"] = {f"{k[0]}:{k[1]}": v for k, v in Counter((r["dataset"], r["arm"]) for r in index_rows).items()}
    index_doc["index_sha256"] = sha(json.dumps(index_rows, sort_keys=True, ensure_ascii=False).encode())
    (out / "h-index.json").write_text(json.dumps(index_doc, ensure_ascii=False, indent=1) + "\n")
    print(f"{len(index_rows)} buntar -> {out}/h-index.json (index-sha {index_doc['index_sha256']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
