// SPDX-License-Identifier: AGPL-3.0-or-later

//! Certified grounded route tracking.
//!
//! Walking a route is not a control problem the way a fight is. The floor is static, the bot knows
//! its own origin and velocity exactly, and pmove is deterministic — so "will steering at this point
//! keep me on the floor" has an *answer*, not an estimate. What the reactive path did instead was
//! probe the ground each frame and slam a full-reverse wish the moment a local sample said "edge",
//! which at 300 ups can only overshoot: the bot arrives at a stair corner already braking, bleeds the
//! speed that would have carried it across, and fumbles the one leg that needed an exact line.
//!
//! [`plan_walk`] rolls the [`pmove`](crate::pmove_sim) forward under the **pursuit policy the steerer
//! actually runs** — aim at a point a fixed distance ahead along the route polyline, optionally shifted
//! into a lane beside it, full wish, no jump — and accepts a policy only if the rolled-out path stays
//! inside a tube around the route for the whole horizon. The steerer then flies exactly that policy,
//! so the rollout is a statement about what the bot will really do. `None` means nothing tracks from
//! here — the predicted boxed state, and the one case the ledge brakes are for.
//!
//! The policy carries a lane offset as well as a look-ahead, for the case where the route's own line
//! is not quite the line to walk. That mattered a great deal before cell origins were seated on real
//! floor (`NavGraph::seat_on_real_floor`): origins sat in the clip hull's 16u skirt, so the line
//! between cell centres left the tread at every riser and dm3's 724 lane could not be certified
//! centred at *any* speed. With the mesh describing the surface honestly the centred line certifies,
//! and the offsets are what remain for genuinely awkward ground rather than for mesh error. Worth
//! remembering which of those two a failure is, before reaching for gains and gates.
//!
//! The plan is a *policy parameter*, not a fixed aim point: prediction and execution both evaluate
//! [`aim_point`] against the same route-anchored polyline, locating the bot on it by projection, so
//! they pursue the same point by construction rather than by agreement. Anchoring to the route and not
//! to the bot is load-bearing — a line that starts under the bot's feet bends to follow it, and
//! offsetting from that walks the bot a little further out every frame, chasing its own displacement.
//! Re-deriving the aim from the live projection each frame is what makes the policy self-correcting
//! instead of a dead-reckoned path the bot drifts off; the residual divergence is bounded by the same
//! [`LATERAL_TOL`] tube the caller re-checks every frame. Same trade [`hopsim`](super::hopsim) accepts.

use glam::{Vec3, Vec3Swizzles};

use super::hopsim::point_on;
use crate::math::yaw_of;
use crate::pmove_sim::{pm_step, Hull, PmParams, PmState};
use rtx_nav::navmesh::PLAYER_HALF_WIDTH;
use rtx_nav::qphys::STEP_HEIGHT;
use rtx_nav::strafe::{Cmd, MOVE_SPEED};

/// Rollout tick length (77 Hz, matching [`hopsim`](super::hopsim) and the offline certifier).
const DT: f32 = 1.0 / 77.0;
/// Tick budget for one walk rollout (~0.52 s). Long enough to outlive the re-certification interval
/// plus the brakes' reaction window, so a certificate never lapses into unproven ground; short enough
/// that the fan stays cheap, which matters more here than for a hop — a *grounded* pmove tick scraping
/// a staircase takes the step-up path and traces the hull several times over.
const MAX_TICKS: usize = 40;
/// Pursuit distances tried, longest first, so the smoothest line that certifies wins. 96 matches the
/// near-field glide's chord (three cells — enough to erase the grid's 45° zigzag), 64 is two cells,
/// and 40 is one leg plus an arrival radius: the tightest tracking still ahead of the bot's feet.
pub const LOOKAHEADS: [f32; 3] = [96.0, 64.0, 40.0];
/// How far off the route polyline the rolled-out path may stray and still count as tracking — one
/// navmesh cell. A pursuit line deliberately cuts the zigzag's corners (that is the point), which
/// deviates about half a cell; twice that is drift.
pub const LATERAL_TOL: f32 = 32.0;
/// …and how far it may sit above or below the matched segment. A riser plus grid quantization slack:
/// deeper than this under the line means the bot left the staircase rather than walked down it.
pub const Z_TOL: f32 = 48.0;
/// How far below an airborne bot the roll looks for floor before calling it a fall. Running a real
/// staircase at speed is *not* a grounded affair: each riser launches the bot and it skims several
/// treads before settling, so counting airborne ticks cannot tell a stair from a cliff — on dm3's
/// 724 lane the bot is off the floor for a third of the flight while tracking the line exactly. What
/// separates the two is whether there is anything underneath. A skim always has a tread within a
/// step or two; a walk off a lip has the void. Probing this directly also means a fall is caught the
/// moment it starts, instead of waiting the ~25 ticks free-fall needs to show up as depth. Matches
/// [`hopsim`](super::hopsim)'s `MAX_FALL`, the same "past this it's an edge, not a step down".
const VOID_PROBE: f32 = 64.0;
/// Hur djupt under origo **punkthullen** sonderas för att skilja ett trappsteg från en
/// avgrund. grok2:s dm3-svep mätte alla 54 häng-off-läppar: trapporna bottnar på 32–48 u,
/// de tretton djupa remsorna på ≥176 u. 128 ligger med marginal åt båda håll.
pub const LIP_PROBE: f32 = 128.0;

/// Hur bred tuben får vara när mitten hänger över djupt tomrum.
///
/// [`LATERAL_TOL`] är 32 u överallt, och det är bredare än golvet där punktgolvet tagit
/// slut: dm3:s L-hylla har 27 u ståbar mark norr om kordan, så ett certifikat kan vara
/// "färskt" 22 u ut på en remsa som slutar 5 u längre bort. Talet är inte trimmat mot
/// mätdata — det är en halv cell, samma storleksordning som fläktens minsta lateral.
pub const LATERAL_TOL_LIP: f32 = 8.0;

