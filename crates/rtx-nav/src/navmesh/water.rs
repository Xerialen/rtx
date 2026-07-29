// SPDX-License-Identifier: AGPL-3.0-or-later

//! Navmesh through the water — layers a swimmer can navigate, and the rim it can climb out at.
//!
//! The carve plants cells on standable floor, so a pool contributes only the ground at the bottom of
//! it. Routes through water therefore run along that bottom, and on dm3 that means a 619-cell pool,
//! 99% of it deep enough to drown in, with **ten** links leading out — because the floor sits 184
//! units below the banks and nothing in the grounded vocabulary spans that.
//!
//! A first version answered that with a single ring at the waterline: floor to swim on, rim to leave
//! by. That is a basin's answer, and water is not obliged to be a basin. Underwater geometry can be
//! any shape at all — a shelf, a mid-depth tunnel, an overhang, a pillar that is a point on the bottom
//! and broad at the surface — and none of it is described by two horizontal sheets. Worse, the rim's
//! adjacency was *inherited from the floor's*, so exactly where the shape changes with depth the mesh
//! confidently offered a link through rock.
//!
//! So the water is layered. Cells sit at intervals from the bottom to the rim, and — this is the part
//! that makes it honest — **every link is traced at the height it is swum**. Grid adjacency only
//! nominates neighbours; whether a player-shaped hull fits between two of them is a question about the
//! geometry at that depth, and only a trace there can answer it.
//!
//! The layer heights are anchored to the **waterline**, not to each column's own floor. Neighbouring
//! columns over a sloping bottom then share layer heights and can be linked to each other; anchored to
//! the floor they would stagger and the sheet would come apart exactly where the bottom is interesting.
//!
//! The rim keeps two extra conditions the deeper layers do not need, because leaving the water is a
//! different act from swimming through it:
//!
//! * **Room to be there.** At least a hull's height of free space above the waterline. Under a bridge
//!   deck with twenty units of air there is no floating spot at all, and a cell claiming one is a lie
//!   the router will happily plan through.
//! * **Something to climb onto.** A dry cell beside it, within the reach of the exit the engine
//!   actually grants. `PM_CheckWaterJump` fires at `waterlevel == 2` against a ledge a stride ahead;
//!   a surface cell with no such ledge is a place to tread water, not a way out.

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

/// Vertical spacing of the water layers.
///
/// Two grid steps. Fine enough that a mid-depth opening — a tunnel mouth, the gap under a shelf — has
/// a layer in it to be navigated by, coarse enough that a deep pool costs a handful of sheets rather
/// than a solid block of cells. dm3's 184-unit pool comes out at three layers over its floor.
const LAYER_STEP: f32 = 64.0;

