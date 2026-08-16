//! Map-pinned navmesh patches, applied once when a build finishes.
//!
//! The column carve samples one column per `GRID` step of XY, so a walkable surface narrower than
//! that pitch and out of phase with it is invisible to the automatic build — see
//! [`NavGraph::plant_cell`]'s doc for the worked example, dm3's machinery shelf west of SNG. A bot
//! that ends up on such a surface localizes through `nearest` to a floor far below it, plans routes
//! that are fiction from where it actually stands, and wedges until the round ends.
//!
//! [`plant_cell`](NavGraph::plant_cell) / [`plant_drop`](NavGraph::plant_drop) exist for exactly
//! that failure class, but only as control-channel verbs — nothing applied them in production, so
//! every server restart forgot the shelf. This module is the missing wiring: a short, reviewable
//! table of hand-verified plants per map, applied right after the build finishes, gated by
//! `rtx_nav_patch` (default on).
//!
//! Fail-closed, and transactional: every mutation goes through the build's own validators
//! (`plant_cell` refuses non-standable spots, `plant_drop` accepts only a drop
//! `classify_grounded` would itself emit), each planted cell must land on the standing height
//! measured on the shipped BSP (`snap_z` ± [`SNAP_TOL`] — a *local geometry precondition*, not a
//! whole-BSP fingerprint: it catches the floor moving, and the link validators catch the
//! surroundings changing, but a map edit that keeps this exact floor height passes), and each
//! patch mutates a clone that only replaces the live graph when everything validated. The outcome
//! is one unambiguous console line per patch: `applied` / `skipped (...)` / `failed (...)`. A
//! skipped or failed patch leaves the graph bit-for-bit what the build produced.
//!
//! Graph identity (GAP 1): the recipe is also pinned to a nivå-1 `graph_stamp` (FNV-1a-64 over
//! map + cell/link/rj counts, [`WORK_LOGS/graphstamp-kontrakt.md`]) and, when the pin carries one,
//! a nivå-2 SHA-256 content hash. A foreign carve is `Failed`, never a silent apply. `snap_z`
//! remains the local floor check; the stamp is the graph check.
//!
//! Undo (GAP 2): [`apply_txn`] keeps the pre-apply graph and [`AppliedTxn::unapply`] restores it
//! bit-identically without process death. Build-time [`apply_for_map`] does not keep a snapshot
//! (the default-on production path). Audit lines carry `applied`/`unapplied` plus the stamps.

use glam::Vec3;
use sha2::{Digest, Sha256};

use crate::bsp::Bsp;
use crate::navmesh::{Link, LinkKind, NavGraph};

/// Tolerance around [`ShelfPatch::snap_z`] for the floor-snap fingerprint. Standing heights come
/// out of the hull trace at exact model coordinates, so a correct BSP matches to well under a unit;
/// anything past this is a different floor than the one the patch was measured on.
const SNAP_TOL: f32 = 0.5;

/// How close an existing cell must be (XY/Z) for a patch position to count as already meshed —
/// mirrors `plant_cell`'s own same-spot test, so the answer agrees with what planting would do.
const ALREADY_XY: f32 = 8.0;
const ALREADY_Z: f32 = 8.0;

/// Graph identity a recipe may apply against. Counts + FNV are required; content hash is optional
/// (nivå 2 — pin it when the golden inventory is known).
#[derive(Clone, Copy, Debug)]
pub struct GraphPin {
    pub cells: u32,
    pub links: u32,
    pub rj_links: u32,
    /// Precomputed FNV-1a-64 of `(map, cells, links, rj_links)`. Must equal [`graph_stamp`].
    pub stamp: u64,
    /// SHA-256 hex of the canonical inventory, or `None` when nivå 2 is not pinned.
    pub content_hash: Option<&'static str>,
}

/// One un-carved standable surface: the cells that give it honest positions and the drops that give
/// it a way off. Positions are aim points (a couple of units above the surface); `plant_cell` snaps
/// them to the actual floor.
pub struct ShelfPatch {
    /// `level.mapname` this patch is pinned to.
    pub map: &'static str,
    /// Short id for the console status line.
    pub name: &'static str,
    /// Cell aim points along the surface.
    pub cells: &'static [[f32; 3]],
    /// `(from, to)` aim points per drop; `from` must resolve to a patch cell, `to` to a carved cell.
    pub drops: &'static [([f32; 3], [f32; 3])],
    /// Standing height every planted cell must snap to on the shipped BSP.
    pub snap_z: f32,
    /// Graph this recipe was measured against. Apply refuses any other identity.
    pub pin: GraphPin,
}

/// Look up a table recipe by short name. Unknown names are a hard error for `fixa` — the table
/// is the only apply path (no second planter). `apply_for_map` still walks only
/// [`PATCHES`] (west-shelf default-on). Ram recipes are named-only (`fixa`).
pub fn patch_by_name(name: &str) -> Option<&'static ShelfPatch> {
    PATCHES.iter().chain(RAM_RECIPES.iter()).find(|p| p.name == name)
}

/// Every named recipe `fixa` may apply (west-shelf + ram package).
pub fn registered_recipe_names() -> impl Iterator<Item = &'static str> {
    PATCHES.iter().chain(RAM_RECIPES.iter()).map(|p| p.name)
}

/// Counts + both identity levels for a live graph. `graph_stamp` is the decimal string.
pub fn live_identity(map: &str, graph: &NavGraph) -> (u32, u32, u32, String, String) {
    let cells = graph.cells.len() as u32;
    let links = graph.links.len() as u32;
    let rj = graph.summary().rocket_jump;
    (
        cells,
        links,
        rj,
        graph_stamp(map, cells, links, rj).to_string(),
        graph_content_hash(graph),
    )
}

