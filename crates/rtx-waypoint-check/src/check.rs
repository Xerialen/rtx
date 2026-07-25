// SPDX-License-Identifier: AGPL-3.0-or-later

//! Building the navmesh offline and classifying each KTX rocket-jump / curl-jump path against it.
//!
//! The build recipe mirrors the game's **default** DM cvars (see [`build`]): stock physics, bunnyhop
//! speed-jumps (curl on) and rocket-jumps enabled, but **double jump off** (`rtx_doublejump` ships
//! disabled). Teleporters are wired from the entity lump ([`crate::ent::teleports`]) so
//! teleport-riding routes resolve; plats and button-gated doors still aren't spliced offline (their
//! traversal needs the live movers), which the report flags per map.
//!
//! For each authored path A→B we ask how well our mesh reproduces it, in descending strength:
//!
//! - **Matched** — a link of the *same kind* (rocket jump for an RJ path, curl speed-jump for a curl
//!   path) leaves near A and lands near B (both endpoints within `radius`).
//! - **TowardConnected** — the shortcut we really care about: a same-kind link launches from around A
//!   and lands in B's *region* (its LoD-cluster neighbourhood). A rocket jump is a shortcut; if we can
//!   RJ from the source toward the destination's area it counts, even if the exact landing cell isn't
//!   B's. Matched + toward = the shortcut is reproduced.
//! - **JumpConnected** — some *other* airborne link bridges the endpoints; the mesh crosses the gap by
//!   different means.
//! - **RouteConnected** — no jump matches: the endpoints connect only by a ground route. For a rocket
//!   jump this is a *miss* — the bot must take the (often multi-second) detour the human shortcut skips.
//! - **Unreachable** — A and B snap to the mesh but nothing connects them at all.
//! - **Unsnapped** — an endpoint didn't land on any nav cell (a marker over a pedestal, water, or
//!   void), so no honest verdict is possible.

use glam::{Vec3, Vec3Swizzles};
use rtx_nav::bsp::Bsp;
use rtx_nav::navmesh::{
    build_navmesh, LinkCosts, LinkKind, NavGraph, RocketJumpParams, SpeedJumpParams, CLOSED_GATE_PENALTY,
    RJ_AIR_RANGE_XY, RJ_MAX_RISE, RJ_MIN_RISE, RJ_RANGE_XY,
};

use crate::botfile::ResolvedPath;

/// Vertical window (units) within which two points count as "the same storey" for endpoint
/// matching. Both marker and cell origins hover ~24u over the floor with author-dependent slop, so
/// horizontal distance is what discriminates a match; a full storey must not blur into one.
const Z_TOL: f32 = 64.0;

/// Which family of authored path we're checking.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Family {
    RocketJump,
    Curl,
}

/// A candidate nav link and how far its endpoints sit from the authored path's.
#[derive(Clone, Copy, Debug)]
pub struct Near {
    pub link: u32,
    pub kind: LinkKind,
    pub d_src: f32,
    pub d_tgt: f32,
}

/// How well the mesh reproduces one authored path.
#[derive(Clone, Copy, Debug)]
pub enum Verdict {
    Matched(Near),
    /// A same-kind (RJ/curl) link launching from near the source that makes meaningful progress
    /// toward the destination — the shortcut exists in roughly the right place and direction, even if
    /// it lands on a different cell than the waypoint's exact target. `Near.d_tgt` is how far the
    /// landing sits from the destination.
    TowardConnected(Near),
    JumpConnected(Near),
    RouteConnected {
        cost: f32,
        legs: usize,
        jump_legs: usize,
        /// The only route is penalty-priced (a shut gate or a chained speed-jump plain A* blocks).
        degenerate: bool,
    },
    Unreachable {
        nearest_kindred: Option<Near>,
    },
    Unsnapped {
        end: &'static str,
        dist: f32,
    },
}

