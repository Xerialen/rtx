// SPDX-License-Identifier: AGPL-3.0-or-later

//! Depth: the movement axis the bot did not have.
//!
//! Out of water the world is a floor and a route across it, and two numbers — a heading and a speed
//! — say everything about where a player is going. In water that stops being true. Depth becomes a
//! free variable: the same route can be swum along the bottom, at the surface, or anywhere between,
//! and which one you pick decides whether you arrive breathing.
//!
//! The navmesh has no opinion about this, and cannot have one. It is built from standable floor, so
//! a route through water is a line of cells along the *bottom* of it. Followed literally that is a
//! route that drowns you: the bot swims the whole crossing submerged, and the only thing that ever
//! looked up was an anti-drown reflex firing five seconds before death — a rescue, arriving after
//! the mistake, which is the shape of every patch this movement code accumulated.
//!
//! So depth is decided here instead, as intent rather than rescue — and decided from **air**, not
//! from the route, because down here the route cannot be trusted to talk about depth at all. Every
//! point of it sits on the pool floor, so its `z` says where the floor is, not where the path goes.
//! An earlier version of this module took that `z` as a depth command and produced the exact failure
//! it was written to prevent: the bot surfaced when its air ran low, breathed, was pulled straight
//! back to the bottom by the route, and cycled there until it died. On dm3, ten of ten routed
//! crossings failed that way while an *unrouted* bot in the same water floated up and lived.
//!
//! The rule that survives contact with the map is therefore simply: **if you are under and there is
//! air above you, go up.** The route supplies the heading; air supplies the depth. Only when there
//! is no air over the water at all — a bridge deck sitting in it, a flooded tunnel — is the route
//! the best information
//! available, and then it is followed until the roof ends.
//!
//! This is a workaround for a missing capability, and worth naming as one: the real repair is cells
//! *through* the water volume, so that a route can say "surface here, cross, climb out there" and
//! this module can go back to simply tracking it.

use glam::Vec3;

/// What the bot can perceive about being in water this frame.
#[derive(Clone, Copy, Debug)]
pub struct Sense {
    /// Fully under (`waterlevel == 3`) — the only state that drains air.
    pub submerged: bool,
    /// Air above the water over this column, so rising actually reaches something breathable.
    ///
    /// *Air*, not open sky: an indoor pool with a ceiling a long way above it counts, because a
    /// swimmer surfacing there can breathe. What this excludes is water that meets solid directly —
    /// a bridge deck sitting in it, a flooded tunnel — where there is no surface to rise to at all.
    pub air_above: bool,
    /// Seconds of air left before drowning damage starts.
    pub air_left: f32,
    /// Where the bot is, and the next route point it is swimming toward.
    pub origin: Vec3,
    pub aim: Vec3,
    /// Where the bot is ultimately *going* — the thing it wants, not the next cell on the way.
    ///
    /// Depth needs this and the waypoint cannot supply it. Route cells are a grid apart, so the next
    /// one is always a stride ahead and (in a pool) far below, which reads identically whether the
    /// bot is crossing the water or descending to something on its floor. The goal tells them apart:
    /// dm3 keeps five ammo pickups on its pool bottom, and a bot that will not dive for them hovers
    /// over the item until something else happens to it.
    pub goal: Vec3,
}