/// Hänger mitten över djupt tomrum?
///
/// **Punkthullen, inte spelarhullen.** `hull1` är "skulle en spelare få plats", och på
/// en häng-off BÄR den — kroppens södra kant vilar på läppen medan origo hänger i luften.
/// Därför kan [`over_void`], som spårar hull1, aldrig se en häng-off. `hull0` är den
/// punktspårning QuakeC:s `traceline` gör, och den ser det verkliga golvet.
pub fn over_lip(bsp: &crate::bsp::Bsp, p: Vec3) -> bool {
    let t = bsp.hull0_trace(p, p - Vec3::Z * LIP_PROBE);
    t.fraction >= 1.0 && !t.all_solid && !t.start_solid
}

/// Vilka kantvakter som är påslagna. Båda av som förval — binären är då
/// beteendemässigt oförändrad, och armarna skiljs åt av cvarer i stället för av byggen.
#[derive(Clone, Copy, Default, PartialEq, Eq, Debug)]
pub struct EdgeGuard {
    /// **F1** — smalna tuben där mitten hänger över djupt tomrum.
    pub narrow: bool,
    /// **F2** — låt certifikatet lapsa när underlaget byter karaktär.
    pub recert: bool,
}

/// Färskhetsvillkorets **rumsdel**: täcker beviset fortfarande marken under boten?
///
/// Tidsdelen (`WALK_RECERT`) och bendelen (`legs`) ägs av steeraren. Den här delen är
/// den som brast: invarianten *"a plan is never flown past the ground it was proven
/// over"* håller längs bågen men inte tvärs den, för tuben är lika bred överallt medan
/// golvet inte är det.
pub fn tube_ok(off: Offset, over_lip_now: bool, over_lip_at_cert: bool, guard: EdgeGuard) -> bool {
    if off.dz.abs() > Z_TOL {
        return false;
    }
    if guard.recert && over_lip_now != over_lip_at_cert {
        return false;
    }
    let tol = if guard.narrow && over_lip_now { LATERAL_TOL_LIP } else { LATERAL_TOL };
    off.lateral <= tol
}

/// Ticks between arc-progress checks.
const PROGRESS_WINDOW: usize = 15;
/// Arc-length the cursor must gain per [`PROGRESS_WINDOW`] (≈40 ups average) or the roll is wedged —
/// nosed into a riser or a wall, sliding without advancing. Well under a walk's ~250 ups, so only a
/// genuine stall trips it.
const PROGRESS_MIN: f32 = 8.0;
/// Reaching within this of the polyline's end counts as tracking it to the end (an arrival radius).
const END_SLACK: f32 = 24.0;

/// What the pursuit policy does when rolled forward from a live grounded state.
#[derive(Clone, Copy, PartialEq, Debug)]
pub enum WalkRollout {
    /// Stayed inside the corridor tube for the whole horizon (or tracked the polyline to its end).
    Held,
    /// Left the tube — momentum beat the pursuit and carried the bot off the line.
    Veered,
    /// Left the floor and stayed off it: walked over a lip rather than down a step.
    Fell,
    /// Stopped gaining arc: wedged against a riser or wall.
    Stalled,
    /// Stepped onto lava/slime.
    Burned,
    /// Entered a volume the world hull cannot see — a closed gate, or a teleporter the route is not
    /// trying to take.
    Blocked,
}

/// Lateral offsets tried for the pursuit point, centred line first — so a straight route is walked
/// straight, and an offset is only taken when the centre provably does not hold. Offering the pursuit
/// a lane a half-cell to either side lets the rollout find ground the route's own line misses, the
/// same way [`plan_hop`](super::hopsim::plan_hop) fans offsets to discover a human's outer-wall line
/// on a curve. Which side is the safe one depends on where the drop is, so both are tried and the
/// geometry decides. Note a persistent need for an offset on ordinary ground is a signal the *mesh*
/// is off, not the ground: that is what dm3's 724 lane looked like before cell origins were seated.
pub const LATERALS: [f32; 5] = [0.0, 16.0, -16.0, 32.0, -32.0];

/// A certified pursuit policy: steer at the corridor point this far ahead along the route, shifted
/// this far to the side of it.
#[derive(Clone, Copy, Debug)]
pub struct WalkPlan {
    pub lookahead: f32,
    pub lateral: f32,
}

/// The point this policy aims at from arc-position `s` along `pts`. Shared by the rollout and the
/// live steerer so prediction and execution pursue exactly the same point.
pub fn aim_point(pts: &[Vec3], s: f32, plan: WalkPlan) -> Vec3 {
    let base = point_on(pts, s + plan.lookahead);
    if plan.lateral.abs() < 1e-3 {
        return base;
    }
    let ahead = point_on(pts, s + plan.lookahead + 16.0);
    let dir = (ahead.xy() - base.xy()).normalize_or_zero();
    base + Vec3::new(-dir.y * plan.lateral, dir.x * plan.lateral, 0.0)
}

/// Height mismatch a match absorbs for free before the level penalty bites. A route leg over a
/// staircase interpolates its height linearly between two cell centres, but the floor underneath is
/// treads: mid-leg the bot legitimately stands up to a riser off its own route line. Charging that
/// like a wrong-level match (what a flat doubled-z penalty does) makes the *previous* segment's
/// clamped corner outscore the correct segment ahead, which freezes the cursor and leaves the pursuit
/// aiming at a point the bot has already walked past. Beyond this, the penalty resumes at full
/// strength, so two flights of a spiral stacked ~100u apart stay as distinct as ever.
const Z_FREE: f32 = 24.0;

