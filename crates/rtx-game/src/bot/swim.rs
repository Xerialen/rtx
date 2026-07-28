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
//! So depth is decided here instead, as intent rather than rescue, from three things the bot can
//! actually perceive:
//!
//! * **Where the route goes next**, in full 3D. If it climbs, climb; if it dives under a wall, dive.
//! * **Whether there is air above.** Open water overhead means surfacing is available and free.
//! * **How much air is left.** Not as a panic threshold but as a preference that grows: a swimmer
//!   with plenty of air can afford to stay down to make a crossing, one running low cannot.
//!
//! The resulting rule is the one a person follows without thinking: *swim at the surface unless the
//! route needs you deeper.* Diving is something the geometry asks for, not something you drift into.

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
    // Air first, and not as a special case bolted on top: below `AIR_RESERVE` the only thing worth
    // doing is getting to air, so the vertical wish saturates upward. Above it, air does not enter
    // into the decision at all. The reserve is generous on purpose — this is meant to make the
    // drowning panic in `run_bot` unreachable rather than to duplicate it.
    if s.submerged && s.air_above && s.air_left < AIR_RESERVE {
        return speed;
    }

    // Otherwise follow the route in three dimensions. Scaled so a climb of more than `CLIMB_FULL`
    // asks for everything the swimmer has, and a small step asks proportionally less — a swimmer
    // tracking a gently rising floor should not be pinned to the ceiling.
    let dz = s.aim.z - s.origin.z;
    let route = (dz / CLIMB_FULL).clamp(-1.0, 1.0) * speed;

    // And prefer the surface whenever the route is not asking for depth. This is the rule that
    // stops the bot swimming a whole crossing along the bottom just because that is where the
    // navmesh's cells are: descending has to be something the geometry asked for.
    if s.submerged && s.air_above && dz > -DESCEND_EPS {
        return route.max(speed * SURFACE_SEEK);
    }
    route
}

/// Air remaining below which surfacing outranks the route. Well clear of the drowning panic in
/// `run_bot` — this exists so that reflex never has to fire.
pub const AIR_RESERVE: f32 = 8.0;

/// Height difference at which a climb or dive asks for the swimmer's full vertical effort.
pub const CLIMB_FULL: f32 = 64.0;

/// How much of a descent the route has to ask for before the surface preference gives way. Below
/// this the route counts as level and the bot rises.
pub const DESCEND_EPS: f32 = 8.0;

/// Share of the swimmer's effort spent rising when the route is level and air is overhead. Not the
/// full amount: the bot is going somewhere, and pinning the wish vertical would stall the crossing.
pub const SURFACE_SEEK: f32 = 0.5;

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

    /// But depth is still available when the geometry asks for it — swimming under a wall is a real
    /// thing routes do, and a bot that always floats cannot do it.
    #[test]
    fn a_diving_route_still_dives() {
        let w = vertical_wish(&sense(true, true, 20.0, -96.0), 320.0);
        assert!(w < 0.0, "the route asked for depth: {w}");
        assert!(w >= -320.0);
    }

    /// Air outranks the route, and does so early enough that the drowning reflex never has to fire.
    #[test]
    fn low_air_beats_a_diving_route() {
        let deep = sense(true, true, AIR_RESERVE - 1.0, -200.0);
        assert_eq!(vertical_wish(&deep, 320.0), 320.0, "must abandon the dive for air");
        // And with air to spare, the same route is swum.
        let ok = sense(true, true, AIR_RESERVE + 5.0, -200.0);
        assert!(vertical_wish(&ok, 320.0) < 0.0);
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
