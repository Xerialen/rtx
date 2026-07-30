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

use glam::{Vec2, Vec3};

use rtx_nav::bsp::{CONTENTS_EMPTY, CONTENTS_SOLID};

/// What the bot can perceive about being in water this frame.
#[derive(Clone, Copy, Debug)]
pub struct Sense {
    /// Fully under (`waterlevel == 3`) — the only state that drains air.
    pub submerged: bool,
    /// The height of the water's surface over this column, if rising actually reaches something
    /// breathable — see [`rtx_nav::hazard::surface_z`].
    ///
    /// *Air*, not open sky: an indoor pool with a ceiling a long way above it counts, because a
    /// swimmer surfacing there can breathe. `None` is water meeting solid directly — a bridge deck
    /// sitting in it, a flooded tunnel — where there is no surface to rise to at all.
    ///
    /// A height rather than a flag, because every rule here is a seek toward a depth, and a rule that
    /// knew only "air is up there" could do nothing but pick a direction — which flips each time the
    /// bot crosses the surface it is aiming for.
    pub surface: Option<f32>,
    /// Seconds of air left before drowning damage starts.
    pub air_left: f32,
    /// Where the bot is, and the next route point it is swimming toward.
    pub origin: Vec3,
    pub aim: Vec3,
    /// Whether `aim`'s *height* describes the water the bot is actually in.
    ///
    /// A route leg always does. A distant objective does not: the steerer aims straight at it once
    /// the route runs out, which is fine for a heading and meaningless for depth — believing it is how
    /// a bot ends up swimming *upward into a bridge deck* because the thing it wants is high and far
    /// away (measured on dm3: submerged under a roofed span, 4.8s of air, a full upward wish pressed
    /// into the underside until it drowned).
    ///
    /// But "is there a route" is the wrong test for that, and getting it wrong cost four and a half
    /// seconds at a time. When the anti-drown override retargets the goal onto a breathing cell, and
    /// the bot resolves *to that very cell* while still floating 26 units beneath it, `find_path(c, c)`
    /// is empty by definition — so a bot 26 units under the exit it was told to reach had no route,
    /// distrusted the one aim that mattered, and swam *down*. Measured at dm3's bridge as
    /// `cell == target == goal`, `route 0/0`, and `up` pinned at −800 for 4.5s.
    ///
    /// So the question is proximity, not provenance: a target overhead in this column is describing
    /// this water, whether or not a search produced legs to it.
    pub aim_trusted: bool,
}

/// Vertical velocity the bot wants, in units per second, positive up.
///
/// Returned as a velocity rather than a flag so it composes with the horizontal wish into one
/// world-space vector — the bot swims *diagonally* toward where it is going, the way a person does,
/// instead of alternating between "go there" and "go up".
pub fn vertical_wish(s: &Sense, speed: f32) -> f32 {
    // Air interrupts everything, and this is the whole of the special-casing. Deliberately late:
    // with a full tank and a short tunnel it never fires, so an ordinary crossing is simply swum.
    if s.submerged && s.air_left < AIR_RESERVE {
        if let Some(line) = s.surface {
            return seek(line - FLOAT_BELOW, s.origin.z, speed);
        }
    }
    // With no route there is no depth to follow, and the goal's own height says nothing about the
    // water in between — so this is a recovery, not a traversal. It is also the ordinary state of a
    // bot that has simply been told to hold position in water, which is why it must be *calm*.
    //
    // Float if there is a surface to float at; otherwise **sink**: every water cell the carve
    // produces sits on the pool floor, so the floor is where the map is, and descending is what
    // re-acquires a route. Holding depth instead strands the bot in the empty middle of the water —
    // measured on dm3 at (1696, 0, -240), off-mesh under a roofed span, swimming at full tilt into
    // geometry at 13 ups with no route and its air running out.
    if !s.aim_trusted {
        return match s.surface {
            Some(line) => seek(line - FLOAT_BELOW, s.origin.z, speed),
            None => -speed,
        };
    }
    // Otherwise follow the route, down as readily as up.
    seek(s.aim.z, s.origin.z, speed)
}