/// How well a candidate segment matches: lateral offset, plus a doubled penalty on whatever height
/// mismatch exceeds [`Z_FREE`]. Lower is better.
fn match_score(lateral: f32, dz: f32) -> f32 {
    lateral + (dz.abs() - Z_FREE).max(0.0) * 2.0
}

/// Where a point sits relative to a polyline.
#[derive(Clone, Copy, Debug)]
pub struct Offset {
    pub lateral: f32,
    pub dz: f32,
}

/// Where a point sits along a polyline: arc-distance travelled, plus how far off the line it is.
#[derive(Clone, Copy, Debug)]
struct Track {
    /// XY arc-distance from the polyline's start to the projection foot.
    s: f32,
    off: Offset,
}

/// Project `p` onto `pts`, keeping the cursor **monotonic**: segments whose foot lies behind `s_prev`
/// are not considered, so a route that doubles back over itself (a switchback, a spiral's flight
/// above its own core) can never snap the pursuit backward onto ground already covered. Among the
/// rest the best [match](match_score) wins. Falls back to holding `s_prev` when nothing qualifies.
fn track(pts: &[Vec3], s_prev: f32, p: Vec3) -> Track {
    let mut acc = 0.0;
    let mut best: Option<(f32, Track)> = None;
    for w in pts.windows(2) {
        let (a, b) = (w[0], w[1]);
        let seg_len = (b.xy() - a.xy()).length();
        if seg_len > 1e-3 {
            let t = ((p.xy() - a.xy()).dot(b.xy() - a.xy()) / (seg_len * seg_len)).clamp(0.0, 1.0);
            let s = acc + t * seg_len;
            if s >= s_prev {
                let foot = a.lerp(b, t);
                let cand = Track {
                    s,
                    off: Offset {
                        lateral: (p.xy() - foot.xy()).length(),
                        dz: p.z - foot.z,
                    },
                };
                if best
                    .as_ref()
                    .is_none_or(|(bs, _)| match_score(cand.off.lateral, cand.off.dz) < *bs)
                {
                    best = Some((match_score(cand.off.lateral, cand.off.dz), cand));
                }
            }
        }
        acc += seg_len;
    }
    best.map(|(_, t)| t).unwrap_or(Track {
        s: s_prev,
        off: Offset { lateral: 0.0, dz: 0.0 },
    })
}

/// How far `p` sits off `pts`, with no monotonicity constraint — the caller's "is the bot still on the
/// line its plan was certified over" test. Shares [`match_score`] with the rollout's cursor, so a
/// certificate is judged against the same notion of on-route that proved it. `None` for a degenerate
/// polyline.
pub fn off_line(pts: &[Vec3], p: Vec3) -> Option<Offset> {
    (pts.len() >= 2).then(|| track(pts, f32::NEG_INFINITY, p).off)
}

/// How far along `pts` the point `p` sits. The live steerer's counterpart to the rollout's cursor:
/// both locate the bot on the same route-anchored line before taking [`aim_point`] from it, which is
/// what makes the flown policy the certified one.
pub fn arc_at(pts: &[Vec3], p: Vec3) -> f32 {
    track(pts, f32::NEG_INFINITY, p).s
}

/// Whether nothing solid sits within [`VOID_PROBE`] under `p` — the bot is over open air rather than
/// skimming a tread. A trace that starts inside solid reports no contact too, so that case is excluded
/// explicitly; it is not a void.
fn over_void(hull: &impl Hull, p: Vec3) -> bool {
    let t = hull.trace(p, p - Vec3::Z * VOID_PROBE);
    t.fraction >= 1.0 && !t.all_solid && !t.start_solid
}

/// Whether `p` sits inside any blocked AABB, grown by the player half-width in XY and by a step in z
/// below the box (the same slack the near-field stamps such volumes with, so a rollout and the
/// clearance grid agree on where a shut door starts).
fn blocked_at(p: Vec3, blocked: &[(Vec3, Vec3)]) -> bool {
    blocked.iter().any(|&(lo, hi)| {
        p.x >= lo.x - PLAYER_HALF_WIDTH
            && p.x <= hi.x + PLAYER_HALF_WIDTH
            && p.y >= lo.y - PLAYER_HALF_WIDTH
            && p.y <= hi.y + PLAYER_HALF_WIDTH
            && p.z >= lo.z - STEP_HEIGHT
            && p.z <= hi.z
    })
}

/// Roll the pursuit policy forward from `st` (a live grounded frame) along `route_pts` — the route
/// polyline, anchored at the **current leg's source cell**, not at the bot. That anchoring matters:
/// the lateral offset is measured from the route, so the line it is measured against has to be one
/// the bot's own position cannot move. A polyline starting under the bot's feet bends to wherever the
/// bot already is, and offsetting from *that* makes the bot chase its own displacement a little
/// further out every frame. Here the reference is fixed, the bot's arc position is found by
/// projection, and the offset lane stays where the geometry put it.
///
/// Every tick re-aims at the cursor's look-ahead point and drives a full forward wish, exactly what
/// the steerer emits; the roll ends the moment the path leaves the tube, falls, burns, enters a
/// blocked volume, or stops advancing.
pub fn roll_walk(
    hull: &impl Hull,
    is_hazard: &impl Fn(Vec3) -> bool,
    blocked: &[(Vec3, Vec3)],
    route_pts: &[Vec3],
    mut st: PmState,
    plan: WalkPlan,
    p: &PmParams,
) -> WalkRollout {
    let total: f32 = route_pts.windows(2).map(|w| (w[1].xy() - w[0].xy()).length()).sum();
    // Where the bot already sits along the route — the cursor starts there, not at the line's head.
    let mut s = track(route_pts, f32::NEG_INFINITY, st.origin).s;
    let mut mark = s;
    for tick in 0..MAX_TICKS {
        let target = aim_point(route_pts, s, plan);
        let cmd = Cmd {
            view_yaw: yaw_of(target.xy() - st.origin.xy()),
            forward: MOVE_SPEED,
            side: 0.0,
            jump: false,
        };
        pm_step(hull, &mut st, &cmd, p, DT);

        if !st.on_ground && over_void(hull, st.origin) {
            return WalkRollout::Fell;
        }
        let t = track(route_pts, s, st.origin);
        s = t.s;
        if t.off.lateral > LATERAL_TOL || t.off.dz.abs() > Z_TOL {
            return WalkRollout::Veered;
        }
        if is_hazard(st.origin) {
            return WalkRollout::Burned;
        }
        if blocked_at(st.origin, blocked) {
            return WalkRollout::Blocked;
        }
        if s >= total - END_SLACK {
            return WalkRollout::Held; // tracked the corridor to its end
        }
        if tick > 0 && tick % PROGRESS_WINDOW == 0 {
            if s - mark < PROGRESS_MIN {
                return WalkRollout::Stalled;
            }
            mark = s;
        }
    }
    WalkRollout::Held
}