/// Build the navmesh the checker compares against — the game's **default-DM** loadout, so coverage
/// reflects what real bots use, not the viewer's. That means double jump OFF (`rtx_doublejump`
/// defaults to 0; see `rtx-game`'s `nav_build.rs`), bhop + curl ON, rocket jump ON at stock physics
/// with no `rj` self-boost. Teleporters are wired from the entity lump; plats/gates aren't (offline).
///
/// Double jump being off is the load-bearing choice: with it disabled the generator mints rocket
/// jumps to reach ledges a mid-air jump would otherwise cover, which is exactly the RJ coverage the
/// KTX files were authored against. Turning it on (the viewer's default) suppresses those RJ links.
pub fn build(bsp: &Bsp) -> NavGraph {
    build_navmesh(
        bsp,
        Vec::new(),
        crate::ent::teleports(bsp),
        Vec::new(),
        None,
        false,
        Some(SpeedJumpParams {
            gravity: 800.0,
            accel: 10.0,
            maxspeed: 320.0,
            friction: 4.0,
            stopspeed: 100.0,
            curl: true,
        }),
        Some(RocketJumpParams {
            gravity: 800.0,
            rj_extra: 0.0,
        }),
    )
}

/// *Why* an authored rocket jump we failed to reproduce is missing — the generator bound that
/// excluded it, measured from the same (rise, horizontal) pair the builder gates on.
///
/// A [`Verdict`] says how badly we missed; this says which knob owns the miss, which is what turns
/// a report into a work list. The variants are ordered the way the builder's gates fire, so each
/// path is attributed to the *first* bound that rejected it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Gap {
    /// An endpoint is off the mesh, or the two don't connect at all. A nav **coverage** hole (water,
    /// an unmeshed ledge, a pit floor) — no amount of rocket-jump tuning reaches it.
    OffMesh,
    /// Rise below [`RJ_MIN_RISE`]: a near-flat crossing, authored to skip a gap rather than climb.
    /// The generator skips these by design — reopening them means deciding horizontal RJs are in scope.
    Flat,
    /// Rise above [`RJ_MAX_RISE`] — higher than one blast is modelled to lift.
    TooHigh,
    /// Horizontal beyond [`RJ_RANGE_XY`]: outside *both* passes, so the pair is never simulated.
    TooFar,
    /// Horizontal past [`RJ_AIR_RANGE_XY`] but inside [`RJ_RANGE_XY`] — the band only the ballistic
    /// pass scans. A steep authored shot puts nearly all its impulse into Z, so the unsteered
    /// parabola can't cover the ground; the air-steered pass that could is capped short of here.
    AirBlind,
    /// Inside the envelope on both axes: the generator did look at this pair and came up empty.
    /// The residue worth debugging in the solver itself rather than in a bound.
    InEnvelope,
}

impl Gap {
    /// Every variant, in gate order — the iteration order of the report's gap roll-up.
    pub const ALL: [Gap; 6] = [
        Gap::OffMesh,
        Gap::Flat,
        Gap::TooHigh,
        Gap::TooFar,
        Gap::AirBlind,
        Gap::InEnvelope,
    ];

    pub fn index(self) -> usize {
        Gap::ALL.iter().position(|&g| g == self).expect("variant in ALL")
    }

    pub fn label(self) -> &'static str {
        match self {
            Gap::OffMesh => "off-mesh",
            Gap::Flat => "rise<min",
            Gap::TooHigh => "rise>max",
            Gap::TooFar => "xy>ballistic",
            Gap::AirBlind => "xy>air-pass",
            Gap::InEnvelope => "in-envelope",
        }
    }
}

/// A navmesh plus the link indices grouped by family, so each path is checked without re-scanning.
pub struct Checker<'a> {
    pub graph: &'a NavGraph,
    radius: f32,
    rj_links: Vec<u32>,
    curl_links: Vec<u32>,
    /// Every airborne link (jump/double/speed/rocket) — the pool for the JumpConnected fallback.
    airborne: Vec<u32>,
    /// Per-LoD-cluster set of spatially adjacent clusters (linked by *local* movement — walk/step/
    /// drop/jump — not by rocket jumps or teleporters). A cluster's "neighborhood" is itself plus this
    /// set, used for the region-level rocket-jump match: a KTX jump cluster(A)→cluster(B) is covered
    /// when we have a jump from A's neighborhood to B's.
    cluster_adj: Vec<std::collections::HashSet<u32>>,
}

