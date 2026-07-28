// SPDX-License-Identifier: AGPL-3.0-or-later

//! Shaping a route into a line worth steering at.
//!
//! A navmesh route is a sequence of cell centres. Steering straight at those is the root of a whole
//! family of movement problems, and it is worth being precise about why: cells are planted on a 32u
//! grid, so a corridor's centres sit wherever the grid fell, and a corner's centres sit *in* the
//! corner. A bot pursuing that polyline aims itself at the inside of every turn, arrives at the wall,
//! and needs a reactive brake to save it. A human does the opposite — they arc *outward* before a
//! corner and carry speed through it. No amount of better braking produces that; the line itself has
//! to be different.
//!
//! So: measure how much room there is either side of each point, then let the polyline relax toward
//! straightness inside that room. Two ideas from mobile robotics, both long-settled there:
//!
//! * **Costmap inflation** — obstacles dilated by the body radius, so a planner naturally prefers the
//!   middle of free space. Here that is [`half_widths`], a pair of perpendicular hull traces.
//! * **Elastic band** (Quinlan & Khatib) — hold the path away from obstacles and let it shorten like
//!   a rubber band under tension. Here that is [`shape`], a clamped Laplacian relaxation.
//!
//! Everything is pure: positions and a tracing closure in, positions out. No engine, no `NavGraph`,
//! no BSP type in the signatures — which is what lets the tests below run on synthetic corridors and
//! keeps the whole thing deterministic (fixed iteration count, index order, no float tie-breaks).
//!
//! Shaping is **XY-only, with each point's z preserved**. That is deliberate rather than a
//! simplification: on a staircase the cell centres step up in z, and a line that interpolated z while
//! moving laterally would leave the floor. What the caller wants from a stair is exactly a *lateral*
//! lane — the same treads, entered further out — and the consumer takes `.xy()` for a heading anyway.

use glam::{Vec2, Vec3};

/// Resample a polyline to roughly uniform arclength `spacing`, always keeping both endpoints.
///
/// Shaping needs points close enough together that a lateral nudge is a small angle, and route legs
/// are 32u or a long stride depending on the link — uniform spacing makes the relaxation behave the
/// same way everywhere along a route instead of harder wherever cells happen to bunch up.
pub fn resample(points: &[Vec3], spacing: f32) -> Vec<Vec3> {
    if points.len() < 2 || spacing <= 0.0 {
        return points.to_vec();
    }
    let mut out = vec![points[0]];
    let mut carry = 0.0f32;
    for seg in points.windows(2) {
        let (a, b) = (seg[0], seg[1]);
        let len = (b - a).length();
        if len < 1e-3 {
            continue;
        }
        let dir = (b - a) / len;
        let mut d = spacing - carry;
        while d < len {
            out.push(a + dir * d);
            d += spacing;
        }
        carry = len - (d - spacing);
    }
    out.push(*points.last().unwrap());
    out
}

/// The left-hand normal of the polyline at `i`, in XY. Uses the neighbours so it is the *corner*
/// bisector rather than either edge, which is what a lateral offset should be measured against.
fn perp_at(pts: &[Vec3], i: usize) -> Vec2 {
    let (a, b) = (pts[i.saturating_sub(1)], pts[(i + 1).min(pts.len() - 1)]);
    let t = (b - a).truncate().normalize_or_zero();
    Vec2::new(-t.y, t.x)
}