/// Vertical velocity the bot wants, in units per second, positive up.
///
/// Returned as a velocity rather than a flag so it composes with the horizontal wish into one
/// world-space vector, which is the whole point — the bot swims *diagonally* toward where it is
/// going, the way a person does, instead of alternating between "go there" and "go up".
pub fn vertical_wish(s: &Sense, speed: f32) -> f32 {
    // Submerged with air overhead: go and get it, and do not ask the route's opinion.
    //
    // This used to consult the route first and only surface if it was not asking for depth. That
    // reads sensibly and is wrong, because of what a route's `z` actually is down here. The navmesh
    // is built from standable floor, so the only cells a pool produces are the ones on its *bottom*
    // — every point of a route through water sits on the floor. Its `z` is not "where the path
    // goes", it is "where the floor happens to be", and obeying it as a depth command is what drowns
    // the bot: it surfaces when the air runs low, breathes, gets pulled straight back down, and
    // cycles there until it dies. Measured on dm3, where ten of ten routed crossings failed while an
    // *unrouted* bot in the same water floated up and survived indefinitely.
    //
    // So depth comes from air, and the route contributes only its heading — until the day the mesh
    // has cells through the water volume, at which point its `z` means something and this can
    // consult it again.
    let dz = s.aim.z - s.origin.z;
    if s.air_above {
        // Air first when it is running out: nothing below is worth drowning for.
        if s.submerged && s.air_left < AIR_RESERVE {
            return speed;
        }
        // Diving is for reaching something that is actually down there. `dm3` puts five pickups on
        // its pool floor, so "never descend under sky" — which is what this rule used to say — left
        // bots hovering above the ammo they were routed to, and produced a demo in which 54% of all
        // water frames sit in the float band and 2% anywhere between it and the bottom.
        //
        // The next waypoint cannot make this call: cells are a grid apart, so it is always a stride
        // ahead and a pool-depth below whether the bot is crossing or descending. The *goal* can.
        if s.goal.z < s.origin.z - DIVE_DEPTH && (s.goal - s.origin).truncate().length() < DIVE_NEAR {
            return (dz / CLIMB_FULL).clamp(-1.0, 1.0) * speed;
        }
        // Otherwise the surface is both faster and safer: rise if under, and never sink.
        //
        // The second half is what actually gets a bot out of a pool, and it is not obvious. Leaving
        // water in QuakeWorld is `PM_CheckWaterJump`, and that fires at `waterlevel == 2` only —
        // floating with the head clear. Rising while submerged reaches that state for a single frame
        // and then loses it: the head breaks the surface, `submerged` goes false, the route's `z`
        // (down on the pool floor, where the only cells are) commands a descent, and the bot sinks
        // back under before the engine ever tests for the exit. It swims in place at the boundary
        // until it drowns — which is exactly what it looks like from the outside.
        //
        // So depth is a ratchet while there is air over the water. The bot holds the surface, and the
        // engine's own water-jump does the rest the moment it faces a bank.
        return if s.submerged {
            speed
        } else {
            (dz / CLIMB_FULL).clamp(0.0, 1.0) * speed
        };
    }

    // No air overhead — roofed, under a bridge or in a tunnel. Now the route is the only information
    // there is, so follow it, down as well as up: scaled so a climb of more than `CLIMB_FULL` asks
    // for everything the swimmer has, and a small step asks proportionally less. Emerging from under
    // the roof flips `air_above` and the ratchet above takes over.
    (dz / CLIMB_FULL).clamp(-1.0, 1.0) * speed
}

/// Height difference at which a climb or dive asks for the swimmer's full vertical effort.
pub const CLIMB_FULL: f32 = 64.0;

/// Air remaining below which nothing underwater is worth going for. Generous, so the drowning
/// reflex in `run_bot` stays unreachable rather than being duplicated here.
pub const AIR_RESERVE: f32 = 6.0;

/// How far below the bot the goal must sit before descending toward it counts as a dive rather than
/// as the route's floor-following.
pub const DIVE_DEPTH: f32 = 48.0;

/// How close, horizontally, the bot must be to a submerged goal before diving for it. Beyond this
/// the surface is the faster way to close the distance — a swimmer covers ground above water and
/// drops at the end, rather than crawling the bottom the whole way.
pub const DIVE_NEAR: f32 = 320.0;

#[cfg(test)]
mod tests {
    use super::*;

    /// A transit sense: the goal is dry land far off, so nothing is asking the bot to go down.
    fn sense(submerged: bool, air_above: bool, air_left: f32, dz: f32) -> Sense {
        Sense {
            submerged,
            air_above,
            air_left,
            origin: Vec3::ZERO,
            aim: Vec3::new(100.0, 0.0, dz),
            goal: Vec3::new(2000.0, 0.0, 0.0),
        }
    }