/// dm3 west-shelf was measured on upstream main (`cc5fa8e`) — the **base** carve, not arm A's
/// V296-plant (5978/48208). Nivå 1 + 2 goldens: `WORK_LOGS/graphstamp-kontrakt.md` §5 / §8.4.
const WEST_SHELF_PIN: GraphPin = GraphPin {
    cells: 5977,
    links: 48207,
    rj_links: 0,
    stamp: 906_595_427_771_298_736,
    content_hash: Some("58787ce0d27ddd49ef109fa380ad5aca1c5fb65ba5125d485ad0e2ebd0f88ad9"),
};

/// The pinned patch table. One entry so far.
///
/// dm3 west shelf (`sng-t`/`lifts` boundary, x −920..−845, y −48): the machinery-top strip bots
/// climb onto in pairs during normal play and then never leave — measured on upstream main
/// (cc5fa8ea) at 13/0/0/20/94/53 stall firings per 600 s T2 across six runs, with per-bot
/// standstill rising to 27-33.5 s in the big-episode runs (10.6-19 s otherwise). The south face is solid (drops that way fail
/// `classify_grounded`); the open lip is north, so every drop lands on the y=0 floor row. Cell
/// spacing follows the measured wander range of trapped bots (x −847..−872 around each episode's
/// entry point) so localization never has to reach more than ~15 u.
pub const PATCHES: &[ShelfPatch] = &[ShelfPatch {
    map: "dm3",
    name: "west-shelf",
    cells: &[
        [-920.0, -48.0, 90.0],
        [-895.0, -48.0, 90.0],
        [-865.0, -48.0, 90.0],
        [-845.0, -48.0, 90.0],
    ],
    drops: &[
        ([-920.0, -48.0, 90.0], [-920.0, 0.0, -16.0]),
        ([-895.0, -48.0, 90.0], [-895.0, 0.0, -16.0]),
        ([-865.0, -48.0, 90.0], [-865.0, 0.0, -16.0]),
        ([-845.0, -48.0, 90.0], [-845.0, 0.0, -16.0]),
    ],
    snap_z: 88.03125,
    pin: WEST_SHELF_PIN,
}];

/// Rail Y coordinates (facit-ram-paket §1). 32 u GRID, y=−784..−624.
const RAM_RAIL_YS: [f32; 6] = [-784.0, -752.0, -720.0, -688.0, -656.0, -624.0];

/// Recovery rail: six indegree-0 cells on the west sliver + one Drop each to
/// the fixture-pinned floor `[-352,-672,-16]` (638-area; geometry probe).
/// Not in [`PATCHES`] — named `fixa` only, never default-on with west-shelf.
pub const RAM_RAIL: ShelfPatch = ShelfPatch {
    map: "dm3",
    name: "ram-rail",
    cells: &[
        [-360.0, -784.0, 128.03125],
        [-360.0, -752.0, 128.03125],
        [-360.0, -720.0, 128.03125],
        [-360.0, -688.0, 128.03125],
        [-360.0, -656.0, 128.03125],
        [-360.0, -624.0, 128.03125],
    ],
    drops: &[
        ([-360.0, -784.0, 128.03125], [-352.0, -672.0, -16.0]),
        ([-360.0, -752.0, 128.03125], [-352.0, -672.0, -16.0]),
        ([-360.0, -720.0, 128.03125], [-352.0, -672.0, -16.0]),
        ([-360.0, -688.0, 128.03125], [-352.0, -672.0, -16.0]),
        ([-360.0, -656.0, 128.03125], [-352.0, -672.0, -16.0]),
        ([-360.0, -624.0, 128.03125], [-352.0, -672.0, -16.0]),
    ],
    snap_z: 128.03125,
    pin: WEST_SHELF_PIN,
};

/// Prevention Drops (trajektorieprov + grok-dom): 733→669 and 734→670.
/// Existing cells only — no new rail/walk topology. Named `fixa` only.
pub const RAM_PREVENT: ShelfPatch = ShelfPatch {
    map: "dm3",
    name: "ram-prevent",
    cells: &[],
    drops: &[
        ([-248.0, -704.0, 152.0], [-320.0, -704.0, -16.0]),
        ([-248.0, -672.0, 152.0], [-320.0, -672.0, -16.0]),
    ],
    snap_z: 128.03125,
    pin: WEST_SHELF_PIN,
};

/// Ram package. Not walked by [`apply_for_map`].
pub const RAM_RECIPES: &[ShelfPatch] = &[RAM_RAIL, RAM_PREVENT];

/// Endpoint resolution bounds for a drop's `to` point — same rationale and values as the control
/// channel's `PlanDrop`: a target with nothing near it must be an error, not a silent snap to
/// whatever cell is closest somewhere else on the map.
const REACH_XY: f32 = 48.0;
const REACH_Z: f32 = 48.0;

/// What applying one patch did. Rendered into the console status line by the caller.
#[derive(Debug)]
pub enum Outcome {
    /// New topology went in (counts are the *new* cells/drops; pre-existing ones are not counted).
    Applied {
        cells: usize,
        drops: usize,
        stamp_before: u64,
        stamp_after: u64,
    },
    /// Every cell **and every drop** the patch asks for is already in the graph — a future carve
    /// that genuinely sees the whole surface makes the patch a no-op rather than a conflict. A
    /// carve that finds the cells but still misses the way off does *not* qualify; the missing
    /// drops get planted and the patch reports `Applied`.
    AlreadyMeshed,
    /// A precondition or a validator said no. The candidate graph is discarded whole, so the
    /// published graph — derived tables included — is bit-for-bit the one the build produced.
    Failed(String),
}

/// Pre-apply snapshot so [`unapply`](AppliedTxn::unapply) can restore the graph without a restart.
/// `fixa` (GAP 7) is the production consumer; cluster 1 only exposes the API.
#[allow(dead_code)]
pub struct AppliedTxn {
    pub name: &'static str,
    pub stamp_before: u64,
    pub stamp_after: u64,
    snapshot: NavGraph,
}

