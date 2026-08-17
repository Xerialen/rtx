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
//!
//! Chained undo (lucka A/B): the snapshots live in a [`TxnStack`], one entry per apply, not in a
//! single slot that each new apply overwrote. `apply×N` then `undo×N` therefore walks back to the
//! base graph bit-identically instead of stopping one recipe short of it. Undo restores a snapshot;
//! it never runs an inverse op list, because an inverse re-appends links at *new* ids — structurally
//! equal to the base, and not the same graph to anything that holds a link id.
//!
//! [`live_txn`] extends the same clone-then-publish guarantee to hand plants over the control
//! channel (lucka C), which used to edit the live graph in place with nothing to roll back to.

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
    /// When true, BSP `plant_cell` skips auto-Walk to same-z neighbours
    /// (ram-rail: slots are 32 u apart, inside the 48 u search). False for
    /// west-shelf, which must keep its Walk neighbours.
    pub no_auto_walk: bool,
    /// Delete these existing edges (id + from/to/kind pin). Empty for plant recipes.
    pub remove_links: &'static [RemoveLink],
    /// Change kind on these existing edges (id + from/to/old/new pin). Empty for plant recipes.
    pub retype_links: &'static [RetypeLink],
}

/// Fail-closed delete: id must exist *and* match from/to/kind, or apply is `Failed`.
#[derive(Clone, Copy, Debug)]
pub struct RemoveLink {
    pub id: u32,
    pub from: u32,
    pub to: u32,
    pub kind: LinkKind,
}

/// Fail-closed kind change: id must exist *and* match from/to/old_kind.
#[derive(Clone, Copy, Debug)]
pub struct RetypeLink {
    pub id: u32,
    pub from: u32,
    pub to: u32,
    pub old_kind: LinkKind,
    pub new_kind: LinkKind,
}

/// Look up a table recipe by short name. Unknown names are a hard error for `fixa` — the table
/// is the only apply path (no second planter). `apply_for_map` still walks only
/// [`PATCHES`] (west-shelf default-on). Ram recipes are named-only (`fixa`).
pub fn patch_by_name(name: &str) -> Option<&'static ShelfPatch> {
    PATCHES
        .iter()
        .chain(RAM_RECIPES.iter())
        .chain(HAZ1462_RECIPES.iter())
        .find(|p| p.name == name)
}

/// The name a hand-planted cell goes onto the undo chain under.
pub const TXN_PLAN_CELL: &str = "plan-cell";
/// The name a hand-planted drop goes onto the undo chain under.
pub const TXN_PLAN_DROP: &str = "plan-drop";
/// The name a hand-planted speed jump (the V296 class) goes onto the undo chain under.
pub const TXN_PLAN_LINK: &str = "plan-link";

/// Undo handles for the hand-plant verbs. Not recipes — there is no `ShelfPatch` to look up and
/// nothing to `--apply`; a plant arrives over `PlanCell` / `PlanDrop` / `PlanLink`. What they need
/// is a *name*, so `fixa --undo` can ask for the snapshot the plant left on the chain.
///
/// Without them the chain was write-only from the control channel's point of view: `do_fixa`
/// resolved the recipe through [`patch_by_name`] before dispatching, so `fixa --undo plan-link`
/// died on `unknown recipe` and a planted V296 could only be removed by reloading the map. The
/// snapshot existed and was unreachable (Sols review av `7670f9a`, F5).
///
/// The set is closed on purpose: a typo must still be refused, not silently accepted as "some
/// plant or other".
pub const PLANT_HANDLES: &[&str] = &[TXN_PLAN_CELL, TXN_PLAN_DROP, TXN_PLAN_LINK];

/// Resolve a name `fixa --undo` may ask for to its `'static` form, or `None` if it is neither a
/// registered recipe nor a plant handle. The `'static` lifetime is what lets the undo chain compare
/// against what it actually recorded instead of against a caller-supplied string.
pub fn undoable_name(name: &str) -> Option<&'static str> {
    PLANT_HANDLES
        .iter()
        .copied()
        .chain(registered_recipe_names())
        .find(|&n| n == name)
}

/// Everything `fixa --undo` accepts, for an error message that lists the real alternatives.
pub fn undoable_names() -> impl Iterator<Item = &'static str> {
    PLANT_HANDLES.iter().copied().chain(registered_recipe_names())
}

/// Every named recipe `fixa` may apply (west-shelf + ram + HAZ-1462 tournament).
pub fn registered_recipe_names() -> impl Iterator<Item = &'static str> {
    PATCHES
        .iter()
        .chain(RAM_RECIPES.iter())
        .chain(HAZ1462_RECIPES.iter())
        .map(|p| p.name)
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
    no_auto_walk: false,
    remove_links: &[],
    retype_links: &[],
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
    no_auto_walk: true,
    remove_links: &[],
    retype_links: &[],
};

/// Xerial path 1: east off the 7 u band toward the 670/702-line (x=−288),
/// same-y ballistic at walk speed — not straight down to 638 (x=−352).
/// Slot y=−688 lands on 702 `[−288,−672,−16]`, matching the demo landing
/// `(−291,−685)`. Named `fixa` only. `on_expected` is pinned later.
const RAM_RAIL_V2_LANDINGS: [[f32; 3]; 6] = [
    [-288.0, -800.0, -16.0],
    [-288.0, -768.0, -16.0],
    [-288.0, -736.0, -16.0],
    [-288.0, -672.0, -16.0],
    [-288.0, -640.0, -16.0],
    [-288.0, -608.0, -16.0],
];

pub const RAM_RAIL_V2: ShelfPatch = ShelfPatch {
    map: "dm3",
    name: "ram-rail-v2",
    cells: &[
        [-360.0, -784.0, 128.03125],
        [-360.0, -752.0, 128.03125],
        [-360.0, -720.0, 128.03125],
        [-360.0, -688.0, 128.03125],
        [-360.0, -656.0, 128.03125],
        [-360.0, -624.0, 128.03125],
    ],
    drops: &[
        ([-360.0, -784.0, 128.03125], [-288.0, -800.0, -16.0]),
        ([-360.0, -752.0, 128.03125], [-288.0, -768.0, -16.0]),
        ([-360.0, -720.0, 128.03125], [-288.0, -736.0, -16.0]),
        ([-360.0, -688.0, 128.03125], [-288.0, -672.0, -16.0]),
        ([-360.0, -656.0, 128.03125], [-288.0, -640.0, -16.0]),
        ([-360.0, -624.0, 128.03125], [-288.0, -608.0, -16.0]),
    ],
    snap_z: 128.03125,
    pin: WEST_SHELF_PIN,
    no_auto_walk: true,
    remove_links: &[],
    retype_links: &[],
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
    no_auto_walk: false,
    remove_links: &[],
    retype_links: &[],
};

/// Ram package. Not walked by [`apply_for_map`].
pub const RAM_RECIPES: &[ShelfPatch] = &[RAM_RAIL, RAM_RAIL_V2, RAM_PREVENT];

/// HAZ-1462 k1: drop Walk 10447 (1416→1461). Attested leave-link
/// (`deepseek-haz1462-diagnos.md`, grok2 JUSTERAS: next live walk is 10446).
/// Named `fixa` only — never default-on. ON-expected is pinned later.
pub const HAZ1462_K1: ShelfPatch = ShelfPatch {
    map: "dm3",
    name: "haz1462-k1",
    cells: &[],
    drops: &[],
    snap_z: 264.0,
    pin: WEST_SHELF_PIN,
    no_auto_walk: false,
    remove_links: &[RemoveLink {
        id: 10447,
        from: 1416,
        to: 1461,
        kind: LinkKind::Walk,
    }],
    retype_links: &[],
};

/// HAZ-1462 k2: drop Walk 10447 *and* the next high walk 10446 (1416→1459).
/// grok2: live-A* against high goals takes 10446→10768 if only 10447 is cut.
/// Named `fixa` only. ON-expected null until pinning.
pub const HAZ1462_K2: ShelfPatch = ShelfPatch {
    map: "dm3",
    name: "haz1462-k2",
    cells: &[],
    drops: &[],
    snap_z: 264.0,
    pin: WEST_SHELF_PIN,
    no_auto_walk: false,
    remove_links: &[
        RemoveLink {
            id: 10447,
            from: 1416,
            to: 1461,
            kind: LinkKind::Walk,
        },
        RemoveLink {
            id: 10446,
            from: 1416,
            to: 1459,
            kind: LinkKind::Walk,
        },
    ],
    retype_links: &[],
};

/// HAZ-1462 k3: retype Walk 10447 → Drop (same endpoints 1416→1461).
/// Cause class is still `slapp_lank` (fable-qa attested); this is a
/// kind-variant remedy, not `lagg_lank` (Drop 10444 already exists).
pub const HAZ1462_K3: ShelfPatch = ShelfPatch {
    map: "dm3",
    name: "haz1462-k3",
    cells: &[],
    drops: &[],
    snap_z: 264.0,
    pin: WEST_SHELF_PIN,
    no_auto_walk: false,
    remove_links: &[],
    retype_links: &[RetypeLink {
        id: 10447,
        from: 1416,
        to: 1461,
        old_kind: LinkKind::Walk,
        new_kind: LinkKind::Drop,
    }],
};

