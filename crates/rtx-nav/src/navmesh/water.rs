// SPDX-License-Identifier: AGPL-3.0-or-later

//! A ring of navmesh at the waterline — the places a swimmer can get *out*.
//!
//! The carve plants cells on standable floor, so a pool contributes only the ground at the bottom of
//! it. Routes through water therefore run along that bottom, and on dm3 that means a 619-cell pool,
//! 99% of it deep enough to drown in, with **ten** links leading out — because the floor sits 184
//! units below the banks and nothing in the grounded vocabulary spans that.
//!
//! What is missing is not a sheet of navmesh over the whole pool. A swimmer crossing open water is
//! served perfectly well by the floor cells' XY and [`crate::qphys`]-driven depth control; what it
//! cannot do is *leave*. So this plants a thin ring: surface cells only where a bot could actually
//! climb out, which takes two conditions, both of which throw most of the pool away.
//!
//! * **Room to be there.** At least a hull's height of free space above the waterline. Under a
//!   bridge deck with twenty units of air there is no floating spot at all, and a cell claiming one
//!   is a lie the router will happily plan through.
//! * **Something to climb onto.** A dry cell beside it, within the reach of the exit the engine
//!   actually grants. `PM_CheckWaterJump` fires at `waterlevel == 2` against a ledge a stride ahead;
//!   a surface cell with no such ledge is a place to tread water, not a way out.
//!
//! The result on dm3 is a rim around the pool rather than a lid over it — every cell of it a spot
//! where a bot floating there gets hauled out by the engine the moment it faces the bank.

use glam::Vec3;

use super::{CellId, Link, LinkKind, NavGraph, GRID};
use crate::bsp::{Bsp, CONTENTS_EMPTY, CONTENTS_SOLID, CONTENTS_WATER};
use crate::navmesh::physics::JUMP_APEX;
use crate::navmesh::physics::SWIM_SPEED;
use crate::qphys::ORIGIN_TO_FEET;

/// Free space a surface cell needs above the waterline: the player hull's full height. Less than
/// this and there is nowhere to float, whatever the contents say.
const HULL_HEIGHT: f32 = 56.0;

/// How far below the surface a floating bot's origin sits — enough that the eyes (22 above origin)
/// clear the water, which is `waterlevel == 2` and the only state `PM_CheckWaterJump` fires in.
const FLOAT_BELOW: f32 = 20.0;

/// Deepest pool this models with a single surface ring.
const MAX_DEPTH: f32 = 1024.0;

/// Seconds charged for climbing out, over the swim to the bank: the launch arc and the landing.
const EXIT_OVERHEAD: f32 = 0.5;

