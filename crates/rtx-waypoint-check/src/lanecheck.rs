// SPDX-License-Identifier: AGPL-3.0-or-later

//! Does the lane actually do anything? — an offline answer, on real map geometry.
//!
//! A live A/B over the human suite says whether the bot got better. It cannot say *why* not, and the
//! two failure modes want opposite fixes: a lane that shapes the line wrongly needs the shaping
//! changed, while a lane that never moves the line at all needs the measurement changed. Aggregates
//! over hundreds of runs cannot tell those apart, and guessing between them is how an afternoon
//! disappears.
//!
//! So this builds the map's navmesh offline, walks real routes across it, and reports what the lane
//! measured and how far it moved the line — with no server, no bots, and a couple of seconds per map.

use glam::Vec3;
use rtx_nav::bsp::Bsp;
use rtx_nav::lane;
use rtx_nav::navmesh::{LinkCosts, LinkKind, NavGraph};

/// Walk `count` routes across the map and report what the lane did to each.
pub fn report(map: &str, bsp: &Bsp, graph: &NavGraph, count: usize) {
    let n = graph.cells.len();
    if n < 2 {
        println!("== {map}: no cells");
        return;
    }
    let costs = LinkCosts::default();

    // Deterministic spread of start/goal pairs across the cell list, so the sample is the same every
    // run and covers the map rather than one corner of it.
    let mut routes = 0usize;
    let mut legs_total = 0usize;
    // Aggregates over every lane point sampled.
    let (mut half_sum, mut half_n) = (0.0f64, 0usize);
    let (mut zero_half, mut moved_sum, mut moved_max) = (0usize, 0.0f64, 0.0f32);
    let mut wall_limited = 0usize;
    let (mut narrow, mut no_floor) = (0usize, 0usize);

    for i in 0..count {
        let from = (i * 7919) % n;
        let to = (i * 104_729 + n / 2) % n;
        if from == to {
            continue;
        }
        let Some(path) = graph.find_path(from as u32, to as u32, &costs) else {
            continue;
        };
        // Ground legs only — the same corridor the runtime shapes.
        let targets: Vec<Vec3> = path
            .iter()
            .take_while(|&&li| matches!(graph.links[li as usize].kind, LinkKind::Walk | LinkKind::Step))
            .map(|&li| graph.cell_origin(graph.links[li as usize].to))
            .collect();
        if targets.len() < 3 {
            continue;
        }
        routes += 1;
        legs_total += targets.len();

        let pts = lane::resample(&targets, lane::LANE_SPACING);
        let half = lane::half_widths(&pts, lane::LANE_MAX_HALF_WIDTH, |f, d, m| {
            lane::ground_room(bsp, f, d, m)
        });
        let shaped = lane::shape(&pts, &half, lane::LANE_MARGIN, lane::LANE_ITERS);
        for (k, h) in half.iter().enumerate() {
            half_sum += *h as f64;
            half_n += 1;
            if *h <= 0.0 {
                zero_half += 1;
            }
            // Why a point has no room, which is the whole question. Two completely different causes
            // want opposite fixes, and one number cannot tell them apart:
            //
            //   * the corridor is narrower than a step, so a 32-unit-wide hull has nowhere to go —
            //     real, and the lane is right to stay put;
            //   * the hull *could* move, but the probe found no standable ground there — either a
            //     genuine ledge, or the probe being wrong.
            let perp = {
                let (a, b) = (pts[k.saturating_sub(1)], pts[(k + 1).min(pts.len() - 1)]);
                let t = (b - a).truncate().normalize_or_zero();
                Vec3::new(-t.y, t.x, 0.0)
            };
            let widest = [perp, -perp]
                .iter()
                .map(|&d| {
                    bsp.hull1_trace(pts[k], pts[k] + d * lane::LANE_MAX_HALF_WIDTH).fraction * lane::LANE_MAX_HALF_WIDTH
                })
                .fold(f32::MAX, f32::min);
            if *h <= 0.0 {
                if widest < lane::PROBE_STEP {
                    narrow += 1;
                } else {
                    no_floor += 1;
                }
            }
            if widest > *h + 1.0 {
                wall_limited += 1;
            }
            let d = (shaped[k] - pts[k]).truncate().length();
            moved_sum += d as f64;
            moved_max = moved_max.max(d);
        }
    }

    if half_n == 0 {
        println!("== {map}: no ground corridors sampled");
        return;
    }
    println!(
        "== {map}: {routes} routes, {} lane points ({:.1} legs/route)\n   \
         half-width mean {:.1}u, {:.0}% pinned at zero ({:.0}% corridor too narrow, {:.0}% no floor)\n   \
         floor binds before wall on {:.0}%; shaping moved mean {:.1}u, max {:.0}u",
        half_n,
        legs_total as f32 / routes.max(1) as f32,
        half_sum / half_n as f64,
        100.0 * zero_half as f32 / half_n as f32,
        100.0 * narrow as f32 / half_n as f32,
        100.0 * no_floor as f32 / half_n as f32,
        100.0 * wall_limited as f32 / half_n as f32,
        moved_sum / half_n as f64,
        moved_max,
    );
}
