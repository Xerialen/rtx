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
//! Fail-closed, in the same sense the DM3 route patch established: every mutation goes through the
//! build's own validators (`plant_cell` refuses non-standable spots, `plant_drop` accepts only a
//! drop `classify_grounded` would itself emit), each planted cell must land on the exact standing
//! height measured on the shipped BSP (`snap_z` ± [`SNAP_TOL`] — a re-lit or edited map misses it
//! and the patch reports `failed` instead of planting links into changed geometry), and the outcome
//! is one unambiguous console line per patch: `applied` / `skipped (...)` / `failed (...)`. A
//! skipped or failed patch leaves route planning exactly as it was before this module existed.

use glam::Vec3;

use crate::bsp::Bsp;
use crate::navmesh::NavGraph;

/// Tolerance around [`ShelfPatch::snap_z`] for the floor-snap fingerprint. Standing heights come
/// out of the hull trace at exact model coordinates, so a correct BSP matches to well under a unit;
/// anything past this is a different floor than the one the patch was measured on.
const SNAP_TOL: f32 = 0.5;

/// How close an existing cell must be (XY/Z) for a patch position to count as already meshed —
/// mirrors `plant_cell`'s own same-spot test, so the answer agrees with what planting would do.
const ALREADY_XY: f32 = 8.0;
const ALREADY_Z: f32 = 8.0;

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
}

/// The pinned patch table. One entry so far.
///
/// dm3 west shelf (`sng-t`/`lifts` boundary, x −920..−845, y −48): the machinery-top strip bots
/// climb onto in pairs during normal play and then never leave — measured on upstream main
/// (cc5fa8ea) at 0/0/13/20/94/53 stall firings per 600 s T2 across six runs, with per-bot
/// standstill doubling in the runs where it hits. The south face is solid (drops that way fail
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
}];

/// Endpoint resolution bounds for a drop's `to` point — same rationale and values as the control
/// channel's `PlanDrop`: a target with nothing near it must be an error, not a silent snap to
/// whatever cell is closest somewhere else on the map.
const REACH_XY: f32 = 48.0;
const REACH_Z: f32 = 48.0;

/// What applying one patch did. Rendered into the console status line by the caller.
pub enum Outcome {
    /// Cells planted (or found already present) and every drop in place.
    Applied { cells: usize, drops: usize },
    /// Every cell position already resolves to a cell — a future carve that sees the surface makes
    /// the patch a no-op rather than a conflict.
    AlreadyMeshed,
    /// A precondition or a validator said no. The graph keeps any cells planted before the failure
    /// (they are honest standing positions and `plant_cell` is idempotent), but no drops are added
    /// and the message says exactly what refused.
    Failed(String),
}

/// Apply every patch pinned to `map`, in table order. The caller owns the graph (build just
/// finished, not shared yet), runs [`NavGraph::rebuild_derived`] once if anything reports
/// `Applied`, and prints the status lines.
pub fn apply_for_map(map: &str, bsp: &Bsp, graph: &mut NavGraph) -> Vec<(&'static str, Outcome)> {
    PATCHES
        .iter()
        .filter(|p| p.map == map)
        .map(|p| (p.name, apply_one(p, bsp, graph)))
        .collect()
}

fn apply_one(patch: &ShelfPatch, bsp: &Bsp, graph: &mut NavGraph) -> Outcome {
    let v = |a: [f32; 3]| Vec3::new(a[0], a[1], a[2]);

    if patch
        .cells
        .iter()
        .all(|&c| graph.cell_within(v(c), ALREADY_XY, ALREADY_Z).is_some())
    {
        return Outcome::AlreadyMeshed;
    }

    let mut planted = Vec::with_capacity(patch.cells.len());
    for &c in patch.cells {
        let Some((id, _)) = graph.plant_cell(bsp, v(c)) else {
            return Outcome::Failed(format!("no standable floor at {c:?}"));
        };
        let z = graph.cell_origin(id).z;
        if (z - patch.snap_z).abs() > SNAP_TOL {
            return Outcome::Failed(format!(
                "cell at {c:?} snapped to z={z}, expected {} ± {SNAP_TOL} — geometry differs from \
                 the BSP this patch was measured on",
                patch.snap_z
            ));
        }
        planted.push(id);
    }

    let mut drops = 0;
    for &(from, to) in patch.drops {
        let Some(from_cell) = graph.cell_within(v(from), ALREADY_XY, ALREADY_Z) else {
            return Outcome::Failed(format!("drop from {from:?} resolves to no cell"));
        };
        let Some(to_cell) = graph.cell_within(v(to), REACH_XY, REACH_Z) else {
            return Outcome::Failed(format!("drop to {to:?} resolves to no cell"));
        };
        if graph.plant_drop(bsp, from_cell, to_cell).is_none() {
            return Outcome::Failed(format!(
                "drop {from:?} -> {to:?} is not one the build would emit"
            ));
        }
        drops += 1;
    }

    Outcome::Applied { cells: planted.len(), drops }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The table is data reviewed by eye; these hold the invariants the apply loop assumes.
    #[test]
    fn table_is_well_formed() {
        for p in PATCHES {
            assert!(!p.map.is_empty() && p.map == p.map.to_lowercase());
            assert!(!p.cells.is_empty(), "{}: a patch with no cells patches nothing", p.name);
            assert!(!p.drops.is_empty(), "{}: a shelf with no way off is still a trap", p.name);
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
    }
}