impl<'a> Checker<'a> {
    pub fn new(graph: &'a NavGraph, radius: f32) -> Self {
        let mut rj_links = Vec::new();
        let mut curl_links = Vec::new();
        let mut airborne = Vec::new();
        for li in 0..graph.links.len() as u32 {
            match graph.link_kind(li) {
                LinkKind::RocketJump => {
                    rj_links.push(li);
                    airborne.push(li);
                }
                LinkKind::SpeedJump => {
                    airborne.push(li);
                    if graph.speed_jump_of_link(li).is_some_and(|s| s.curl_gain > 0.0) {
                        curl_links.push(li);
                    }
                }
                LinkKind::JumpGap | LinkKind::DoubleJump => airborne.push(li),
                _ => {}
            }
        }
        // Cluster adjacency from local movement only, so a "neighbor" is a spatially adjacent region,
        // not one reachable only by the very rocket jumps we're auditing.
        let mut cluster_adj = vec![std::collections::HashSet::new(); graph.cluster_count()];
        for li in 0..graph.links.len() as u32 {
            if !is_local(graph.link_kind(li)) {
                continue;
            }
            if let (Some(cf), Some(ct)) = (
                graph.cluster_of(graph.link_source(li)),
                graph.cluster_of(graph.link_target(li)),
            ) {
                if cf != ct {
                    cluster_adj[cf as usize].insert(ct);
                    cluster_adj[ct as usize].insert(cf);
                }
            }
        }
        Checker {
            graph,
            radius,
            cluster_adj,
            rj_links,
            curl_links,
            airborne,
        }
    }

    pub fn rj_link_count(&self) -> usize {
        self.rj_links.len()
    }
    pub fn curl_link_count(&self) -> usize {
        self.curl_links.len()
    }

    /// Classify one authored path within the given family.
    pub fn classify(&self, p: &ResolvedPath, fam: Family) -> Verdict {
        let g = self.graph;
        let r = self.radius;
        let a = p.from.pos();
        let b = p.to.pos();
        let nb = g.nearest(b);
        let kindred = match fam {
            Family::RocketJump => &self.rj_links,
            Family::Curl => &self.curl_links,
        };

        // Strongest: a same-kind link whose endpoints both land within radius — a confident exact match.
        let mut best: Option<(f32, Near)> = None;
        for &li in kindred {
            let ds = self.d_src_window(li, a);
            let dt = dist_window(g.cell_origin(g.link_target(li)), b);
            if ds <= r && dt <= r {
                let score = ds.max(dt);
                let near = Near {
                    link: li,
                    kind: g.link_kind(li),
                    d_src: ds,
                    d_tgt: dt,
                };
                if best.as_ref().is_none_or(|(s, _)| score < *s) {
                    best = Some((score, near));
                }
            }
        }
        if let Some((_, near)) = best {
            return Verdict::Matched(near);
        }

        // The shortcut that matters: a same-kind jump from the source's *region* to the destination's.
        // A rocket jump is a shortcut — KTX's jump goes cluster(A)→cluster(B), and we cover it when some
        // link launches from A's LoD-cluster neighborhood and lands in B's, even if neither endpoint is
        // exact. This is what a bare "route-connected" verdict hides: an authored RJ we *can* do.
        if let Some(near) = self.toward(kindred, a, b) {
            return Verdict::TowardConnected(near);
        }

        // Next: some other airborne link bridges the same gap.
        if let Some(near) = self.jump_connected(fam, a, b, nb) {
            return Verdict::JumpConnected(near);
        }

        // Otherwise fall back to snapping + reachability.
        let Some(ca) = g.nearest(a) else {
            return Verdict::Unsnapped {
                end: "src",
                dist: f32::INFINITY,
            };
        };
        let Some(cb) = nb else {
            return Verdict::Unsnapped {
                end: "tgt",
                dist: f32::INFINITY,
            };
        };
        let snap_a = (g.cell_origin(ca) - a).length();
        let snap_b = (g.cell_origin(cb) - b).length();
        if snap_a > 2.0 * r {
            return Verdict::Unsnapped {
                end: "src",
                dist: snap_a,
            };
        }
        if snap_b > 2.0 * r {
            return Verdict::Unsnapped {
                end: "tgt",
                dist: snap_b,
            };
        }
        if g.reachable(ca, cb) {
            match g.find_path(ca, cb, &LinkCosts::default()) {
                Some(route) => {
                    let cost: f32 = route.iter().map(|&li| g.link_cost(li)).sum();
                    let jump_legs = route.iter().filter(|&&li| is_airborne(g.link_kind(li))).count();
                    Verdict::RouteConnected {
                        cost,
                        legs: route.len(),
                        jump_legs,
                        degenerate: cost >= CLOSED_GATE_PENALTY,
                    }
                }
                // Reachable per the closure, but plain A* priced the only route away (a chained
                // speed-jump it blocks): still connected, but degenerately.
                None => Verdict::RouteConnected {
                    cost: f32::INFINITY,
                    legs: 0,
                    jump_legs: 0,
                    degenerate: true,
                },
            }
        } else {
            Verdict::Unreachable {
                nearest_kindred: self.nearest_kindred_3d(kindred, a, b),
            }
        }
    }