impl AppliedTxn {
    /// Restore the graph to the pre-apply snapshot. Returns the restored stamp for the audit line.
    #[allow(dead_code)]
    pub fn unapply(self, graph: &mut NavGraph) -> u64 {
        *graph = self.snapshot;
        self.stamp_before
    }
}

/// FNV-1a-64 over `map_utf8 ++ LE32(cells) ++ LE32(links) ++ LE32(rj_links)`.
pub fn graph_stamp(map: &str, cells: u32, links: u32, rj_links: u32) -> u64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in map
        .as_bytes()
        .iter()
        .copied()
        .chain(cells.to_le_bytes())
        .chain(links.to_le_bytes())
        .chain(rj_links.to_le_bytes())
    {
        h ^= b as u64;
        h = h.wrapping_mul(0x0000_0100_0000_01b3);
    }
    h
}

fn stamp_of(map: &str, graph: &NavGraph) -> u64 {
    graph_stamp(
        map,
        graph.cells.len() as u32,
        graph.links.len() as u32,
        graph.summary().rocket_jump,
    )
}

fn kind_token(kind: LinkKind) -> &'static str {
    match kind {
        LinkKind::Walk => "walk",
        LinkKind::Step => "step",
        LinkKind::Drop => "drop",
        LinkKind::JumpGap => "jump",
        LinkKind::DoubleJump => "doublejump",
        LinkKind::SpeedJump => "speedjump",
        LinkKind::Plat => "plat",
        LinkKind::Teleport => "teleport",
        LinkKind::Hook => "hook",
        LinkKind::RocketJump => "rocketjump",
        LinkKind::Swim => "swim",
    }
}