    /// The behaviour the navmesh cannot express: a route across the bottom of a pool is level, so
    /// the bot swims it *at the surface* rather than along the cells it was given.
    #[test]
    fn a_level_route_is_swum_at_the_surface() {
        let w = vertical_wish(&sense(true, true, 20.0, 0.0), 320.0);
        assert!(w > 0.0, "submerged on a level route must rise: {w}");
    }

    /// The bug this rule exists for: a route through water runs along the pool *floor*, because
    /// floor is the only thing the navmesh meshes. Obeying its `z` drags a submerged bot back down
    /// every time it surfaces to breathe. Air overhead outranks the route, with air to spare or not.
    #[test]
    fn a_route_along_the_bottom_never_outranks_air() {
        for air in [1.0, 8.0, 20.0] {
            let w = vertical_wish(&sense(true, true, air, -200.0), 320.0);
            assert_eq!(w, 320.0, "air {air}: the floor is not a depth command");
        }
    }

    /// With no air over the water — a deck sitting in it — rising is pointless, so the route is
    /// followed instead of the bot pressing itself against the underside.
    #[test]
    fn a_ceiling_suppresses_surfacing() {
        let w = vertical_wish(&sense(true, false, 1.0, 0.0), 320.0);
        assert_eq!(w, 0.0, "level route under a ceiling: {w}");
        let d = vertical_wish(&sense(true, false, 1.0, -96.0), 320.0);
        assert!(d < 0.0, "and a dive is still a dive: {d}");
    }

    /// Once the head is out it stays out. Leaving water is `PM_CheckWaterJump`, which fires only at
    /// `waterlevel == 2`; a bot that sinks the instant it surfaces never holds that state long
    /// enough to be granted the exit, and swims at the boundary until it drowns.
    #[test]
    fn the_surface_is_a_ratchet_while_there_is_air() {
        // Floating, with the route far below on the pool floor: hold, do not chase it down.
        assert_eq!(vertical_wish(&sense(false, true, 30.0, -200.0), 320.0), 0.0);
        // A route that climbs out is still followed.
        assert!(vertical_wish(&sense(false, true, 30.0, 64.0), 320.0) > 0.0);
        // With the water meeting solid the ratchet is off — the route is all there is.
        assert!(vertical_wish(&sense(false, false, 30.0, -200.0), 320.0) < 0.0);
    }

    /// Diving has to remain possible, or a bot routed to something on the pool floor hovers over it.
    /// dm3 keeps five pickups down there, and forbidding descent under open sky produced a demo with
    /// 54% of water frames in the float band and 2% between it and the bottom.
    #[test]
    fn it_dives_for_a_goal_that_is_actually_down_there() {
        let mut s = sense(false, true, 20.0, -160.0);
        s.goal = Vec3::new(80.0, 0.0, -180.0); // the ammo on the pool floor, near and below
        assert!(vertical_wish(&s, 320.0) < 0.0, "must be able to dive for it");

        // The same descent while merely crossing — goal dry and far — still holds the surface.
        assert_eq!(vertical_wish(&sense(false, true, 20.0, -160.0), 320.0), 0.0);

        // And a dive is abandoned when the air is nearly gone.
        let mut low = s;
        low.submerged = true;
        low.air_left = AIR_RESERVE - 1.0;
        assert_eq!(vertical_wish(&low, 320.0), 320.0, "breathing outranks the pickup");
    }

    /// A gentle rise asks for a gentle climb, not everything the swimmer has.
    #[test]
    fn the_climb_is_proportional() {
        let small = vertical_wish(&sense(false, false, 30.0, 16.0), 320.0);
        let full = vertical_wish(&sense(false, false, 30.0, CLIMB_FULL), 320.0);
        assert!(small > 0.0 && small < full, "small {small} full {full}");
        assert!((full - 320.0).abs() < 1e-3);
    }
}
