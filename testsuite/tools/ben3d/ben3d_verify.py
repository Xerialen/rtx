#!/usr/bin/env python3
"""ben3d_verify.py — räknar om varje bunt + rot ur förseglade bytes (P4/D1-D2).

RFC 8785-kanonisering (nycklar sorterade, minimala escapes, inga mellanslag) med
tillägget ben3d-num-as-string/1: flyttal får inte förekomma (de är strängar i
buntarna). Verifierar varje bunt: bundle_payload_sha256 = SHA256(canonical
({schema,ben_id,geometri,tickserie})), proveniens_sha256 = SHA256(canonical
(proveniens)), och bygger artefaktroten per D2. Avvikelse = STOPP (exit 2)."""

from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path


def canonical(v) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        raise SystemExit("STOPP: flyttal i kanonisering — ben3d-num-as-string/1 gäller")
    if isinstance(v, str):
        out = ['"']
        for c in v:
            o = ord(c)
            if c == '"':
                out.append('\\"')
            elif c == "\\":
                out.append("\\\\")
            elif o == 0x08:
                out.append("\\b")
            elif o == 0x09:
                out.append("\\t")
            elif o == 0x0A:
                out.append("\\n")
            elif o == 0x0C:
                out.append("\\f")
            elif o == 0x0D:
                out.append("\\r")
            elif o < 0x20:
                out.append("\\u%04x" % o)
            else:
                out.append(c)
        out.append('"')
        return "".join(out)
    if isinstance(v, list):
        return "[" + ",".join(canonical(e) for e in v) + "]"
    if isinstance(v, dict):
        keys = sorted(v.keys())
        return "{" + ",".join(canonical(k) + ":" + canonical(v[k]) for k in keys) + "}"
    raise SystemExit("STOPP: okänd typ i kanonisering: %r" % type(v))


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--buntar", required=True)
    ap.add_argument("--t1h", required=True)
    ap.add_argument("--t20m", required=True)
    ap.add_argument("--fork-dump", required=True)
    ap.add_argument("--base-dump", required=True)
    ap.add_argument("--extractor-bin", required=True)
    ap.add_argument("--viewer", required=True)
    ap.add_argument("--n", type=int, default=97)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    buntar = sorted(Path(args.buntar).glob("*.bunt.json"))
    if len(buntar) != args.n:
        print(f"STOPP: {len(buntar)} buntar != {args.n}", file=sys.stderr)
        return 2

    ben = []
    for f in buntar:
        b = json.loads(f.read_text())
        payload = {
            "schema": b["schema"],
            "ben_id": b["ben_id"],
            "geometri": b["geometri"],
            "tickserie": b["tickserie"],
        }
        got = sha(canonical(payload))
        want = b["bundle_payload_sha256"]
        if got != want:
            print(f"STOPP: payload-sha {got} != {want} i {f.name}", file=sys.stderr)
            return 2
        prov_sha = sha(canonical(b["proveniens"]))
        ben.append({
            "ben_id": b["ben_id"],
            "bundle_payload_sha256": got,
            "proveniens_sha256": prov_sha,
        })

    ben.sort(key=lambda r: r["ben_id"])
    man = [
        {"id": "t1h-dataset-manifest-20260817T1536Z.sha256",
         "sha256": sha_bytes(Path(args.t1h).read_bytes())},
        {"id": "t20m-dataset-manifest-20260817T1339Z.sha256",
         "sha256": sha_bytes(Path(args.t20m).read_bytes())},
    ]
    man.sort(key=lambda r: r["id"])
    dumps = [
        {"id": "dm3-fork-v296-ram", "sha256": sha_bytes(Path(args.fork_dump).read_bytes())},
        {"id": "dm3-base", "sha256": sha_bytes(Path(args.base_dump).read_bytes())},
    ]
    dumps.sort(key=lambda r: r["id"])
    rot = {
        "schema": "ben3d-rot/1",
        "dataset_manifests": man,
        "dumps": dumps,
        "extractor_bin_sha256": sha_bytes(Path(args.extractor_bin).read_bytes()),
        "viewer_bundle_sha256": sha_bytes(Path(args.viewer).read_bytes()),
        "ben": ben,
    }
    rot_sha = sha(canonical(rot))
    report = {
        "schema": "ben3d-verify/1",
        "ok": True,
        "n_buntar": len(ben),
        "rot_sha256": rot_sha,
        "rot": rot,
    }
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    print(f"{len(ben)} buntar OK · rot_sha256={rot_sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