    /// Attribute a *missed* rocket-jump path to the generator bound that owns it. `None` when the
    /// shortcut is reproduced (matched/toward/jump) or the path isn't a rocket jump — there's nothing
    /// to explain.
    ///
    /// The (rise, horizontal) pair is measured between the mesh cells the endpoints snap to, since
    /// those — not the authored marker origins — are what the builder actually enumerates. If either
    /// end fails to snap the path is [`Gap::OffMesh`] regardless of geometry.
    pub fn gap(&self, p: &ResolvedPath, fam: Family, v: &Verdict) -> Option<Gap> {
        if fam != Family::RocketJump {
            return None;
        }
        match v {
            Verdict::Matched(_) | Verdict::TowardConnected(_) | Verdict::JumpConnected(_) => return None,
            Verdict::Unreachable { .. } | Verdict::Unsnapped { .. } => return Some(Gap::OffMesh),
            Verdict::RouteConnected { .. } => {}
        }
        let g = self.graph;
        let (Some(ca), Some(cb)) = (g.nearest(p.from.pos()), g.nearest(p.to.pos())) else {
            return Some(Gap::OffMesh);
        };
        let (a, b) = (g.cell_origin(ca), g.cell_origin(cb));
        Some(gap_of(b.z - a.z, (b.xy() - a.xy()).length()))
    }

    /// A same-kind link launching from around the source that lands in the destination's *region*.
    /// You have to be at the launch spot to rocket-jump, so the source stays tight (within `radius` of
    /// A). The landing is the forgiving end: it counts if it falls in B's LoD-cluster neighbourhood
    /// (B's cluster or one adjacent to it) — KTX's exact target cell needn't be ours, only its region.
    /// A "toward B" guard (the landing sits closer to B than the launch) rules out jumps heading away.
    /// Returns the candidate landing closest to `b`; its `d_tgt` is the residual to `b`.
    fn toward(&self, kindred: &[u32], a: Vec3, b: Vec3) -> Option<Near> {
        let g = self.graph;
        let r = self.radius;
        let clb = g.nearest(b).and_then(|c| g.cluster_of(c))?;
        let in_nbh_b = |cl: u32| cl == clb || self.cluster_adj[clb as usize].contains(&cl);
        let mut best: Option<(f32, Near)> = None;
        for &li in kindred {
            let ds = self.d_src_window(li, a);
            if ds > r {
                continue; // must launch from around the origin
            }
            let land = g.cell_origin(g.link_target(li));
            let in_region = g.cluster_of(g.link_target(li)).is_some_and(in_nbh_b) || dist_window(land, b) <= r;
            let resid = (land - b).length();
            let toward = resid < (self.launch_origin(li) - b).length();
            if in_region && toward && best.as_ref().is_none_or(|(br, _)| resid < *br) {
                best = Some((
                    resid,
                    Near {
                        link: li,
                        kind: g.link_kind(li),
                        d_src: ds,
                        d_tgt: resid,
                    },
                ));
            }
        }
        best.map(|(_, n)| n)
    }

    /// The world origin a kindred link launches from — the source cell, or a speed jump's takeoff
    /// ledge (its `from` cell is the runway start, not the launch point).
    fn launch_origin(&self, li: u32) -> Vec3 {
        match self.graph.speed_jump_of_link(li) {
            Some(sj) => sj.takeoff,
            None => self.graph.cell_origin(self.graph.link_source(li)),
        }
    }

    /// A source-anchor distance under the z-window metric: the nearer of the link's source cell and,
    /// for a speed jump, its takeoff ledge (a speed jump's `from` is the runway start, not the ledge).
    fn d_src_window(&self, li: u32, a: Vec3) -> f32 {
        let g = self.graph;
        let mut best = dist_window(g.cell_origin(g.link_source(li)), a);
        if let Some(sj) = g.speed_jump_of_link(li) {
            best = best.min(dist_window(sj.takeoff, a));
        }
        best
    }