/// How much usable room there is either side of each point, up to `max`.
///
/// `room(from, dir, max)` must return how far along `dir` the bot could actually *go and still be
/// standing* — not merely how far until a wall. That distinction is the whole difference between a
/// lane and a hazard: a horizontal trace sails straight over a cliff edge and reports open space, so
/// shaping against it would relax the line off the ledge it was trying to follow. Room has to mean
/// floor, and the caller wires it to a probe that says so.
///
/// The nearer side wins, because a lane has to fit the body on both sides; there is no point knowing
/// a wall is far away on the left if the right one is close.
///
/// A **three-point minimum filter** runs over the result, and it is load-bearing rather than
/// cosmetic. Probes are point samples of a continuous corridor: a doorway one probe wide reads as
/// open at the points either side of it, and a lane shaped against that would be aimed at the
/// door frame. Taking each point's own width as the smallest of itself and its neighbours makes the
/// narrowest thing nearby govern the approach to it, which is what a player does on sight.
pub fn half_widths(pts: &[Vec3], max: f32, room: impl Fn(Vec3, Vec3, f32) -> f32) -> Vec<f32> {
    let raw: Vec<f32> = (0..pts.len())
        .map(|i| {
            // Endpoints are probed too, even though `shape` pins them. Using a sentinel there
            // instead would feed a zero into the min-filter and so pin the *first interior point* of
            // every lane — which is the point nearest the bot, the one that matters most.
            let perp = perp_at(pts, i);
            let d = Vec3::new(perp.x, perp.y, 0.0);
            room(pts[i], d, max).min(room(pts[i], -d, max)).max(0.0)
        })
        .collect();
    (0..raw.len())
        .map(|i| {
            let lo = i.saturating_sub(1);
            let hi = (i + 1).min(raw.len() - 1);
            raw[lo].min(raw[i]).min(raw[hi])
        })
        .collect()
}

/// Relax `pts` toward straightness, laterally, without leaving the room [`half_widths`] measured.
///
/// Each interior point is pulled toward the midpoint of its neighbours — the discrete Laplacian,
/// which is what makes a polyline shorten and its corners round off. The pull is then projected onto
/// the point's perpendicular and clamped to `half_width - margin`, so the line can slide sideways
/// within the corridor but can never be dragged into a wall no matter how many iterations run.
///
/// The clamp is against the **original** point, not the previous iteration, so error cannot
/// accumulate: however far the relaxation wants to go, the result is a bounded offset from the route
/// the planner actually chose. Endpoints are pinned, so the line still starts and ends on the route.
///
/// `iters` is fixed by the caller and the loop is in index order, which is the whole determinism
/// story — no convergence test, no tolerance, no float comparison deciding when to stop. Eight passes
/// is plenty: the Laplacian's high-frequency error decays geometrically, and the clamp binds long
/// before the low-frequency modes matter.
pub fn shape(pts: &[Vec3], half: &[f32], margin: f32, iters: usize) -> Vec<Vec3> {
    if pts.len() < 3 {
        return pts.to_vec();
    }
    let n = pts.len();
    // Precompute the axis each point may slide along, from the *unshaped* line: letting the
    // perpendicular follow the shaped line would let the whole thing rotate away over iterations.
    let axes: Vec<Vec2> = (0..n).map(|i| perp_at(pts, i)).collect();
    let limits: Vec<f32> = (0..n)
        .map(|i| (half.get(i).copied().unwrap_or(0.0) - margin).max(0.0))
        .collect();

    let mut cur = pts.to_vec();
    for _ in 0..iters {
        // Sequential (Gauss-Seidel) rather than a copy: it propagates a correction along the line
        // within one pass, and being order-dependent is fine here precisely because the order is
        // fixed. Endpoints are skipped, so they stay pinned by construction.
        for i in 1..n - 1 {
            let mid = (cur[i - 1].truncate() + cur[i + 1].truncate()) * 0.5;
            let want = cur[i].truncate().lerp(mid, RELAX);
            let off = (want - pts[i].truncate()).dot(axes[i]).clamp(-limits[i], limits[i]);
            let moved = pts[i].truncate() + axes[i] * off;
            cur[i] = Vec3::new(moved.x, moved.y, pts[i].z);
        }
    }
    cur
}

/// How far each relaxation pass moves a point toward its neighbours' midpoint. Under-relaxed on
/// purpose: at 1.0 a three-point zigzag flips sides every pass instead of settling.
const RELAX: f32 = 0.5;

/// Default spacing for lane control points. Fine enough that a lateral offset is a small heading
/// change, coarse enough that a lane's worth of hull traces stays cheap.
pub const LANE_SPACING: f32 = 24.0;

/// Furthest a lane point may be probed, and so the widest offset it can take.
pub const LANE_MAX_HALF_WIDTH: f32 = 96.0;

