// SPDX-License-Identifier: AGPL-3.0-or-later

//! R1-LITE: grounded in an indegree-0 cell whose only out-link is a Drop
//! and which has no usable route → take that Drop.
//!
//! Recovery-poll 2 + terra grind: exact indegree/outdegree/kind, fixture-pinned
//! landing, cvar default off (callers pass `enabled`). Ordinary cells never match.

use glam::Vec3;

use crate::navmesh::{LinkKind, NavGraph};

/// Pinned ground-level landings (Xerial path 1 / 670–702-line). Destinations
/// outside this set are refused even if the topological predicate matches.
pub const R1_LITE_LANDINGS: &[[f32; 3]] = &[
    [-288.0, -800.0, -16.0],
    [-288.0, -768.0, -16.0],
    [-288.0, -736.0, -16.0],
    [-288.0, -672.0, -16.0], // 702 — Xerial (−291, −685)
    [-288.0, -640.0, -16.0],
    [-288.0, -608.0, -16.0],
];

const LAND_XY: f32 = 48.0;
const LAND_Z: f32 = 24.0;

/// If the predicate matches, the sole Drop's link id. `enabled` is the cvar.
pub fn escape(graph: &NavGraph, cell: u32, has_usable_route: bool, enabled: bool) -> Option<u32> {
    if !enabled || has_usable_route {
        return None;
    }
    let indegree = graph.links.iter().filter(|l| l.to == cell).count();
    if indegree != 0 {
        return None;
    }
    let outs: Vec<(u32, &crate::navmesh::Link)> = graph
        .links
        .iter()
        .enumerate()
        .filter(|(_, l)| l.from == cell)
        .map(|(i, l)| (i as u32, l))
        .collect();
    if outs.len() != 1 {
        return None;
    }
    let (id, link) = outs[0];
    if link.kind != LinkKind::Drop {
        return None;
    }
    let dest = graph.cell_origin(link.to);
    if !landing_pinned(dest) {
        return None;
    }
    Some(id)
}

fn landing_pinned(dest: Vec3) -> bool {
    R1_LITE_LANDINGS.iter().any(|&p| {
        let dx = dest.x - p[0];
        let dy = dest.y - p[1];
        let dz = dest.z - p[2];
        dx * dx + dy * dy <= LAND_XY * LAND_XY && dz.abs() <= LAND_Z
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::navmesh::{Link, NavGraph};

    fn rail_graph() -> (NavGraph, u32) {
        // [0] landing 702-line, [1] rail slot, [2] ordinary walk cell with two walks
        let origins = [
            Vec3::new(-288.0, -672.0, -16.0),
            Vec3::new(-360.0, -688.0, 128.03125),
            Vec3::new(0.0, 0.0, -16.0),
            Vec3::new(32.0, 0.0, -16.0),
        ];
        let links = [
            Link {
                from: 1,
                to: 0,
                kind: LinkKind::Drop,
                cost: 1.0,
            },
            Link {
                from: 2,
                to: 3,
                kind: LinkKind::Walk,
                cost: 1.0,
            },
            Link {
                from: 3,
                to: 2,
                kind: LinkKind::Walk,
                cost: 1.0,
            },
        ];
        (NavGraph::from_topology(&origins, &links), 1)
    }

    #[test]
    fn cvar_off_is_none_even_on_rail() {
        let (g, rail) = rail_graph();
        assert_eq!(escape(&g, rail, false, false), None);
    }

    #[test]
    fn rail_no_route_cvar_on_takes_drop() {
        let (g, rail) = rail_graph();
        let id = escape(&g, rail, false, true).expect("escape");
        assert_eq!(g.links[id as usize].to, 0);
        assert_eq!(g.links[id as usize].kind, LinkKind::Drop);
    }

    #[test]
    fn usable_route_blocks() {
        let (g, rail) = rail_graph();
        assert_eq!(escape(&g, rail, true, true), None);
    }

    #[test]
    fn ordinary_walk_cell_never_fires() {
        let (g, _) = rail_graph();
        assert_eq!(escape(&g, 2, false, true), None);
        assert_eq!(escape(&g, 3, false, true), None);
    }

    #[test]
    fn walk_out_instead_of_drop_never_fires() {
        let origins = [Vec3::new(-288.0, -672.0, -16.0), Vec3::new(-360.0, -688.0, 128.03125)];
        let links = [Link {
            from: 1,
            to: 0,
            kind: LinkKind::Walk,
            cost: 1.0,
        }];
        let g = NavGraph::from_topology(&origins, &links);
        assert_eq!(escape(&g, 1, false, true), None);
    }

    #[test]
    fn two_drops_never_fires() {
        let origins = [
            Vec3::new(-288.0, -672.0, -16.0),
            Vec3::new(-360.0, -688.0, 128.03125),
            Vec3::new(-288.0, -640.0, -16.0),
        ];
        let links = [
            Link {
                from: 1,
                to: 0,
                kind: LinkKind::Drop,
                cost: 1.0,
            },
            Link {
                from: 1,
                to: 2,
                kind: LinkKind::Drop,
                cost: 1.0,
            },
        ];
        let g = NavGraph::from_topology(&origins, &links);
        assert_eq!(escape(&g, 1, false, true), None);
    }

    #[test]
    fn indegree_nonzero_never_fires() {
        let origins = [
            Vec3::new(-288.0, -672.0, -16.0),
            Vec3::new(-360.0, -688.0, 128.03125),
            Vec3::new(-360.0, -656.0, 128.03125),
        ];
        let links = [
            Link {
                from: 2,
                to: 1,
                kind: LinkKind::Walk,
                cost: 1.0,
            },
            Link {
                from: 1,
                to: 0,
                kind: LinkKind::Drop,
                cost: 1.0,
            },
        ];
        let g = NavGraph::from_topology(&origins, &links);
        assert_eq!(escape(&g, 1, false, true), None);
    }

    #[test]
    fn unpinned_landing_never_fires() {
        let origins = [
            Vec3::new(256.0, -704.0, -16.0), // not on 670/702-line
            Vec3::new(-360.0, -688.0, 128.03125),
        ];
        let links = [Link {
            from: 1,
            to: 0,
            kind: LinkKind::Drop,
            cost: 1.0,
        }];
        let g = NavGraph::from_topology(&origins, &links);
        assert_eq!(escape(&g, 1, false, true), None);
    }
}
