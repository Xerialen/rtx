// SPDX-License-Identifier: AGPL-3.0-or-later

//! Depth: the movement axis the bot did not have.
//!
//! Out of water, two numbers — a heading and a speed — say everything about where a player is going.
//! In water they do not: depth is a third axis, and the engine will not take it from `forwardmove`
//! and `sidemove`. Supplying it is what this module is for, and after several attempts at being
//! clever the rule is very short.
//!
//! **The route decides depth. Air interrupts.**
//!
//! The temptation is to distrust the route down here, and the reasoning is genuinely persuasive: the
//! carve plants cells on standable floor, so a route across a pool is a line along its *bottom*, and
//! its `z` looks like "where the floor happens to be" rather than "where the path goes". Three
//! successive rules were built on that — surface unless the route needs depth, then never descend
//! while there is air, then dive only for a goal both below and near — and each broke a direction of
//! travel, because the floor is *also* where the route's ramps, tunnels and chambers are. Holding
//! the surface leaves a bot sliding above the ramp it must climb or the tunnel mouth it must enter,
//! its waypoints a few dozen units beneath it and no way to reach them. On dm3 that stranded it in
//! both directions between the pentagram and the lightning gun.
//!
//! Swimming the bottom is slower than crossing on the surface. That is an acceptable price: a bot
//! which swims the bottom arrives, and one which will not descend does not arrive at all.
//!
//! What remains genuinely special is air, because running out of it is fatal and the route has no
//! opinion about it. Below the reserve, with air overhead to reach, surfacing outranks everything;
//! one breath refills the tank and the crossing resumes.

use glam::Vec3;

/// What the bot can perceive about being in water this frame.
#[derive(Clone, Copy, Debug)]
pub struct Sense {
    /// Fully under (`waterlevel == 3`) — the only state that drains air.
    pub submerged: bool,
    /// Air above the water over this column, so rising actually reaches something breathable.
    ///
    /// *Air*, not open sky: an indoor pool with a ceiling a long way above it counts, because a
    /// swimmer surfacing there can breathe. What this excludes is water meeting solid directly —
    /// a bridge deck sitting in it, a flooded tunnel — where there is no surface to rise to at all.
    pub air_above: bool,
    /// Seconds of air left before drowning damage starts.
    pub air_left: f32,
    /// Where the bot is, and the next route point it is swimming toward.
    pub origin: Vec3,
    pub aim: Vec3,
    /// Whether `aim` is a real route waypoint rather than the distant goal.
    ///
    /// Past the end of a route — or when the search found none at all — the steerer aims straight at
    /// the objective, which is fine for a heading and meaningless for depth. Believing it there is
    /// how a bot ends up swimming *upward into a bridge deck* because the thing it wants is high and
    /// far away: measured on dm3, submerged under a roofed span with 4.8 seconds of air, no route,
    /// and a full upward wish pressed into the underside until it drowned.
    pub routed: bool,
}

/// Vertical velocity the bot wants, in units per second, positive up.
///
/// Returned as a velocity rather than a flag so it composes with the horizontal wish into one
/// world-space vector — the bot swims *diagonally* toward where it is going, the way a person does,
/// instead of alternating between "go there" and "go up".
pub fn vertical_wish(s: &Sense, speed: f32) -> f32 {
    // Air interrupts everything, and this is the whole of the special-casing. Deliberately late:
    // with a full tank and a short tunnel it never fires, so an ordinary crossing is simply swum.
    if s.submerged && s.air_above && s.air_left < AIR_RESERVE {
        return speed;
    }
    // With no route there is no depth to follow, and the goal's own height says nothing about the
    // water in between — so this is a recovery, not a traversal.
    //
    // Air first if it is reachable. Otherwise **sink**: every water cell the carve produces sits on
    // the pool floor, so the floor is where the map is, and descending is what re-acquires a route.
    // Holding depth instead strands the bot in the empty middle of the water — measured on dm3 at
    // (1696, 0, -240), off-mesh under a roofed span, swimming at full tilt into geometry at 13 ups
    // with no route and its air running out.
    if !s.routed {
        return if s.submerged && s.air_above { speed } else { -speed };
    }
    // Otherwise follow the route, down as readily as up. Scaled so a change of more than
    // [`CLIMB_FULL`] asks for everything the swimmer has and a smaller one asks proportionally less,
    // which keeps a bot tracking a gently shelving floor off both the ceiling and the bottom.
    let dz = s.aim.z - s.origin.z;
    (dz / CLIMB_FULL).clamp(-1.0, 1.0) * speed
}