/// Vertical effort that closes on `target_z` — the one control law this module has.
///
/// Proportional and clamped: a gap of more than [`CLIMB_FULL`] asks for everything the swimmer has,
/// a smaller one asks proportionally less, and at the target it asks for nothing. Every rule above is
/// this function with a different reference height, which is what makes them compose instead of
/// fighting: an earlier version chose a *sign* per rule, and two rules disagreeing about the sign at
/// the surface produced 46-270 full-throttle reversals in a single crossing — a bot thrashing
/// up-down-up-down at the waterline instead of floating in it.
fn seek(target_z: f32, from_z: f32, speed: f32) -> f32 {
    ((target_z - from_z) / CLIMB_FULL).clamp(-1.0, 1.0) * speed
}

/// The wish, deflected around solid the engine would otherwise pin the bot against.
///
/// Everything above composes a wish out of the *route*, which knows where the bot should end up and
/// nothing about what stands in the way. `PM_WaterMove` clips velocity against the planes it hits,
/// so a wish pressed square into a corner nets nothing: the bot asks at full effort, the engine
/// subtracts all of it, and the position does not change. Measured on dm3's east rim — `up` held at
/// 800 into the underside of the lip with `z` pinned to the decimal, and `forwardmove` 740 square
/// into the wall at x=1888 at zero speed for seconds, until the stuck watchdog force-jumped it free.
///
/// On the ground this is already handled: the bhop controller traces a forward wall probe and
/// carves. In water there was nothing, so this is that missing sense — one deflection rather than a
/// rule per obstacle. Press into a roof and the vertical is dropped, because rising is not
/// available here; press into a wall and the horizontal slides along it toward open water. What
/// falls out is what a person does at a submerged lip: clear it first, *then* rise.
///
/// **What is lost is the part heading *into* the surface, and only that.** The tempting reading of
/// "blocked" is "there is solid within reach", and it is wrong twice over. It is a binary test on the
/// distance to a wall — a quantity the bot's own motion changes, so it clears, the bot swims at the
/// wall, it blocks, the bot turns off, it clears again, and the view snaps back and forth. And it
/// condemns walls that cost nothing: dm3's flooded pentagram tunnel is one to two hull widths across,
/// so *every* frame in it has solid within reach, and steering away from that turned a 6.4s crossing
/// into 14.7s and a failure. A wall you swim parallel to takes nothing from you.
///
/// So the question is asked of the plane, not the distance. `wish · n` is exactly the effort the
/// engine is about to subtract; removing it leaves the bot sliding along the surface with everything
/// else intact, which in a corridor is the whole wish and against a flat wall is the tangential part.
/// It is zero when running parallel, so a tunnel is untouched, and it is continuous in both position
/// and heading, so nothing flips.
///
/// `trace(a, b)` gives the fraction of `a → b` the player hull covers before hitting solid (1.0 for a
/// clear line) and the surface normal at impact, oriented back against the segment. Pure over that
/// oracle, so the tests below can pose exact geometry.
pub fn deflect(trace: &impl Fn(Vec3, Vec3) -> (f32, Vec3), origin: Vec3, wish: Vec3, waypoint: Vec3) -> Vec3 {
    let speed = wish.length();
    if speed < 1.0 {
        return wish;
    }
    let dir = wish / speed;
    let (frac, n) = trace(origin, origin + dir * LOOKAHEAD);
    let into = wish.dot(n);
    if frac >= 1.0 || n == Vec3::ZERO || into >= 0.0 {
        return wish; // clear, or already heading away from the surface we found
    }
    // Faded by how close the surface is, so distant geometry costs nothing and contact costs all of
    // it. This is the only place proximity enters, and it scales a magnitude rather than choosing a
    // branch — which is what keeps the approach smooth instead of snapping at a threshold.
    let closeness = (1.0 - frac).clamp(0.0, 1.0);
    let mut out = wish - n * into * closeness;

    // Head-on there is no tangential part to keep, and a bot with nothing left to ask for is the
    // stall this exists to remove — the lip on dm3's east rim, where the climb was pressed into the
    // underside at full effort and the bot hung there. Spend what the surface took on sliding along
    // it toward the waypoint. `lost` is continuous and the direction depends on the plane and the
    // route rather than on anything the bot's own drift perturbs, so this neither snaps nor chatters.
    let lost = (speed - out.length()).max(0.0);
    if lost > 0.0 {
        let toward = waypoint - origin;
        let along = toward - n * toward.dot(n);
        out += along.normalize_or_zero() * lost * closeness;
    }

    // Never hand back more than was asked for.
    if out.length() > speed {
        out.normalize_or_zero() * speed
    } else {
        out
    }
}