    fn jump_connected(&self, fam: Family, a: Vec3, b: Vec3, nb: Option<u32>) -> Option<Near> {
        let g = self.graph;
        let r = self.radius;
        let mut best: Option<(f32, Near)> = None;
        for &li in &self.airborne {
            let is_kindred = match fam {
                Family::RocketJump => g.link_kind(li) == LinkKind::RocketJump,
                Family::Curl => g.speed_jump_of_link(li).is_some_and(|s| s.curl_gain > 0.0),
            };
            if is_kindred {
                continue;
            }
            let ds = self.d_src_window(li, a);
            let dt = dist_window(g.cell_origin(g.link_target(li)), b);
            if !ds.is_finite() || !dt.is_finite() {
                continue;
            }
            let accepted =
                ds <= r && (dt <= r || (dt <= 3.0 * r && nb.is_some_and(|nb| self.same_shelf(g.link_target(li), nb))));
            if accepted {
                let score = ds.max(dt);
                let near = Near {
                    link: li,
                    kind: g.link_kind(li),
                    d_src: ds,
                    d_tgt: dt,
                };
                if best.as_ref().is_none_or(|(s, _)| score < *s) {
                    best = Some((score, near));
                }
            }
        }
        best.map(|(_, n)| n)
    }

    /// Whether `y` is reachable from `x` over flat ground (Walk/Step) within 6 hops — the same
    /// shelf. Absorbs our rocket-jump solver landing on the same ledge but an offset cell.
    fn same_shelf(&self, x: u32, y: u32) -> bool {
        if x == y {
            return true;
        }
        let g = self.graph;
        let mut seen = std::collections::HashSet::new();
        seen.insert(x);
        let mut frontier = vec![x];
        for _ in 0..6 {
            let mut next = Vec::new();
            for c in frontier {
                for &li in &g.adjacency[c as usize] {
                    if matches!(g.link_kind(li), LinkKind::Walk | LinkKind::Step) {
                        let t = g.link_target(li);
                        if t == y {
                            return true;
                        }
                        if seen.insert(t) {
                            next.push(t);
                        }
                    }
                }
            }
            if next.is_empty() {
                break;
            }
            frontier = next;
        }
        false
    }

    /// The nearest same-kind link by raw 3D endpoint distance (no z-window) — the number that tells
    /// whether an unreachable path is a radius-tuning miss or a genuine hole.
    fn nearest_kindred_3d(&self, kindred: &[u32], a: Vec3, b: Vec3) -> Option<Near> {
        let g = self.graph;
        kindred
            .iter()
            .map(|&li| {
                let mut ds = (g.cell_origin(g.link_source(li)) - a).length();
                if let Some(sj) = g.speed_jump_of_link(li) {
                    ds = ds.min((sj.takeoff - a).length());
                }
                let dt = (g.cell_origin(g.link_target(li)) - b).length();
                (
                    ds.max(dt),
                    Near {
                        link: li,
                        kind: g.link_kind(li),
                        d_src: ds,
                        d_tgt: dt,
                    },
                )
            })
            .min_by(|x, y| x.0.total_cmp(&y.0))
            .map(|(_, n)| n)
    }
}

/// Which generator bound rejects a `(rise, horizontal)` pair, evaluated in the order the builder's
/// own gates fire so a miss is blamed on the *first* one that excluded it. `InEnvelope` means both
/// passes were allowed to look at this pair and neither produced a link.
fn gap_of(dz: f32, horiz: f32) -> Gap {
    if dz < RJ_MIN_RISE {
        Gap::Flat
    } else if dz > RJ_MAX_RISE {
        Gap::TooHigh
    } else if horiz > RJ_RANGE_XY {
        Gap::TooFar
    } else if horiz > RJ_AIR_RANGE_XY {
        Gap::AirBlind
    } else {
        Gap::InEnvelope
    }
}

/// Horizontal distance if the two points share a storey (`|Δz| ≤ Z_TOL`), else infinite.
fn dist_window(p: Vec3, q: Vec3) -> f32 {
    if (p.z - q.z).abs() <= Z_TOL {
        ((p.x - q.x).powi(2) + (p.y - q.y).powi(2)).sqrt()
    } else {
        f32::INFINITY
    }
}

fn is_airborne(k: LinkKind) -> bool {
    matches!(
        k,
        LinkKind::JumpGap | LinkKind::DoubleJump | LinkKind::SpeedJump | LinkKind::RocketJump | LinkKind::Hook
    )
}