/// Height difference at which a climb or dive asks for the swimmer's full vertical effort.
pub const CLIMB_FULL: f32 = 64.0;

/// Air remaining below which surfacing outranks the route.
///
/// Low on purpose. QuakeWorld gives twelve seconds, and a bot that bails for the surface at the
/// halfway mark abandons crossings it would comfortably have finished — the tunnel between dm3's
/// lightning gun and its pentagram is a few seconds of swimming. This is the point where the route
/// genuinely will not get there in time.
pub const AIR_RESERVE: f32 = 3.0;

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
            routed: true,
        }
    }

    /// The rule that matters, and the one three cleverer versions got wrong: a route that descends
    /// is followed. Ramps into pools, tunnel mouths and sunken chambers all present as "the waypoint
    /// is below me", and refusing that leaves the bot sliding above the thing it is trying to reach.
    #[test]
    fn the_route_is_followed_downward() {
        for air in [4.0, 8.0, 20.0] {
            assert!(
                vertical_wish(&sense(true, true, air, -200.0), 320.0) < 0.0,
                "air {air}: a descending route must be swum"
            );
        }
        // And with no air overhead — a flooded tunnel — just the same.
        assert!(vertical_wish(&sense(true, false, 8.0, -200.0), 320.0) < 0.0);
    }

    /// Climbing out is the same rule in the other direction.
    #[test]
    fn the_route_is_followed_upward() {
        assert!(vertical_wish(&sense(true, true, 8.0, 96.0), 320.0) > 0.0);
        // Level water asks for nothing.
        assert_eq!(vertical_wish(&sense(false, true, 8.0, 0.0), 320.0), 0.0);
    }

    /// Air outranks the route, but only once it is genuinely short — otherwise ordinary crossings
    /// get abandoned halfway.
    #[test]
    fn air_interrupts_only_when_it_is_nearly_gone() {
        let deep = sense(true, true, AIR_RESERVE - 1.0, -200.0);
        assert_eq!(vertical_wish(&deep, 320.0), 320.0, "must abandon the dive for air");
        // With air to spare the same descent is swum.
        assert!(vertical_wish(&sense(true, true, AIR_RESERVE + 1.0, -200.0), 320.0) < 0.0);
        // With no surface to reach, rising would not help: follow the route and hope it leads out.
        assert!(vertical_wish(&sense(true, false, 0.5, -200.0), 320.0) < 0.0);
    }

    /// Past the end of a route the aim is the objective itself, whose height says nothing about the
    /// water in between. Believing it drowned a bot under a roofed span on dm3: no route, a goal high
    /// and far, and a full upward wish held against the underside of a bridge.
    #[test]
    fn an_unrouted_bot_does_not_chase_the_goals_height() {
        let mut roofed = sense(true, false, 5.0, 400.0);
        roofed.routed = false;
        assert_eq!(
            vertical_wish(&roofed, 320.0),
            -320.0,
            "roofed and lost: sink to the floor, where the mesh is"
        );
        // With air above, surfacing is still the right instinct.
        let mut open = roofed;
        open.air_above = true;
        assert_eq!(vertical_wish(&open, 320.0), 320.0);
    }

    /// A gentle rise asks for a gentle climb, not everything the swimmer has.
    #[test]
    fn the_climb_is_proportional() {
        let small = vertical_wish(&sense(false, false, 8.0, 16.0), 320.0);
        let full = vertical_wish(&sense(false, false, 8.0, CLIMB_FULL), 320.0);
        assert!(small > 0.0 && small < full, "small {small} full {full}");
        assert!((full - 320.0).abs() < 1e-3);
    }
}