/// The direction to face to be let out of the water here, if there is one.
///
/// Not a guess at where the bank is — the engine's own question, asked directly. `PM_CheckWaterJump`
/// probes a single point 24 units along the *flattened view*, and grants the climb only when that
/// point is `CONTENTS_SOLID` 8 above the origin and `CONTENTS_EMPTY` 32 above it. Nothing else about
/// the bot matters: not where its route goes, not which way it is swimming, only where it is looking.
///
/// So the way to leave the water is to face a direction that satisfies that, and the way to find one
/// is to try. Earlier attempts inferred it — the bearing to the next cell, then the struck plane's
/// normal — and both are indirections that fail in the case that matters most: a bot that has just
/// fumbled a hop and dropped in beside a wall, whose route points somewhere else entirely and whose
/// nearest surface is not the one it can climb. `None` means this spot is not a way out at all, and
/// the caller should swim on rather than press.
///
/// Among the directions that work, the one to take is the one facing the bank most **squarely**, and
/// that is the one in which the bank is *nearest*. Preferring the most forward-facing instead — the
/// first hit scanning out from the route's bearing — sounds harmless and is not: alongside dm3's water
/// bridge the along-the-bridge direction often grants as well as the across-it one, so the bot took it
/// and swam the length of the bridge instead of climbing the bit right next to it. Nearest-solid picks
/// the perpendicular, which is both what a person does and what leaves the engine's 24-unit probe the
/// most room to be inside the lip.
pub fn exit_yaw(contents: &impl Fn(Vec3) -> i32, at: Vec3, prefer: Vec2) -> Option<Vec2> {
    let base = if prefer == Vec2::ZERO {
        Vec2::X
    } else {
        prefer.normalize()
    };
    let granted = |d: Vec2| {
        let spot = at + (d * WATERJUMP_PROBE_AHEAD).extend(0.0);
        contents(spot + Vec3::Z * WATERJUMP_PROBE_LOW) == CONTENTS_SOLID
            && contents(spot + Vec3::Z * WATERJUMP_PROBE_HIGH) == CONTENTS_EMPTY
    };
    // How far away the lip is in this direction, measured at the height the engine's near probe reads.
    // Small means square-on to the face; large means glancing along it.
    let reach = |d: Vec2| {
        let mut r = 4.0;
        while r <= WATERJUMP_PROBE_AHEAD {
            let p = at + (d * r).extend(0.0) + Vec3::Z * WATERJUMP_PROBE_LOW;
            if contents(p) == CONTENTS_SOLID {
                return r;
            }
            r += 4.0;
        }
        WATERJUMP_PROBE_AHEAD
    };

    let mut best: Option<(Vec2, f32, f32)> = None; // (direction, distance to lip, turn from prefer)
    let mut off = 0.0f32;
    while off <= 180.0 {
        for signed in [off, -off] {
            let (s, c) = signed.to_radians().sin_cos();
            let d = Vec2::new(base.x * c - base.y * s, base.x * s + base.y * c);
            if granted(d) {
                let score = (reach(d), off);
                // Nearest lip wins; an equally near one is broken by the smaller turn, so a bank the
                // route already faces is preferred over an identical one behind the bot.
                if best.is_none_or(|(_, r, t)| score.0 < r - 0.5 || (score.0 <= r + 0.5 && score.1 < t)) {
                    best = Some((d, score.0, score.1));
                }
            }
            if off == 0.0 {
                break; // +0 and -0 are the same direction
            }
        }
        off += EXIT_SCAN_STEP;
    }
    best.map(|(d, _, _)| d)
}

