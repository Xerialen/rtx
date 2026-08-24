#!/usr/bin/env python3
"""Exportera den LEVANDE navmeshen KOMPLETT — alla länkar, med traverserbarhetsflagga.

Varför en till dumpare: `~/rtx-tools/mkgraph.py` går `CellById` per cell och tar
cellens `out`, som `describe_cell` bygger ur ADJACENSEN. Två carve-pass i
`splice.rs` rensar avsiktligt länkar ur adjacensen men behåller dem i
länkarrayen (id:n och sidotabeller ska stå stilla). Följden är att dumpen fick
48193 länkar där status räknade 48208 — de 15 saknade är precis de avsiktligt
icke-traverserbara.

De 15 är inte skräp att fylla på med. De är svaret på den fråga en strukturdom
ställer, och en inventering som utelämnar dem tyst är lika fel som en som tyst
befordrar dem till gångbara. Därför läser den här exporten BÅDE `out` och det
nya `out_pruned` och skriver flaggan `T` per länk: 1 = i adjacensen
(planner-traverserbar), 0 = rensad.

Kräver ett motorbygge med `out_pruned` på cellsvaret (branch
`toolbox/b-planner-telemetry`). Mot ett äldre bygge saknas fältet — då säger
exporten ifrån i stället för att tyst skriva en ofullständig dump.

  python3 mkgraph_full.py <ut.json> [--port 27980] [--max-cells 8000]

Skriver även den kanoniska inventeringen och dess SHA-256 (nivå 2) enligt
WORK_LOGS/graphstamp-kontrakt.md §8.2, så gyllene värdet kan läsas av direkt.
"""
import hashlib
import json
import socket
import struct
import sys

sys.path.insert(0, "/home/xerial/rtx-testsuite/testsuite")
from runner import mpwire  # noqa: E402


class Chan:
    """En socket för hela crawlen — en per cell tar minuter i stället."""

    def __init__(self, port=27980, timeout=20.0):
        self.s = socket.create_connection(("127.0.0.1", port), timeout)
        self.buf = b""

    def rpc(self, cmd, rid):
        body = mpwire.packb({"id": rid, "cmd": cmd})
        self.s.sendall(struct.pack("<I", len(body)) + body)
        while True:
            while len(self.buf) >= 4:
                n = struct.unpack("<I", self.buf[:4])[0]
                if len(self.buf) < 4 + n:
                    break
                msg = mpwire.unpackb(self.buf[4:4 + n])
                self.buf = self.buf[4 + n:]
                if isinstance(msg, dict) and "Reply" in msg and msg["Reply"].get("id") == rid:
                    return msg["Reply"]
            chunk = self.s.recv(1 << 20)
            if not chunk:
                raise SystemExit("kontrollkanalen stängde")
            self.buf += chunk


def _fmt(v):
    """Talformen ur kontraktet §8.2: heltal utan decimal, annars %.2f."""
    v = float(v)
    return str(int(v)) if v == int(v) else format(round(v, 2), ".2f")


def canonical_inventory(doc: dict) -> bytes:
    """Kontraktets kanoniska inventering, §8.2 — C-poster följt av L-poster med T."""
    lines = []
    for cid, c in sorted(zip(doc["cell_ids"], doc["cells"])):
        lines.append(f"C\t{cid}\t{_fmt(c[0])}\t{_fmt(c[1])}\t{_fmt(c[2])}")
    for src, dst, kind, t in sorted(
        (int(l["from"]), int(l["to_cell"]), str(l["kind"]).lower(), int(l["T"]))
        for l in doc["links"]
    ):
        lines.append(f"L\t{src}\t{dst}\t{kind}\t{t}")
    return "\n".join(lines).encode("utf-8")


def main():
    out_p = sys.argv[1]
    port, max_cells = 27980, 8000
    for i, a in enumerate(sys.argv):
        if a == "--port":
            port = int(sys.argv[i + 1])
        elif a == "--max-cells":
            max_cells = int(sys.argv[i + 1])

    ch = Chan(port)
    st = ch.rpc({"Status": None}, 1)["result"]["Ok"]["Status"]
    n_cells = st["cells"]
    stamp = {k: st[k] for k in ("map", "cells", "links", "rj_links") if k in st}

    cells, cell_ids, links, link_ids = [], [], [], []
    missing = 0
    saw_pruned_field = False
    for cid in range(min(n_cells, max_cells)):
        try:
            c = ch.rpc({"CellById": {"cell": cid}}, cid + 100)["result"]["Ok"]["Cell"]
        except Exception:
            missing += 1
            continue
        o = c["origin"]
        cells.append([int(o[0]), int(o[1]), int(o[2])])
        cell_ids.append(c["cell"])
        # T=1: i adjacensen. T=0: rensad — finns i arrayen, går inte att traversera.
        for lk in c.get("out", []):
            links.append({"from": c["cell"], "to_cell": lk["to_cell"],
                          "kind": lk["kind"], "T": 1})
            link_ids.append(lk["link"])
        if "out_pruned" in c:
            saw_pruned_field = True
            for lk in c["out_pruned"]:
                links.append({"from": c["cell"], "to_cell": lk["to_cell"],
                              "kind": lk["kind"], "T": 0})
                link_ids.append(lk["link"])
        if cid % 500 == 0:
            print(f"  {cid}/{n_cells}…", file=sys.stderr)

    if not saw_pruned_field:
        raise SystemExit(
            "motorn svarar utan 'out_pruned' — det här är ett äldre bygge, och dumpen "
            "skulle bli ofullständig på exakt det sätt den här exporten finns för att "
            "undvika. Kör mot toolbox/b-planner-telemetry."
        )

    n_expected = stamp.get("links")
    if n_expected is not None and len(links) != n_expected:
        print(f"VARNING: {len(links)} länkar exporterade, status säger {n_expected}. "
              f"Skillnaden ska vara 0 — rapportera den, gissa inte.", file=sys.stderr)

    doc = {
        "schema": "qw-nav-graph/1",
        "map": stamp.get("map"),
        "grid": st.get("grid", 32.0),
        "cells": cells,
        "links": links,
        "cell_ids": cell_ids,
        "link_ids": link_ids,
        "provenance": f"live control channel :{port} — {stamp}; komplett dump "
                      f"(out + out_pruned), T=adjacensmedlemskap",
    }
    inv = canonical_inventory(doc)
    doc["graph_content_hash"] = hashlib.sha256(inv).hexdigest()

    with open(out_p, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    n_pruned = sum(1 for l in links if l["T"] == 0)
    print(f"skrev {out_p}: {len(cells)} celler ({missing} tomma id), {len(links)} länkar "
          f"({n_pruned} rensade ur adjacensen)", file=sys.stderr)
    print(f"graph_content_hash = {doc['graph_content_hash']}", file=sys.stderr)


if __name__ == "__main__":
    main()