/// Local movement kinds that define spatial cluster adjacency — walking, stepping, dropping, and
/// short jumps. Rocket jumps, teleporters, plats and hooks connect distant regions and would make
/// far-apart clusters false "neighbors", so they're excluded.
fn is_local(k: LinkKind) -> bool {
    matches!(
        k,
        LinkKind::Walk
            | LinkKind::Step
            | LinkKind::Drop
            | LinkKind::JumpGap
            | LinkKind::DoubleJump
            | LinkKind::SpeedJump
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gap_blames_the_first_gate_that_rejects() {
        // A near-flat crossing is "too flat" even when it's also absurdly long — rise gates first.
        assert_eq!(gap_of(16.0, 700.0), Gap::Flat);
        assert_eq!(gap_of(400.0, 100.0), Gap::TooHigh);
        // Past the ballistic reach: no pass looks here at all.
        assert_eq!(gap_of(200.0, RJ_RANGE_XY + 1.0), Gap::TooFar);
        // The band only the (unsteered) ballistic pass scans — dm3's ~250u-rise / ~380u-out family.
        assert_eq!(gap_of(250.0, 380.0), Gap::AirBlind);
        // Bounds themselves are inclusive, matching the builder's `(MIN..=MAX).contains` gate.
        assert_eq!(gap_of(RJ_MIN_RISE, RJ_AIR_RANGE_XY), Gap::InEnvelope);
        assert_eq!(gap_of(RJ_MAX_RISE, RJ_RANGE_XY), Gap::AirBlind);
        // Squarely inside both passes' reach: the solver looked and found nothing.
        assert_eq!(gap_of(120.0, 100.0), Gap::InEnvelope);
    }

    #[test]
    fn window_metric_respects_storeys() {
        let a = Vec3::new(0.0, 0.0, 0.0);
        let same = Vec3::new(30.0, 40.0, 40.0); // Δz 40 ≤ 64 → 3-4-5 horizontal = 50
        assert_eq!(dist_window(a, same), 50.0);
        let upstairs = Vec3::new(30.0, 40.0, 200.0); // Δz 200 > 64 → infinite
        assert!(dist_window(a, upstairs).is_infinite());
    }

    /// Full-pipeline check against a real install, gated on the same env idiom as the rtx-nav /
    /// rtx-game tests. `cargo test` runs with the crate dir as CWD, so pass **absolute** paths:
    ///   RTX_TEST_BASEDIR="$PWD/playground" RTX_TEST_WAYPOINTS="$PWD/waypoints" \
    ///     cargo test -p rtx-waypoint-check --release -- --nocapture ktx_dm4
    #[test]
    fn ktx_dm4_end_to_end() {
        let (Ok(base), Ok(wp)) = (std::env::var("RTX_TEST_BASEDIR"), std::env::var("RTX_TEST_WAYPOINTS")) else {
            eprintln!("RTX_TEST_BASEDIR / RTX_TEST_WAYPOINTS not set; skipping");
            return;
        };
        let text = std::fs::read_to_string(std::path::Path::new(&wp).join("dm4.bot")).expect("dm4.bot");
        let bytes = crate::pak::resolve_bsp(std::path::Path::new(&base), "dm4").expect("dm4.bsp");
        let bsp = Bsp::parse(&bytes).expect("parse dm4.bsp");

        let file = crate::botfile::parse(&text);
        let markers = crate::ent::marker_walk(&bsp);
        assert_eq!(markers.len(), 54, "entity-walk K");
        assert_eq!(file.implied_entity_markers(), 54, "file-implied K");

        let (paths, dropped) = crate::botfile::resolve(&file, &markers);
        assert_eq!(dropped, 0, "every path reference resolves");
        let rj = paths.iter().filter(|p| p.is_rj()).count();
        let curl = paths.iter().filter(|p| p.is_curl()).count();
        assert_eq!(rj, 23, "dm4 rocket-jump paths");
        assert_eq!(curl, 1, "dm4 curl paths");

        let graph = build(&bsp);
        let checker = Checker::new(&graph, 96.0);
        // Pin the game-default recipe: double jump OFF mints ~110 rj links for dm4; the viewer's
        // double-jump-ON recipe would collapse it to ~27. Guards against the recipe regressing.
        assert!(
            checker.rj_link_count() > 60,
            "expected the double-jump-off rj count (~110), got {}",
            checker.rj_link_count()
        );
        for p in paths.iter().filter(|p| p.is_rj()) {
            assert!(
                !matches!(checker.classify(p, Family::RocketJump), Verdict::Unsnapped { .. }),
                "rj {}->{} should snap",
                p.src,
                p.dst
            );
        }
    }
}