/// How finely [`exit_yaw`] scans. The probe is a single point 24 units out, so 15 degrees moves it
/// about 6 units sideways — fine enough not to step over a doorway-width opening in the bank.
const EXIT_SCAN_STEP: f32 = 15.0;

/// `PM_CheckWaterJump`'s probe, verbatim: 24 units along the flattened view, solid at +8, empty at
/// +32. Empty and not merely non-solid — water at the high probe fails it, which is what stops a bot
/// "climbing out" onto something still submerged.
const WATERJUMP_PROBE_AHEAD: f32 = 24.0;
const WATERJUMP_PROBE_LOW: f32 = 8.0;
const WATERJUMP_PROBE_HIGH: f32 = 32.0;

/// How far ahead the deflection looks for a surface to slide along.
///
/// Only anticipation now that the deflection keys on the plane rather than on proximity: a wall the
/// bot is swimming parallel to contributes nothing however near it is, so this can be generous
/// without a narrow corridor reading as an obstruction. Two grid steps is about a third of a second
/// at swimming pace — enough that the heading eases round rather than arriving at the wall square.
const LOOKAHEAD: f32 = 64.0;

/// How far below the surface a floating bot holds its origin, so its eyes (22 above origin) stay
/// clear of the water — `waterlevel == 2`, which is both breathing and the only state
/// `PM_CheckWaterJump` will haul it out of. Matches the exit ring's own float height.
pub const FLOAT_BELOW: f32 = 20.0;

/// Height difference at which a climb or dive asks for the swimmer's full vertical effort.
pub const CLIMB_FULL: f32 = 64.0;