/// One submerged column: the floor cell under it, its layers bottom-up, and the bank its rim can climb
/// onto if it has one.
struct Column {
    floor: CellId,
    layers: Vec<CellId>,
    bank: Option<CellId>,
}

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

        // One stack of cells per submerged column, bottom layer first, plus the rim's bank when the
        // column has one. `by_column` keys the stacks by grid square so neighbouring columns can be
        // matched layer for layer below.
        let mut stacks: Vec<Column> = Vec::new();
        let mut by_column: std::collections::HashMap<(i32, i32), usize> = std::collections::HashMap::new();
        for floor in seeds {
            let below = self.cells[floor as usize].origin;
            let Some(line) = waterline(bsp, below) else {
                continue;
            };
            if line - below.z > MAX_DEPTH {
                continue;
            }
            // Heights are stepped down from the float depth, so every column in a pool agrees on where
            // the layers are however its own floor wanders.
            let mut layers: Vec<CellId> = Vec::new();
            let mut z = line - FLOAT_BELOW;
            while z > below.z + LAYER_STEP * 0.5 {
                let at = Vec3::new(below.x, below.y, z);
                // Water the hull fits in. A layer that is solid, dry, or too tight is simply absent —
                // the stack is as tall as the column really is, and no taller.
                if bsp.pointcontents(at) == CONTENTS_WATER && !bsp.is_solid(at) {
                    layers.push(self.add_cell(at));
                }
                z -= LAYER_STEP;
            }
            layers.reverse(); // bottom-up, so consecutive pairs are one step apart
            if layers.is_empty() {
                continue;
            }
            // The topmost layer is the rim, and only it is asked the two extra questions that make an
            // exit: room to float, and somewhere to climb.
            let top = *layers.last().unwrap();
            let rim = self.cells[top as usize].origin;
            let bank = if fits_above(bsp, below, line) {
                self.climb_target(bsp, rim)
            } else {
                None
            };
            let (gx, gy) = (self.cells[floor as usize].gx, self.cells[floor as usize].gy);
            by_column.insert((gx, gy), stacks.len());
            stacks.push(Column { floor, layers, bank });
        }

        let mut links: Vec<Link> = Vec::new();
        let swim = |links: &mut Vec<Link>, a: CellId, b: CellId, extra: f32| {
            let (pa, pb) = (self.cells[a as usize].origin, self.cells[b as usize].origin);
            if !swimmable(bsp, pa, pb) {
                return;
            }
            let cost = (pb - pa).length().max(GRID) / SWIM_SPEED + extra;
            links.push(Link {
                from: a,
                to: b,
                kind: LinkKind::Swim,
                cost,
            });
        };

        // Up and down each column: floor to the first layer, then layer to layer. Traced, because a
        // column clear at the bottom can be roofed partway up by an overhang, and a climb through rock
        // is a link the router plans and the bot can only press against.
        for c in &stacks {
            let mut below = c.floor;
            for &up in &c.layers {
                swim(&mut links, below, up, 0.0);
                swim(&mut links, up, below, 0.0);
                below = up;
            }
        }
        // Across each layer, between columns the pool's own adjacency nominates as neighbours — but
        // traced at that layer's height, which is the whole point. An inverted cone is passable along
        // the floor and solid across the top; inheriting the floor's answer would run the link through
        // the rock, and the bot would swim at a wall the mesh promised was open water.
        for c in &stacks {
            for &li in &self.adjacency[c.floor as usize] {
                let nbr_floor = self.links[li as usize].to;
                let (gx, gy) = (self.cells[nbr_floor as usize].gx, self.cells[nbr_floor as usize].gy);
                let Some(&n) = by_column.get(&(gx, gy)) else {
                    continue;
                };
                for (&a, &b) in c.layers.iter().zip(stacks[n].layers.iter()) {
                    swim(&mut links, a, b, 0.0);
                }
            }
        }
        // And out, from the rim to the bank it was proven against — pointedly *not* traced.
        //
        // Every other water link is a swim, and a swim through solid is a lie. The haul-out is not a
        // swim: `PM_CheckWaterJump` throws the bot in an arc *over* the lip, so a straight line from
        // the water to a standing spot on top of the bank passes through the bank's own edge by
        // construction. Tracing it rejects every exit on the map — measured, dm3 went from fifty swim
        // exits to none — which is why the geometry test for this link is `climb_target`'s reach and
        // height, not a clear line.
        for c in &stacks {
            if let (Some(&top), Some(bank)) = (c.layers.last(), c.bank) {
                let (a, b) = (self.cells[top as usize].origin, self.cells[bank as usize].origin);
                links.push(Link {
                    from: top,
                    to: bank,
                    kind: LinkKind::Swim,
                    cost: (b - a).length().max(GRID) / SWIM_SPEED + EXIT_OVERHEAD,
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

/// Whether a player-sized swimmer can get from `a` to `b` without meeting solid.
///
/// One hull trace, and it is the only thing that makes a water link honest. Grid adjacency answers
/// "are these columns neighbours", which is a question about the *floor*; a pillar that changes shape
/// with depth — an inverted cone, a shelf, a bridge footing that flares — makes that answer wrong at
/// every other height. The trace asks at the height the link is actually swum.
fn swimmable(bsp: &Bsp, a: Vec3, b: Vec3) -> bool {
    bsp.hull1_trace(a, b).fraction >= 1.0
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
