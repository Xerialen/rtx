#!/usr/bin/env python3
"""MATAPPARAT: porta `out_pruned` pa cellsvaret till branch ring2quad.

Varfor: nivå-2 (grafens innehallshash) hashas over HELA inventariet — varje
cell och varje lank, ocksa de 15 som carven rensat ur adjacensen men behallit i
lankarrayen. `~/rtx-tools/mkgraph.py` gar via cellens `out`, som byggs ur
adjacensen, och tappar darfor precis de 15. Utan dem gar slutlaget for ett
recept inte att harleda oberoende — och ett observerat varde far aldrig bli sin
egen forvantan.

Verktyget som loser det finns redan: `testsuite/tools/mkgraph_full.py`
(gren toolbox/b-planner-telemetry). Det laser BADE `out` och `out_pruned` och
skriver T-flaggan per lank. Det kraver att motorn svarar med `out_pruned` — och
det faltet finns i b-grenen men inte i main.

Den har patchen ar den porten, ordagrant ur b-grenen: ett nytt LASFALT pa
cellsvaret. Den ror ingen botlogik, ingen kostnad, ingen graf. Den kan inte
andra hur boten ror sig — bara vad kanalen kan beratta.

Kalla: ~/rtx-toolbox-b @ toolbox/b-planner-telemetry
  crates/rtx-nav/src/navmesh/query.rs   pruned_out_links()
  crates/rtx-ctlproto/src/lib.rs        CellResp.out_pruned
  crates/rtx-game/src/control.rs        describe_cell fyller det
"""
import pathlib

ROOT = pathlib.Path("/home/xerial/rtx-ring2quad")


def patcha(rel, gammal, ny):
    p = ROOT / rel
    s = p.read_text()
    n = s.count(gammal)
    assert n == 1, f"{rel}: ankaret traffade {n} ganger, vill ha exakt 1"
    p.write_text(s.replace(gammal, ny, 1))
    print("patchad", rel)


# 1) Hjalparen: lankar som lamnar cellen men INTE ligger i adjacensen.
patcha(
    "crates/rtx-nav/src/navmesh/query.rs",
    """    /// Counts per link kind, for the load-time debug line.""",
    """    /// The links leaving `cell` that are present in the array but absent from the adjacency — the
    /// ones a carve pass severed on purpose (ids and side tables must stand still, so the link stays).
    /// The planner cannot take them; an inventory that omits them is incomplete, and one that lists
    /// them as `out` is wrong. Reported separately so a dump can be both.
    pub fn pruned_out_links(&self, cell: CellId) -> Vec<u32> {
        let live = &self.adjacency[cell as usize];
        self.links
            .iter()
            .enumerate()
            .filter(|(li, l)| l.from == cell && !live.contains(&(*li as u32)))
            .map(|(li, _)| li as u32)
            .collect()
    }

    /// Counts per link kind, for the load-time debug line.""",
)

# 2) Faltet pa cellsvaret.
patcha(
    "crates/rtx-ctlproto/src/lib.rs",
    """    pub out: Vec<CellLinkOut>,
    pub incoming: Vec<CellLinkIn>,
}""",
    """    pub out: Vec<CellLinkOut>,
    pub incoming: Vec<CellLinkIn>,
    /// Links leaving this cell that the carve severed: in the array, out of the adjacency, and
    /// untraversable.
    ///
    /// Without this a dump has to choose between two wrong answers: walk the adjacency and silently
    /// omit them, or walk the array and silently present them as walkable. The first is what produced
    /// the 48208-vs-48193 gap in the dm3 dump. Reported separately so an inventory can be complete
    /// *and* honest about what is actually traversable.
    #[serde(default)]
    pub out_pruned: Vec<CellLinkOut>,
}""",
)

# 3) Fyll det i describe_cell.
patcha(
    "crates/rtx-game/src/control.rs",
    """    proto::CellResp {
        cell,
        origin: a3(g.cell_origin(cell)),
        hazard: format!("{:?}", g.cell_hazard(cell)),
        ledge: g.is_ledge(cell),
        out,
        incoming,
    }""",
    """    // The links leaving this cell that the carve severed: present in the array, absent from the
    // adjacency, and untraversable. Same shape as `out` so a consumer can merge the two into a
    // complete inventory and keep the distinction.
    let out_pruned = g
        .pruned_out_links(cell)
        .into_iter()
        .map(|li| proto::CellLinkOut {
            link: li,
            kind: kind_name(g.link_kind(li)).to_string(),
            to_cell: g.link_target(li),
            to: a3(g.cell_origin(g.link_target(li))),
            cost: g.link_cost(li),
            tgt_hazard: format!("{:?}", g.cell_hazard(g.link_target(li))),
            hazard_hp: g.link_hazard_hp(li),
            water_extra: g.link_water_extra(li),
        })
        .collect();
    proto::CellResp {
        cell,
        origin: a3(g.cell_origin(cell)),
        hazard: format!("{:?}", g.cell_hazard(cell)),
        ledge: g.is_ledge(cell),
        out,
        incoming,
        out_pruned,
    }""",
)

print("KLART")
