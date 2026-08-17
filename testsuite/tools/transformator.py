#!/usr/bin/env python3
"""Offline graftransformator: basdump + komponerad op-lista -> per-op delta och stamp.

VARFÖR DEN FINNS
----------------
Ompinningsspec r2 (``WORK_LOGS/deepseek-ompinning-spec.md``) kräver att
komponatets mellanstampar och slutstamp **härleds** ur basen + op-listan och
förseglas FÖRE första körning — aldrig apply-then-copy, aldrig bas + summa av
delstampars delta. Delstamparnas FNV gäller bara den ensamma applyn mot bas;
id-tilldelning sker sekventiellt vid apply, så en kedja måste räknas om.

Den öppna frågan specen inte kunde stänga är V296:s delta: ``+0/+1`` (snap mot
befintliga celler) eller ``+1/+1`` (om PlanLink carvar en cell). Det här
verktyget **avgör** den ur samma regler som motorn i stället för att anta —
se ``PlanLinkOp`` och bevisfälten den skriver i manifestet.

SEMANTIKEN SPEGLAR MOTORN
-------------------------
Varje op är modellerad mot koden i ``crates/`` (rtx-toolbox-d), inte mot prosan:

``remove_links``  ``NavGraph::remove_links_by_id`` (mod.rs:618). Kompakterar
    länkarrayen och bygger om adjacensen från noll via ``push_link``. **Det
    betyder att varje behållen länk hamnar i adjacensen igen** — även de 15
    som teleport-triggerrensningen tagit ut (§8.5 i graphstamp-kontrakt.md).
    ``rebuild_derived`` kör bara reachability + LOD och rensar inte om. En
    remove-op flyttar alltså ``T`` 0->1 på de 15. Se ``T_ATERUPPSTAR``.

``shelf_patch``   ``nav_patch::apply_one`` (nav_patch.rs:827). Celler först i
    tabellordning (``cell_within`` 8/8; finns cellen redan hoppas den över),
    sedan drops i tabellordning (``cell_within`` 8/8 för from, 48/48 för to,
    och en identisk Drop from->to hoppas över). Append, aldrig insert.

``plan_link``     ``control.rs::plant_link_resp`` -> ``NavGraph::plant_speed_jump``.
    ``nearest`` på båda ändarna mot BEFINTLIGA celler, sedan ``push_speed_jump``.
    Vägen innehåller **ingen** ``plant_cell``, så den kan inte skapa en cell.

GRÄNSER (namngivna, inte gömda)
-------------------------------
- **BSP-geometrin modelleras inte.** ``plant_cell``/``plant_drop`` gör en
  hull-trace som kan VÄGRA en cell eller ett drop. En vägran ändrar utfallet
  till ``Failed`` — den kan aldrig ändra ett delta. Verktyget räknar därför
  det accepterade utfallet och säger ifrån att geometrikontrollen ligger
  utanför modellen. De förseglade delstamparna är beviset att motorn accepterade.
- **Cellorigin trunkeras i dumpen** (``int()``, kontraktets §8.2), så ``nearest``
  körs på trunkerade koordinater medan motorn har exakta. Verktyget rapporterar
  marginalen till näst bästa cell och VÄGRAR när den är inom trunkeringsfelet.
- **Auto-Walk vid plantering stöds inte.** ``plant_cell`` (utan
  ``no_auto_walk``) länkar nya celler till samma-z-grannar via BSP-trace.
  Det går inte att räkna offline, så en op som begär det vägras hellre än
  gissas. Komponatets ops behöver den inte.

Ingen riggkontakt. Läser dump-JSON och receptfiler, skriver JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import graphstamp  # noqa: E402  (samma katalog; en enda stamp-implementation)

GRID = 32.0

#: ``remove_links_by_id`` bygger om adjacensen från noll, så en tidigare rensad
#: länk (T=0) blir traverserbar igen. Konstanten finns för att beteendet ska gå
#: att hitta och ifrågasätta, inte ligga gömt i en loop.
T_ATERUPPSTAR = True

CELL_WITHIN_XY = 8.0
CELL_WITHIN_Z = 8.0
DROP_REACH_XY = 48.0
DROP_REACH_Z = 48.0

#: Trunkeringen (``int()`` mot noll) kan flytta en cellorigin upp till 1 enhet
#: per axel. Två celler vars avstånd till en punkt skiljer mindre än så kan byta
#: plats mellan dumpen och motorn — då är snappen inte avgjord av datat.
TRUNK_MARGINAL = math.sqrt(3.0) * 2.0


class Vagran(Exception):
    """Verktyget vägrar hellre än gissar. Meddelandet ska räcka för att åtgärda."""


def _v3(p) -> tuple[float, float, float]:
    if not isinstance(p, (list, tuple)) or len(p) != 3:
        raise Vagran(f"väntade en [x, y, z], fick {p!r}")
    return (float(p[0]), float(p[1]), float(p[2]))


def floor_grid(v: float) -> int:
    """``geom.rs::floor_grid``."""
    return math.floor(v / GRID)


class Graf:
    """Ren grafmodell med motorns semantik. Inga BSP-anrop, inga cvarer."""

    def __init__(self, map_name: str, cells: list, links: list):
        self.map = map_name
        self.cells = [list(c) for c in cells]
        self.links = [dict(l) for l in links]
        self.grid: dict[tuple[int, int], list[int]] = {}
        for cid, c in enumerate(self.cells):
            self.grid.setdefault((floor_grid(c[0]), floor_grid(c[1])), []).append(cid)

    # -- konstruktion ------------------------------------------------------

    @classmethod
    def from_dump(cls, doc: dict) -> "Graf":
        """Läs en ``qw-nav-graph/1``-dump. Icke-kontinuerliga id vägras."""
        for key in ("cells", "links", "cell_ids"):
            if key not in doc:
                raise Vagran(f"dumpen saknar '{key}' — är det en qw-nav-graph/1-dump?")
        cell_ids = list(doc["cell_ids"])
        if cell_ids != list(range(len(cell_ids))):
            raise Vagran(
                "dumpens cell_ids är inte 0..n-1; modellen indexerar celler på id "
                "och kan inte spegla motorn på en gles dump"
            )
        raa = []
        for i, l in enumerate(doc["links"]):
            if "from" not in l or ("to_cell" not in l and "to" not in l):
                raise Vagran(f"länk {i} saknar from/to_cell")
            rec = {
                "from": int(l["from"]),
                "to_cell": int(l["to_cell"] if "to_cell" in l else l["to"]),
                "kind": str(l["kind"]).lower(),
                "T": 0 if l.get("T", l.get("traversable", 1)) in (0, False) else 1,
            }
            for k in graphstamp.LINK_PARAM_KEYS:
                if l.get(k) is not None:
                    rec[k] = l[k]
            raa.append(rec)

        # Dumpen listar länkar i cellordning (per-cell `out` + `out_pruned`), inte i
        # id-ordning — `link_ids` bär motorns riktiga index. Modellen MÅSTE indexera
        # på motorns id, annars pekar en remove-op på fel länk. Nivå-2 påverkas inte
        # (inventeringen sorteras), men ankarkontrollen och id-remappen gör det.
        link_ids = doc.get("link_ids")
        if link_ids is None:
            raise Vagran(
                "dumpen saknar 'link_ids' — utan motorns länk-id går en remove-op "
                "inte att ankra. Kör mkgraph_full mot en motor med out_pruned."
            )
        link_ids = [int(x) for x in link_ids]
        if len(link_ids) != len(raa) or sorted(link_ids) != list(range(len(raa))):
            raise Vagran(
                "dumpens link_ids är inte en permutation av 0..n-1; id-rymden går "
                "inte att återskapa och remove-opens remap kan inte modelleras"
            )
        links = [None] * len(raa)
        for rec, lid in zip(raa, link_ids):
            links[lid] = rec
        return cls(str(doc.get("map") or "dm3"), doc["cells"], links)

    def kopia(self) -> "Graf":
        return Graf(self.map, self.cells, self.links)

    # -- motorns uppslag ---------------------------------------------------

    def _kolumner(self, gx: int, gy: int, radius: int) -> list[int]:
        """``NavGraph::neighbors_within`` — dx yttre, dy inre, kolumnordning bevarad."""
        out: list[int] = []
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                out.extend(self.grid.get((gx + dx, gy + dy), ()))
        return out

    def _d2(self, cid: int, p) -> float:
        c = self.cells[cid]
        return (c[0] - p[0]) ** 2 + (c[1] - p[1]) ** 2 + (c[2] - p[2]) ** 2

    def nearest(self, p) -> int | None:
        """``NavGraph::nearest`` (query.rs:54), loop för loop.

        Obundet: söker utåt kolumnvis till radie 4 och bryter så snart något
        hittats och radien passerat 1. Strikt ``<`` gör att den FÖRSTA
        minimipunkten vinner vid lika avstånd — samma tie-break som motorn.
        """
        gx, gy = floor_grid(p[0]), floor_grid(p[1])
        best: tuple[int, float] | None = None
        for radius in range(0, 5):
            for cid in self._kolumner(gx, gy, radius):
                d = self._d2(cid, p)
                if best is None or d < best[1]:
                    best = (cid, d)
            if best is not None and radius >= 1:
                break
        return None if best is None else best[0]

    def nearest_rangordnad(self, p, n: int = 2) -> list[tuple[int, float]]:
        """De n närmaste inom motorns sökfönster, för marginalbeviset."""
        gx, gy = floor_grid(p[0]), floor_grid(p[1])
        sedda = dict.fromkeys(self._kolumner(gx, gy, 4))
        rank = sorted(((cid, math.sqrt(self._d2(cid, p))) for cid in sedda), key=lambda t: t[1])
        return rank[:n]

    def cell_within(self, p, horiz: float, vert: float) -> int | None:
        """``NavGraph::nearest_within`` (mod.rs:2048): bundet uppslag."""
        gx, gy = floor_grid(p[0]), floor_grid(p[1])
        r = math.ceil(horiz / GRID)
        kandidater = []
        for cid in self._kolumner(gx, gy, r):
            c = self.cells[cid]
            if math.hypot(c[0] - p[0], c[1] - p[1]) <= horiz and abs(c[2] - p[2]) <= vert:
                kandidater.append(cid)
        if not kandidater:
            return None
        return min(kandidater, key=lambda cid: self._d2(cid, p))

    # -- mutationer --------------------------------------------------------

    def insert_cell(self, origin) -> int:
        cid = len(self.cells)
        self.cells.append(list(origin))
        self.grid.setdefault((floor_grid(origin[0]), floor_grid(origin[1])), []).append(cid)
        return cid

    def insert_link(self, frm: int, to: int, kind: str, params: dict | None = None) -> int:
        rec = {"from": int(frm), "to_cell": int(to), "kind": kind, "T": 1}
        for k in graphstamp.LINK_PARAM_KEYS:
            if params and params.get(k) is not None:
                rec[k] = params[k]
        self.links.append(rec)
        return len(self.links) - 1

    def remove_links_by_id(self, ids: list[int]) -> None:
        """``NavGraph::remove_links_by_id``: kompaktera och bygg om adjacensen."""
        n = len(self.links)
        sedda: set[int] = set()
        for i in ids:
            if i < 0 or i >= n:
                raise Vagran(f"okänt länk-id {i}")
            if i in sedda:
                raise Vagran(f"dubblerat länk-id {i}")
            sedda.add(i)
        kvar = [dict(l) for i, l in enumerate(self.links) if i not in sedda]
        if T_ATERUPPSTAR:
            # push_link lägger varje behållen länk i adjacensen igen. En tidigare
            # rensad länk (T=0) blir därmed traverserbar. Det är motorns beteende,
            # inte en förenkling: se modulhuvudet.
            for l in kvar:
                l["T"] = 1
        self.links = kvar

    # -- identitet ---------------------------------------------------------

    def _doc(self, med_params: bool) -> dict:
        links = []
        for l in self.links:
            rec = {"from": l["from"], "to_cell": l["to_cell"], "kind": l["kind"], "T": l["T"]}
            if med_params:
                for k in graphstamp.LINK_PARAM_KEYS:
                    if l.get(k) is not None:
                        rec[k] = l[k]
            links.append(rec)
        return {
            "map": self.map,
            "cells": self.cells,
            "cell_ids": list(range(len(self.cells))),
            "links": links,
        }

    def rj_links(self) -> int:
        return sum(1 for l in self.links if l["kind"] == "rocketjump")

    def identitet(self, register: list | None = None) -> dict:
        """Nivå-1 + nivå-2. Två nivå-2: med och utan V296-params.

        ``graph_content_hash`` är den params-bärande (grok2 1f71274, korskontrakt
        F1/F2) — den är receptets egen härledning och skiljer en carried-märkt
        1167->1191 från en omärkt. ``graph_content_hash_utan_params`` är den som
        en motordump kan jämföras mot, eftersom motorns graf inte bär ``carried``
        (fältet finns inte i ``Cmd::PlanLink``). Båda skrivs, med namn, för att
        ingen ska jämföra fel par.
        """
        cells, links, rj = len(self.cells), len(self.links), self.rj_links()
        stamp = graphstamp.graph_stamp(self.map, cells, links, rj)
        hit = graphstamp.match_kollision(cells, links, rj, stamp, register or [])
        return {
            "map": self.map,
            "cells": cells,
            "links": links,
            "rj_links": rj,
            "graph_stamp": str(stamp),
            "graph_content_hash": graphstamp.graph_content_hash(self._doc(True)),
            "graph_content_hash_utan_params": graphstamp.graph_content_hash(self._doc(False)),
            "kollision": hit,
        }


# ---------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------


def _anchor_gate(graf: Graf, spec: dict) -> None:
    """Ankarregeln (spec r2 §1): id ensamt duger aldrig som ankare."""
    lid = int(spec["id"])
    if lid >= len(graf.links):
        raise Vagran(f"länk-id {lid} finns inte i grafen ({len(graf.links)} länkar)")
    l = graf.links[lid]
    vill = (int(spec["from"]), int(spec["to"]), str(spec["kind"]).lower())
    har = (l["from"], l["to_cell"], l["kind"])
    if har != vill:
        raise Vagran(
            f"ankaret håller inte: id {lid} är {har[0]}->{har[1]} {har[2]}, "
            f"receptet säger {vill[0]}->{vill[1]} {vill[2]}. Ett rått länk-id från "
            f"en annan graf får aldrig bli apply-ankare."
        )


def op_remove_links(graf: Graf, op: dict) -> dict:
    specs = op.get("links") or []
    if not specs:
        raise Vagran(f"op '{op.get('name')}': remove_links utan länkar")
    for s in specs:
        _anchor_gate(graf, s)
    t0_noll = sum(1 for l in graf.links if l["T"] == 0)
    graf.remove_links_by_id([int(s["id"]) for s in specs])
    t1_noll = sum(1 for l in graf.links if l["T"] == 0)
    bevis = {
        "borttagna_id": [int(s["id"]) for s in specs],
        "ankare": [f"{s['from']}->{s['to']} {str(s['kind']).lower()}" for s in specs],
        "T0_fore": t0_noll,
        "T0_efter": t1_noll,
    }
    if t0_noll and not t1_noll:
        bevis["not"] = (
            f"{t0_noll} tidigare rensade länkar (T=0) är traverserbara igen: "
            "remove_links_by_id bygger om adjacensen från noll och rebuild_derived "
            "kör ingen teleport-rensning. Det är motorns beteende och det syns i nivå-2."
        )
    return bevis


def op_shelf_patch(graf: Graf, op: dict) -> dict:
    """``apply_one``: celler i tabellordning, sedan drops i tabellordning."""
    if op.get("cells") and not op.get("no_auto_walk"):
        raise Vagran(
            f"op '{op.get('name')}': plantering med auto-Walk går inte att räkna "
            "offline (plant_cell länkar till samma-z-grannar via BSP-trace). "
            "Sätt no_auto_walk om receptet planterar isolerade celler, annars är "
            "op:en utanför transformatorns modell."
        )
    snap_z = op.get("snap_z")
    nya_celler, hoppade_celler = [], []
    for c in op.get("cells") or []:
        p = _v3(c)
        fanns = graf.cell_within(p, CELL_WITHIN_XY, CELL_WITHIN_Z)
        if fanns is not None:
            hoppade_celler.append({"origin": list(p), "cell": fanns})
            continue
        origin = (p[0], p[1], float(snap_z) if snap_z is not None else p[2])
        nya_celler.append({"origin": list(origin), "cell": graf.insert_cell(origin)})

    nya_drops, hoppade_drops = [], []
    for d in op.get("drops") or []:
        frm, to = _v3(d["from"]), _v3(d["to"])
        fc = graf.cell_within(frm, CELL_WITHIN_XY, CELL_WITHIN_Z)
        if fc is None:
            raise Vagran(f"op '{op.get('name')}': drop från {list(frm)} träffar ingen cell")
        tc = graf.cell_within(to, DROP_REACH_XY, DROP_REACH_Z)
        if tc is None:
            raise Vagran(f"op '{op.get('name')}': drop till {list(to)} träffar ingen cell")
        if "to_cell" in d and int(d["to_cell"]) != tc:
            raise Vagran(
                f"op '{op.get('name')}': dropets ankare säger målcell {d['to_cell']}, "
                f"origin {list(to)} snappar till {tc}"
            )
        if any(l["from"] == fc and l["to_cell"] == tc and l["kind"] == "drop" for l in graf.links):
            hoppade_drops.append({"from_cell": fc, "to_cell": tc})
            continue
        nya_drops.append({"link": graf.insert_link(fc, tc, "drop"), "from_cell": fc, "to_cell": tc})

    return {
        "nya_celler": nya_celler,
        "hoppade_celler": hoppade_celler,
        "nya_drops": nya_drops,
        "hoppade_drops": hoppade_drops,
        "bsp_not": (
            "plant_cell/plant_drop hull-tracar geometrin och kan VÄGRA. En vägran "
            "ger Failed, aldrig ett annat delta — modellen räknar det accepterade "
            "utfallet och den förseglade delstampen är beviset att motorn accepterade."
        ),
    }


def op_plan_link(graf: Graf, op: dict) -> dict:
    """``plant_link_resp`` -> ``plant_speed_jump``. Här avgörs V296:s delta.

    Vägen har exakt tre steg som rör grafen: ``nearest(from)``, ``nearest(tgt)``
    och ``push_speed_jump``. Ingen ``plant_cell``, ingen idempotenskontroll, ingen
    recertifiering av en befintlig länk. Deltat faller ut ur modellen; det antas
    inte, och de tre alternativa läsningarna avvisas var för sig med data.
    """
    frm, takeoff, tgt = _v3(op["from"]), _v3(op["takeoff"]), _v3(op["tgt"])

    def snap(namn: str, p) -> tuple[int, dict]:
        cid = graf.nearest(p)
        if cid is None:
            raise Vagran(f"op '{op.get('name')}': nearest({namn}) hittar ingen cell — grafen är tom?")
        rank = graf.nearest_rangordnad(p, 2)
        d0 = rank[0][1] if rank else 0.0
        d1 = rank[1][1] if len(rank) > 1 else float("inf")
        marginal = d1 - d0
        if marginal < TRUNK_MARGINAL:
            raise Vagran(
                f"op '{op.get('name')}': {namn} snappar till cell {cid} på {d0:.2f} u, "
                f"näst bästa {rank[1][0]} på {d1:.2f} u — marginalen {marginal:.2f} u är "
                f"inom trunkeringsfelet ({TRUNK_MARGINAL:.2f} u). Dumpen skriver "
                f"cellorigin med int(), så datat avgör inte snappen. Kräv en "
                f"otrunkerad dump innan den här op:en förseglas."
            )
        return cid, {
            "origin": list(p),
            "cell": cid,
            "cell_origin": list(graf.cells[cid]),
            "avstand": round(d0, 3),
            "nast_basta": rank[1][0] if len(rank) > 1 else None,
            "nast_basta_avstand": round(d1, 3) if math.isfinite(d1) else None,
            "marginal": round(marginal, 3),
        }

    from_cell, from_bevis = snap("from", frm)
    takeoff_cell, takeoff_bevis = snap("takeoff", takeoff)
    to_cell, to_bevis = snap("tgt", tgt)

    ankare = op.get("anchor") or {}
    for namn, vantad, faktisk in (
        ("from_cell", ankare.get("from_cell"), from_cell),
        ("to_cell", ankare.get("to_cell"), to_cell),
    ):
        if vantad is not None and int(vantad) != faktisk:
            raise Vagran(
                f"op '{op.get('name')}': ankaret väntar {namn}={vantad}, "
                f"origin-snappen ger {faktisk}. Ankaret är en korskontroll, "
                f"aldrig indata — antingen är receptet fel eller basen fel graf."
            )

    befintliga = [
        i for i, l in enumerate(graf.links) if l["from"] == from_cell and l["to_cell"] == to_cell
    ]
    # Fixturens `link_vid_cert` är gift som ankare: id-rymden är inte densamma
    # som den certifierades i. Rapporten pekar därför ut vad id:t träffar i
    # grafen SOM DEN SER UT FÖRE DEN HÄR OP:EN — inte i basen, om en tidigare op
    # redan kompakterat länkarrayen. Att kalla det "i basen" hade varit fel så
    # snart en remove-op ligger före.
    cert_id = op.get("link_vid_cert")
    cert_ar = None
    if cert_id is not None and 0 <= int(cert_id) < len(graf.links):
        l = graf.links[int(cert_id)]
        cert_ar = f"{l['from']}->{l['to_cell']} {l['kind']}"

    celler_fore = len(graf.cells)
    li = graf.insert_link(
        from_cell,
        to_cell,
        "speedjump",
        {"carried": op.get("carried"), "v_req": op.get("v_req"), "gain": op.get("gain")},
    )
    return {
        "from": from_bevis,
        "takeoff": takeoff_bevis,
        "tgt": to_bevis,
        "planterad_lank": li,
        "d_celler": len(graf.cells) - celler_fore,
        "befintliga_lankar_from_till_tgt": [
            {"id": i, "kind": graf.links[i]["kind"]} for i in befintliga
        ],
        "link_vid_cert": None
        if cert_id is None
        else {
            "id": int(cert_id),
            "pekar_pa_i_grafen_fore_op": cert_ar,
            "not": "cert-id är inte ett ankare: id-rymden flyttas av varje tidigare op",
        },
        "uteslutna_lasningar": {
            "+0/+0 (carried-cert av befintlig länk)": (
                "utesluten: 0 länkar {}->{} i grafen före op:en".format(from_cell, to_cell)
                if not befintliga
                else "EJ utesluten: {} länk(ar) {}->{} finns redan".format(
                    len(befintliga), from_cell, to_cell
                )
            ),
            "+1/+1 (PlanLink carvar en cell)": (
                "utesluten: plant_link_resp kallar plant_speed_jump och innehåller "
                "ingen plant_cell — båda ändarna snappade till befintliga celler"
            ),
            "+4/+10 (west-shelf)": (
                "utesluten: annan receptfil, maskinhyllan z~88; den här op:ens punkter "
                "ligger på z=296/328"
            ),
        },
    }


OPS = {
    "remove_links": op_remove_links,
    "shelf_patch": op_shelf_patch,
    "plan_link": op_plan_link,
}


# ---------------------------------------------------------------------------
# Körning
# ---------------------------------------------------------------------------


def _identitet_matchar(fick: dict, vantat: dict) -> list[str]:
    """Vilka fält som skiljer. Tom lista = allt stämmer."""
    fel = []
    for key in ("cells", "links", "rj_links"):
        if key in vantat and int(vantat[key]) != int(fick[key]):
            fel.append(f"{key}: fick {fick[key]}, väntade {vantat[key]}")
    if "graph_stamp" in vantat and str(vantat["graph_stamp"]) != fick["graph_stamp"]:
        fel.append(f"nivå-1: fick {fick['graph_stamp']}, väntade {vantat['graph_stamp']}")
    if "graph_content_hash" in vantat:
        # Jämför mot den params-fria: en motordump bär inga V296-params.
        got = fick["graph_content_hash_utan_params"]
        if str(vantat["graph_content_hash"]) != got:
            fel.append(f"nivå-2: fick {got}, väntade {vantat['graph_content_hash']}")
    return fel


def manifestsokvag(receptvag: str | Path) -> Path:
    """Manifestet som hör till en receptfil: ``<stam>.manifest.json`` bredvid den."""
    p = Path(receptvag)
    return p.with_name(p.stem + ".manifest.json")


def korskontrollera_manifest(steg: list, annat: dict) -> list[str]:
    """Nivå-1-krockar mellan två komponat.

    Registret fångar namnkollisioner mellan *kända grafer*. Det här fångar den
    andra sorten: två op-listor vars steg landar på samma counts/FNV men olika
    inventering. Den farliga formen är en MELLANSTAMP i den ena som är lika med
    SLUTSTAMPEN i den andra — en grind som läser counts/FNV ser då "klart" på ett
    halvapplicerat komponat. Nivå-2 skiljer dem, och det är hela poängen med att
    säga det högt i stället för att lita på att någon läser rätt kolumn.
    """
    varningar = []
    andra = {
        s["identitet"]["graph_stamp"]: s
        for s in annat.get("steg", [])
        if s.get("index", 0) > 0
    }
    sista = (annat.get("steg") or [{}])[-1].get("index")
    for s in steg:
        if s["index"] == 0:
            continue
        i = s["identitet"]
        träff = andra.get(i["graph_stamp"])
        if träff is None:
            continue
        deras = träff["identitet"]
        if deras["graph_content_hash_utan_params"] == i["graph_content_hash_utan_params"]:
            continue  # samma graf, ingen fälla
        var = "SLUTSTAMPEN" if träff["index"] == sista else f"steg {träff['index']}"
        varningar.append(
            f"VARNING: nivå-1-krock mot {annat.get('recept_id')}: steg {s['index']} "
            f"({s['name']}) är {i['cells']}/{i['links']} FNV {i['graph_stamp']} — samma "
            f"som {var} ({träff['name']}) där. Olika grafer: nivå-2 {i['graph_content_hash_utan_params'][:16]}… "
            f"mot {deras['graph_content_hash_utan_params'][:16]}…. En grind som läser "
            f"counts/FNV kan inte skilja ett halvapplicerat komponat från ett färdigt."
        )
    return varningar


def kor_recept(bas: Graf, recept: dict, register: list) -> dict:
    """Applicera op-listan i ordning och skriv ett steg per op."""
    graf = bas.kopia()
    varningar: list[str] = []

    start = graf.identitet(register)
    pin = recept.get("base") or {}
    pinfel = _identitet_matchar(start, pin)
    if pinfel:
        raise Vagran(
            "op 0 (pin) avviker från basdumpen: " + "; ".join(pinfel) + ". Inget appliceras."
        )
    if start["kollision"]:
        varningar.append(graphstamp.warn_kollision(start["kollision"]))

    steg = [
        {
            "index": 0,
            "op": "pin",
            "name": recept.get("id", "bas"),
            "d_cells": 0,
            "d_links": 0,
            "identitet": start,
        }
    ]
    forra = start
    for i, op in enumerate(recept.get("ops") or [], start=1):
        art = op.get("op")
        if art not in OPS:
            raise Vagran(f"op {i}: okänd art {art!r} (kan: {', '.join(sorted(OPS))})")
        bevis = OPS[art](graf, op)
        nu = graf.identitet(register)
        d_cells = nu["cells"] - forra["cells"]
        d_links = nu["links"] - forra["links"]
        vantat = op.get("expect")
        if vantat:
            fel = []
            if "d_cells" in vantat and int(vantat["d_cells"]) != d_cells:
                fel.append(f"d_cells: fick {d_cells}, receptet säger {vantat['d_cells']}")
            if "d_links" in vantat and int(vantat["d_links"]) != d_links:
                fel.append(f"d_links: fick {d_links}, receptet säger {vantat['d_links']}")
            if fel:
                raise Vagran(f"op {i} ({op.get('name')}): " + "; ".join(fel))
        if nu["kollision"]:
            varningar.append(f"efter op {i} ({op.get('name')}): " + graphstamp.warn_kollision(nu["kollision"]))
        steg.append(
            {
                "index": i,
                "op": art,
                "name": op.get("name"),
                "d_cells": d_cells,
                "d_links": d_links,
                "harledd": vantat is None,
                "identitet": nu,
                "bevis": bevis,
            }
        )
        forra = nu

    return {
        "schema": "komponat-manifest/1",
        "recept_id": recept.get("id"),
        # Driftstatusen bor i receptet och följer med hit, så ett manifest aldrig
        # kan läsas som deploybart utan att receptet säger att det är det.
        "status": recept.get("status", "OKAND"),
        "status_skal": recept.get("status_skal"),
        "map": graf.map,
        "harledning": (
            "bas + op-lista i ordning, offline i transformator.py; ingen live-apply, "
            "ingen apply-then-copy, ingen summering av delstampars delta"
        ),
        "steg": steg,
        "slut": forra,
        "varningar": varningar,
    }


def kanonisk_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"


DEFAULT_BAS = "/home/xerial/lab/toolbox/dm3-base-full-graph.json"


def bygg_manifest(bas: Graf, recept: dict, register: list, receptvag: str | Path, basvag: str) -> dict:
    """Hela manifestet, i EN kodväg.

    `kor_recept` räcker inte som sanning om filen: korskontrollen mot det komponat
    receptet ersätter, och vilken basdump som lästes, hör också till artefakten.
    Låg de stegen kvar i `main` blev den committade filen något bara CLI:t kunde
    reproducera — och då är "byte-stabil" ett påstående ingen kan pröva
    (deepseeks korsreview av 2232fcc, punkt iv). Skriv- och testvägen går genom
    den här funktionen, så det som ligger i repot är per konstruktion verktygets
    utdata.
    """
    manifest = kor_recept(bas, recept, register)
    ersatter = (recept.get("ersatter") or {}).get("recept")
    if ersatter:
        annanvag = manifestsokvag(Path(receptvag).parent / Path(ersatter).name)
        try:
            annat = json.loads(annanvag.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest["varningar"].append(
                f"kunde inte korskontrollera mot {annanvag.name} — kör den först om "
                "krockkontrollen ska vara gjord"
            )
        else:
            manifest["korskontrollerat_mot"] = annat.get("recept_id")
            manifest["varningar"].extend(korskontrollera_manifest(manifest["steg"], annat))
    manifest["bas_dump"] = str(basvag)
    return manifest


# ---------------------------------------------------------------------------
# Validering mot de förseglade delstamparna
# ---------------------------------------------------------------------------

#: Ensamma-mot-bas-recept, som op-listor. Sanningen ligger i receptfilerna;
#: det här är bara vilken op-art var och en är och var ankarna kommer ifrån.
VALIDERING = [
    {
        "recept": "haz1462-k2.json",
        "op": lambda d: {
            "op": "remove_links",
            "name": "haz1462-k2",
            "links": d["remove_links"],
        },
    },
    {
        "recept": "ram-rail-v2.json",
        "op": lambda d: {
            "op": "shelf_patch",
            "name": "ram-rail-v2",
            "cells": [s["origin"] for s in d["source"]],
            "snap_z": 128.03125,
            "no_auto_walk": True,
            "drops": d["drops"],
        },
    },
    {
        "recept": "ram-prevent.json",
        "op": lambda d: {
            "op": "shelf_patch",
            "name": "ram-prevent",
            "cells": [],
            "drops": d["drops"],
        },
    },
]


def validera(bas: Graf, receptdir: Path, register: list) -> tuple[int, list[dict]]:
    """Kör varje känt recept ensamt mot bas och jämför med dess ``on_expected``."""
    utfall = []
    fel = 0
    for v in VALIDERING:
        p = receptdir / v["recept"]
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise Vagran(f"kan inte läsa {p}: {exc}") from exc
        recept = {"id": d["id"], "base": d["off"], "ops": [v["op"](d)]}
        try:
            man = kor_recept(bas, recept, register)
            slut = man["slut"]
            avvikelser = _identitet_matchar(slut, d["on_expected"])
        except Vagran as exc:
            slut, avvikelser = None, [str(exc)]
        ok = not avvikelser
        fel += 0 if ok else 1
        utfall.append(
            {
                "recept": d["id"],
                "ok": ok,
                "vantat": d["on_expected"],
                "fick": slut,
                "avvikelser": avvikelser,
            }
        )
    return fel, utfall


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="transformator.py",
        description=(
            "Härled per-op delta och mellanstampar för ett komponerat recept, offline. "
            "Vägrar hellre än gissar; alla antaganden står i manifestet."
        ),
        epilog=(
            "Exempel:\n"
            "  transformator.py --validera            # reproducera de tre förseglade delstamparna\n"
            "  transformator.py --recept recept/komponat-k2-v296-ram.json --ut manifest.json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--bas",
        default=DEFAULT_BAS,
        help="basdump (qw-nav-graph/1, komplett med T-flaggor)",
    )
    p.add_argument("--recept", help="komponerat recept (komponat/1)")
    p.add_argument("--ut", help="skriv manifestet hit i stället för stdout")
    p.add_argument(
        "--validera",
        action="store_true",
        help="kör K2/rail/prevent ensamma mot bas och jämför med deras förseglade on_expected",
    )
    p.add_argument("--receptdir", default=str(HERE / "recept"))
    p.add_argument("--register", default=str(graphstamp.DEFAULT_REGISTER))
    args = p.parse_args(argv)

    if not args.recept and not args.validera:
        p.print_usage(sys.stderr)
        print("ange --recept eller --validera", file=sys.stderr)
        return 2

    try:
        register = graphstamp.load_register(args.register)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"VÄGRAR: kan inte läsa kollisionsregistret: {exc}", file=sys.stderr)
        return 2

    try:
        doc = json.loads(Path(args.bas).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"VÄGRAR: kan inte läsa basdumpen {args.bas}: {exc}", file=sys.stderr)
        return 2

    try:
        bas = Graf.from_dump(doc)
    except Vagran as exc:
        print(f"VÄGRAR: {exc}", file=sys.stderr)
        return 2

    if args.validera:
        try:
            fel, utfall = validera(bas, Path(args.receptdir), register)
        except Vagran as exc:
            print(f"VÄGRAR: {exc}", file=sys.stderr)
            return 2
        for u in utfall:
            print(f"{'OK  ' if u['ok'] else 'FEL '} {u['recept']}")
            for a in u["avvikelser"]:
                print(f"       {a}")
        if fel:
            print(
                f"\n{fel} av {len(utfall)} recept reproduceras inte exakt — semantiken är fel, "
                "inte stampen. Ingen härledning får förseglas på den här modellen.",
                file=sys.stderr,
            )
            return 1
        print("\nAlla tre delstampar reproducerade exakt ur bas + op-lista.")
        if not args.recept:
            return 0

    try:
        recept = json.loads(Path(args.recept).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"VÄGRAR: kan inte läsa receptet {args.recept}: {exc}", file=sys.stderr)
        return 2

    try:
        manifest = bygg_manifest(bas, recept, register, args.recept, args.bas)
    except Vagran as exc:
        print(f"VÄGRAR: {exc}", file=sys.stderr)
        return 1

    blob = kanonisk_json(manifest)
    if args.ut:
        Path(args.ut).write_bytes(blob)
        print(f"manifest: {args.ut}")
    else:
        sys.stdout.buffer.write(blob)
    print(f"manifest_sha256 {hashlib.sha256(blob).hexdigest()}", file=sys.stderr)
    for w in manifest["varningar"]:
        print(w, file=sys.stderr)
    slut = manifest["slut"]
    print(
        f"slut {slut['cells']}/{slut['links']} nivå-1 {slut['graph_stamp']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