/// Certify a pursuit policy for the grounded state `st` against `route_pts` — the route polyline
/// anchored at the current leg's source (see [`roll_walk`] for why not at the bot). Sweeps
/// [`LOOKAHEADS`] longest-first and, within each,
/// [`LATERALS`] centred-line-first, returning the first policy that tracks the corridor for the whole
/// horizon: a longer look-ahead cuts corners more smoothly, a shorter one hugs the line, and an offset
/// one buys a lane the cell centres don't describe — so this yields the smoothest, straightest policy
/// that provably stays on the floor. `None` when none do, which is exactly when the fallback brakes
/// should own the frame.
pub fn plan_walk(
    hull: &impl Hull,
    is_hazard: &impl Fn(Vec3) -> bool,
    blocked: &[(Vec3, Vec3)],
    route_pts: &[Vec3],
    st: PmState,
    p: &PmParams,
) -> Option<WalkPlan> {
    if route_pts.len() < 2 {
        return None;
    }
    LOOKAHEADS
        .iter()
        .flat_map(|&lookahead| LATERALS.iter().map(move |&lateral| WalkPlan { lookahead, lateral }))
        .find(|&plan| roll_walk(hull, is_hazard, blocked, route_pts, st, plan, p) == WalkRollout::Held)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pmove_sim::HeightHull;

    fn no_hazard(_: Vec3) -> bool {
        false
    }

    /// A centred pursuit at this look-ahead — the policy shape most of these fixtures exercise.
    fn straight(lookahead: f32) -> WalkPlan {
        WalkPlan {
            lookahead,
            lateral: 0.0,
        }
    }

    /// A diagonal staircase whose treads are shallow enough that a single 8u near-field column can
    /// straddle two 16u risers — the dm3 geometry that reads as a cliff to a local probe — with a
    /// fatal drop off both sides of a 64u-wide lane. Height rises 16u per 22.5u of travel along the
    /// diagonal, so a 45u leg climbs 32u: two risers in one move.
    fn wedge_stairs() -> HeightHull<impl Fn(f32, f32) -> Option<f32>> {
        HeightHull {
            floor: |x, y| {
                // Risers run across +y, 16u tall on a 16u tread — so the route's 32u of y per leg
                // crosses two of them. The lane is 224u of x; past its edges is the fatal drop.
                ((-192.0..=32.0).contains(&x) && y >= -16.0).then(|| (y / 16.0).floor().max(0.0) * 16.0)
            },
        }
    }

    /// The route polyline up that staircase: dm3's 1014 → 977 → 938 → 895 shape, legs of (−32, +32)
    /// climbing 32u each — a pure 45° diagonal that cuts every riser at its corner.
    fn stair_route() -> Vec<Vec3> {
        (0..8)
            .map(|i| {
                let k = i as f32;
                Vec3::new(-32.0 * k, 32.0 * k, 32.0 * k)
            })
            .collect()
    }

    /// The lane this whole module exists for, against the **real** dm3 geometry rather than a
    /// synthetic hull: cells 1014 -> 977 -> 938 -> 895, the 45-degree diagonal beside a fatal drop
    /// that the reactive brakes fumbled.
    ///
    /// This began life asserting the opposite: that a *centred* pursuit could not hold this lane at
    /// any speed, because the straight line between cell centres cut each step's corner where there
    /// was air. That was true, and it was a fact about the mesh rather than about control — the cell
    /// origins were sitting in the clip hull's 16u skirt, off the real tread. Seating them on render
    /// floor (`NavGraph::seat_on_real_floor`) removed the cause, so the centred line now tracks and
    /// the lane offset is no longer needed. Kept, inverted, as the regression guard for that.
    ///
    /// Needs the map: set `RTX_TEST_MAPS` to a directory holding `dm3.bsp` (`playground/qw/maps`).
    /// Vacuously green otherwise, the same opt-in idiom as [`crate::demo_replay`].
    #[test]
    fn dm3_stair_lane_certifies_on_the_centred_line() {
        let Some(dir) = std::env::var("RTX_TEST_MAPS").ok() else {
            eprintln!("RTX_TEST_MAPS not set; skipping");
            return;
        };
        let Ok(bytes) = std::fs::read(std::path::Path::new(&dir).join("dm3.bsp")) else {
            eprintln!("no dm3.bsp in RTX_TEST_MAPS; skipping");
            return;
        };
        let bsp = crate::bsp::Bsp::parse(&bytes).expect("parse dm3");
        let graph = rtx_nav::navmesh::build_navmesh(
            &bsp,
            Vec::new(),
            Vec::new(),
            Vec::new(),
            None,
            false,
            Some(rtx_nav::navmesh::SpeedJumpParams {
                gravity: 800.0,
                accel: 10.0,
                maxspeed: 320.0,
                friction: 4.0,
                stopspeed: 100.0,
                curl: true,
            }),
            None,
        );
        let costs = rtx_nav::navmesh::LinkCosts::default();
        let start = graph.nearest(Vec3::new(-64.0, 640.0, 56.0)).expect("start cell");
        let goal = graph.nearest(Vec3::new(-160.0, 736.0, 120.0)).expect("goal cell");
        let route = graph.find_path(start, goal, &costs).expect("a route up the stairs");
        let origin = graph.cell_origin(start);
        let pts: Vec<Vec3> = std::iter::once(origin)
            .chain(route.iter().map(|&l| graph.cell_origin(graph.link_target(l))))
            .collect();
        let p = PmParams::default();

        for spd in [160.0f32, 240.0, 320.0] {
            let d = (pts[1].xy() - origin.xy()).normalize() * spd;
            let st = PmState {
                origin,
                vel: Vec3::new(d.x, d.y, 0.0),
                on_ground: true,
                jump_held: false,
            };
            // The lane certifies, and the *centred* line is what does it. That is the whole point of
            // seating cell origins on real floor: before that, the line between cell centres ran
            // through the clip hull's skirt and left the tread at every riser, so only an offset lane
            // could be certified. The centred line certifying now is the mesh finally describing the
            // surface the feet are on — if this regresses to needing an offset, the seating is broken.
            assert_eq!(
                roll_walk(&bsp, &no_hazard, &[], &pts, st, straight(LOOKAHEADS[0]), &p),
                WalkRollout::Held,
                "the centred line should track the seated stair lane at {spd} ups"
            );
            let plan = plan_walk(&bsp, &no_hazard, &[], &pts, st, &p)
                .unwrap_or_else(|| panic!("dm3's stair lane should certify at {spd} ups"));
            assert_eq!(plan.lateral, 0.0, "no lane offset should be needed now: {plan:?}");
            assert_eq!(roll_walk(&bsp, &no_hazard, &[], &pts, st, plan, &p), WalkRollout::Held);
        }
    }

    /// The target case: a 45° diagonal traverse across risers, beside a fatal drop. A certified
    /// pursuit tracks it at speed — the whole point being that the physics is determined, so the line
    /// is holdable and no brake is needed.
    #[test]
    fn plan_walk_tracks_wedge_stairs_beside_a_cliff() {
        let hull = wedge_stairs();
        let p = PmParams::default();
        let route = stair_route();
        let dir = (route[1] - route[0]).xy().normalize() * 300.0;
        let st = PmState {
            origin: route[0],
            vel: Vec3::new(dir.x, dir.y, 0.0),
            on_ground: true,
            jump_held: false,
        };
        let plan = plan_walk(&hull, &no_hazard, &[], &route, st, &p).expect("the stair lane should certify");
        assert_eq!(
            roll_walk(&hull, &no_hazard, &[], &route, st, plan, &p),
            WalkRollout::Held,
            "flying the certified policy must track the lane"
        );
        // …and it certifies the *smoothest* policy, not merely the tightest one that scrapes through.
        assert_eq!(plan.lookahead, LOOKAHEADS[0]);
        assert_eq!(plan.lateral, 0.0);

        // Re-fly it by hand to check what "held" actually bought: the bot climbs several risers, keeps
        // walking speed the whole way (no reverse, no brake-to-a-crawl), and never leaves the lane.
        let mut fly = st;
        let mut s = 0.0;
        for _ in 0..MAX_TICKS {
            let target = aim_point(&route, s, plan);
            let cmd = Cmd {
                view_yaw: yaw_of(target.xy() - fly.origin.xy()),
                forward: MOVE_SPEED,
                side: 0.0,
                jump: false,
            };
            pm_step(&hull, &mut fly, &cmd, &p, DT);
            s = track(&route, s, fly.origin).s;
        }
        assert!(
            fly.origin.z >= 96.0,
            "should have climbed the flight, z={}",
            fly.origin.z
        );
        assert!(
            fly.vel.xy().length() > 200.0,
            "should still be walking, speed={}",
            fly.vel.xy().length()
        );
        let off = off_line(&route, fly.origin).expect("on the route");
        assert!(off.lateral <= LATERAL_TOL, "drifted off the lane: {}", off.lateral);
    }

    /// Bygg dm3:s L-hylla ur riktig BSP och ge armarnas rutt 1367→1416→1459→1461.
    ///
    /// Basgrafen har genvägen 1416→1461 (L10447) och A\* tar den; de mätta armarna gick
    /// den aldrig (attributionen: noll gångna ingångar via den i båda armarna). Den
    /// maskas därför bort, annars certifierar fixturen en rutt ingen bot körde.
    ///
    /// Syntetisk höjdfältshull duger inte här: `0b8d237` slog fast att den ljuger om
    /// den här sortens geometri, och min egen sond bekräftade det — den lät la=96
    /// falla alltid, medan riggen släpper igenom 87–96 % av samma passager. Mot riktig
    /// BSP finns häng-off-remsan av sig själv, i både hull0 och hull1.
    #[cfg(test)]
    fn dm3_lhyllan() -> Option<(crate::bsp::Bsp, Vec<Vec3>)> {
        let dir = std::env::var("RTX_TEST_MAPS").ok()?;
        let bytes = std::fs::read(std::path::Path::new(&dir).join("dm3.bsp")).ok()?;
        let bsp = crate::bsp::Bsp::parse(&bytes).expect("parse dm3");
        let graph = rtx_nav::navmesh::build_navmesh(
            &bsp, Vec::new(), Vec::new(), Vec::new(), None, false,
            Some(rtx_nav::navmesh::SpeedJumpParams {
                gravity: 800.0, accel: 10.0, maxspeed: 320.0,
                friction: 4.0, stopspeed: 100.0, curl: true,
            }), None,
        );
        let costs = rtx_nav::navmesh::LinkCosts::default();
        let c1367 = graph.nearest(Vec3::new(256.0, -844.0, 264.0))?;
        let c1416 = graph.nearest(Vec3::new(288.0, -844.0, 264.0))?;
        let c1461 = graph.nearest(Vec3::new(328.0, -800.0, 264.0))?;
        let mask: Vec<u32> = (0..)
            .take_while(|&i| graph.has_link(i))
            .filter(|&i| graph.link_source(i) == c1416 && graph.link_target(i) == c1461)
            .collect();
        let route = graph.find_path_masked(c1367, c1461, &costs, &mask)?;
        let pts: Vec<Vec3> = std::iter::once(graph.cell_origin(c1367))
            .chain(route.iter().map(|&l| graph.cell_origin(graph.link_target(l))))
            .collect();
        Some((bsp, pts))
    }

    /// Den mätta påfarten: första 264-kontakt vid (242, −822), 22 u norr om 1367:s
    /// origo (36/36 hårda ben, median 22,5 u; `opus5-m1-lateralmatning-protokoll.md`).
    const PAFART: Vec3 = Vec3::new(242.0, -822.0, 264.0);

    fn pafart_state(spd: f32, bearing_deg: f32) -> PmState {
        let r = bearing_deg.to_radians();
        PmState {
            origin: PAFART,
            vel: Vec3::new(spd * r.cos(), spd * r.sin(), 0.0),
            on_ground: true,
            jump_held: false,
        }
    }

    /// Karaktärisering, mot riktig geometri: den längsta sikten går av nordkanten.
    ///
    /// Detta är fyndet som annars tyst kan gå sönder igen — samma skäl som
    /// `0b8d237` pinnade sitt trapptest. Att `plan_walk` väljer bort la=96 här är
    /// halva poängen; den andra halvan är att fältdatan säger att 449 av 480 ben ändå
    /// FLÖG la=96 (2,6–3,0° anpassningsfel mot 11–14° för alternativen). Ingen av dem
    /// kan ha certifierats på hyllan.
    #[test]
    fn dm3_langsta_sikten_gar_av_nordkanten() {
        let Some((bsp, pts)) = dm3_lhyllan() else {
            eprintln!("RTX_TEST_MAPS not set; skipping");
            return;
        };
        let p = PmParams::default();
        for (spd, bar) in [(236.0f32, 29.0f32), (200.0, 7.0)] {
            let st = pafart_state(spd, bar);
            for &lat in LATERALS.iter() {
                let plan = WalkPlan { lookahead: LOOKAHEADS[0], lateral: lat };
                assert_eq!(
                    roll_walk(&bsp, &no_hazard, &[], &pts, st, plan, &p),
                    WalkRollout::Fell,
                    "la=96 lat={lat} borde gå av nordkanten vid {spd} ups {bar}°"
                );
            }
            let vald = plan_walk(&bsp, &no_hazard, &[], &pts, st, &p)
                .expect("någon kortare sikt håller");
            assert!(
                vald.lookahead < LOOKAHEADS[0],
                "certifieraren får inte välja den längsta sikten här: {vald:?}"
            );
        }
    }

    /// RÖTT TEST — defekten, inte fenomenet.
    ///
    /// Ett certifikat utfärdas uppströms (i trappan, där den längsta sikten håller) och
    /// är enligt färskhetsregeln fortfarande giltigt på hyllan: boten ligger inom
    /// `LATERAL_TOL` från ruttlinjen, och `w.legs` bär åtta ben så benbytet river det
    /// inte. Men rullat från hyllans påfart faller samma plan.
    ///
    /// Invarianten som brister står i `bot/mod.rs`: *"a plan is never flown past the
    /// ground it was proven over"*. Den håller i TID — 0,3 s omcertifiering mot 0,52 s
    /// horisont — men inte i RUM: tuben är 32 u bred och plattan har 27 u golv norr om
    /// kordan (grok2:s steplandningssvep).
    ///
    /// Testet ska vara RÖTT tills F1 eller F2 landar. Det påstår inte hur det ska
    /// lagas, bara att ett färskt certifikat inte får rulla till `Fell`.
    /// `ignore` tills fixen landar: testet ÄR rött och ska vara det, men en röd svit
    /// är allas problem och inte bara mitt. Körs med
    /// `cargo test -- --ignored rott_farskt_certifikat` (kräver `RTX_TEST_MAPS`).
    #[test]
    #[ignore = "rött tills F1 eller F2 landar — dokumenterar defekten"]
    fn rott_farskt_certifikat_far_inte_falla() {
        let Some((bsp, pts)) = dm3_lhyllan() else {
            eprintln!("RTX_TEST_MAPS not set; skipping");
            return;
        };
        let p = PmParams::default();
        // Uppströms: trappsteget 1314 → 1367, dit boten kommer med fart österut.
        let uppstroms = PmState {
            origin: Vec3::new(224.0, -844.0, 248.0),
            vel: Vec3::new(220.0, 0.0, 0.0),
            on_ground: true,
            jump_held: false,
        };
        let Some(cert) = plan_walk(&bsp, &no_hazard, &[], &pts, uppstroms, &p) else {
            panic!("uppströms borde certifiera");
        };

        let pa_hyllan = pafart_state(236.0, 29.0);
        // Färskhetsregeln i steer.rs: certifikatet lever om boten är kvar i tuben.
        let off = off_line(&pts, pa_hyllan.origin).expect("på rutten");
        assert!(
            tube_ok(off, true, false, EdgeGuard::default()),
            "utan kantvakt godkänner färskhetsregeln påfarten ({} u) — det är därför \
             certifikatet överlever hit",
            off.lateral
        );

        assert_ne!(
            roll_walk(&bsp, &no_hazard, &[], &pts, pa_hyllan, cert, &p),
            WalkRollout::Fell,
            "ett certifikat som färskhetsregeln fortfarande godkänner ({cert:?}) rullar \
             till Fell från påfarten — planen flygs över mark den inte bevisades över"
        );
    }

    /// Läppsonden mot riktig geometri: den ser häng-offen där `over_void` inte kan.
    #[test]
    fn dm3_lappsonden_ser_hangoffen() {
        let Some((bsp, _)) = dm3_lhyllan() else {
            eprintln!("RTX_TEST_MAPS not set; skipping");
            return;
        };
        // Påfarten: hull1 bär (kroppens södra kant vilar på läppen), punktgolvet är slut.
        assert!(over_lip(&bsp, PAFART), "påfarten ska läsas som läpp");
        // Inne på hyllan, söder om punktläppen −834: riktigt golv under mitten.
        assert!(
            !over_lip(&bsp, Vec3::new(272.0, -850.0, 264.0)),
            "inne på hyllan är det golv, inte läpp"
        );
        // Trappsteget under: 16 u ner, inte en avgrund. LIP_PROBE ska skona det.
        assert!(
            !over_lip(&bsp, Vec3::new(232.0, -850.0, 248.0)),
            "ett trappsteg är ingen läpp — annars blir bots rädda för trappor"
        );
    }

    /// F1 och F2 lapsar certifikatet på läppen; utan vakt gör ingen av dem det.
    ///
    /// Det är hela fixen uttryckt som ett påstående: certifikatet ska inte överleva in
    /// på mark av en annan sort än den det bevisades över. Att det lapsar betyder att
    /// steeraren certifierar om — och på plats väljer fläkten en kortare sikt, vilket
    /// `dm3_langsta_sikten_gar_av_nordkanten` visar.
    #[test]
    fn kantvakterna_lapsar_certifikatet_pa_lappen() {
        let Some((_, pts)) = dm3_lhyllan() else {
            eprintln!("RTX_TEST_MAPS not set; skipping");
            return;
        };
        let off = off_line(&pts, PAFART).expect("på rutten");
        assert!(off.lateral > LATERAL_TOL_LIP, "påfarten ligger utanför den smalnade tuben");
        assert!(off.lateral <= LATERAL_TOL, "men innanför den globala — det är luckan");

        let av = EdgeGuard::default();
        let f1 = EdgeGuard { narrow: true, recert: false };
        let f2 = EdgeGuard { narrow: false, recert: true };

        // Certifierat uppströms på punktgolv (over_lip_at_cert = false), nu på läpp.
        assert!(tube_ok(off, true, false, av), "utan vakt överlever certifikatet");
        assert!(!tube_ok(off, true, false, f1), "F1 ska lapsa det");
        assert!(!tube_ok(off, true, false, f2), "F2 ska lapsa det");
    }

    /// Ingen av vakterna får ändra något utanför läpparna — det är villkoret för att
    /// riskytan ska vara grok2:s tretton remsor och inte hela kartan.
    #[test]
    fn kantvakterna_ar_inerta_pa_vanlig_mark() {
        let off = Offset { lateral: 20.0, dz: 0.0 };
        for guard in [
            EdgeGuard::default(),
            EdgeGuard { narrow: true, recert: false },
            EdgeGuard { narrow: false, recert: true },
            EdgeGuard { narrow: true, recert: true },
        ] {
            assert!(
                tube_ok(off, false, false, guard),
                "på vanlig mark ska {guard:?} vara inert"
            );
        }
        // …och lika inert för en bot som certifierades på läpp och står kvar på läpp:
        // F2 triggar på BYTE, inte på att marken är en läpp.
        assert!(tube_ok(
            Offset { lateral: 20.0, dz: 0.0 },
            true,
            true,
            EdgeGuard { narrow: false, recert: true }
        ));
    }

    /// F1 smalnar tuben, den stänger den inte: en bot som håller sig nära linjen behåller
    /// sitt certifikat även på en läpp.
    #[test]
    fn f1_slapper_igenom_den_som_haller_linjen() {
        let f1 = EdgeGuard { narrow: true, recert: false };
        assert!(tube_ok(Offset { lateral: 4.0, dz: 0.0 }, true, true, f1));
        assert!(!tube_ok(Offset { lateral: 12.0, dz: 0.0 }, true, true, f1));
    }

    /// Bälte och hängslen: hela sanningstabellen på en läpp, med båda vakterna var för
    /// sig och tillsammans (deepseeks anmärkning på b9fcee8).
    ///
    /// Poängen är raderna där **en** vakt släpper igenom och kombinationen inte gör det.
    /// F1 och F2 är två triggers för samma korrigering, men de triggar på olika saker:
    /// F1 på hur långt ut boten är, F2 på att marken bytt sort. En bot som certifierades
    /// på läppen och står kvar där har inget byte för F2 att se; en bot som just lämnat
    /// läppen är inte längre på den för F1 att mäta mot. Var för sig har de varsitt hål,
    /// och hålen överlappar inte.
    #[test]
    fn kombinationen_pa_lappen_tacker_bada_halen() {
        let av = EdgeGuard::default();
        let f1 = EdgeGuard { narrow: true, recert: false };
        let f2 = EdgeGuard { narrow: false, recert: true };
        let bada = EdgeGuard { narrow: true, recert: true };

        // (lateral, på läpp nu, på läpp vid cert, av, F1, F2, båda)
        let fall = [
            // Nattens fall: certifierat på punktgolv uppströms, nu 22 u ut på läppen.
            (22.0, true, false, true, false, false, false),
            // Certifierat PÅ läppen och kvar där: inget byte, så F2 ser ingenting.
            // Det är F1:s hål-täckning.
            (22.0, true, true, true, false, true, false),
            // Just lämnat läppen, fortfarande 22 u ut: F1 mäter mot vanlig mark och
            // släpper igenom. Det är F2:s hål-täckning.
            (22.0, false, true, true, true, false, false),
            // Nära linjen på läppen, inget byte: ingen vakt har skäl att lapsa.
            (4.0, true, true, true, true, true, true),
            // Vanlig mark, inget byte: allt inert (riskytan ska vara läpparna).
            (22.0, false, false, true, true, true, true),
        ];

        for (lat, nu, vid_cert, v_av, v_f1, v_f2, v_bada) in fall {
            let off = Offset { lateral: lat, dz: 0.0 };
            let namn = format!("lat={lat} läpp_nu={nu} läpp_cert={vid_cert}");
            assert_eq!(tube_ok(off, nu, vid_cert, av), v_av, "utan vakt: {namn}");
            assert_eq!(tube_ok(off, nu, vid_cert, f1), v_f1, "F1: {namn}");
            assert_eq!(tube_ok(off, nu, vid_cert, f2), v_f2, "F2: {namn}");
            assert_eq!(tube_ok(off, nu, vid_cert, bada), v_bada, "F1+F2: {namn}");
            // Kombinationen får aldrig vara slappare än den strängaste enskilda.
            assert!(
                tube_ok(off, nu, vid_cert, bada) <= (tube_ok(off, nu, vid_cert, f1)
                    && tube_ok(off, nu, vid_cert, f2)),
                "kombinationen får inte släppa igenom mer än F1 och F2 var för sig: {namn}"
            );
        }
    }

    /// A route running off a cliff certifies nothing: every look-ahead carries the bot over the lip.
    /// That `None` is the honest boxed state the fallback brakes exist for.
    #[test]
    fn plan_walk_none_when_the_route_runs_off_a_cliff() {
        let hull = HeightHull {
            floor: |x, _| (x <= 60.0).then_some(0.0),
        };
        let p = PmParams::default();
        let route = [
            Vec3::new(0.0, 0.0, 0.0),
            Vec3::new(60.0, 0.0, 0.0),
            Vec3::new(400.0, 0.0, 0.0), // past the lip, over the void
        ];
        let st = PmState {
            origin: Vec3::new(0.0, 0.0, 0.0),
            vel: Vec3::new(400.0, 0.0, 0.0),
            on_ground: true,
            jump_held: false,
        };
        assert!(plan_walk(&hull, &no_hazard, &[], &route, st, &p).is_none());
    }

    /// A wall across the corridor stops the bot advancing, and the roll reports that rather than
    /// claiming it tracked (sliding in place is not holding the line).
    #[test]
    fn roll_walk_stalls_into_a_wall() {
        // Floor everywhere, but a 200u-tall block from x=20 on: the bot noses into it and stops well
        // inside the horizon, so the stall is what the roll reports rather than the tick budget.
        let hull = HeightHull {
            floor: |x, _| Some(if x >= 20.0 { 200.0 } else { 0.0 }),
        };
        let p = PmParams::default();
        let route = [Vec3::new(0.0, 0.0, 0.0), Vec3::new(400.0, 0.0, 0.0)];
        let st = PmState {
            origin: Vec3::new(0.0, 0.0, 0.0),
            vel: Vec3::new(200.0, 0.0, 0.0),
            on_ground: true,
            jump_held: false,
        };
        assert_eq!(
            roll_walk(&hull, &no_hazard, &[], &route, st, straight(96.0), &p),
            WalkRollout::Stalled
        );
    }

    /// A shut gate's volume is invisible to the world hull, so the rollout would happily certify a
    /// walk straight through it. The blocked-box check is what stops that.
    #[test]
    fn roll_walk_blocked_by_a_closed_gate_box() {
        let hull = HeightHull {
            floor: |_, _| Some(0.0),
        };
        let p = PmParams::default();
        let route = [Vec3::new(0.0, 0.0, 0.0), Vec3::new(400.0, 0.0, 0.0)];
        let st = PmState {
            origin: Vec3::new(0.0, 0.0, 0.0),
            vel: Vec3::new(300.0, 0.0, 0.0),
            on_ground: true,
            jump_held: false,
        };
        let gate = [(Vec3::new(150.0, -64.0, 0.0), Vec3::new(170.0, 64.0, 80.0))];
        assert_eq!(
            roll_walk(&hull, &no_hazard, &gate, &route, st, straight(96.0), &p),
            WalkRollout::Blocked
        );
        // …and with the gate open the same walk tracks fine, so the box is what rejected it.
        assert_eq!(
            roll_walk(&hull, &no_hazard, &[], &route, st, straight(96.0), &p),
            WalkRollout::Held
        );
    }

    /// The cursor never snaps backward onto a stretch the route already covered. On a switchback (out
    /// along +x, back along −x a body-height above) the outbound pursuit must keep aiming outbound,
    /// even though the return flight is nearer in XY than the route ahead.
    #[test]
    fn pursuit_cursor_never_snaps_back() {
        let pts = [
            Vec3::new(0.0, 0.0, 0.0),
            Vec3::new(300.0, 0.0, 0.0),
            Vec3::new(300.0, 0.0, 56.0),
            Vec3::new(0.0, 0.0, 56.0),
        ];
        // Halfway out on the lower flight: the upper flight sits directly overhead.
        let mut s = 0.0;
        for x in [10.0f32, 60.0, 120.0, 180.0, 240.0] {
            let t = track(&pts, s, Vec3::new(x, 0.0, 0.0));
            assert!(t.s >= s, "cursor went backward at x={x}: {} < {s}", t.s);
            assert!(t.s <= 300.0, "cursor jumped onto the return flight at x={x}: s={}", t.s);
            s = t.s;
        }
        // Once past the turn, the cursor may advance onto the return flight — but never back below it.
        let t = track(&pts, 356.0, Vec3::new(200.0, 0.0, 56.0));
        assert!(t.s >= 356.0, "cursor regressed onto the outbound flight: {}", t.s);
    }
}