/// Canonical inventory bytes (kontrakt §8.2, no per-kind params — matches the dm3 golden dump).
fn canonical_inventory(graph: &NavGraph) -> String {
    let mut lines: Vec<String> = graph
        // CellId == Vec index in rtx (kontrakt §8.2 sorts on cell_id). dm3 holds that
        // identity; a future non-index cell table would need an explicit id field.
        .cells
        .iter()
        .enumerate()
        .map(|(id, c)| {
            format!(
                "C\t{id}\t{}\t{}\t{}",
                c.origin.x as i32, c.origin.y as i32, c.origin.z as i32
            )
        })
        .collect();
    let in_adj: std::collections::HashSet<u32> = graph.adjacency.iter().flatten().copied().collect();
    let mut lrecs: Vec<(u32, u32, &'static str, u8)> = graph
        .links
        .iter()
        .enumerate()
        .map(|(i, l)| {
            let t = if in_adj.contains(&(i as u32)) { 1 } else { 0 };
            (l.from, l.to, kind_token(l.kind), t)
        })
        .collect();
    lrecs.sort_unstable();
    for (src, dst, kind, t) in lrecs {
        lines.push(format!("L\t{src}\t{dst}\t{kind}\t{t}"));
    }
    lines.join("\n")
}

/// Nivå-2 SHA-256 hex of [`canonical_inventory`].
pub fn graph_content_hash(graph: &NavGraph) -> String {
    let mut h = Sha256::new();
    h.update(canonical_inventory(graph).as_bytes());
    format!("{:x}", h.finalize())
}

/// Console / audit line for one apply outcome (includes stamps on `applied`).
pub fn console_line(name: &str, outcome: &Outcome) -> String {
    match outcome {
        Outcome::Applied {
            cells,
            drops,
            stamp_before,
            stamp_after,
        } => format!(
            "rtx: navpatch {name}: applied ({cells} cells, {drops} drops) \
             stamp_before={stamp_before} stamp_after={stamp_after}\n"
        ),
        Outcome::AlreadyMeshed => format!("rtx: navpatch {name}: skipped (already meshed)\n"),
        Outcome::Failed(why) => format!("rtx: navpatch {name}: failed ({why})\n"),
    }
}

/// Audit line after a successful undo.
#[allow(dead_code)] // GAP 7 consumer
pub fn console_unapplied(name: &str, stamp: u64) -> String {
    format!("rtx: navpatch {name}: unapplied stamp={stamp}\n")
}

fn v(a: [f32; 3]) -> Vec3 {
    Vec3::new(a[0], a[1], a[2])
}

fn fully_meshed(patch: &ShelfPatch, graph: &NavGraph) -> bool {
    patch
        .cells
        .iter()
        .all(|&c| graph.cell_within(v(c), ALREADY_XY, ALREADY_Z).is_some())
        && patch.drops.iter().all(|&(from, to)| {
            let Some(from_cell) = graph.cell_within(v(from), ALREADY_XY, ALREADY_Z) else {
                return false;
            };
            let Some(to_cell) = graph.cell_within(v(to), REACH_XY, REACH_Z) else {
                return false;
            };
            graph
                .links
                .iter()
                .any(|l| l.from == from_cell && l.to == to_cell && l.kind == LinkKind::Drop)
        })
}

fn pin_matches(patch: &ShelfPatch, map: &str, graph: &NavGraph) -> Result<u64, String> {
    if map != patch.map {
        return Err(format!("map mismatch: graph map={map:?}, pin map={:?}", patch.map));
    }
    let cells = graph.cells.len() as u32;
    let links = graph.links.len() as u32;
    let rj = graph.summary().rocket_jump;
    let stamp = graph_stamp(map, cells, links, rj);
    if cells != patch.pin.cells || links != patch.pin.links || rj != patch.pin.rj_links || stamp != patch.pin.stamp {
        return Err(format!(
            "stamp mismatch: graph cells={cells} links={links} rj={rj} stamp={stamp}, \
             pin cells={} links={} rj={} stamp={}",
            patch.pin.cells, patch.pin.links, patch.pin.rj_links, patch.pin.stamp
        ));
    }
    if let Some(want) = patch.pin.content_hash {
        let got = graph_content_hash(graph);
        if got != want {
            return Err(format!("content_hash mismatch: graph={got}, pin={want}"));
        }
    }
    Ok(stamp)
}

/// Apply every patch pinned to `map`, in table order — transactionally: each patch mutates a
/// clone, which replaces `graph` (derived tables rebuilt) only when the whole patch validated.
/// A `Failed` patch therefore cannot leave partial topology or stale reachability/LOD behind.
pub fn apply_for_map(map: &str, bsp: &Bsp, graph: &mut NavGraph) -> Vec<(&'static str, Outcome)> {
    let mut out = Vec::new();
    for patch in PATCHES.iter().filter(|p| p.map == map) {
        let mut candidate = graph.clone();
        let outcome = apply_one(patch, Some(bsp), &mut candidate);
        if let Outcome::Applied { .. } = outcome {
            candidate.rebuild_derived();
            *graph = candidate;
        }
        out.push((patch.name, outcome));
    }
    out
}

/// Undoable apply: on `Applied` the live graph is replaced and the pre-apply snapshot is returned.
/// `Failed` / `AlreadyMeshed` leave `graph` untouched (the latter is `Err` so the caller can log it).
#[allow(dead_code)] // GAP 7 consumer
pub fn apply_txn(patch: &ShelfPatch, bsp: Option<&Bsp>, graph: &mut NavGraph) -> Result<AppliedTxn, Outcome> {
    let snapshot = graph.clone();
    let mut candidate = graph.clone();
    let outcome = apply_one(patch, bsp, &mut candidate);
    match outcome {
        Outcome::Applied {
            stamp_before,
            stamp_after,
            ..
        } => {
            candidate.rebuild_derived();
            *graph = candidate;
            Ok(AppliedTxn {
                name: patch.name,
                stamp_before,
                stamp_after,
                snapshot,
            })
        }
        other => Err(other),
    }
}

fn apply_one(patch: &ShelfPatch, bsp: Option<&Bsp>, graph: &mut NavGraph) -> Outcome {
    // Already-meshed is decided before the pin: a second apply on the *post*-plant graph must stay
    // idempotent. The pin describes the *pre*-apply carve; checking it first would turn a re-apply
    // into a false stamp-mismatch.
    //
    // Invariant (kluster-1 minor A/4a): AlreadyMeshed assumes the live graph *started* as the
    // pin-verified base (or is that base + this recipe). A foreign carve that happens to carry
    // the four shelf cells would no-op here. GAP 4 / `fixa --apply` start from the sealed OFF
    // pin, which is the verified-base gate.
    if fully_meshed(patch, graph) {
        return Outcome::AlreadyMeshed;
    }

    let stamp_before = match pin_matches(patch, patch.map, graph) {
        Ok(s) => s,
        Err(why) => return Outcome::Failed(why),
    };

    let mut new_cells = 0;
    for &c in patch.cells {
        if let Some(id) = graph.cell_within(v(c), ALREADY_XY, ALREADY_Z) {
            let z = graph.cell_origin(id).z;
            if (z - patch.snap_z).abs() > SNAP_TOL {
                return Outcome::Failed(format!(
                    "cell at {c:?} snapped to z={z}, expected {} ± {SNAP_TOL} — the floor here is not \
                     the one this patch was measured on",
                    patch.snap_z
                ));
            }
            continue;
        }
        let planted = match bsp {
            Some(bsp) => {
                let cells_before = graph.cells.len();
                let Some((id, _)) = graph.plant_cell(bsp, v(c)) else {
                    return Outcome::Failed(format!("no standable floor at {c:?}"));
                };
                let existed = graph.cells.len() == cells_before;
                let z = graph.cell_origin(id).z;
                if (z - patch.snap_z).abs() > SNAP_TOL {
                    return Outcome::Failed(format!(
                        "cell at {c:?} snapped to z={z}, expected {} ± {SNAP_TOL} — the floor here is not \
                         the one this patch was measured on",
                        patch.snap_z
                    ));
                }
                !existed
            }
            None => {
                graph.insert_cell(Vec3::new(c[0], c[1], patch.snap_z));
                true
            }
        };
        if planted {
            new_cells += 1;
        }
    }

    let mut new_drops = 0;
    for &(from, to) in patch.drops {
        let Some(from_cell) = graph.cell_within(v(from), ALREADY_XY, ALREADY_Z) else {
            return Outcome::Failed(format!("drop from {from:?} resolves to no cell"));
        };
        let Some(to_cell) = graph.cell_within(v(to), REACH_XY, REACH_Z) else {
            return Outcome::Failed(format!("drop to {to:?} resolves to no cell"));
        };
        if graph
            .links
            .iter()
            .any(|l| l.from == from_cell && l.to == to_cell && l.kind == LinkKind::Drop)
        {
            continue;
        }
        match bsp {
            Some(bsp) => {
                if graph.plant_drop(bsp, from_cell, to_cell).is_none() {
                    return Outcome::Failed(format!("drop {from:?} -> {to:?} is not one the build would emit"));
                }
            }
            None => graph.insert_link(Link {
                from: from_cell,
                to: to_cell,
                kind: LinkKind::Drop,
                cost: 1.0,
            }),
        }
        new_drops += 1;
    }

    if new_cells == 0 && new_drops == 0 {
        return Outcome::AlreadyMeshed;
    }
    let stamp_after = stamp_of(patch.map, graph);
    Outcome::Applied {
        cells: new_cells,
        drops: new_drops,
        stamp_before,
        stamp_after,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The table is data reviewed by eye; these hold the invariants the apply loop assumes.
    #[test]
    fn table_is_well_formed() {
        for p in PATCHES.iter().chain(RAM_RECIPES.iter()) {
            assert!(!p.map.is_empty() && p.map == p.map.to_lowercase());
            assert!(
                !p.drops.is_empty(),
                "{}: a shelf with no way off is still a trap",
                p.name
            );
            if p.cells.is_empty() {
                // Link-only recipe (ram-prevent): drops start on already-carved cells.
                assert_eq!(p.name, "ram-prevent");
            } else {
                for (from, _) in p.drops {
                    assert!(
                        p.cells.iter().any(|c| {
                            let dx = c[0] - from[0];
                            let dy = c[1] - from[1];
                            (dx * dx + dy * dy).sqrt() <= ALREADY_XY
                        }),
                        "{}: every drop must start on a patch cell",
                        p.name
                    );
                }
            }
            assert_eq!(
                p.pin.stamp,
                graph_stamp(p.map, p.pin.cells, p.pin.links, p.pin.rj_links),
                "{}: pin.stamp must equal FNV of the pin counts",
                p.name
            );
            if let Some(h) = p.pin.content_hash {
                assert_eq!(h.len(), 64, "{}: content_hash is SHA-256 hex", p.name);
                assert!(h.chars().all(|c| c.is_ascii_hexdigit()), "{}: hex", p.name);
            }
        }
        assert_eq!(RAM_RAIL.cells.len(), 6);
        assert_eq!(RAM_RAIL.drops.len(), 6);
        assert_eq!(RAM_PREVENT.cells.len(), 0);
        assert_eq!(RAM_PREVENT.drops.len(), 2);
        assert!(patch_by_name("ram-rail").is_some());
        assert!(patch_by_name("ram-prevent").is_some());
        assert!(patch_by_name("west-shelf").is_some());
        assert!(patch_by_name("no-such").is_none());
        for (i, &y) in RAM_RAIL_YS.iter().enumerate() {
            assert_eq!(RAM_RAIL.cells[i][0], -360.0);
            assert_eq!(RAM_RAIL.cells[i][1], y);
            assert_eq!(RAM_RAIL.cells[i][2], 128.03125);
            assert_eq!(RAM_RAIL.drops[i].1, [-352.0, -672.0, -16.0]);
        }
    }

    #[test]
    fn fnv_matches_contract_vectors_and_dm3_goldens() {
        // kontrakt §3
        let empty = graph_stamp("", 0, 0, 0);
        let mut h: u64 = 0xcbf2_9ce4_8422_2325;
        assert_eq!(h, 0xcbf2_9ce4_8422_2325);
        h ^= b'a' as u64;
        h = h.wrapping_mul(0x0000_0100_0000_01b3);
        assert_eq!(h, 0xaf63_dc4c_8601_ec8c);
        h = 0xcbf2_9ce4_8422_2325;
        for b in b"foobar" {
            h ^= *b as u64;
            h = h.wrapping_mul(0x0000_0100_0000_01b3);
        }
        assert_eq!(h, 0x8594_4171_f739_67e8);
        // empty message is the offset basis; graph_stamp("") still appends three LE32 zeros.
        assert_ne!(empty, 0xcbf2_9ce4_8422_2325);
        // kontrakt §5 + STATUS bas-graf
        assert_eq!(graph_stamp("dm3", 5978, 48208, 0), 13_090_435_456_435_551_592);
        assert_eq!(graph_stamp("dm3", 5977, 48207, 0), 906_595_427_771_298_736);
        assert_eq!(WEST_SHELF_PIN.stamp, graph_stamp("dm3", 5977, 48207, 0));
    }

    #[test]
    fn content_hash_matches_contract_minifixtures() {
        fn sha(s: &str) -> String {
            let mut h = Sha256::new();
            h.update(s.as_bytes());
            format!("{:x}", h.finalize())
        }
        assert_eq!(
            sha("C\t10\t0\t0\t0\nC\t11\t32\t0\t0\nL\t10\t11\twalk\t1\nL\t11\t10\twalk\t1"),
            "6d8af07e9580a26c19959861e21d295b95995d903fada013c4c4e54e142beeaf"
        );
        assert_eq!(
            sha("C\t10\t0\t0\t0\nC\t11\t32\t0\t0\nL\t10\t11\twalk\t1\nL\t11\t10\twalk\t0"),
            "6819c5bea29a4d690db502c8ef3186154dacb254548de82aa7a5ecd883a76c02"
        );

        let mut g = NavGraph::from_topology(
            &[Vec3::new(0.0, 0.0, 0.0), Vec3::new(32.0, 0.0, 0.0)],
            &[Link {
                from: 0,
                to: 1,
                kind: LinkKind::Walk,
                cost: 1.0,
            }],
        );
        g.insert_pruned_link(Link {
            from: 1,
            to: 0,
            kind: LinkKind::Walk,
            cost: 1.0,
        });
        assert_eq!(
            canonical_inventory(&g),
            "C\t0\t0\t0\t0\nC\t1\t32\t0\t0\nL\t0\t1\twalk\t1\nL\t1\t0\twalk\t0"
        );
        let hashed = graph_content_hash(&g);
        assert_eq!(hashed.len(), 64);
        // T=1 vs T=0 must not collide.
        let both_live = NavGraph::from_topology(
            &[Vec3::new(0.0, 0.0, 0.0), Vec3::new(32.0, 0.0, 0.0)],
            &[
                Link {
                    from: 0,
                    to: 1,
                    kind: LinkKind::Walk,
                    cost: 1.0,
                },
                Link {
                    from: 1,
                    to: 0,
                    kind: LinkKind::Walk,
                    cost: 1.0,
                },
            ],
        );
        assert_ne!(graph_content_hash(&both_live), hashed);
    }

    fn dest_origins() -> Vec<Vec3> {
        PATCHES[0].drops.iter().map(|&(_, to)| v(to)).collect()
    }

    fn shelf_origins_at(z: f32) -> Vec<Vec3> {
        PATCHES[0].cells.iter().map(|&c| Vec3::new(c[0], c[1], z)).collect()
    }

    fn drop_links(shelf_start: u32) -> Vec<Link> {
        (0..4)
            .map(|i| Link {
                from: shelf_start + i,
                to: i,
                kind: LinkKind::Drop,
                cost: 1.0,
            })
            .collect()
    }

    fn pin_for(graph: &NavGraph) -> GraphPin {
        let cells = graph.cells.len() as u32;
        let links = graph.links.len() as u32;
        let rj = graph.summary().rocket_jump;
        GraphPin {
            cells,
            links,
            rj_links: rj,
            stamp: graph_stamp("dm3", cells, links, rj),
            content_hash: None,
        }
    }

    fn fixture_patch(graph: &NavGraph) -> ShelfPatch {
        ShelfPatch {
            map: "dm3",
            name: "west-shelf-fixture",
            cells: PATCHES[0].cells,
            drops: PATCHES[0].drops,
            snap_z: PATCHES[0].snap_z,
            pin: pin_for(graph),
        }
    }

    fn topology_eq(a: &NavGraph, b: &NavGraph) -> bool {
        a.cells.len() == b.cells.len()
            && a.links.len() == b.links.len()
            && a.cells
                .iter()
                .zip(&b.cells)
                .all(|(x, y)| x.origin == y.origin && x.gx == y.gx && x.gy == y.gy)
            && a.links.iter().zip(&b.links).all(|(x, y)| {
                x.from == y.from && x.to == y.to && x.kind == y.kind && x.cost.to_bits() == y.cost.to_bits()
            })
            && a.adjacency == b.adjacency
    }

    #[test]
    fn stamp_mismatch_fails_closed() {
        let mut g = NavGraph::from_topology(&dest_origins(), &[]);
        let before = g.clone();
        let outcome = apply_one(&PATCHES[0], None, &mut g);
        match outcome {
            Outcome::Failed(why) => assert!(why.contains("stamp mismatch"), "{why}"),
            other => panic!("expected Failed, got {other:?}"),
        }
        assert!(topology_eq(&g, &before), "Failed must not mutate");
    }

    #[test]
    fn content_hash_mismatch_fails_closed() {
        let mut g = NavGraph::from_topology(&dest_origins(), &[]);
        let before = g.clone();
        let mut patch = fixture_patch(&g);
        patch.pin.content_hash = Some("0000000000000000000000000000000000000000000000000000000000000000");
        let outcome = apply_one(&patch, None, &mut g);
        match outcome {
            Outcome::Failed(why) => assert!(why.contains("content_hash mismatch"), "{why}"),
            other => panic!("expected Failed, got {other:?}"),
        }
        assert!(topology_eq(&g, &before));
    }

    #[test]
    fn already_meshed_is_idempotent() {
        let mut origins = dest_origins();
        origins.extend(shelf_origins_at(PATCHES[0].snap_z));
        let drops = drop_links(4);
        let mut g = NavGraph::from_topology(&origins, &drops);
        let patch = fixture_patch(&g);
        let before = g.clone();
        match apply_one(&patch, None, &mut g) {
            Outcome::AlreadyMeshed => {}
            other => panic!("expected AlreadyMeshed, got {other:?}"),
        }
        assert!(topology_eq(&g, &before));
        match apply_one(&patch, None, &mut g) {
            Outcome::AlreadyMeshed => {}
            other => panic!("second apply: {other:?}"),
        }
        assert!(topology_eq(&g, &before));
    }

    #[test]
    fn snap_z_mismatch_fails_without_publish() {
        let mut origins = dest_origins();
        origins.extend(shelf_origins_at(PATCHES[0].snap_z + 1.0));
        let mut g = NavGraph::from_topology(&origins, &[]);
        let patch = fixture_patch(&g);
        let before = g.clone();
        match apply_txn(&patch, None, &mut g) {
            Err(Outcome::Failed(why)) => assert!(why.contains("snapped to z="), "{why}"),
            Err(other) => panic!("expected snap_z Failed, got {other:?}"),
            Ok(_) => panic!("expected snap_z Failed, got Applied"),
        }
        assert!(topology_eq(&g, &before));
    }

    #[test]
    fn failed_rollback_discards_partial_plant() {
        // First shelf cell missing (would plant); second sits at a bad z → fail after a mutation
        // on the candidate. The live graph must stay bit-identical.
        let mut origins = dest_origins();
        let second = PATCHES[0].cells[1];
        origins.push(Vec3::new(second[0], second[1], PATCHES[0].snap_z + 1.0));
        let mut g = NavGraph::from_topology(&origins, &[]);
        let patch = fixture_patch(&g);
        let before = g.clone();
        match apply_txn(&patch, None, &mut g) {
            Err(Outcome::Failed(why)) => assert!(why.contains("snapped to z="), "{why}"),
            Err(other) => panic!("expected Failed, got {other:?}"),
            Ok(_) => panic!("expected Failed, got Applied"),
        }
        assert!(topology_eq(&g, &before), "partial plant must roll back");
    }

    #[test]
    fn undo_roundtrip_restores_topology() {
        let mut g = NavGraph::from_topology(&dest_origins(), &[]);
        let patch = fixture_patch(&g);
        let before = g.clone();
        let txn = apply_txn(&patch, None, &mut g).expect("fixture apply");
        assert!(g.cells.len() > before.cells.len());
        assert!(g.links.len() > before.links.len());
        assert_eq!(txn.stamp_before, stamp_of("dm3", &before));
        assert_eq!(txn.stamp_after, stamp_of("dm3", &g));
        assert_ne!(txn.stamp_before, txn.stamp_after);
        let line = console_line(
            txn.name,
            &Outcome::Applied {
                cells: 4,
                drops: 4,
                stamp_before: txn.stamp_before,
                stamp_after: txn.stamp_after,
            },
        );
        assert!(line.contains("stamp_before="));
        let stamp = txn.unapply(&mut g);
        assert_eq!(stamp, stamp_of("dm3", &before));
        assert!(topology_eq(&g, &before));
        assert!(console_unapplied("west-shelf-fixture", stamp).contains("unapplied stamp="));
    }

    #[test]
    fn apply_then_reapply_is_already_meshed() {
        let mut g = NavGraph::from_topology(&dest_origins(), &[]);
        let patch = fixture_patch(&g);
        apply_txn(&patch, None, &mut g).expect("first apply");
        // Pin still describes the *pre*-apply graph; already-meshed must win so re-apply is a no-op.
        let after = g.clone();
        match apply_one(&patch, None, &mut g) {
            Outcome::AlreadyMeshed => {}
            other => panic!("re-apply: {other:?}"),
        }
        assert!(topology_eq(&g, &after));
    }

    #[test]
    fn apply_undo_stale_on_link_does_not_panic() {
        // GAP 4 grind: a bot held ON-link 48216 after undo to 48207 links → query.rs:480 panic.
        let mut g = NavGraph::from_topology(&dest_origins(), &[]);
        let n0 = g.links.len() as u32;
        let patch = fixture_patch(&g);
        let txn = apply_txn(&patch, None, &mut g).expect("apply");
        let n1 = g.links.len() as u32;
        assert!(n1 > n0, "apply must add links so there is an ON-only id");
        let stale = n1 - 1;
        assert!(g.has_link(stale));
        let mut route = vec![stale];
        assert!(g.route_in_bounds(&route));
        txn.unapply(&mut g);
        assert!(!g.has_link(stale));
        assert!(!g.route_in_bounds(&route));
        let _ = g.link_kind(stale);
        let _ = g.link_target(stale);
        let _ = g.link_source(stale);
        let _ = g.link_cost(stale);
        let _ = g.cell_origin(n1); // planted cell id, also gone
        route.clear(); // replan
        assert!(g.route_in_bounds(&route));
    }

    fn landing_origin() -> Vec3 {
        Vec3::new(-352.0, -672.0, -16.0)
    }

    fn prevent_origins() -> Vec<Vec3> {
        vec![
            Vec3::new(-248.0, -704.0, 152.0), // 733
            Vec3::new(-248.0, -672.0, 152.0), // 734
            Vec3::new(-320.0, -704.0, -16.0), // 669
            Vec3::new(-320.0, -672.0, -16.0), // 670
            landing_origin(),                 // 638-area
        ]
    }

    fn ram_rail_patch(graph: &NavGraph) -> ShelfPatch {
        ShelfPatch {
            map: "dm3",
            name: "ram-rail",
            cells: RAM_RAIL.cells,
            drops: RAM_RAIL.drops,
            snap_z: RAM_RAIL.snap_z,
            pin: pin_for(graph),
        }
    }

    fn ram_prevent_patch(graph: &NavGraph) -> ShelfPatch {
        ShelfPatch {
            map: "dm3",
            name: "ram-prevent",
            cells: RAM_PREVENT.cells,
            drops: RAM_PREVENT.drops,
            snap_z: RAM_PREVENT.snap_z,
            pin: pin_for(graph),
        }
    }

    fn incoming<'a>(g: &'a NavGraph, cell: u32) -> Vec<&'a Link> {
        g.links.iter().filter(|l| l.to == cell).collect()
    }

    fn outgoing<'a>(g: &'a NavGraph, cell: u32) -> Vec<&'a Link> {
        g.links.iter().filter(|l| l.from == cell).collect()
    }

    #[test]
    fn ram_rail_indegree_zero_exactly_one_drop() {
        // Tempting same-z neighbor 32 u east of each rail slot — if apply
        // invented Walk, these would become in/out links.
        let mut origins = vec![landing_origin()];
        for &y in &RAM_RAIL_YS {
            origins.push(Vec3::new(-328.0, y, 128.03125));
        }
        let mut g = NavGraph::from_topology(&origins, &[]);
        let patch = ram_rail_patch(&g);
        apply_txn(&patch, None, &mut g).expect("ram-rail apply");
        assert_eq!(g.cells.len(), origins.len() + 6);
        assert_eq!(g.links.len(), 6);
        for &aim in RAM_RAIL.cells {
            let id = g.cell_within(v(aim), ALREADY_XY, ALREADY_Z).expect("rail cell planted");
            assert!(incoming(&g, id).is_empty(), "rail {aim:?} must have indegree 0");
            let out = outgoing(&g, id);
            assert_eq!(out.len(), 1, "rail {aim:?} must have exactly one out-link");
            assert_eq!(out[0].kind, LinkKind::Drop);
            let dest = g.cell_origin(out[0].to);
            assert_eq!(dest, landing_origin());
            assert!(
                !g.links
                    .iter()
                    .any(|l| { (l.from == id || l.to == id) && l.kind == LinkKind::Walk }),
                "rail {aim:?} must not grow Walk in or out"
            );
        }
    }

    #[test]
    fn ram_rail_stamp_mismatch_fails_closed() {
        let mut g = NavGraph::from_topology(&[landing_origin()], &[]);
        let before = g.clone();
        match apply_one(&RAM_RAIL, None, &mut g) {
            Outcome::Failed(why) => assert!(why.contains("stamp mismatch"), "{why}"),
            other => panic!("expected Failed, got {other:?}"),
        }
        assert!(topology_eq(&g, &before));
    }

    #[test]
    fn ram_rail_already_meshed_is_idempotent() {
        let mut origins = vec![landing_origin()];
        origins.extend(RAM_RAIL.cells.iter().map(|&c| Vec3::new(c[0], c[1], RAM_RAIL.snap_z)));
        let n_land = 1u32;
        let drops: Vec<Link> = (0..6)
            .map(|i| Link {
                from: n_land + i,
                to: 0,
                kind: LinkKind::Drop,
                cost: 1.0,
            })
            .collect();
        let mut g = NavGraph::from_topology(&origins, &drops);
        let patch = ram_rail_patch(&g);
        let before = g.clone();
        match apply_one(&patch, None, &mut g) {
            Outcome::AlreadyMeshed => {}
            other => panic!("expected AlreadyMeshed, got {other:?}"),
        }
        assert!(topology_eq(&g, &before));
    }

    #[test]
    fn ram_rail_undo_roundtrip() {
        let mut g = NavGraph::from_topology(&[landing_origin()], &[]);
        let patch = ram_rail_patch(&g);
        let before = g.clone();
        let txn = apply_txn(&patch, None, &mut g).expect("apply");
        assert_eq!(g.cells.len(), before.cells.len() + 6);
        assert_eq!(g.links.len(), before.links.len() + 6);
        txn.unapply(&mut g);
        assert!(topology_eq(&g, &before));
    }

    #[test]
    fn ram_rail_failed_rollback_discards_partial() {
        let mut origins = vec![landing_origin()];
        let second = RAM_RAIL.cells[1];
        origins.push(Vec3::new(second[0], second[1], RAM_RAIL.snap_z + 1.0));
        let mut g = NavGraph::from_topology(&origins, &[]);
        let patch = ram_rail_patch(&g);
        let before = g.clone();
        match apply_txn(&patch, None, &mut g) {
            Err(Outcome::Failed(why)) => assert!(why.contains("snapped to z="), "{why}"),
            Err(other) => panic!("expected Failed, got {other:?}"),
            Ok(_) => panic!("expected Failed, got Applied"),
        }
        assert!(topology_eq(&g, &before));
    }

    #[test]
    fn ram_rail_apply_then_reapply_is_already_meshed() {
        let mut g = NavGraph::from_topology(&[landing_origin()], &[]);
        let patch = ram_rail_patch(&g);
        apply_txn(&patch, None, &mut g).expect("first");
        let after = g.clone();
        match apply_one(&patch, None, &mut g) {
            Outcome::AlreadyMeshed => {}
            other => panic!("re-apply: {other:?}"),
        }
        assert!(topology_eq(&g, &after));
    }

    #[test]
    fn ram_prevent_plants_two_drops_no_cells() {
        let mut g = NavGraph::from_topology(&prevent_origins(), &[]);
        let before_cells = g.cells.len();
        let patch = ram_prevent_patch(&g);
        apply_txn(&patch, None, &mut g).expect("prevent apply");
        assert_eq!(g.cells.len(), before_cells, "prevention must not plant cells");
        assert_eq!(g.links.len(), 2);
        let from_733 = g
            .cell_within(Vec3::new(-248.0, -704.0, 152.0), ALREADY_XY, ALREADY_Z)
            .unwrap();
        let from_734 = g
            .cell_within(Vec3::new(-248.0, -672.0, 152.0), ALREADY_XY, ALREADY_Z)
            .unwrap();
        let to_669 = g
            .cell_within(Vec3::new(-320.0, -704.0, -16.0), REACH_XY, REACH_Z)
            .unwrap();
        let to_670 = g
            .cell_within(Vec3::new(-320.0, -672.0, -16.0), REACH_XY, REACH_Z)
            .unwrap();
        assert!(g
            .links
            .iter()
            .any(|l| l.from == from_733 && l.to == to_669 && l.kind == LinkKind::Drop));
        assert!(g
            .links
            .iter()
            .any(|l| l.from == from_734 && l.to == to_670 && l.kind == LinkKind::Drop));
        assert!(incoming(&g, from_733).is_empty());
        assert!(incoming(&g, from_734).is_empty());
    }

    #[test]
    fn ram_prevent_stamp_mismatch_fails_closed() {
        let mut g = NavGraph::from_topology(&prevent_origins(), &[]);
        let before = g.clone();
        match apply_one(&RAM_PREVENT, None, &mut g) {
            Outcome::Failed(why) => assert!(why.contains("stamp mismatch"), "{why}"),
            other => panic!("expected Failed, got {other:?}"),
        }
        assert!(topology_eq(&g, &before));
    }

    #[test]
    fn ram_prevent_undo_roundtrip() {
        let mut g = NavGraph::from_topology(&prevent_origins(), &[]);
        let patch = ram_prevent_patch(&g);
        let before = g.clone();
        let txn = apply_txn(&patch, None, &mut g).expect("apply");
        assert_eq!(g.links.len(), before.links.len() + 2);
        txn.unapply(&mut g);
        assert!(topology_eq(&g, &before));
    }

    #[test]
    fn ram_prevent_apply_then_reapply_is_already_meshed() {
        let mut g = NavGraph::from_topology(&prevent_origins(), &[]);
        let patch = ram_prevent_patch(&g);
        apply_txn(&patch, None, &mut g).expect("first");
        let after = g.clone();
        match apply_one(&patch, None, &mut g) {
            Outcome::AlreadyMeshed => {}
            other => panic!("re-apply: {other:?}"),
        }
        assert!(topology_eq(&g, &after));
    }

    #[test]
    fn apply_for_map_does_not_include_ram_recipes() {
        assert!(PATCHES.iter().all(|p| p.name == "west-shelf"));
        assert!(RAM_RECIPES.iter().all(|p| p.name.starts_with("ram-")));
    }
}
