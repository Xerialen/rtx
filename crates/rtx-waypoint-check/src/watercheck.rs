// SPDX-License-Identifier: AGPL-3.0-or-later

//! Where is the water, and what does the navmesh think of it?
//!
//! The mesh is built from standable floor, so a water volume appears in it only as the floor at the
//! *bottom* of the pool — a route through water is a line of cells along that bottom. Whether that
//! is survivable depends on things the mesh does record (is the standing origin submerged, is there
//! air at eye height) and one it does not (is there any way up and out from here).
//!
//! This lists the water it found, grouped into pools, with the two facts that decide whether a bot
//! crossing one drowns: how many of its cells leave the eyes underwater, and whether open air sits
//! directly overhead or a ceiling roofs it. A pool that is fully submerged *and* fully roofed is one
//! a bot can only survive by leaving horizontally — which is exactly the case local depth control
//! cannot solve and a water-volume mesh would.

use glam::Vec3;
use rtx_nav::bsp::{Bsp, CONTENTS_WATER};
use rtx_nav::navmesh::NavGraph;

/// How many spread cells per pool to name for live probing.
const SAMPLES: usize = 10;

/// Report the map's water cells, grouped into connected pools.
pub fn report(map: &str, bsp: &Bsp, graph: &NavGraph) {
    let wet: Vec<u32> = (0..graph.cells.len() as u32)
        .filter(|&c| graph.cell_in_water(c))
        .collect();
    if wet.is_empty() {
        println!("== {map}: no water cells in the navmesh");
        // The mesh only sees water where a *standable floor* is submerged. A pool whose bottom is
        // not walkable — or one the carve never sampled — leaves no trace here at all, so say so
        // rather than let "no water cells" read as "no water".
        let hits = probe_volume(bsp, graph);
        if hits > 0 {
            println!("   but {hits} sampled points above known floor are inside water — unmeshed volume");
        }
        return;
    }

    // Group into pools by simple flood over the graph's own adjacency, so "one pool" means "cells a
    // swimmer can move between", not "cells that happen to be near each other".
    let mut seen = vec![false; graph.cells.len()];
    let mut pools: Vec<Vec<u32>> = Vec::new();
    for &start in &wet {
        if seen[start as usize] {
            continue;
        }
        let mut stack = vec![start];
        let mut pool = Vec::new();
        seen[start as usize] = true;
        while let Some(c) = stack.pop() {
            pool.push(c);
            for &li in &graph.adjacency[c as usize] {
                let t = graph.links[li as usize].to;
                if graph.cell_in_water(t) && !seen[t as usize] {
                    seen[t as usize] = true;
                    stack.push(t);
                }
            }
        }
        pool.sort_unstable();
        pools.push(pool);
    }
    pools.sort_by_key(|p| std::cmp::Reverse(p.len()));

    println!("== {map}: {} water cells in {} pool(s)", wet.len(), pools.len());
    for (i, pool) in pools.iter().enumerate().take(8) {
        let n = pool.len() as f32;
        let drowning = pool.iter().filter(|&&c| !graph.cell_breathable(c)).count();
        let roofed = pool
            .iter()
            .filter(|&&c| !rtx_nav::hazard::surface_above(&|p| bsp.pointcontents(p), graph.cell_origin(c)))
            .count();
        let (mut lo, mut hi) = (Vec3::splat(f32::MAX), Vec3::splat(f32::MIN));
        for &c in pool {
            let o = graph.cell_origin(c);
            lo = lo.min(o);
            hi = hi.max(o);
        }
        // The fact that decides whether falling in is survivable: is there a link from any wet cell
        // to a dry one? A bot swims the route it is given, and if the graph offers no way out of the
        // pool then no amount of local depth control saves it — it is trapped by the routing, not by
        // its steering.
        let mut exits: std::collections::BTreeMap<String, usize> = std::collections::BTreeMap::new();
        for &c in pool {
            for &li in &graph.adjacency[c as usize] {
                let l = &graph.links[li as usize];
                if !graph.cell_in_water(l.to) {
                    *exits.entry(format!("{:?}", l.kind)).or_default() += 1;
                }
            }
        }
        let exit_note = if exits.is_empty() {
            "NO EXIT LINKS — anything routed in here is trapped".to_string()
        } else {
            let total: usize = exits.values().sum();
            format!(
                "{total} exit links ({})",
                exits
                    .iter()
                    .map(|(k, v)| format!("{k} x{v}"))
                    .collect::<Vec<_>>()
                    .join(", ")
            )
        };
        // Where the surface actually is, and what sits beside it. A slice of navmesh at the
        // waterline is only worth planting if it lands near the height of the dry ground around the
        // pool — that is what lets ordinary Step links carry a swimmer out. From the floor, the
        // climb is the pool's whole depth and no link can span it.
        let mut surfaces: Vec<f32> = pool
            .iter()
            .step_by((pool.len() / 40).max(1))
            .filter_map(|&c| waterline(bsp, graph.cell_origin(c)))
            .collect();
        surfaces.sort_by(f32::total_cmp);
        let dry_near: Vec<f32> = (0..graph.cells.len() as u32)
            .filter(|&c| !graph.cell_in_water(c))
            .map(|c| graph.cell_origin(c))
            .filter(|o| o.x >= lo.x - 96.0 && o.x <= hi.x + 96.0 && o.y >= lo.y - 96.0 && o.y <= hi.y + 96.0)
            .map(|o| o.z)
            .collect();
        if let Some(&mid) = surfaces.get(surfaces.len() / 2) {
            let reachable = dry_near.iter().filter(|&&z| (z - mid).abs() <= 40.0).count();
            println!(
                "      waterline z ~{mid:.0} (floor {:.0}, {:.0} deep); dry cells within a step of it: {reachable}",
                lo.z,
                mid - lo.z,
            );
        }
        // Does a route out of the pool leave the water at the first opportunity, or swim on next to
        // dry land because the planner thinks water is nearly as fast? Ground travel is bunnyhopped
        // at 600-800 ups against a swim's 224, so a route that lingers is paying three times over.
        // Reported as the share of the journey spent wet.
        let goal = (0..graph.cells.len() as u32)
            .filter(|&c| !graph.cell_in_water(c))
            .min_by_key(|&c| {
                let d = (graph.cell_origin(c) - graph.cell_origin(pool[pool.len() / 2])).length();
                ((d - 900.0).abs() * 10.0) as i64 // roughly 900u away, so the route has a choice
            });
        // Dry cells once, for the bank test below.
        let dry_cells: Vec<(Vec3, u32)> = (0..graph.cells.len() as u32)
            .filter(|&c| !graph.cell_in_water(c))
            .map(|c| (graph.cell_origin(c), c))
            .collect();
        if let Some(goal) = goal {
            let costs = rtx_nav::navmesh::LinkCosts::default();
            // Banded, because that is what the game plans with (`rtx_bot_bandplan`, on by default)
            // and it prices water very differently: entering it collapses the speed band to a swim,
            // so the planner already knows it is giving up its momentum. Measuring the unbanded path
            // would be measuring a search the bot never runs.
            if let Some(path) = graph
                .find_path_banded(pool[pool.len() / 2], goal, rtx_nav::navmesh::MAX_SPEED, &costs)
                .map(|r| r.links)
                .or_else(|| graph.find_path(pool[pool.len() / 2], goal, &costs))
            {
                let (mut wet, mut total) = (0.0f32, 0.0f32);
                for &li in &path {
                    let l = &graph.links[li as usize];
                    let d = (graph.cell_origin(l.to) - graph.cell_origin(l.from)).length();
                    total += d;
                    if graph.cell_in_water(l.to) {
                        wet += d;
                    }
                }
                // The part that is actually a complaint: swimming *alongside* land. A long lake
                // crossing being mostly wet is honest; swimming past a bank you could have climbed
                // onto and then run along is the planner mispricing water against ground.
                let mut beside = 0.0f32;
                for &li in &path {
                    let l = &graph.links[li as usize];
                    if !graph.cell_in_water(l.to) {
                        continue;
                    }
                    let o = graph.cell_origin(l.to);
                    let has_bank = dry_cells
                        .iter()
                        .any(|&(p, _)| (p - o).truncate().length() <= 64.0 && (p.z - o.z).abs() <= 64.0);
                    if has_bank {
                        beside += (o - graph.cell_origin(l.from)).length();
                    }
                }
                println!(
                    "      route out ({} legs, {:.0}u): {:.0}% swum, of which {:.0}% was alongside reachable bank",
                    path.len(),
                    total,
                    100.0 * wet / total.max(1.0),
                    100.0 * beside / wet.max(1.0),
                );
            }
        }
        // Name cells spread across the pool, marked roofed (R) or open (O), so a bot can be dropped
        // on each and the pool measured by what actually happens rather than by its geometry.
        let roofed_pts: Vec<String> = pool
            .iter()
            .step_by((pool.len() / SAMPLES).max(1))
            .take(SAMPLES)
            .map(|&c| {
                let o = graph.cell_origin(c);
                let open = rtx_nav::hazard::surface_above(&|p| bsp.pointcontents(p), o);
                format!("{}:{:.0},{:.0},{:.0}", if open { 'O' } else { 'R' }, o.x, o.y, o.z)
            })
            .collect();
        println!(
            "   pool {i}: {} cells, {:.0}% eyes-under, {:.0}% roofed
      samples (O=open above, R=roofed): {}\n      bounds ({:.0},{:.0},{:.0}) .. ({:.0},{:.0},{:.0}), sample cell {}\n      {exit_note}",
            pool.len(),
            100.0 * drowning as f32 / n,
            100.0 * roofed as f32 / n,
            roofed_pts.join(" "),
            lo.x, lo.y, lo.z, hi.x, hi.y, hi.z,
            pool[pool.len() / 2],
        );
    }
}

/// How many points a step above the mesh's floor cells are inside water — a crude check for water
/// the cell flags miss entirely.
fn probe_volume(bsp: &Bsp, graph: &NavGraph) -> usize {
    (0..graph.cells.len())
        .step_by(7)
        .filter(|&i| {
            let o = graph.cell_origin(i as u32);
            bsp.pointcontents(o + Vec3::Z * 32.0) == CONTENTS_WATER
        })
        .count()
}

/// The z where the water column above `p` ends, or `None` if it is roofed before it does.
///
/// This is the plane a surface slice would sit on. Stepped rather than bisected because a bridge
/// deck sitting *in* the water splits the column, and the first ceiling above the bot is the one
/// that matters to it.
fn waterline(bsp: &Bsp, p: Vec3) -> Option<f32> {
    let mut z = 0.0;
    while z <= 1024.0 {
        match bsp.pointcontents(p + Vec3::Z * z) {
            CONTENTS_WATER => z += 8.0,
            rtx_nav::bsp::CONTENTS_SOLID => return None,
            _ => return Some(p.z + z),
        }
    }
    None
}