/// How near, horizontally, a target has to be for its height to describe the water the bot is in.
///
/// Three grid steps. Nearer than this and a target above the bot is in the same column of water it is
/// floating in, so rising toward it is the right move whether or not a search produced legs — which
/// is the whole of the anti-drown case, where the goal *is* the breathing cell overhead. Further away
/// and the height is a fact about somewhere else, and following it swims a bot into a ceiling.
pub const AIM_TRUST_XY: f32 = 96.0;

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
    use rtx_nav::bsp::CONTENTS_WATER;

    /// The bot sits at z 0; `air_above` places a surface far enough overhead that the float target
    /// is well above it (so "reachable air" reads as a strong upward ask, as it used to).
    fn sense(submerged: bool, air_above: bool, air_left: f32, dz: f32) -> Sense {
        Sense {
            submerged,
            surface: air_above.then_some(400.0),
            air_left,
            origin: Vec3::ZERO,
            aim: Vec3::new(100.0, 0.0, dz),
            aim_trusted: true,
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
        roofed.aim_trusted = false;
        assert_eq!(
            vertical_wish(&roofed, 320.0),
            -320.0,
            "roofed and lost: sink to the floor, where the mesh is"
        );
        // With a surface far above, it heads for it — but for the surface, not for the goal.
        let mut open = roofed;
        open.surface = Some(400.0);
        assert_eq!(vertical_wish(&open, 320.0), 320.0);
    }

    /// The failure a spectator actually sees, and the reason this module is one control law.
    ///
    /// A routeless bot bobbing at the waterline crosses `submerged` constantly. The rule this
    /// replaced picked a *sign* from that bit, so each crossing swung the ask the full width of the
    /// range — `+speed` to `-speed` between adjacent frames, which on dm3 was 46-270 reversals in one
    /// crossing and looked, accurately, like a breakdown.
    ///
    /// The property that rules that out is not "few sign changes" — a seek tracking a bobbing float
    /// legitimately changes sign at every crossing — but **continuity**: adjacent positions must
    /// command near-adjacent efforts, and the effort near the target must be a trim rather than
    /// everything the swimmer has. Both are asserted by sweeping z through the whole band.
    #[test]
    fn a_routeless_bot_settles_at_the_surface_instead_of_thrashing() {
        let (line, speed) = (-208.0f32, 320.0f32);
        let at = |z: f32| {
            vertical_wish(
                &Sense {
                    submerged: z < line - 22.0,
                    surface: Some(line),
                    air_left: 11.0,
                    origin: Vec3::new(0.0, 0.0, z),
                    aim: Vec3::new(100.0, 0.0, 400.0), // a high, far goal: must not be chased
                    aim_trusted: false,
                },
                speed,
            )
        };
        let float_at = line - FLOAT_BELOW;
        let mut prev = at(float_at - 40.0);
        let mut last_sign = prev.signum();
        let mut sign_changes = 0;
        for i in 1..=160 {
            let z = float_at - 40.0 + 0.5 * i as f32; // sweep up through the surface
            let w = at(z);
            assert!(
                (w - prev).abs() <= 0.05 * speed,
                "z {z}: ask jumped {prev} -> {w} over half a unit — that is a switch, not a seek"
            );
            // Against the last *non-zero* sign: the sweep lands exactly on the target, and passing
            // cleanly through zero is the seek working, not a missing crossing.
            if w != 0.0 {
                if w.signum() != last_sign {
                    sign_changes += 1;
                }
                last_sign = w.signum();
            }
            prev = w;
        }
        // Monotone sweep through the target: the effort reverses exactly once, at the float depth.
        assert_eq!(sign_changes, 1, "a monotone sweep must cross zero once");
        assert!(at(float_at).abs() < 1.0, "at the float depth it should ask for nothing");
        assert!(at(float_at + 4.0) < 0.0, "a little high: trim down");
        assert!(at(float_at - 4.0) > 0.0, "a little low: trim up");
    }

    /// A trace oracle over an axis-aligned plane, exact rather than sampled: the deflection keys on
    /// the plane's normal, so a quantized fraction would blur the very thing under test. `normal` is
    /// supplied because a solidity predicate cannot report one.
    fn plane(at: f32, axis: usize, normal: Vec3) -> impl Fn(Vec3, Vec3) -> (f32, Vec3) + Copy {
        move |a: Vec3, b: Vec3| {
            let (a_c, b_c) = (a[axis], b[axis]);
            if b_c == a_c || (at - a_c).signum() != (b_c - a_c).signum() {
                return (1.0, Vec3::ZERO); // parallel to it, or heading away
            }
            let f = ((at - a_c) / (b_c - a_c)).clamp(0.0, 1.0);
            if f >= 1.0 {
                (1.0, Vec3::ZERO)
            } else {
                (f, normal)
            }
        }
    }

    /// A wall across +x, its normal pointing back at a bot approaching from below it.
    fn wall_x(at: f32) -> impl Fn(Vec3, Vec3) -> (f32, Vec3) + Copy {
        plane(at, 0, Vec3::new(-1.0, 0.0, 0.0))
    }

    /// Open water: the wish is the bot's own business and comes back untouched.
    #[test]
    fn open_water_leaves_the_wish_alone() {
        let t = |_: Vec3, _: Vec3| (1.0, Vec3::ZERO);
        let wish = Vec3::new(200.0, -140.0, 90.0);
        assert_eq!(deflect(&t, Vec3::ZERO, wish, Vec3::new(400.0, 0.0, 300.0)), wish);
    }

    /// The regression that sent the first design back: dm3's flooded pentagram tunnel is one to two
    /// hull widths across, so solid sits within reach on *every* frame of it. Keying the deflection on
    /// proximity therefore fired continuously and turned a 6.4s crossing into 14.7s and a failure. A
    /// wall you swim parallel to must cost nothing at all.
    #[test]
    fn a_wall_alongside_costs_nothing() {
        let t = wall_x(20.0); // a hull's width to the side
        let along = Vec3::new(0.0, 320.0, 0.0); // straight down the corridor
        assert_eq!(
            deflect(&t, Vec3::ZERO, along, Vec3::new(0.0, 900.0, 0.0)),
            along,
            "a wall to the side takes nothing from a stroke not aimed at it"
        );
        // Nor does a stroke leaning slightly away from it.
        let leaning = Vec3::new(-30.0, 318.0, 0.0);
        assert_eq!(deflect(&t, Vec3::ZERO, leaning, Vec3::new(0.0, 900.0, 0.0)), leaning);
    }

    /// Into a wall: the part aimed at it is exactly what the engine would eat, so that is what goes,
    /// and the bot slides along the surface rather than pressing into it.
    #[test]
    fn a_wall_ahead_becomes_a_slide_along_it() {
        let t = wall_x(4.0); // hard against
        let wish = Vec3::new(226.0, 226.0, 0.0); // forty-five degrees into it
        let got = deflect(&t, Vec3::ZERO, wish, Vec3::new(200.0, 900.0, 0.0));
        assert!(got.x < 30.0, "the component into the wall must go, got {got}");
        assert!(got.y > 200.0, "the component along it must survive, got {got}");
    }

    /// The dm3 east-rim stall: pressing straight up into an overhang. Nothing tangential survives, so
    /// what the roof took is spent sliding along it toward the waypoint — which is what gets the bot
    /// out from under the lip instead of hanging beneath it at full effort.
    #[test]
    fn a_roof_turns_a_wasted_climb_into_going_somewhere() {
        let t = plane(2.0, 2, Vec3::new(0.0, 0.0, -1.0)); // ceiling just above
        let got = deflect(&t, Vec3::ZERO, Vec3::new(0.0, 0.0, 320.0), Vec3::new(300.0, 0.0, 400.0));
        assert!(got.z < 32.0, "rising into a roof is effort spent on nothing, got {got}");
        assert!(
            got.truncate().length() > 100.0,
            "must go looking for open water, got {got}"
        );
        assert!(got.x > 0.0, "and toward the waypoint side, got {got}");
    }

    /// The invariant that would have caught the first version of this before it reached the server:
    /// obstruction must be a *weight*, not a verdict.
    ///
    /// Written as "if blocked, slide", the deflection is a binary test on distance-to-wall — a
    /// quantity the bot's own motion changes — so it clears, the bot swims at the wall, it blocks, the
    /// bot turns away, it clears again, and the view snaps between the two headings. Sweeping the bot
    /// toward a wall a unit at a time, the commanded heading must never jump and never double back.
    #[test]
    fn approaching_a_wall_turns_the_wish_gradually_not_in_a_snap() {
        let t = wall_x(100.0);
        let wish = Vec3::new(320.0, 0.0, 0.0);
        let waypoint = Vec3::new(400.0, 200.0, 0.0);
        let at = |x: f32| deflect(&t, Vec3::new(x, 0.0, 0.0), wish, waypoint);
        // How far the heading has swung from the original wish, signed. A single component would do
        // instead, but only until the turn passes ninety degrees and that component saturates.
        let swing = |v: Vec3| {
            let (a, b) = (wish.truncate().normalize_or_zero(), v.truncate().normalize_or_zero());
            f32::atan2(a.x * b.y - a.y * b.x, a.dot(b)).to_degrees()
        };
        let (mut prev, mut prev_swing) = (at(0.0), 0.0f32);
        for i in 1..=95 {
            let got = at(i as f32);
            assert!(
                (got - prev).length() <= 0.09 * wish.length(),
                "x={i}: heading jumped {prev} -> {got} over one unit — that is a switch, not a turn"
            );
            let s = swing(got);
            assert!(
                s >= prev_swing - 0.5,
                "x={i}: heading turned back, {prev_swing}deg -> {s}deg"
            );
            prev = got;
            prev_swing = s;
        }
        // And by the time it is against the wall it is running along it, not into it.
        let against = at(96.0);
        assert!(
            against.y.abs() > against.x.abs(),
            "at the wall the stroke should be along it, got {against}"
        );
    }

    /// Which flank it slides onto is the route's business, since either is equally valid against a
    /// flat wall.
    #[test]
    fn a_wall_slides_toward_the_waypoint_side() {
        let t = wall_x(4.0);
        let wish = Vec3::new(320.0, 0.0, 0.0);
        let north = deflect(&t, Vec3::ZERO, wish, Vec3::new(0.0, 500.0, 0.0));
        let south = deflect(&t, Vec3::ZERO, wish, Vec3::new(0.0, -500.0, 0.0));
        assert!(north.y > 100.0, "waypoint north: slide north, got {north}");
        assert!(south.y < -100.0, "waypoint south: slide south, got {south}");
    }

    /// A dead-square approach must not chatter: nudge the bot by fractions of a unit, and the answer
    /// holds.
    ///
    /// The design this replaced summed weighted open directions, and in a balanced corner those
    /// *cancel* — normalising the near-zero sum turned sub-unit drift into a full-amplitude sign flip,
    /// measured on dm3's rim as `up` alternating +-103 and `forwardmove` +-557 with the view and route
    /// perfectly still. Deriving the slide from the plane has no such degeneracy.
    #[test]
    fn a_balanced_corner_does_not_chatter() {
        let t = wall_x(4.0);
        let wish = Vec3::new(320.0, 0.0, 0.0); // dead square into it
        let waypoint = Vec3::new(500.0, 60.0, 0.0);
        let first = deflect(&t, Vec3::ZERO, wish, waypoint);
        for (i, j) in [0.03f32, -0.02, 0.05, -0.04, 0.01].into_iter().enumerate() {
            let got = deflect(&t, Vec3::new(j, j * 0.5, 0.0), wish, waypoint);
            assert!(
                got.dot(first) > 0.0,
                "nudge {i} ({j}u) flipped the wish: {first} -> {got}"
            );
        }
        assert!(first.y > 0.0, "should commit to the waypoint's flank, got {first}");
    }

    /// Leaving the water is entirely about where the bot looks, so the direction has to be *found*,
    /// not inferred — and the case that decides it is a bot that fumbled a hop into the water beside a
    /// wall, whose route points somewhere else.
    #[test]
    fn the_exit_direction_is_the_one_the_engine_grants() {
        // A bank across +x whose walkable top is 16 above the bot, so `PM_CheckWaterJump`'s probe finds
        // solid at +8 and air at +32. Everything else is water.
        let w = |p: Vec3| {
            if p.x >= 24.0 && p.z < 16.0 {
                CONTENTS_SOLID
            } else if p.z < 0.0 {
                CONTENTS_WATER
            } else {
                CONTENTS_EMPTY
            }
        };
        let at = Vec3::new(0.0, 0.0, -10.0);

        // Already pointing at it: keep pointing at it.
        let east = exit_yaw(&w, at, Vec2::new(1.0, 0.0)).expect("the bank is right there");
        assert!(east.x > 0.9, "should face the bank, got {east}");

        // The route runs *along* the shore — the old bearing-based guess would face north and press
        // forever. The scan must still find the bank to the east.
        let along = exit_yaw(&w, at, Vec2::new(0.0, 1.0)).expect("the bank is still there");
        assert!(along.x > 0.5, "must turn toward the bank, got {along}");

        // Route pointing flat away from it — the fumbled-hop case. Still found.
        let away = exit_yaw(&w, at, Vec2::new(-1.0, 0.0)).expect("a bank behind is still a bank");
        assert!(away.x > 0.5, "must turn round to the bank, got {away}");

        // Open water: no direction works, and saying so is what stops the bot pressing at nothing.
        let sea = |p: Vec3| if p.z < 0.0 { CONTENTS_WATER } else { CONTENTS_EMPTY };
        assert_eq!(exit_yaw(&sea, at, Vec2::new(1.0, 0.0)), None);

        // Two banks at once, at different distances — dm3's water bridge, where the along-the-bridge
        // direction grants as readily as the across-it one. Facing the nearer is facing squarely, and
        // taking the merely-more-forward one is what had the bot swim the bridge's length instead of
        // climbing the piece beside it.
        let bridge = |p: Vec3| {
            // Near bank 20 out along +y; far bank 96 out along +x. Both climbable.
            let near = p.y >= 20.0 && p.z < 16.0;
            let far = p.x >= 96.0 && p.z < 16.0;
            if near || far {
                CONTENTS_SOLID
            } else if p.z < 0.0 {
                CONTENTS_WATER
            } else {
                CONTENTS_EMPTY
            }
        };
        // Route running along the bridge (+x, toward the far bank): the near one is still chosen.
        let square = exit_yaw(&bridge, at, Vec2::new(1.0, 0.0)).expect("both banks grant");
        assert!(
            square.y > 0.7,
            "should face the near bank square-on, not swim the length of the far one: {square}"
        );

        // A wall too tall to climb: solid at +8 *and* at +32, so the engine refuses and so must this.
        let cliff = |p: Vec3| {
            if p.x >= 24.0 {
                CONTENTS_SOLID
            } else if p.z < 0.0 {
                CONTENTS_WATER
            } else {
                CONTENTS_EMPTY
            }
        };
        assert_eq!(exit_yaw(&cliff, at, Vec2::new(1.0, 0.0)), None);
    }

    /// Never more than was asked for, whatever the geometry does.
    #[test]
    fn deflection_never_speeds_the_swimmer_up() {
        for t in [wall_x(4.0), wall_x(40.0)] {
            for wish in [
                Vec3::new(300.0, 0.0, 300.0),
                Vec3::new(320.0, 0.0, 0.0),
                Vec3::new(226.0, 226.0, 0.0),
                Vec3::new(40.0, 200.0, -180.0),
            ] {
                let got = deflect(&t, Vec3::ZERO, wish, Vec3::new(0.0, 400.0, 0.0));
                assert!(got.length() <= wish.length() + 1e-3, "{wish} -> {got}");
            }
        }
    }

    /// A bot sent to the breathing cell directly above it must rise to it, not sink.
    ///
    /// This is the failure that survived every steering fix. When air runs low the goal is retargeted
    /// onto a breathing cell; `nearest_in_medium` then resolves the bot *to that very cell* while it
    /// still floats beneath it (the ring sits 26u up, the pool floor 152u down, so the ring really is
    /// nearest); and `find_path(c, c)` is empty by definition. Judged on "is there a route", the one
    /// aim that mattered was thrown away and the roofed-column rule swam the bot *down* — measured at
    /// dm3's bridge as `cell == target == goal`, `route 0/0`, `up` pinned at −800 for 4.5 seconds at a
    /// time, which is exactly the "swims back and forth under the bridge instead of leaving" a
    /// spectator sees.
    #[test]
    fn an_exit_overhead_is_climbed_even_with_no_route_to_it() {
        let s = Sense {
            submerged: true,
            surface: None, // roofed here: the old rule's cue to sink
            air_left: 6.0,
            origin: Vec3::new(1856.0, -128.0, -240.0),
            aim: Vec3::new(1856.0, -128.0, -214.0), // the exit, 26u straight up
            aim_trusted: true,                      // same column, so its height is about this water
        };
        assert!(
            vertical_wish(&s, 320.0) > 0.0,
            "an exit directly overhead must be climbed, not sunk away from"
        );
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