/// HAZ-1462 tournament recipes. Not walked by [`apply_for_map`].
pub const HAZ1462_RECIPES: &[ShelfPatch] = &[HAZ1462_K1, HAZ1462_K2, HAZ1462_K3];

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
/// `fixa` (GAP 7) is the production consumer; held in a [`TxnStack`] so chained applies each keep
/// their own base image.
#[allow(dead_code)]
pub struct AppliedTxn {
    pub name: &'static str,
    pub stamp_before: u64,
    pub stamp_after: u64,
    snapshot: NavGraph,
}

/// Names and stamps only: the snapshot is a whole graph, and printing it would bury the two numbers
/// that actually identify the transaction.
impl std::fmt::Debug for AppliedTxn {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AppliedTxn")
            .field("name", &self.name)
            .field("stamp_before", &self.stamp_before)
            .field("stamp_after", &self.stamp_after)
            .finish_non_exhaustive()
    }
}

impl AppliedTxn {
    /// Restore the graph to the pre-apply snapshot. Returns the restored stamp for the audit line.
    #[allow(dead_code)]
    pub fn unapply(self, graph: &mut NavGraph) -> u64 {
        *graph = self.snapshot;
        self.stamp_before
    }
}

/// How deep the undo chain may go before another apply is refused.
///
/// Every entry is a whole `NavGraph`, so the ceiling is memory, not bookkeeping: dm3's graph clones
/// to roughly 2 MB, which puts a full stack around 60 MB. Recipe chains are four ops at the outside;
/// the headroom is there for hand plants during a bring-up sweep. Past the ceiling the *apply* is
/// refused rather than the oldest snapshot dropped — dropping it would silently cost the base image,
/// which is the exact failure this stack exists to remove.
pub const TXN_STACK_MAX: usize = 32;

/// One applied mutation and the fingerprint of the graph it produced.
struct TxnEntry {
    txn: AppliedTxn,
    /// Nivå-2 of the graph this apply published. Undo checks it against the live graph before
    /// restoring anything: a snapshot is only a valid undo for the graph it was pushed against.
    content_after: String,
}

/// The chain of undo points behind the live graph, newest last.
///
/// Replaces the single `Option<AppliedTxn>` slot, which lost the base image the moment a second
/// apply ran: the second apply overwrote the first's snapshot, so `--undo` landed on the *previous
/// recipe*, not on the base the chain started from. With a stack, `apply×N` + `undo×N` walks back
/// through the same intermediate graphs it walked forward through and ends bit-identically on the
/// base.
///
/// Undo is snapshot **restore**, never an inverse op list. Inverting a removal re-appends the link
/// at a new id, which passes a structural (nivå-2) comparison while the live link ids, the adjacency
/// index and every parallel side table disagree with the base — the R2 crash class exactly (a stale
/// ON-graph link id read against an OFF graph). A restore has no such gap: it *is* the old bytes.
#[derive(Default)]
pub struct TxnStack {
    entries: Vec<TxnEntry>,
}

/// What an [`undo`](TxnStack::undo) rolled back, for the audit line.
#[derive(Debug)]
pub struct Undone {
    pub name: &'static str,
    /// Nivå-1 stamp of the graph now live again.
    pub stamp_restored: u64,
    /// Undo points still behind the live graph.
    pub depth_left: usize,
}

impl TxnStack {
    pub fn depth(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// The recipe on top of the chain — the only one `undo` will roll back.
    pub fn top_name(&self) -> Option<&'static str> {
        self.entries.last().map(|e| e.txn.name)
    }

    /// Oldest-first names, for an audit line that shows the whole chain.
    pub fn names(&self) -> Vec<&'static str> {
        self.entries.iter().map(|e| e.txn.name).collect()
    }

    /// Whether another apply may be pushed. Callers check this **before** mutating, so a full stack
    /// refuses the apply instead of committing a mutation it cannot take back.
    pub fn has_room(&self) -> bool {
        self.entries.len() < TXN_STACK_MAX
    }

    /// Record `txn` as the newest undo point. `live` is the graph the apply just published; its
    /// nivå-2 is captured here as the guard `undo` will check.
    ///
    /// A refusal hands `txn` back rather than dropping it, so the caller can roll the apply it
    /// already published straight back off instead of leaving a live graph with no way home.
    pub fn push(&mut self, txn: AppliedTxn, live: &NavGraph) -> Result<(), (AppliedTxn, String)> {
        if !self.has_room() {
            let why = format!(
                "undo chain is full ({TXN_STACK_MAX} applies deep) — undo back down or reload the map"
            );
            return Err((txn, why));
        }
        let content_after = graph_content_hash(live);
        self.entries.push(TxnEntry { txn, content_after });
        Ok(())
    }

    /// Roll the newest apply back, fail-closed.
    ///
    /// Both identity levels must still describe the live graph: nivå-1 catches a different-sized
    /// graph, nivå-2 catches a same-sized one with different contents. Either mismatch means
    /// something moved the graph out from under the chain — a foreign carve, an out-of-band plant,
    /// a rebuild — and the snapshot on top is no longer this graph's predecessor. Restoring it then
    /// would not be an undo; it would be a swap to an unrelated graph. So we refuse, and leave both
    /// the live graph and the chain exactly as they were.
    ///
    /// `expect` names the recipe the caller asked to undo. Undoing out of order is refused for the
    /// same reason: the audit line would name a recipe that is not what came off.
    pub fn undo(&mut self, map: &str, expect: &'static str, live: &mut NavGraph) -> Result<Undone, String> {
        let entry = self
            .entries
            .last()
            .ok_or("no apply snapshot — nothing to undo")?;
        if entry.txn.name != expect {
            return Err(format!(
                "top of the undo chain is '{}', not '{expect}' — undo in reverse order",
                entry.txn.name
            ));
        }
        let live_stamp = stamp_of(map, live);
        if live_stamp != entry.txn.stamp_after {
            return Err(format!(
                "refusing undo of '{}': live graph is stamp={live_stamp}, this apply published \
                 stamp={} — the graph changed outside the chain",
                entry.txn.name, entry.txn.stamp_after
            ));
        }
        let live_hash = graph_content_hash(live);
        if live_hash != entry.content_after {
            return Err(format!(
                "refusing undo of '{}': live content_hash={live_hash}, this apply published {} — \
                 same counts, different graph",
                entry.txn.name, entry.content_after
            ));
        }
        let entry = self.entries.pop().expect("checked above");
        let name = entry.txn.name;
        let stamp_restored = entry.txn.unapply(live);
        Ok(Undone {
            name,
            stamp_restored,
            depth_left: self.entries.len(),
        })
    }

    /// Drop the whole chain. Called when the navmesh is rebuilt: the snapshots describe a graph that
    /// no longer exists, and `undo`'s guards would refuse them one at a time anyway — clearing says
    /// so once, up front.
    pub fn clear(&mut self) {
        self.entries.clear();
    }
}

