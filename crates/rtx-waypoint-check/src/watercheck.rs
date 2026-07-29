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
        // Name a few roofed cells outright: those are the ones worth putting a bot on, since a roof
        // is what makes local surface-seeking useless and the exit distance decisive.
        let roofed_pts: Vec<String> = pool
            .iter()
            .filter(|&&c| !rtx_nav::hazard::surface_above(&|p| bsp.pointcontents(p), graph.cell_origin(c)))
            .step_by((roofed / 3).max(1))
            .take(3)
            .map(|&c| {
                let o = graph.cell_origin(c);
                format!("{c}@({:.0},{:.0},{:.0})", o.x, o.y, o.z)
            })
            .collect();
        println!(
            "   pool {i}: {} cells, {:.0}% eyes-under, {:.0}% roofed [{}]\n      bounds ({:.0},{:.0},{:.0}) .. ({:.0},{:.0},{:.0}), sample cell {}\n      {exit_note}",
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