impl NavGraph {
    /// Plant the waterline exit ring and wire it to the pool below and the bank beside.
    ///
    /// Runs before the liquid flags, so the cells it plants are flagged and priced by the same pass
    /// as every other cell — a ring cell reads `water` (its origin is under the surface) and
    /// `breathable` (its eyes are not), which is exactly what it is.
    pub fn add_water_surface(&mut self, bsp: &Bsp) {
        let seeds: Vec<CellId> = (0..self.cells.len() as CellId)
            .filter(|&c| bsp.pointcontents(self.cells[c as usize].origin) == CONTENTS_WATER)
            .collect();

        let mut ring: Vec<(CellId, CellId)> = Vec::new(); // (pool floor below, planted surface)
        for floor in seeds {
            let below = self.cells[floor as usize].origin;
            let Some(line) = waterline(bsp, below) else {
                continue;
            };
            if line - below.z > MAX_DEPTH || !fits_above(bsp, below, line) {
                continue;
            }
            let at = Vec3::new(below.x, below.y, line - FLOAT_BELOW);
            if bsp.is_solid(at) || self.climb_target(bsp, at).is_none() {
                continue; // nowhere to float, or nothing to climb onto: not an exit
            }
            let id = self.add_cell(at);
            ring.push((floor, id));
        }

        // Up from the pool floor and back down. `Swim` because no grounded kind describes moving
        // vertically through water, and the cost is the full 3D distance at swimming pace.
        let mut links: Vec<Link> = Vec::new();
        for &(floor, top) in &ring {
            for (from, to) in [(floor, top), (top, floor)] {
                let d = (self.cells[to as usize].origin - self.cells[from as usize].origin).length();
                links.push(Link {
                    from,
                    to,
                    kind: LinkKind::Swim,
                    cost: d.max(GRID) / SWIM_SPEED,
                });
            }
        }
        // Along the rim, following the pool's own adjacency so the ring is connected exactly where
        // the water is, around pillars a bare grid would swim straight through.
        let of_floor: std::collections::HashMap<CellId, CellId> = ring.iter().copied().collect();
        for &(floor, top) in &ring {
            for &li in &self.adjacency[floor as usize] {
                if let Some(&nbr) = of_floor.get(&self.links[li as usize].to) {
                    let d = (self.cells[nbr as usize].origin - self.cells[top as usize].origin).length();
                    links.push(Link {
                        from: top,
                        to: nbr,
                        kind: LinkKind::Swim,
                        cost: d.max(GRID) / SWIM_SPEED,
                    });
                }
            }
        }
        // And out. The bank was already proven reachable when the cell was planted, so this just
        // names it and prices the haul-out.
        for &(_, top) in &ring {
            let at = self.cells[top as usize].origin;
            if let Some(dry) = self.climb_target(bsp, at) {
                let d = (self.cells[dry as usize].origin - at).length();
                links.push(Link {
                    from: top,
                    to: dry,
                    kind: LinkKind::Swim,
                    cost: d.max(GRID) / SWIM_SPEED + EXIT_OVERHEAD,
                });
            }
        }
        for l in links {
            self.push_link(l);
        }
    }

    /// The dry cell a bot floating at `at` could climb onto, if any.
    ///
    /// Within a stride horizontally, and at a height the exit actually reaches: from the shallows a
    /// bot walks out, and from deeper water `PM_CheckWaterJump` throws it up onto a ledge. Anything
    /// higher is a wall it would tread water against forever.
    fn climb_target(&self, bsp: &Bsp, at: Vec3) -> Option<CellId> {
        self.cells_near(at.truncate(), GRID * 1.5)
            .into_iter()
            .filter(|&c| {
                let o = self.cells[c as usize].origin;
                if bsp.pointcontents(o) == CONTENTS_WATER {
                    return false;
                }
                // Measured foot to foot: the bank's floor against the swimmer's own.
                let rise = (o.z - ORIGIN_TO_FEET) - (at.z - ORIGIN_TO_FEET);
                (-GRID..=JUMP_APEX + ORIGIN_TO_FEET).contains(&rise)
            })
            .min_by(|&a, &b| {
                let d = |c: CellId| (self.cells[c as usize].origin - at).length();
                d(a).total_cmp(&d(b))
            })
    }
}

/// Whether a hull's height of free space sits above the waterline over this column.
fn fits_above(bsp: &Bsp, p: Vec3, line: f32) -> bool {
    let mut z = 8.0;
    while z <= HULL_HEIGHT {
        if bsp.pointcontents(Vec3::new(p.x, p.y, line + z)) != CONTENTS_EMPTY {
            return false;
        }
        z += 8.0;
    }
    true
}

/// The z at which the water column above `p` meets open space, or `None` if solid roofs it first.
///
/// Stepped rather than bisected: a bridge deck sitting *in* the water splits the column, and the
/// first ceiling above the bot is the one that decides whether it can surface here at all.
fn waterline(bsp: &Bsp, p: Vec3) -> Option<f32> {
    let mut z = 0.0;
    while z <= MAX_DEPTH {
        match bsp.pointcontents(p + Vec3::Z * z) {
            CONTENTS_WATER => z += 8.0,
            CONTENTS_SOLID => return None,
            _ => return Some(p.z + z),
        }
    }
    None
}
