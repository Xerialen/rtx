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
//! is no air overhead — under a bridge, inside a tunnel — is the route the best information
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
    /// Open water directly overhead, so rising actually reaches air.
    pub air_above: bool,
    /// Seconds of air left before drowning damage starts.
    pub air_left: f32,
    /// Where the bot is, and the next route point it is swimming toward.
    pub origin: Vec3,
    pub aim: Vec3,
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
    if s.submerged && s.air_above {
        return speed;
    }

    // No air overhead — roofed, under a bridge or in a tunnel. Now the route is the only information
    // there is, so follow it: scaled so a climb of more than `CLIMB_FULL` asks for everything the
    // swimmer has, and a small step asks proportionally less. Emerging from under the roof flips
    // `air_above` and the rule above takes over.
    //
    // At the surface (not submerged) the same applies for the opposite reason: nothing is being
    // asked of depth, so an exit ramp that climbs is followed and level water is held.
    let dz = s.aim.z - s.origin.z;
    (dz / CLIMB_FULL).clamp(-1.0, 1.0) * speed
}

/// Height difference at which a climb or dive asks for the swimmer's full vertical effort.
pub const CLIMB_FULL: f32 = 64.0;

#[cfg(test)]
mod tests {
    use super::*;

    fn sense(submerged: bool, air_above: bool, air_left: f32, dz: f32) -> Sense {
        Sense {
            submerged,
            air_above,
            air_left,
            origin: Vec3::ZERO,
            aim: Vec3::new(100.0, 0.0, dz),
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

    /// With no air above, rising is pointless — under a ceiling the bot follows the route instead of
    /// pressing itself against the roof.
    #[test]
    fn a_ceiling_suppresses_surfacing() {
        let w = vertical_wish(&sense(true, false, 1.0, 0.0), 320.0);
        assert_eq!(w, 0.0, "level route under a ceiling: {w}");
        let d = vertical_wish(&sense(true, false, 1.0, -96.0), 320.0);
        assert!(d < 0.0, "and a dive is still a dive: {d}");
    }

    /// Wading — waist-deep but breathing — takes no vertical wish it was not asked for.
    #[test]
    fn a_swimmer_with_its_head_out_just_follows_the_route() {
        assert_eq!(vertical_wish(&sense(false, true, 30.0, 0.0), 320.0), 0.0);
        assert!(vertical_wish(&sense(false, true, 30.0, 64.0), 320.0) > 0.0);
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