/// Clearance kept between the lane and the measured wall, on top of the body's own half-width being
/// implicit in a hull trace. A lane exactly on the limit is one pmove nudge from scraping.
pub const LANE_MARGIN: f32 = 8.0;

/// Relaxation passes per lane build.
pub const LANE_ITERS: usize = 8;

/// Shape a route polyline into a lane: resample, measure, relax.
///
/// The single entry point callers should use, so a lane is built the same way everywhere.
pub fn build(route: &[Vec3], room: impl Fn(Vec3, Vec3, f32) -> f32) -> Vec<Vec3> {
    let pts = resample(route, LANE_SPACING);
    let half = half_widths(&pts, LANE_MAX_HALF_WIDTH, room);
    shape(&pts, &half, LANE_MARGIN, LANE_ITERS)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A `room` closure for a corridor of usable half-width `w` centred on y=0, running along x.
    fn corridor(w: f32) -> impl Fn(Vec3, Vec3, f32) -> f32 {
        move |from: Vec3, dir: Vec3, max: f32| {
            let d = dir.normalize_or_zero();
            if d.y.abs() < 1e-6 {
                return max;
            }
            let avail = if d.y > 0.0 { w - from.y } else { w + from.y };
            (avail / d.y.abs()).clamp(0.0, max)
        }
    }

    #[test]
    fn resample_is_uniform_and_keeps_endpoints() {
        let r = resample(&[Vec3::ZERO, Vec3::new(1000.0, 0.0, 0.0)], 100.0);
        assert_eq!(r.first().unwrap().x, 0.0);
        assert!((r.last().unwrap().x - 1000.0).abs() < 1e-3);
        assert!((9..=12).contains(&r.len()), "count {}", r.len());
    }

    /// The point of the whole module: a route that zigzags down a corridor — which is what a 32u grid
    /// produces — comes out straight, because straightening it never leaves the corridor.
    #[test]
    fn a_grid_zigzag_relaxes_straight() {
        let zig: Vec<Vec3> = (0..=20)
            .map(|i| Vec3::new(32.0 * i as f32, if i % 2 == 0 { 16.0 } else { -16.0 }, 0.0))
            .collect();
        let before = zig.iter().map(|p| p.y.abs()).fold(0.0f32, f32::max);
        let out = build(&zig, corridor(64.0));
        // Interior only: the endpoints are pinned and keep their original offset.
        let after = out[2..out.len() - 2].iter().map(|p| p.y.abs()).fold(0.0f32, f32::max);
        assert!(before >= 16.0);
        assert!(after < 4.0, "zigzag survived shaping: {after} (was {before})");
    }

    /// And it must not straighten *through* a wall: the same zigzag in a corridor barely wider than
    /// the line keeps its shape, because there is nowhere to relax to.
    #[test]
    fn a_narrow_corridor_pins_the_line() {
        let zig: Vec<Vec3> = (0..=20)
            .map(|i| Vec3::new(32.0 * i as f32, if i % 2 == 0 { 16.0 } else { -16.0 }, 0.0))
            .collect();
        let out = build(&zig, corridor(20.0));
        for (i, p) in out.iter().enumerate() {
            assert!(
                p.y.abs() <= 20.0 + 1e-3,
                "point {i} left the corridor: y={} (walls at +-20)",
                p.y
            );
        }
    }

    /// A lane never strays further from its route than the room measured for it, however many passes
    /// run — the clamp is against the original line, so relaxation error cannot accumulate.
    #[test]
    fn offsets_stay_inside_the_measured_room() {
        let route: Vec<Vec3> = (0..=30).map(|i| Vec3::new(24.0 * i as f32, 0.0, 0.0)).collect();
        // A hard right-angle in the middle gives the relaxation something to pull against.
        let bent: Vec<Vec3> = route
            .iter()
            .enumerate()
            .map(|(i, p)| if i > 15 { Vec3::new(360.0, p.x - 360.0, 0.0) } else { *p })
            .collect();
        let pts = resample(&bent, LANE_SPACING);
        let half = vec![40.0; pts.len()];
        let out = shape(&pts, &half, LANE_MARGIN, 200);
        let lim = 40.0 - LANE_MARGIN + 1e-2;
        for i in 0..pts.len() {
            let d = (out[i] - pts[i]).truncate().length();
            assert!(d <= lim, "point {i} moved {d:.2}, limit {lim:.2}");
        }
        assert_eq!(out[0], pts[0], "start pinned");
        assert_eq!(*out.last().unwrap(), *pts.last().unwrap(), "end pinned");
    }

    /// Shaping is lateral: z is carried through untouched, so a staircase keeps its treads.
    #[test]
    fn z_is_never_interpolated() {
        let stair: Vec<Vec3> = (0..=20)
            .map(|i| Vec3::new(32.0 * i as f32, if i % 2 == 0 { 16.0 } else { -16.0 }, 18.0 * i as f32))
            .collect();
        let pts = resample(&stair, LANE_SPACING);
        let half = vec![64.0; pts.len()];
        let out = shape(&pts, &half, LANE_MARGIN, LANE_ITERS);
        for i in 0..pts.len() {
            assert_eq!(out[i].z, pts[i].z, "z moved at {i}");
        }
    }

    /// Room means *standable ground*, not "no wall". A ledge with open air beside it reads as wide
    /// open to a horizontal trace, and a lane shaped against that walks off the edge — which is the
    /// one failure the reactive ledge brake exists to catch, and the reason it cannot be retired
    /// until the lane measures this correctly.
    #[test]
    fn open_air_beside_a_ledge_is_not_room() {
        let pts: Vec<Vec3> = (0..9).map(|i| Vec3::new(24.0 * i as f32, 0.0, 0.0)).collect();
        // Floor exists only for y <= 0; beyond that is a drop, though nothing blocks a trace.
        let floor_only_left = |from: Vec3, dir: Vec3, max: f32| {
            let d = dir.normalize_or_zero();
            if d.y > 0.0 {
                (-from.y).clamp(0.0, max)
            } else {
                max
            }
        };
        let half = half_widths(&pts, 96.0, floor_only_left);
        assert!(half.iter().all(|&h| h == 0.0), "the ledge side gives no room: {half:?}");
        let out = shape(&pts, &half, LANE_MARGIN, LANE_ITERS);
        for (i, p) in out.iter().enumerate() {
            assert!(p.y <= 1e-3, "point {i} was relaxed off the ledge to y={}", p.y);
        }
    }

    /// The min-filter is what keeps a lane out of a door frame: one narrow probe governs its
    /// neighbours, so the line is already committed to the gap before it arrives.
    #[test]
    fn a_pinch_narrows_its_neighbours() {
        let pts: Vec<Vec3> = (0..7).map(|i| Vec3::new(24.0 * i as f32, 0.0, 0.0)).collect();
        // Wide everywhere except a single pinch at index 3.
        let half = half_widths(
            &pts,
            96.0,
            |from: Vec3, _dir: Vec3, max: f32| {
                if (from.x - 72.0).abs() < 1.0 {
                    12.0
                } else {
                    max
                }
            },
        );
        assert_eq!(half[3], 12.0, "the pinch itself");
        assert_eq!(half[2], 12.0, "and the point before it");
        assert_eq!(half[4], 12.0, "and the point after it");
        assert_eq!(half[1], 96.0, "but not the whole corridor");
    }

    /// Same input, same output — no iteration-count heuristics, no float tie-breaks. The bot brain
    /// runs server-side and as a netclient, and they have to agree exactly.
    #[test]
    fn shaping_is_deterministic() {
        let zig: Vec<Vec3> = (0..=40)
            .map(|i| Vec3::new(31.0 * i as f32, if i % 3 == 0 { 20.0 } else { -14.0 }, i as f32))
            .collect();
        let a = build(&zig, corridor(50.0));
        let b = build(&zig, corridor(50.0));
        assert_eq!(a.len(), b.len());
        for (x, y) in a.iter().zip(&b) {
            assert_eq!(x.to_array().map(f32::to_bits), y.to_array().map(f32::to_bits));
        }
    }
}