/// Run `mutate` against a **clone** of `graph`, publishing it only if the mutation succeeded.
///
/// This is [`apply_txn`]'s guarantee for mutations that are not a [`ShelfPatch`]: the live graph is
/// never the thing being edited, so a refusal half-way through leaves it bit-for-bit as it was —
/// there is no partially-planted state to detect afterwards. On success the derived tables are
/// rebuilt on the candidate before it goes live (reachability and the LOD router answer for the
/// pre-plant graph otherwise), and the pre-mutation snapshot comes back as an [`AppliedTxn`] for
/// [`TxnStack`].
///
/// `mutate` returns the value the caller wants out of the transaction — a link id, a cell id — so
/// nothing has to be read back off the live graph to find out what happened.
pub fn live_txn<T>(
    name: &'static str,
    map: &str,
    graph: &mut NavGraph,
    mutate: impl FnOnce(&mut NavGraph) -> Result<T, String>,
) -> Result<(AppliedTxn, T), String> {
    let stamp_before = stamp_of(map, graph);
    let snapshot = graph.clone();
    let mut candidate = graph.clone();
    // `?` here drops `candidate` and `snapshot` untouched: `graph` was never written to.
    let value = mutate(&mut candidate)?;
    candidate.rebuild_derived();
    let stamp_after = stamp_of(map, &candidate);
    *graph = candidate;
    Ok((
        AppliedTxn {
            name,
            stamp_before,
            stamp_after,
            snapshot,
        },
        value,
    ))
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

fn plant_fully_meshed(patch: &ShelfPatch, graph: &NavGraph) -> bool {
    if patch.cells.is_empty() && patch.drops.is_empty() {
        return true;
    }
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

fn remove_edge_present(graph: &NavGraph, spec: &RemoveLink) -> bool {
    graph
        .links
        .iter()
        .any(|l| l.from == spec.from && l.to == spec.to && l.kind == spec.kind)
}

fn remove_has_conflict(graph: &NavGraph, spec: &RemoveLink) -> bool {
    match graph.links.get(spec.id as usize) {
        Some(l) if l.from == spec.from && l.to == spec.to && l.kind != spec.kind => true,
        Some(l) if (l.from != spec.from || l.to != spec.to) && remove_edge_present(graph, spec) => true,
        _ => false,
    }
}

fn remove_fully_done(patch: &ShelfPatch, graph: &NavGraph) -> bool {
    patch
        .remove_links
        .iter()
        .all(|spec| !remove_has_conflict(graph, spec) && !remove_edge_present(graph, spec))
}

fn retype_fully_done(patch: &ShelfPatch, graph: &NavGraph) -> bool {
    patch
        .retype_links
        .iter()
        .all(|spec| match graph.links.get(spec.id as usize) {
            Some(l) if l.from == spec.from && l.to == spec.to && l.kind == spec.new_kind => true,
            _ => false,
        })
}

fn fully_meshed(patch: &ShelfPatch, graph: &NavGraph) -> bool {
    let has_edit = !patch.remove_links.is_empty() || !patch.retype_links.is_empty();
    if has_edit {
        return plant_fully_meshed(patch, graph) && remove_fully_done(patch, graph) && retype_fully_done(patch, graph);
    }
    plant_fully_meshed(patch, graph)
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
                let planted = if patch.no_auto_walk {
                    graph.plant_cell_isolated(bsp, v(c))
                } else {
                    graph.plant_cell(bsp, v(c))
                };
                let Some((id, _)) = planted else {
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

    let mut n_removed = 0usize;
    let mut to_remove: Vec<u32> = Vec::new();
    for spec in patch.remove_links {
        match graph.links.get(spec.id as usize) {
            None => {
                if graph
                    .links
                    .iter()
                    .any(|l| l.from == spec.from && l.to == spec.to && l.kind == spec.kind)
                {
                    return Outcome::Failed(format!(
                        "link {} missing but {}→{} {:?} exists at another id",
                        spec.id, spec.from, spec.to, spec.kind
                    ));
                }
            }
            Some(l) if l.from == spec.from && l.to == spec.to && l.kind == spec.kind => {
                to_remove.push(spec.id);
            }
            Some(l) => {
                return Outcome::Failed(format!(
                    "link {} is {}→{} {:?}, want {}→{} {:?}",
                    spec.id, l.from, l.to, l.kind, spec.from, spec.to, spec.kind
                ));
            }
        }
    }
    if !to_remove.is_empty() {
        if let Err(why) = graph.remove_links_by_id(&to_remove) {
            return Outcome::Failed(why);
        }
        n_removed = to_remove.len();
    }

    let mut n_retyped = 0usize;
    for spec in patch.retype_links {
        let Some(l) = graph.links.get(spec.id as usize).copied() else {
            return Outcome::Failed(format!("unknown link id {}", spec.id));
        };
        if l.from == spec.from && l.to == spec.to && l.kind == spec.new_kind {
            continue;
        }
        if l.from == spec.from && l.to == spec.to && l.kind == spec.old_kind {
            if let Err(why) = graph.retype_link(spec.id, spec.new_kind) {
                return Outcome::Failed(why);
            }
            n_retyped += 1;
            continue;
        }
        return Outcome::Failed(format!(
            "link {} is {}→{} {:?}, want {}→{} {:?}→{:?}",
            spec.id, l.from, l.to, l.kind, spec.from, spec.to, spec.old_kind, spec.new_kind
        ));
    }

    if new_cells == 0 && new_drops == 0 && n_removed == 0 && n_retyped == 0 {
        return Outcome::AlreadyMeshed;
    }
    let stamp_after = stamp_of(patch.map, graph);
    Outcome::Applied {
        cells: new_cells,
        drops: new_drops + n_removed + n_retyped,
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
        for p in PATCHES.iter().chain(RAM_RECIPES.iter()).chain(HAZ1462_RECIPES.iter()) {
            assert!(!p.map.is_empty() && p.map == p.map.to_lowercase());
            let link_edit = !p.remove_links.is_empty() || !p.retype_links.is_empty();
            assert!(
                !p.drops.is_empty() || link_edit,
                "{}: a shelf with no way off is still a trap",
                p.name
            );
            if p.cells.is_empty() && !link_edit {
                // Link-only recipe (ram-prevent): drops start on already-carved cells.
                assert_eq!(p.name, "ram-prevent");
            } else if !p.cells.is_empty() {
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
        assert!(patch_by_name("ram-rail-v2").is_some());
        assert!(patch_by_name("ram-prevent").is_some());
        assert!(patch_by_name("west-shelf").is_some());
        assert!(patch_by_name("haz1462-k1").is_some());
        assert!(patch_by_name("haz1462-k2").is_some());
        assert!(patch_by_name("haz1462-k3").is_some());
        assert!(patch_by_name("no-such").is_none());
        assert_eq!(HAZ1462_K1.remove_links.len(), 1);
        assert_eq!(HAZ1462_K1.remove_links[0].id, 10447);
        assert_eq!(HAZ1462_K1.remove_links[0].from, 1416);
        assert_eq!(HAZ1462_K1.remove_links[0].to, 1461);
        assert_eq!(HAZ1462_K1.remove_links[0].kind, LinkKind::Walk);
        assert!(HAZ1462_K1.retype_links.is_empty());
        assert!(HAZ1462_K1.cells.is_empty() && HAZ1462_K1.drops.is_empty());
        assert_eq!(HAZ1462_K2.remove_links.len(), 2);
        assert_eq!(HAZ1462_K2.remove_links[0].id, 10447);
        assert_eq!(HAZ1462_K2.remove_links[1].id, 10446);
        assert_eq!(HAZ1462_K2.remove_links[1].from, 1416);
        assert_eq!(HAZ1462_K2.remove_links[1].to, 1459);
        assert_eq!(HAZ1462_K2.remove_links[1].kind, LinkKind::Walk);
        assert!(HAZ1462_K3.remove_links.is_empty());
        assert_eq!(HAZ1462_K3.retype_links.len(), 1);
        assert_eq!(HAZ1462_K3.retype_links[0].id, 10447);
        assert_eq!(HAZ1462_K3.retype_links[0].old_kind, LinkKind::Walk);
        assert_eq!(HAZ1462_K3.retype_links[0].new_kind, LinkKind::Drop);
        assert!(RAM_RAIL.no_auto_walk, "ram-rail must not auto-Walk");
        assert!(RAM_RAIL_V2.no_auto_walk, "ram-rail-v2 must not auto-Walk");
        assert!(!PATCHES[0].no_auto_walk, "west-shelf keeps auto-Walk");
        assert!(!RAM_PREVENT.no_auto_walk);
        for (i, &y) in RAM_RAIL_YS.iter().enumerate() {
            assert_eq!(RAM_RAIL.cells[i][0], -360.0);
            assert_eq!(RAM_RAIL.cells[i][1], y);
            assert_eq!(RAM_RAIL.cells[i][2], 128.03125);
            assert_eq!(RAM_RAIL.drops[i].1, [-352.0, -672.0, -16.0]);
            assert_eq!(RAM_RAIL_V2.cells[i][1], y);
            assert_eq!(RAM_RAIL_V2.drops[i].1, RAM_RAIL_V2_LANDINGS[i]);
            assert_ne!(
                RAM_RAIL_V2.drops[i].1,
                [-352.0, -672.0, -16.0],
                "v2 must not drop onto 638"
            );
            assert!((RAM_RAIL_V2.drops[i].1[0] + 288.0).abs() < 1.0);
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
            no_auto_walk: false,
            remove_links: &[],
            retype_links: &[],
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
            no_auto_walk: true,
            remove_links: &[],
            retype_links: &[],
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
            no_auto_walk: false,
            remove_links: &[],
            retype_links: &[],
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

    fn ram_rail_v2_patch(graph: &NavGraph) -> ShelfPatch {
        ShelfPatch {
            map: "dm3",
            name: "ram-rail-v2",
            cells: RAM_RAIL_V2.cells,
            drops: RAM_RAIL_V2.drops,
            snap_z: RAM_RAIL_V2.snap_z,
            pin: pin_for(graph),
            no_auto_walk: true,
            remove_links: &[],
            retype_links: &[],
        }
    }

    fn v2_landing_origins() -> Vec<Vec3> {
        RAM_RAIL_V2_LANDINGS
            .iter()
            .map(|&p| Vec3::new(p[0], p[1], p[2]))
            .collect()
    }

    #[test]
    fn ram_rail_v2_drops_east_to_702_line_not_638() {
        let mut origins = v2_landing_origins();
        for &y in &RAM_RAIL_YS {
            origins.push(Vec3::new(-328.0, y, 128.03125));
        }
        let mut g = NavGraph::from_topology(&origins, &[]);
        let patch = ram_rail_v2_patch(&g);
        apply_txn(&patch, None, &mut g).expect("ram-rail-v2 apply");
        assert_eq!(g.links.len(), 6);
        for (i, &aim) in RAM_RAIL_V2.cells.iter().enumerate() {
            let id = g.cell_within(v(aim), ALREADY_XY, ALREADY_Z).expect("v2 rail");
            assert!(incoming(&g, id).is_empty(), "v2 rail {aim:?} indegree 0");
            let out = outgoing(&g, id);
            assert_eq!(out.len(), 1);
            assert_eq!(out[0].kind, LinkKind::Drop);
            let dest = g.cell_origin(out[0].to);
            let want = RAM_RAIL_V2_LANDINGS[i];
            assert!(
                (dest.x - want[0]).abs() < 1.0 && (dest.y - want[1]).abs() < 1.0,
                "v2 {aim:?} dest {dest:?} want {want:?}"
            );
            assert!((dest.x + 352.0).abs() > 8.0, "v2 must not land on 638 x=−352");
            assert!(!g
                .links
                .iter()
                .any(|l| (l.from == id || l.to == id) && l.kind == LinkKind::Walk));
        }
    }

    /// Blockerare B2: `ram-rail-v2` måste gå att nå via namn, annars finns receptet
    /// inte för `fixa` hur väl det än är definierat.
    ///
    /// Riggbinären (qwprogs 56cc6b22) byggdes före `bb1ece6` och känner bara
    /// [west-shelf, ram-rail, ram-prevent, haz1462-k1/k2/k3]. Källan har haft v2
    /// hela tiden — det som saknades var en byggd binär. Det här testet är
    /// skillnaden mellan "det står i tabellen" och "receptet går att applicera",
    /// och det faller på en binär som inte bär det.
    #[test]
    fn ram_rail_v2_is_reachable_by_name_in_the_registry() {
        let p = patch_by_name("ram-rail-v2").expect("ram-rail-v2 måste finnas i receptregistret");
        assert_eq!(p.name, "ram-rail-v2");
        assert_eq!(p.map, "dm3");
        let names: Vec<_> = registered_recipe_names().collect();
        assert!(names.contains(&"ram-rail-v2"), "registrerade: {names:?}");
        // Hela registret, så en borttappad post syns som en diff och inte som tystnad.
        assert_eq!(
            names,
            vec![
                "west-shelf",
                "ram-rail",
                "ram-rail-v2",
                "ram-prevent",
                "haz1462-k1",
                "haz1462-k2",
                "haz1462-k3",
            ]
        );
    }

    /// Motorns tabellpost mot den förseglade fixturen, fält för fält.
    ///
    /// Källa: `testsuite/tools/recept/ram-rail-v2.json` (sha256
    /// `7202490688f6e7f84f7cd66505f02d1ba6e6b72a8bf70ecafefd792ab6a8048f` efter
    /// statusmärkningen; op-innehållet är oförändrat sedan grok författade det).
    /// Värdena står här som literaler med flit: ett test som läser samma JSON som
    /// implementationen bevisar ingenting om att de två speglar varandra.
    #[test]
    fn ram_rail_v2_mirrors_the_sealed_fixture() {
        let p = patch_by_name("ram-rail-v2").expect("registrerad");
        assert_eq!(
            p.cells,
            &[
                [-360.0, -784.0, 128.03125],
                [-360.0, -752.0, 128.03125],
                [-360.0, -720.0, 128.03125],
                [-360.0, -688.0, 128.03125],
                [-360.0, -656.0, 128.03125],
                [-360.0, -624.0, 128.03125],
            ]
        );
        assert_eq!(
            p.drops,
            &[
                ([-360.0, -784.0, 128.03125], [-288.0, -800.0, -16.0]),
                ([-360.0, -752.0, 128.03125], [-288.0, -768.0, -16.0]),
                ([-360.0, -720.0, 128.03125], [-288.0, -736.0, -16.0]),
                ([-360.0, -688.0, 128.03125], [-288.0, -672.0, -16.0]),
                ([-360.0, -656.0, 128.03125], [-288.0, -640.0, -16.0]),
                ([-360.0, -624.0, 128.03125], [-288.0, -608.0, -16.0]),
            ]
        );
        assert_eq!(p.snap_z, 128.031_25);
        assert!(p.no_auto_walk, "rälscellerna ska vara indegree-0, inte auto-Walk:as ihop");
        assert!(p.remove_links.is_empty() && p.retype_links.is_empty(), "v2 är rent additiv");
        // Fixturens `off`: basgrafen, med nivå-2 pinnad.
        assert_eq!((p.pin.cells, p.pin.links, p.pin.rj_links), (5977, 48207, 0));
        assert_eq!(p.pin.stamp, 906_595_427_771_298_736);
        assert_eq!(
            p.pin.content_hash,
            Some("58787ce0d27ddd49ef109fa380ad5aca1c5fb65ba5125d485ad0e2ebd0f88ad9")
        );
    }

    /// Registreringens delta ska vara det transformatorn härleder: +6 celler /
    /// +6 länkar, vilket ensamt mot bas ger 5983/48213 och fixturens förseglade
    /// `on_expected`-FNV.
    ///
    /// Deltat mäts på en syntetisk graf (basgrafen finns inte i en enhetstest), och
    /// slutcountsen räknas sedan ur motorns EGEN pin — så testet binder ihop tre
    /// saker som annars bara påstås höra ihop: vad applyt gör, vad receptet pinnar,
    /// och vad stampen blir.
    #[test]
    fn ram_rail_v2_alone_on_base_lands_on_the_sealed_on_expected() {
        let mut g = NavGraph::from_topology(&v2_landing_origins(), &[]);
        let patch = ram_rail_v2_patch(&g);
        let (cells, drops) = match apply_one(&patch, None, &mut g) {
            Outcome::Applied { cells, drops, .. } => (cells, drops),
            other => panic!("v2 apply: {other:?}"),
        };
        assert_eq!((cells, drops), (6, 6), "v2 är +6 celler / +6 drops");

        let pin = RAM_RAIL_V2.pin;
        let (slut_cells, slut_links) = (pin.cells + cells as u32, pin.links + drops as u32);
        assert_eq!((slut_cells, slut_links), (5983, 48213));
        assert_eq!(
            graph_stamp("dm3", slut_cells, slut_links, pin.rj_links),
            8_774_822_664_048_001_128,
            "ram-rail-v2.json on_expected.graph_stamp"
        );
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
        assert!(HAZ1462_RECIPES.iter().all(|p| p.name.starts_with("haz1462-")));
        assert!(PATCHES
            .iter()
            .all(|p| p.remove_links.is_empty() && p.retype_links.is_empty()));
    }

    /// 3 cells, 3 walks: 0→1, 0→2, 1→2. Remove/retype target id 1 (0→2).
    fn edit_origins() -> Vec<Vec3> {
        vec![
            Vec3::new(288.0, -844.0, 264.0),
            Vec3::new(328.0, -800.0, 264.0),
            Vec3::new(320.0, -768.0, -16.0),
        ]
    }

    fn edit_walks() -> Vec<Link> {
        vec![
            Link {
                from: 0,
                to: 1,
                kind: LinkKind::Walk,
                cost: 1.0,
            },
            Link {
                from: 0,
                to: 2,
                kind: LinkKind::Walk,
                cost: 1.0,
            },
            Link {
                from: 1,
                to: 2,
                kind: LinkKind::Walk,
                cost: 1.0,
            },
        ]
    }

    /// Non-zero distinct taxes so a remap miss cannot hide as 0==0.
    fn priced_edit_graph() -> NavGraph {
        NavGraph::from_topology_priced(&edit_origins(), &edit_walks(), &[10.0, 20.0, 30.0], &[1.0, 2.0, 3.0])
    }

    fn remove_patch(graph: &NavGraph, specs: &'static [RemoveLink]) -> ShelfPatch {
        ShelfPatch {
            map: "dm3",
            name: "remove-fixture",
            cells: &[],
            drops: &[],
            snap_z: 264.0,
            pin: pin_for(graph),
            no_auto_walk: false,
            remove_links: specs,
            retype_links: &[],
        }
    }

    fn retype_patch(graph: &NavGraph, specs: &'static [RetypeLink]) -> ShelfPatch {
        ShelfPatch {
            map: "dm3",
            name: "retype-fixture",
            cells: &[],
            drops: &[],
            snap_z: 264.0,
            pin: pin_for(graph),
            no_auto_walk: false,
            remove_links: &[],
            retype_links: specs,
        }
    }

    #[test]
    fn remove_link_unknown_id_fails_closed() {
        let mut g = NavGraph::from_topology(&edit_origins(), &edit_walks());
        let before = g.clone();
        static SPECS: [RemoveLink; 1] = [RemoveLink {
            id: 99,
            from: 0,
            to: 2,
            kind: LinkKind::Walk,
        }];
        let patch = remove_patch(&g, &SPECS);
        match apply_one(&patch, None, &mut g) {
            Outcome::Failed(why) => assert!(why.contains("unknown link id") || why.contains("99"), "{why}"),
            other => panic!("expected Failed, got {other:?}"),
        }
        assert!(topology_eq(&g, &before));
    }

    #[test]
    fn remove_link_wrong_kind_fails_closed() {
        let mut g = NavGraph::from_topology(&edit_origins(), &edit_walks());
        let before = g.clone();
        static SPECS: [RemoveLink; 1] = [RemoveLink {
            id: 1,
            from: 0,
            to: 2,
            kind: LinkKind::Drop,
        }];
        let patch = remove_patch(&g, &SPECS);
        match apply_one(&patch, None, &mut g) {
            Outcome::Failed(why) => assert!(why.contains("want"), "{why}"),
            other => panic!("expected Failed, got {other:?}"),
        }
        assert!(topology_eq(&g, &before));
    }

    #[test]
    fn remove_link_stamp_mismatch_fails_closed() {
        let mut g = NavGraph::from_topology(&edit_origins(), &edit_walks());
        let before = g.clone();
        static SPECS: [RemoveLink; 1] = [RemoveLink {
            id: 1,
            from: 0,
            to: 2,
            kind: LinkKind::Walk,
        }];
        let mut patch = remove_patch(&g, &SPECS);
        patch.pin = WEST_SHELF_PIN;
        match apply_one(&patch, None, &mut g) {
            Outcome::Failed(why) => assert!(why.contains("stamp mismatch"), "{why}"),
            other => panic!("expected Failed, got {other:?}"),
        }
        assert!(topology_eq(&g, &before));
    }

    #[test]
    fn remove_link_undo_roundtrip_bit_identity() {
        let mut g = priced_edit_graph();
        static SPECS: [RemoveLink; 1] = [RemoveLink {
            id: 1,
            from: 0,
            to: 2,
            kind: LinkKind::Walk,
        }];
        let patch = remove_patch(&g, &SPECS);
        let before = g.clone();
        assert_eq!(g.link_hazard_hp(1), 20.0);
        assert_eq!(g.link_water_extra(1), 2.0);
        let txn = apply_txn(&patch, None, &mut g).expect("remove apply");
        assert_eq!(g.links.len(), 2);
        assert!(!g.links.iter().any(|l| l.from == 0 && l.to == 2));
        assert!(g.links.iter().any(|l| l.from == 0 && l.to == 1));
        // id 1 is now old id 2 (1→2): tax 30/3, not old id 1's 20/2.
        assert_eq!(g.link_hazard_hp(0), 10.0);
        assert_eq!(g.link_water_extra(0), 1.0);
        assert_eq!(g.link_hazard_hp(1), 30.0);
        assert_eq!(g.link_water_extra(1), 3.0);
        txn.unapply(&mut g);
        assert!(topology_eq(&g, &before));
        assert_eq!(g.link_hazard_hp(1), 20.0);
        assert_eq!(g.link_water_extra(1), 2.0);
    }

    #[test]
    fn remove_two_links_undo_roundtrip() {
        let mut g = priced_edit_graph();
        static SPECS: [RemoveLink; 2] = [
            RemoveLink {
                id: 1,
                from: 0,
                to: 2,
                kind: LinkKind::Walk,
            },
            RemoveLink {
                id: 0,
                from: 0,
                to: 1,
                kind: LinkKind::Walk,
            },
        ];
        let patch = remove_patch(&g, &SPECS);
        let before = g.clone();
        let txn = apply_txn(&patch, None, &mut g).expect("remove two");
        assert_eq!(g.links.len(), 1);
        assert!(g.links.iter().all(|l| l.from == 1 && l.to == 2));
        // K2 shift-2: only old id 2 remains, at new index 0. Tax 30/3, not 10/1 or 20/2.
        assert_eq!(g.link_hazard_hp(0), 30.0);
        assert_eq!(g.link_water_extra(0), 3.0);
        txn.unapply(&mut g);
        assert!(topology_eq(&g, &before));
        assert_eq!(g.link_hazard_hp(0), 10.0);
        assert_eq!(g.link_hazard_hp(1), 20.0);
        assert_eq!(g.link_hazard_hp(2), 30.0);
        assert_eq!(g.link_water_extra(2), 3.0);
    }

    #[test]
    fn remove_link_apply_then_reapply_is_already_meshed() {
        let mut g = NavGraph::from_topology(&edit_origins(), &edit_walks());
        static SPECS: [RemoveLink; 1] = [RemoveLink {
            id: 1,
            from: 0,
            to: 2,
            kind: LinkKind::Walk,
        }];
        let patch = remove_patch(&g, &SPECS);
        apply_txn(&patch, None, &mut g).expect("first");
        let after = g.clone();
        match apply_one(&patch, None, &mut g) {
            Outcome::AlreadyMeshed => {}
            other => panic!("re-apply: {other:?}"),
        }
        assert!(topology_eq(&g, &after));
    }

    #[test]
    fn retype_link_unknown_id_fails_closed() {
        let mut g = NavGraph::from_topology(&edit_origins(), &edit_walks());
        let before = g.clone();
        static SPECS: [RetypeLink; 1] = [RetypeLink {
            id: 99,
            from: 0,
            to: 2,
            old_kind: LinkKind::Walk,
            new_kind: LinkKind::Drop,
        }];
        let patch = retype_patch(&g, &SPECS);
        match apply_one(&patch, None, &mut g) {
            Outcome::Failed(why) => assert!(why.contains("unknown link id"), "{why}"),
            other => panic!("expected Failed, got {other:?}"),
        }
        assert!(topology_eq(&g, &before));
    }

    #[test]
    fn retype_link_wrong_kind_fails_closed() {
        let mut g = NavGraph::from_topology(&edit_origins(), &edit_walks());
        let before = g.clone();
        static SPECS: [RetypeLink; 1] = [RetypeLink {
            id: 1,
            from: 0,
            to: 2,
            old_kind: LinkKind::Drop,
            new_kind: LinkKind::JumpGap,
        }];
        let patch = retype_patch(&g, &SPECS);
        match apply_one(&patch, None, &mut g) {
            Outcome::Failed(why) => assert!(why.contains("want"), "{why}"),
            other => panic!("expected Failed, got {other:?}"),
        }
        assert!(topology_eq(&g, &before));
    }

    #[test]
    fn retype_link_undo_roundtrip_bit_identity() {
        let mut g = NavGraph::from_topology(&edit_origins(), &edit_walks());
        static SPECS: [RetypeLink; 1] = [RetypeLink {
            id: 1,
            from: 0,
            to: 2,
            old_kind: LinkKind::Walk,
            new_kind: LinkKind::Drop,
        }];
        let patch = retype_patch(&g, &SPECS);
        let before = g.clone();
        let txn = apply_txn(&patch, None, &mut g).expect("retype apply");
        assert_eq!(g.links.len(), before.links.len());
        assert_eq!(g.links[1].kind, LinkKind::Drop);
        assert_eq!(g.links[1].from, 0);
        assert_eq!(g.links[1].to, 2);
        txn.unapply(&mut g);
        assert!(topology_eq(&g, &before));
    }

    #[test]
    fn retype_link_apply_then_reapply_is_already_meshed() {
        let mut g = NavGraph::from_topology(&edit_origins(), &edit_walks());
        static SPECS: [RetypeLink; 1] = [RetypeLink {
            id: 1,
            from: 0,
            to: 2,
            old_kind: LinkKind::Walk,
            new_kind: LinkKind::Drop,
        }];
        let patch = retype_patch(&g, &SPECS);
        apply_txn(&patch, None, &mut g).expect("first");
        let after = g.clone();
        match apply_one(&patch, None, &mut g) {
            Outcome::AlreadyMeshed => {}
            other => panic!("re-apply: {other:?}"),
        }
        assert!(topology_eq(&g, &after));
    }

    fn load_dm3_bsp() -> Bsp {
        let mut paths = Vec::new();
        if let Ok(p) = std::env::var("RTX_TEST_BSP") {
            paths.push(p);
        }
        if let Ok(home) = std::env::var("HOME") {
            paths.push(format!(
                "{home}/.local/share/qw-fasttrack/runtime-tbx-d/qw/maps/dm3.bsp"
            ));
            paths.push(format!("{home}/.local/share/route-lab/nav-ab/qw/maps/dm3.bsp"));
        }
        for p in paths {
            if let Ok(bytes) = std::fs::read(&p) {
                if let Some(bsp) = Bsp::parse(&bytes) {
                    return bsp;
                }
            }
        }
        panic!(
            "need dm3.bsp (RTX_TEST_BSP or runtime-tbx-d/qw/maps) \
             to prove plant_cell live path"
        );
    }

    #[test]
    fn ram_rail_live_plant_cell_path_has_no_walk() {
        let bsp = load_dm3_bsp();
        let mut g = NavGraph::from_topology(&[landing_origin()], &[]);
        let patch = ram_rail_patch(&g);
        apply_txn(&patch, Some(&bsp), &mut g).expect("ram-rail live apply");
        assert_eq!(g.cells.len(), 7, "landing + 6 rail");
        let drops: Vec<_> = g.links.iter().filter(|l| l.kind == LinkKind::Drop).collect();
        let walks: Vec<_> = g.links.iter().filter(|l| l.kind == LinkKind::Walk).collect();
        assert_eq!(drops.len(), 6);
        assert!(
            walks.is_empty(),
            "ram-rail live path must not grow Walk (got {})",
            walks.len()
        );
        for &aim in RAM_RAIL.cells {
            let id = g
                .cell_within(v(aim), ALREADY_XY, ALREADY_Z)
                .expect("rail planted via plant_cell_isolated");
            assert!(incoming(&g, id).is_empty());
            let out = outgoing(&g, id);
            assert_eq!(out.len(), 1);
            assert_eq!(out[0].kind, LinkKind::Drop);
        }
    }

    #[test]
    fn ram_rail_v2_live_plant_east_drops() {
        let bsp = load_dm3_bsp();
        let mut g = NavGraph::from_topology(&v2_landing_origins(), &[]);
        let patch = ram_rail_v2_patch(&g);
        apply_txn(&patch, Some(&bsp), &mut g).expect("ram-rail-v2 live apply");
        let drops: Vec<_> = g.links.iter().filter(|l| l.kind == LinkKind::Drop).collect();
        let walks: Vec<_> = g.links.iter().filter(|l| l.kind == LinkKind::Walk).collect();
        assert_eq!(drops.len(), 6);
        assert!(walks.is_empty(), "v2 live path must not grow Walk");
        for (i, &aim) in RAM_RAIL_V2.cells.iter().enumerate() {
            let id = g
                .cell_within(v(aim), ALREADY_XY, ALREADY_Z)
                .expect("v2 rail planted isolated");
            assert!(incoming(&g, id).is_empty());
            let out = outgoing(&g, id);
            assert_eq!(out.len(), 1);
            assert_eq!(out[0].kind, LinkKind::Drop);
            let dest = g.cell_origin(out[0].to);
            assert!((dest.x - RAM_RAIL_V2_LANDINGS[i][0]).abs() < 8.0);
            assert!((dest.x + 352.0).abs() > 8.0);
        }
    }

    #[test]
    fn west_shelf_live_plant_cell_keeps_walk_neighbors() {
        let bsp = load_dm3_bsp();
        let mut g = NavGraph::from_topology(&dest_origins(), &[]);
        let patch = fixture_patch(&g);
        assert!(!patch.no_auto_walk);
        apply_txn(&patch, Some(&bsp), &mut g).expect("west-shelf live apply");
        let walks = g.links.iter().filter(|l| l.kind == LinkKind::Walk).count();
        assert!(
            walks > 0,
            "west-shelf plant_cell must still auto-Walk same-z shelf neighbours"
        );
        // The four shelf cells exist and at least one pair is Walk-linked.
        let shelf_ids: Vec<_> = PATCHES[0]
            .cells
            .iter()
            .map(|&c| g.cell_within(v(c), ALREADY_XY, ALREADY_Z).expect("shelf cell"))
            .collect();
        let inter_shelf_walk = g
            .links
            .iter()
            .any(|l| l.kind == LinkKind::Walk && shelf_ids.contains(&l.from) && shelf_ids.contains(&l.to));
        assert!(
            inter_shelf_walk,
            "west-shelf cells must Walk to each other (flag must not leak)"
        );
    }

    // ---- undo chain (lucka A/B) + transactional hand plants (lucka C) --------------------------

    use crate::navmesh::SpeedJumpTraversal;

    /// Stronger than [`topology_eq`]: everything an undo has to put back, not just the shape.
    ///
    /// `topology_eq` compares cells, links (id order included) and adjacency. A restore also has to
    /// return the *parallel* tables — the per-link taxes and the speed-jump side table — because an
    /// inverse-op "undo" is exactly what gets those wrong: it rebuilds the same topology at new link
    /// ids, so the shape matches while every id-keyed table has slid. Nivå-2 goes on top, which is
    /// the machine half of the structural verdict.
    fn bit_image_eq(a: &NavGraph, b: &NavGraph) -> bool {
        if !topology_eq(a, b) {
            return false;
        }
        if graph_content_hash(a) != graph_content_hash(b) {
            return false;
        }
        for li in 0..a.links.len() as u32 {
            if a.link_hazard_hp(li).to_bits() != b.link_hazard_hp(li).to_bits()
                || a.link_water_extra(li).to_bits() != b.link_water_extra(li).to_bits()
            {
                return false;
            }
            match (a.speed_jump_of_link(li), b.speed_jump_of_link(li)) {
                (None, None) => {}
                (Some(x), Some(y)) => {
                    if x.takeoff != y.takeoff
                        || x.v_req.to_bits() != y.v_req.to_bits()
                        || x.airtime.to_bits() != y.airtime.to_bits()
                        || x.chained != y.chained
                        || x.curl_gain.to_bits() != y.curl_gain.to_bits()
                        || x.weave_cap.to_bits() != y.weave_cap.to_bits()
                    {
                        return false;
                    }
                }
                _ => return false,
            }
        }
        true
    }

    /// The shape `plant_link_resp` runs inside `live_txn`, minus the cvar reads: resolve both
    /// endpoints, refuse if either is missing, plant a speed jump.
    fn plant_one(g: &mut NavGraph, from: Vec3, tgt: Vec3, v_req: f32) -> Result<u32, String> {
        let from_cell = g.nearest(from).ok_or("no cell near from")?;
        let to_cell = g.nearest(tgt).ok_or("no cell near tgt")?;
        let tr = SpeedJumpTraversal {
            takeoff: from,
            v_req,
            airtime: 0.5,
            chained: false,
            curl_gain: 12.0,
            ..Default::default()
        };
        Ok(g.plant_speed_jump(from_cell, to_cell, 1.5, tr))
    }

    /// Four chained hand plants, then four undos. This is the regression against the single
    /// `Option<AppliedTxn>` slot: with one slot, plant 2 overwrote plant 1's snapshot, so the first
    /// undo landed on the *pre-plant-2* graph and the remaining three had nothing to restore. The
    /// intermediate assertions are the point — the chain has to walk back through the same graphs it
    /// walked forward through, not merely end up somewhere that looks like the base.
    #[test]
    fn chained_plants_undo_all_the_way_back_to_a_bit_identical_base() {
        let mut g = priced_edit_graph();
        let mut stack = TxnStack::default();
        let base = g.clone();
        let mut waypoints = vec![base.clone()];

        let targets = [
            (edit_origins()[0], edit_origins()[2], 320.0_f32),
            (edit_origins()[1], edit_origins()[2], 400.0),
            (edit_origins()[2], edit_origins()[0], 280.0),
            (edit_origins()[0], edit_origins()[1], 360.0),
        ];
        for (i, &(from, tgt, v_req)) in targets.iter().enumerate() {
            let (txn, li) = live_txn("plan-link", "dm3", &mut g, |c| plant_one(c, from, tgt, v_req))
                .unwrap_or_else(|e| panic!("plant {i}: {e}"));
            assert_eq!(li as usize, base.links.len() + i, "each plant appends one link");
            stack.push(txn, &g).expect("room on the chain");
            waypoints.push(g.clone());
        }
        assert_eq!(stack.depth(), 4);
        assert_eq!(g.links.len(), 7);

        for i in (0..4).rev() {
            let undone = stack.undo("dm3", "plan-link", &mut g).expect("undo");
            assert_eq!(undone.depth_left, i);
            assert!(
                bit_image_eq(&g, &waypoints[i]),
                "undo #{} must restore the graph the previous apply published",
                4 - i
            );
        }
        assert!(stack.is_empty());
        assert!(bit_image_eq(&g, &base), "four undos must land on the base");
        assert_eq!(g.links.len(), 3);
        // The taxes are the id-keyed tables an inverse-op undo slides. Spot-check them by value.
        assert_eq!(g.link_hazard_hp(0), 10.0);
        assert_eq!(g.link_hazard_hp(1), 20.0);
        assert_eq!(g.link_hazard_hp(2), 30.0);
        assert_eq!(g.link_water_extra(2), 3.0);
    }

    /// The same chain through the recipe path: two `ShelfPatch` applies, two undos, base restored.
    /// Both ops edit link ids, so the second apply's snapshot is taken on an already-remapped graph
    /// — the case the single slot could not represent at all.
    #[test]
    fn chained_shelf_applies_undo_to_a_bit_identical_base() {
        let mut g = priced_edit_graph();
        let mut stack = TxnStack::default();
        let base = g.clone();

        static REMOVE: [RemoveLink; 1] = [RemoveLink {
            id: 1,
            from: 0,
            to: 2,
            kind: LinkKind::Walk,
        }];
        let p1 = remove_patch(&g, &REMOVE);
        let txn1 = apply_txn(&p1, None, &mut g).expect("remove apply");
        stack.push(txn1, &g).expect("room");
        let after1 = g.clone();

        // After the removal, old id 2 (1→2) sits at id 1. Retyping it proves the second snapshot is
        // taken against the *remapped* graph.
        static RETYPE: [RetypeLink; 1] = [RetypeLink {
            id: 1,
            from: 1,
            to: 2,
            old_kind: LinkKind::Walk,
            new_kind: LinkKind::Step,
        }];
        let p2 = retype_patch(&g, &RETYPE);
        let txn2 = apply_txn(&p2, None, &mut g).expect("retype apply");
        stack.push(txn2, &g).expect("room");
        assert_eq!(stack.depth(), 2);
        assert_eq!(g.links[1].kind, LinkKind::Step);

        stack
            .undo("dm3", "retype-fixture", &mut g)
            .expect("undo retype");
        assert!(bit_image_eq(&g, &after1), "first undo lands on the post-remove graph");
        stack
            .undo("dm3", "remove-fixture", &mut g)
            .expect("undo remove");
        assert!(bit_image_eq(&g, &base), "second undo lands on the base");
        assert_eq!(g.link_hazard_hp(1), 20.0, "the removed link's tax is back at its own id");
    }

    /// The deploy shape: four chained ops of *both* kinds — recipe applies and a hand plant —
    /// unwound in reverse to a bit-identical base. The composed recipe is three `ShelfPatch` ops
    /// plus the V296 plant, and the plant is the one that used to sit outside the transaction path
    /// entirely: it mutated the live graph with nothing recorded, so the chain around it could not
    /// be walked back through it.
    #[test]
    fn mixed_recipe_and_plant_chain_of_four_undoes_to_a_bit_identical_base() {
        let mut g = priced_edit_graph();
        let mut stack = TxnStack::default();
        let base = g.clone();
        let mut waypoints = vec![base.clone()];

        static REMOVE: [RemoveLink; 1] = [RemoveLink {
            id: 1,
            from: 0,
            to: 2,
            kind: LinkKind::Walk,
        }];
        let p1 = remove_patch(&g, &REMOVE);
        stack
            .push(apply_txn(&p1, None, &mut g).expect("op1 remove"), &g)
            .expect("room");
        waypoints.push(g.clone());

        let (txn2, _) = live_txn("plan-link", "dm3", &mut g, |c| {
            plant_one(c, edit_origins()[0], edit_origins()[2], 320.0)
        })
        .expect("op2 plant");
        stack.push(txn2, &g).expect("room");
        waypoints.push(g.clone());

        // Old id 2 (1→2) sits at id 1 after the removal; the plant appended at id 2 and left it there.
        static RETYPE: [RetypeLink; 1] = [RetypeLink {
            id: 1,
            from: 1,
            to: 2,
            old_kind: LinkKind::Walk,
            new_kind: LinkKind::Step,
        }];
        let p3 = retype_patch(&g, &RETYPE);
        stack
            .push(apply_txn(&p3, None, &mut g).expect("op3 retype"), &g)
            .expect("room");
        waypoints.push(g.clone());

        let (txn4, _) = live_txn("plan-link", "dm3", &mut g, |c| {
            plant_one(c, edit_origins()[1], edit_origins()[2], 400.0)
        })
        .expect("op4 plant");
        stack.push(txn4, &g).expect("room");
        waypoints.push(g.clone());

        assert_eq!(stack.depth(), 4);
        assert_eq!(
            stack.names(),
            vec!["remove-fixture", "plan-link", "retype-fixture", "plan-link"]
        );

        for i in (0..4).rev() {
            let expect = stack.top_name().expect("chain is not empty");
            let undone = stack.undo("dm3", expect, &mut g).expect("undo");
            assert_eq!(undone.depth_left, i);
            assert!(
                bit_image_eq(&g, &waypoints[i]),
                "undo #{} must restore what op {} published",
                4 - i,
                i
            );
        }
        assert!(stack.is_empty());
        assert!(bit_image_eq(&g, &base));
        assert_eq!(g.links.len(), 3);
        assert_eq!(g.links[1].kind, LinkKind::Walk, "the retype is gone with the rest");
        assert_eq!(g.link_hazard_hp(1), 20.0);
    }

    /// Nivå-1 guard: the live graph is a different size than the one this apply published, so the
    /// snapshot on top is not this graph's predecessor. Refuse, and change nothing.
    #[test]
    fn undo_refuses_when_the_graph_moved_outside_the_chain() {
        let mut g = priced_edit_graph();
        let mut stack = TxnStack::default();
        let (txn, _) = live_txn("plan-link", "dm3", &mut g, |c| {
            plant_one(c, edit_origins()[0], edit_origins()[2], 320.0)
        })
        .expect("plant");
        stack.push(txn, &g).expect("room");

        // Something plants outside the chain — a foreign carve, an out-of-band command.
        plant_one(&mut g, edit_origins()[1], edit_origins()[2], 400.0).expect("out-of-band plant");
        let live = g.clone();

        let why = stack.undo("dm3", "plan-link", &mut g).expect_err("must refuse");
        assert!(why.contains("changed outside the chain"), "{why}");
        assert!(bit_image_eq(&g, &live), "a refused undo mutates nothing");
        assert_eq!(stack.depth(), 1, "a refused undo keeps the chain");
    }

    /// Nivå-2 guard: same counts, different graph. This is the counts-coincidence class — nivå-1
    /// cannot see it, and restoring on a nivå-1 match alone would swap in an unrelated graph while
    /// reporting a clean undo.
    #[test]
    fn undo_refuses_a_same_counts_different_graph() {
        let mut g = priced_edit_graph();
        let mut stack = TxnStack::default();
        let (txn, li) = live_txn("plan-link", "dm3", &mut g, |c| {
            plant_one(c, edit_origins()[0], edit_origins()[2], 320.0)
        })
        .expect("plant");
        stack.push(txn, &g).expect("room");
        let stamp_pushed = stamp_of("dm3", &g);

        // Swap the planted link for a different one: same cells, same link count, same rj count.
        g.links[li as usize].to = 1;
        g.rebuild_derived();
        assert_eq!(stamp_of("dm3", &g), stamp_pushed, "nivå-1 is blind to this edit");
        let live = g.clone();

        let why = stack.undo("dm3", "plan-link", &mut g).expect_err("must refuse");
        assert!(why.contains("same counts, different graph"), "{why}");
        assert!(bit_image_eq(&g, &live), "a refused undo mutates nothing");
        assert_eq!(stack.depth(), 1);
    }

    /// Undo is a stack, so it only ever takes the top. Asking for a recipe further down is refused
    /// rather than served from the top, because the audit line would otherwise name a recipe that is
    /// not the one that came off.
    #[test]
    fn undo_refuses_out_of_order() {
        let mut g = priced_edit_graph();
        let mut stack = TxnStack::default();

        static REMOVE: [RemoveLink; 1] = [RemoveLink {
            id: 1,
            from: 0,
            to: 2,
            kind: LinkKind::Walk,
        }];
        let p1 = remove_patch(&g, &REMOVE);
        let txn1 = apply_txn(&p1, None, &mut g).expect("remove apply");
        stack.push(txn1, &g).expect("room");
        let (txn2, _) = live_txn("plan-link", "dm3", &mut g, |c| {
            plant_one(c, edit_origins()[0], edit_origins()[2], 320.0)
        })
        .expect("plant");
        stack.push(txn2, &g).expect("room");
        let live = g.clone();

        let why = stack
            .undo("dm3", "remove-fixture", &mut g)
            .expect_err("must refuse");
        assert!(why.contains("undo in reverse order"), "{why}");
        assert!(bit_image_eq(&g, &live));
        assert_eq!(stack.depth(), 2);
        assert_eq!(stack.top_name(), Some("plan-link"));
        assert_eq!(stack.names(), vec!["remove-fixture", "plan-link"]);
    }

    /// A full chain refuses the push and hands the transaction back, so the caller can roll the
    /// apply off rather than leave a live graph with no snapshot behind it. Dropping the oldest
    /// entry instead would cost the base image silently — the failure this whole stack removes.
    #[test]
    fn txn_stack_refuses_past_capacity_and_hands_the_txn_back() {
        let mut g = priced_edit_graph();
        let mut stack = TxnStack::default();
        for i in 0..TXN_STACK_MAX {
            let (txn, _) = live_txn("plan-link", "dm3", &mut g, |c| {
                plant_one(c, edit_origins()[0], edit_origins()[2], 320.0 + i as f32)
            })
            .expect("plant");
            stack.push(txn, &g).expect("room");
        }
        assert!(!stack.has_room());
        let full = g.clone();

        let (txn, _) = live_txn("plan-link", "dm3", &mut g, |c| {
            plant_one(c, edit_origins()[1], edit_origins()[2], 999.0)
        })
        .expect("plant");
        let (txn, why) = stack.push(txn, &g).expect_err("must refuse");
        assert!(why.contains("undo chain is full"), "{why}");
        assert_eq!(stack.depth(), TXN_STACK_MAX, "a refused push adds nothing");
        // The handed-back transaction is what makes the caller's rollback possible.
        txn.unapply(&mut g);
        assert!(bit_image_eq(&g, &full), "rolled back onto the last snapshotted graph");
    }

    /// Lucka C, the core of it: a hand plant that refuses part-way through must leave the live graph
    /// bit-for-bit as it was. The old path edited the live graph in place, so a refusal after the
    /// first mutation left a half-planted graph with nothing to roll back to.
    #[test]
    fn live_txn_leaves_the_graph_untouched_when_the_mutation_refuses() {
        let mut g = priced_edit_graph();
        let before = g.clone();
        let err = live_txn("plan-link", "dm3", &mut g, |c| {
            // Plant first, *then* refuse — the half-applied shape, not a precondition that never
            // touched anything.
            plant_one(c, edit_origins()[0], edit_origins()[2], 320.0)?;
            assert_eq!(c.links.len(), 4, "the clone really was mutated");
            Err::<u32, String>("no cell near tgt".into())
        })
        .expect_err("must refuse");
        assert_eq!(err, "no cell near tgt");
        assert!(bit_image_eq(&g, &before), "a refused plant leaves live untouched");
        assert_eq!(g.links.len(), 3);
    }

    /// A hand-planted fixture is a cell *and* the way off it, planted by two separate control-channel
    /// commands. Both go through [`live_txn`] now, so both leave an undo point — and undoing the pair
    /// takes the link and the cell back, in that order, onto a bit-identical base.
    ///
    /// Before the c-round only `PlanLink` was transactional: a `PlanCell` published a cell with
    /// nothing recorded, so the chain around it could not be walked back through it and the fixture
    /// could only be undone by reloading the map.
    #[test]
    fn a_planted_fixture_undoes_cell_and_link_together() {
        let mut g = priced_edit_graph();
        let mut stack = TxnStack::default();
        let base = g.clone();
        let shelf = Vec3::new(288.0, -900.0, 264.0);

        // Kommando 1: plantera hyllcellen.
        let (txn_cell, cell) = live_txn("plan-cell", "dm3", &mut g, |c| Ok(c.insert_cell(shelf)))
            .expect("plant cell");
        stack.push(txn_cell, &g).expect("room");
        let efter_cell = g.clone();
        assert_eq!(cell as usize, base.cells.len(), "cellen appendas sist");

        // Kommando 2: plantera vägen ner från den.
        let (txn_drop, link) = live_txn("plan-drop", "dm3", &mut g, |c| {
            c.insert_link(Link {
                from: cell,
                to: 2,
                kind: LinkKind::Drop,
                cost: 1.0,
            });
            Ok(c.links.len() as u32 - 1)
        })
        .expect("plant drop");
        stack.push(txn_drop, &g).expect("room");
        assert_eq!(stack.names(), vec!["plan-cell", "plan-drop"]);
        assert_eq!(link as usize, base.links.len(), "dropet appendas sist");
        assert_eq!(g.cells.len(), base.cells.len() + 1);
        assert_eq!(g.links.len(), base.links.len() + 1);
        assert!(g.reachable(cell, 2), "fixturen ger cellen en väg ner");

        // Undo i omvänd ordning tar hela fixturen, inte bara dess sista halva.
        stack.undo("dm3", "plan-drop", &mut g).expect("undo drop");
        assert!(bit_image_eq(&g, &efter_cell), "cellen står kvar när bara dropet rullas");
        assert_eq!(g.cells.len(), base.cells.len() + 1);
        stack.undo("dm3", "plan-cell", &mut g).expect("undo cell");
        assert!(bit_image_eq(&g, &base), "hela fixturen är borta, bit för bit");
        assert_eq!(g.cells.len(), base.cells.len());
        assert!(stack.is_empty());
    }

    /// The undo chain must be reachable under the exact name the control channel sends.
    ///
    /// This is Sols F5 in one test. `do_fixa` used to resolve every recipe through
    /// [`patch_by_name`] *before* dispatching, so `fixa --undo plan-link` died on
    /// `unknown recipe plan-link` and a planted V296 stayed live: the snapshot existed and no verb
    /// could reach it. The sequence below is what `do_fixa` now performs — resolve the wire name to
    /// a `'static` handle, then pop the chain with it — minus the `GameState` plumbing a unit test
    /// cannot stand up.
    #[test]
    fn plan_link_undo_is_reachable_under_its_control_channel_name() {
        let mut g = priced_edit_graph();
        let mut stack = TxnStack::default();
        let base = g.clone();

        let (txn, _) = live_txn(TXN_PLAN_LINK, "dm3", &mut g, |c| {
            plant_one(c, edit_origins()[0], edit_origins()[2], 320.0)
        })
        .expect("plant");
        stack.push(txn, &g).expect("room");
        assert_eq!(stack.top_name(), Some(TXN_PLAN_LINK));

        // Vad runnern skickar över tråden är en sträng, inte en `&'static str`.
        let wire = String::from("plan-link");
        let resolved = undoable_name(&wire).expect("plan-link måste gå att ange för undo");
        let undone = stack.undo("dm3", resolved, &mut g).expect("undo");

        assert_eq!(undone.name, TXN_PLAN_LINK);
        assert_eq!(undone.depth_left, 0);
        assert!(bit_image_eq(&g, &base), "snapshot-restore, inte invers-op");
    }

    /// The handles are exactly the three plant verbs, and nothing else resolves.
    #[test]
    fn undoable_names_cover_the_plants_and_the_recipes_and_nothing_more() {
        assert_eq!(PLANT_HANDLES, &["plan-cell", "plan-drop", "plan-link"]);
        for h in PLANT_HANDLES {
            assert_eq!(undoable_name(h), Some(*h));
            assert!(patch_by_name(h).is_none(), "{h} är ett handtag, inte ett recept");
        }
        for r in registered_recipe_names() {
            assert_eq!(undoable_name(r), Some(r), "recept måste också gå att undo:a");
        }
        // Ett stavfel får inte glida igenom som "någon plantering".
        for typo in ["plan_link", "planlink", "plan-links", "", "PLAN-LINK"] {
            assert_eq!(undoable_name(typo), None, "{typo:?}");
        }
        let alla: Vec<_> = undoable_names().collect();
        assert_eq!(alla.len(), PLANT_HANDLES.len() + registered_recipe_names().count());
    }

    /// A plant handle is undoable but not appliable: there is no `ShelfPatch` behind it, and
    /// `fixa --apply plan-link` must stay an error rather than become a second planting verb.
    #[test]
    fn plant_handles_are_undoable_but_never_appliable() {
        for h in PLANT_HANDLES {
            assert!(undoable_name(h).is_some());
            assert!(patch_by_name(h).is_none());
            assert!(!registered_recipe_names().any(|n| n == *h));
        }
    }

    /// A refused plant leaves the live graph untouched — the `PlanCell` / `PlanDrop` shape.
    ///
    /// The refusal that matters is the one that comes *after* the mutation: `plant_drop` resolves
    /// both endpoints and then hands the pair to the build's own validators, which say no to a drop
    /// the carve would never emit. On the old in-place path that left the graph carrying whatever the
    /// command had already done to it.
    #[test]
    fn a_refused_plant_leaves_the_live_graph_bit_identical() {
        let mut g = priced_edit_graph();
        let before = g.clone();

        let err = live_txn("plan-cell", "dm3", &mut g, |c| {
            c.insert_cell(Vec3::new(288.0, -900.0, 264.0));
            Err::<u32, String>("cell position is not standable dry floor".into())
        })
        .expect_err("must refuse");
        assert_eq!(err, "cell position is not standable dry floor");
        assert!(bit_image_eq(&g, &before));
        assert_eq!(g.cells.len(), before.cells.len(), "ingen halvplanterad cell blir kvar");

        let err = live_txn("plan-drop", "dm3", &mut g, |c| {
            c.insert_link(Link {
                from: 0,
                to: 2,
                kind: LinkKind::Drop,
                cost: 1.0,
            });
            Err::<u32, String>("not a drop the build would emit".into())
        })
        .expect_err("must refuse");
        assert_eq!(err, "not a drop the build would emit");
        assert!(bit_image_eq(&g, &before));
        assert_eq!(g.links.len(), before.links.len());
    }

    /// `live_txn` rebuilds the derived tables before publishing. Without it the O(1) reachability
    /// gate and the coarse router keep answering for the pre-plant graph, so a `goto` across a fresh
    /// plant reroutes instead of taking it.
    #[test]
    fn live_txn_publishes_a_graph_whose_derived_tables_see_the_plant() {
        let origins = vec![
            Vec3::new(288.0, -844.0, 264.0),
            Vec3::new(328.0, -800.0, 264.0),
            Vec3::new(320.0, -768.0, -16.0),
        ];
        // No links at all: cell 2 is unreachable from cell 0 until the plant.
        let mut g = NavGraph::from_topology(&origins, &[]);
        g.rebuild_derived();
        assert!(!g.reachable(0, 2), "precondition: nothing links 0 to 2");
        let (txn, _) = live_txn("plan-link", "dm3", &mut g, |c| {
            plant_one(c, origins[0], origins[2], 320.0)
        })
        .expect("plant");
        assert!(g.reachable(0, 2), "the published graph's reachability sees the plant");
        txn.unapply(&mut g);
        assert!(!g.reachable(0, 2), "and the restored graph does not");
    }

    /// A navmesh build replaces the graph the whole chain describes, so the chain goes with it.
    #[test]
    fn clearing_the_chain_leaves_nothing_to_undo() {
        let mut g = priced_edit_graph();
        let mut stack = TxnStack::default();
        let (txn, _) = live_txn("plan-link", "dm3", &mut g, |c| {
            plant_one(c, edit_origins()[0], edit_origins()[2], 320.0)
        })
        .expect("plant");
        stack.push(txn, &g).expect("room");
        stack.clear();
        assert!(stack.is_empty());
        assert_eq!(stack.top_name(), None);
        let why = stack.undo("dm3", "plan-link", &mut g).expect_err("nothing to undo");
        assert!(why.contains("nothing to undo"), "{why}");
    }
}
