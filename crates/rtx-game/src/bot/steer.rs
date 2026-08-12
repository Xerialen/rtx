// SPDX-License-Identifier: AGPL-3.0-or-later

//! `run_bot`'s route-steering core: the disjoint `&nav` / `&mut bot` region.
//!
//! Everything from "we have a bot cell and a goal" through "we have a movement command" — teleport
//! invalidation, gate errands, the repath / banded-A*, leg advancement, the plat standoff, the
//! stuck/progress watchdogs, the bunnyhop policy verdicts + controller, the hook/rocket-jump leg
//! drivers, and the final steering/look/move composition. It runs entirely on `graph` (an immutable
//! `&NavGraph`) plus `&mut BotState` plus the all-`Copy` frame snapshot in [`SteerCtx`] — never
//! `&mut GameState`. That is exactly what lets `run_bot` hold the two disjoint borrows here and then
//! resume the `&mut game` spine (combat/grenade overlays, `emit`) once [`steer`] returns.

use glam::{Vec2, Vec3, Vec3Swizzles};

use super::*;
use crate::bot::state::{AirCommit, Commit, GateErrand, PlatWait, StallRecord};
use crate::bot::swim;
use crate::bsp::Bsp;
use crate::defs::{Weapon, BOT_MOVE_SPEED as MOVE_SPEED, BUTTON_ATTACK, BUTTON_JUMP};
use crate::game::cstring;
use crate::math::{angle_vectors, angles_to, wrap180, yaw_of};
use crate::nav_build::PlatStatus;
use crate::navmesh::{
    CellId, Corridor, LinkCosts, LinkKind, NavGraph, CLOSED_GATE_PENALTY, CURL_PSI_TOL, CURL_V_HOLD_TOL,
    RJ_UNFIT_PENALTY,
};
use crate::nearfield;
use rtx_nav::qphys::ORIGIN_TO_FEET;

/// The all-`Copy` frame snapshot `steer` reads: the [`Sense`] and [`Objective`] this frame, the
/// per-bot A* costs, and the live gate/plat state gathered before the borrow (see `run_bot`).
pub(super) struct SteerCtx<'a> {
    pub s: Sense,
    pub o: Objective,
    pub costs: LinkCosts<'a>,
    pub plat_status: &'a [PlatStatus],
    pub gate_ready: &'a [bool],
    pub bot_cell: CellId,
    pub goal_cell: CellId,
    pub race_line_ahead: Option<Vec3>,
    pub weapons_hot: bool,
    /// The collision hull for the live forward wall probe (bhop wall-avoidance). `None` = no BSP
    /// (degenerate/test map) → the probe reports open, same as off the live path.
    pub bsp: Option<&'a Bsp>,
}

/// What `steer` hands back to the spine: the frame's command (which the combat/grenade overlays then
/// mutate), the bhop/hook/rocket-jump driver outputs `emit` applies, and the two gates that decide
/// whether the overlays run.
pub(super) struct SteerOut {
    pub cmd: BotCmd,
    pub bhop_cmd: Option<bhop::Cmd>,
    pub hook: hook::HookDrive,
    pub rj: rj::RjDrive,
    /// Traversal-critical leg (hook/rj lock, airborne, or a gap/double/speed jump) — combat is
    /// locked out (`engage` owns movement and clears +jump).
    pub traversal_lock: bool,
    /// The grenade/rocket overlays may run (not hooking/rj/bhop-ing and not traversal-locked).
    pub overlays_ok: bool,
}

/// The LOD steer corridor toward `goal` — the interim search target plus its cluster window — or
/// `None` when lod is off or the goal is near enough to steer at directly. Shared by the main repath
/// and the one-shot gate-errand route so both bound a far target the same way.
fn corridor_to(graph: &NavGraph, from: CellId, goal: CellId, costs: &LinkCosts, lod: bool) -> Option<Corridor> {
    lod.then(|| graph.corridor(from, goal, costs, STEER_LOD_HORIZON))
        .flatten()
}

/// What finishing a plan costs *from where the bot actually is*, in the seconds A\* minimises.
///
/// The naive answer — sum the priced cost of the legs still ahead — is anchored at the current leg's
/// *source cell*, which a moving bot has already partly traversed. A freshly-planned route is
/// anchored at the bot. Comparing the two therefore compares one number that includes up to a whole
/// leg the bot has already covered against one that does not, and the error (~0.1-0.15s) is the same
/// size as both the difference between two competing plans and the switching margin. That is a
/// comparison that decides nothing, and it is why the incumbent kept losing to its own rival every
/// re-path: measured on dm3, 8- and 9-leg routes trading places while the bot swam a 35u box.
///
/// So the first leg's stored cost is replaced by the straight-line approach to where it *ends*,
/// priced at [`MAX_SPEED`] — the same basis as the search's own heuristic. Both candidates get that
/// treatment, so whatever the approximation costs in absolute accuracy it costs both sides equally,
/// and what remains is a like-for-like ordering.
fn remaining_cost(graph: &NavGraph, origin: Vec3, legs: &[u32], costs: &LinkCosts) -> f32 {
    let Some(&first) = legs.first() else {
        return 0.0;
    };
    let approach = (graph.cell_origin(graph.link_target(first)) - origin).length() / crate::navmesh::MAX_SPEED;
    approach + legs[1..].iter().map(|&l| graph.priced_link_cost(l, costs)).sum::<f32>()
}

/// Whether the route being executed is still one this bot can simply carry on with: it has legs
/// left, it was planned for the same goal, the bot is still *on* the leg it is flying, and that leg
/// is not one the bot has already condemned.
///
/// "On the leg" is measured against the leg's endpoints, not against the bot's resolved cell. A
/// swimmer is suspended between cells rather than standing on one, and its resolved cell flips
/// between neighbours as it moves — so an exact cell match rejects a perfectly good plan on most
/// frames, which is precisely the situation the hysteresis exists to survive.
///
/// The penalty check is what keeps the hysteresis from fighting the watchdogs. When a bot wedges,
/// the stuck detector surcharges the offending leg *so that the forced re-path diverts* — which
/// makes the diverted route legitimately more expensive than the wedged one. Tie-breaking toward the
/// incumbent there hands the bot straight back the plan it is stuck on, and the two mechanisms
/// deadlock: measured on dm3, a swimmer pressed into the east wall at x=1888 holding `forwardmove`
/// 740 at zero speed for seconds while the watchdog re-condemned the same leg every 0.7s. A plan we
/// have just struck is not a plan worth defending.
fn route_still_ours(graph: &NavGraph, bot: &BotState, origin: Vec3, goal: CellId, costs: &LinkCosts) -> bool {
    bot.goal_cell == Some(goal)
        && bot.route.get(bot.route_pos).is_some_and(|&l| {
            let near = |c| (graph.cell_origin(c) - origin).length() <= ROUTE_KEEP_RADIUS;
            (near(graph.link_source(l)) || near(graph.link_target(l)))
                && !costs.penalties.iter().any(|&(li, _)| li == l)
        })
}

/// How far from its current leg's endpoints a bot may be and still count as executing that leg.
/// Loose on purpose — a couple of grid steps — because the point is to keep a plan through the
/// ordinary drift of travel, not to police adherence to it.
const ROUTE_KEEP_RADIUS: f32 = 128.0;

/// Whether `route` crosses a link this bot is *priced out of* rather than merely charged for. The
/// capability penalties ([`RJ_UNFIT_PENALTY`], `CLOSED_GATE_PENALTY`) are deliberately finite so a
/// planner with no other option still returns something rather than stranding the bot — but that only
/// works when the caller can tell "expensive" from "cannot". Inside a cluster window there may be no
/// other option *in the window* while the map at large has a perfectly ordinary walk, so a finite wall
/// gets crossed on paper and then refused by the driver, every frame, forever.
fn priced_out(graph: &NavGraph, route: &[u32], costs: &LinkCosts) -> bool {
    costs.rocket_jump_extra >= RJ_UNFIT_PENALTY && route.iter().any(|&l| graph.link_kind(l) == LinkKind::RocketJump)
}

/// A plain (unbanded) route from `from` to `target`, restricted to the corridor `window` when present.
/// The abstract corridor is a real in-window fine path, so the restricted search normally succeeds.
///
/// Two results are treated as failures of the window rather than answers. An **empty** one is the
/// obvious case. The other is a route that only exists by crossing a capability wall the bot cannot
/// pay — an unfit bot handed a rocket-jump leg. Both mean "this window has nothing this bot can walk",
/// and both fall back to the unrestricted search, which on dm3 finds the ordinary 43-leg walk west.
/// Without the second case the bot flies a route its own driver refuses on arrival, clearing and
/// rebuilding the identical plan every other frame: a standstill that no watchdog breaks, because the
/// rocket-jump leg exempts the bot from the stuck and progress detectors while it lasts.
fn windowed_route(
    graph: &NavGraph,
    from: CellId,
    target: CellId,
    costs: &LinkCosts,
    window: Option<&[bool]>,
) -> Vec<u32> {
    let plain = match window {
        Some(w) => graph.find_path_within(from, target, costs, w),
        None => graph.find_path(from, target, costs),
    };
    let r = plain.unwrap_or_default();
    if window.is_some() && (r.is_empty() || priced_out(graph, &r, costs)) {
        return graph.find_path(from, target, costs).unwrap_or_default();
    }
    r
}

/// The closed gates whose shut volume sits near `origin` — the near-field's invalidation key (each a
/// bit) and, when it rebuilds, the door boxes it stamps unwalkable. A door opening/shutting nearby
/// flips a bit and forces a rebuild; the radius carries a recenter's slack so a bit stays stable under
/// sub-recenter movement (no per-frame churn). Gate ids are single-digit, so `1 << gi` fits a `u32`.
fn nearfield_gates<'a>(graph: &'a NavGraph, gate_closed: &'a [bool], origin: Vec3) -> impl Iterator<Item = usize> + 'a {
    let reach = nearfield::NEAR_HALF + nearfield::NEAR_RECENTER;
    (0..graph.gate_count()).filter(move |&gi| {
        gate_closed.get(gi).copied().unwrap_or(false) && {
            let g = graph.gate(gi);
            let nearest = origin.xy().clamp(g.closed_min.xy(), g.closed_max.xy());
            (nearest - origin.xy()).length() <= reach
        }
    })
}

/// The teleporter trigger volumes near `origin` that the bot is *not* trying to enter — the near-field
/// stamps these unwalkable so a leg running past one can't clip it.
///
/// A teleporter is a hole in the floor that doesn't look like one: the trigger is invisible to the
/// clip hull, so nothing in the ordinary steering stops a bot from brushing it. Where a walk corridor
/// runs alongside a trigger the clearance can be nil — aerowalk's y=416 corridor passes exactly one
/// half-width from the trigger face — and the slightest wobble throws the bot across the map, after
/// which it re-paths back into the same trigger, for ever.
///
/// The exception is the point of the exception: a volume containing the current waypoint is one the
/// route means to step into, and blocking that would make every teleporter unusable.
fn nearfield_teleports<'a>(graph: &'a NavGraph, origin: Vec3, waypoint: Vec3) -> impl Iterator<Item = usize> + 'a {
    let reach = nearfield::NEAR_HALF + nearfield::NEAR_RECENTER;
    graph
        .tele_volumes()
        .iter()
        .enumerate()
        .filter(move |(_, &(lo, hi))| {
            let nearest = origin.xy().clamp(lo.xy(), hi.xy());
            (nearest - origin.xy()).length() <= reach && !inside_box(waypoint, lo, hi)
        })
        .map(|(i, _)| i)
}

/// Whether `p` is within a box grown by the player's half-width — the same slack `nearfield::blocks`
/// applies, so "the waypoint is in this trigger" agrees with "this trigger blocks that column".
fn inside_box(p: Vec3, lo: Vec3, hi: Vec3) -> bool {
    let m = crate::navmesh::PLAYER_HALF_WIDTH;
    p.x >= lo.x - m && p.x <= hi.x + m && p.y >= lo.y - m && p.y <= hi.y + m && p.z >= lo.z - 32.0 && p.z <= hi.z + 32.0
}

/// How far down the route to look for dry land before deciding a swimmer is on its way out. A few
/// legs: near enough that a bot merely passing a bank mid-crossing is not diverted onto it, far enough
/// that the rim legs leading up to an exit still count as heading for it.
const EXIT_LEGS_AHEAD: usize = 4;

/// View pitch held while climbing out, in Quake's convention where negative looks up. Facing the
/// bank is what `PM_CheckWaterJump` reads; tilting up is what carries the bot over the lip after the
/// engine launches it.
const WATER_EXIT_PITCH: f32 = -45.0;

/// How much cheaper a freshly-planned route must be before it displaces the one being executed.
///
/// A repath runs every [`REPATH_INTERVAL`]; without a margin, two plans of near-equal cost trade
/// places every time the bot's resolved cell does. On a boundary between two cells whose best routes
/// leave in *opposite* directions — each route's first step carrying the bot back to the other cell —
/// that is a stable oscillation the bot cannot escape, and it is a tie being re-tossed 2.5 times a
/// second rather than any change in the world. Ties go to the plan already in progress.
///
/// Ten percent: comfortably above the jitter between two near-equal plans, well below the difference
/// a genuinely better route makes.
const ROUTE_SWITCH_GAIN: f32 = 0.90;

/// How far down the route the eyes look ahead of the feet, in legs. Enough that the view sweeps a
/// corridor instead of snapping to each 32u cell the bot steps through.
const EYE_LOOKAHEAD_LEGS: usize = 2;

/// How near the committed LOD interim counts as reached, releasing it so the corridor may advance.
/// A grid step and a half: close enough that the bot is plainly there, loose enough that it need not
/// land on the exact cell the coarse route happened to name.
const INTERIM_REACHED: f32 = 48.0;

/// How long a committed haul-out may run before the ordinary route steering gets its eyes back.
/// `PM_CheckWaterJump` fires within a stroke or two of facing the bank, so a climb still going after
/// this is one that is not going to happen — a lip too high, or a ledge that moved out of reach.
const WATER_EXIT_MAX: f32 = 1.5;

/// How many legs of ground corridor a walk certification rolls over. Twelve 32u legs is ~380u, well
/// past the ~130u a 40-tick rollout at walk speed can cover, so the horizon — not the polyline — is
/// what ends the roll.
const WALK_ROUTE_LEGS: usize = 12;

/// The route polyline a walk plan is certified against and then flown along: the current leg's source
/// cell, then the leading ground leg targets. Truncated at the first non-ground leg (unlike the hop
/// planner's raw leg walk) — pursuing a point past a plat, teleport or jump target would drag the feet
/// at ground the bot must not walk toward.
///
/// Anchored at the leg source rather than at the bot, deliberately, and everything downstream depends
/// on it. It is what lets the caller measure how far *off* its route the bot has drifted — a line
/// starting under the bot's feet says it is always on it — and it is what keeps the plan's lateral
/// offset meaningful: a self-anchored line bends to follow the bot, so offsetting from it walks the
/// bot steadily further out, chasing its own displacement. `None` when there's no ground corridor.
fn walk_line_pts(graph: &NavGraph, bot: &BotState, cur_leg: Option<u32>) -> Option<Vec<Vec3>> {
    let src = graph.cell_origin(graph.link_source(cur_leg?));
    let pts: Vec<Vec3> = std::iter::once(src)
        .chain(ground_leg_targets(graph, &bot.route, bot.route_pos).take(WALK_ROUTE_LEGS))
        .collect();
    (pts.len() >= 2).then_some(pts)
}

/// The lip is "right here" — inside this distance the takeoff jump must fire *now* or the bot wedges
/// against the step face; beyond it the run-up gate applies.
const JUMP_NOW_DIST: f32 = 40.0;

/// How far ahead the takeoff looks for the end of the floor. Three grid columns: far enough that the
/// number is recorded (and readable in the audit) well before it matters, short enough to stay a
/// handful of point probes on the frames that run it.
const LIP_LOOKAHEAD: f32 = 96.0;
/// Frames of travel (plus [`LIP_PAD`]) of remaining floor at which a jump leg stops waiting for its
/// run-up and takes off regardless. Two, not one: the bot's own frame is not the engine's, and pmove
/// can advance the player more than once between commands, so a one-frame window is not reliably the
/// last frame that can still jump.
const LIP_FRAMES: f32 = 2.0;
const LIP_PAD: f32 = 4.0;
/// How aligned with the leg the travel must be for the lip commitment to fire: merely *toward* it.
///
/// Only the sign is load-bearing, and deliberately so. The tempting reading is that a badly aligned
/// takeoff is not worth making, but at a vanishing lip the alternative is not a better jump — it is
/// walking into the gap, which has no air control and no chance. dm3's stair crest is the case that
/// settled it: the cursor turns onto the jump leg with the velocity pointing 97° *away*, the bot swings
/// 45° in the 0.08s of tread it has left, and arrives at the lip 62° off (0.46 of its speed toward the
/// target) — under a 60° cone by a hair. Jumping there lands it: the arc keeps air-strafing to 229 ups
/// toward the target and the platform's near edge is 76u away. Refusing there dropped it 300u to the
/// floor and cost a full climb back, which is what made the route time out. So the only takeoff this
/// rejects is one heading *away* from the leg, where a leap would carry the bot further from safety.
const LIP_ALIGN_COS: f32 = 0.0;

/// A fast Walk/Step can cross a 32u waypoint between frames while its bhop lobe is laterally offset.
/// Treat crossing the waypoint's forward plane as progress while still inside this corridor. Without
/// this, missing the old 64u radial gate leaves the route pointing behind the bot and the controller
/// can make a destructive U-turn toward a stale cell.
const FAST_WAYPOINT_CORRIDOR: f32 = 96.0;

fn ground_waypoint_arrived(origin: Vec2, source: Vec2, target: Vec2, speed: f32, frametime: f32) -> bool {
    let to = target - origin;
    let arrive_r = ARRIVE_RADIUS.max(2.0 * speed * frametime);
    if to.length() <= arrive_r {
        return true;
    }
    if speed <= 0.0 {
        return false;
    }
    let along = (target - source).normalize_or_zero();
    if along == Vec2::ZERO || (origin - target).dot(along) < 0.0 {
        return false;
    }
    let lateral = (origin - target) - along * (origin - target).dot(along);
    lateral.length() <= FAST_WAYPOINT_CORRIDOR
}

/// Whether a plain jump leg (`JumpGap`/`DoubleJump`) may fire its takeoff jump this frame. Applying
/// forward *after* the leap barely helps in QW air physics, so the speed must already be carried
/// *toward the waypoint* before jumping — gate on the velocity component along `to_wp` reaching
/// `frac · maxspeed`. Two escapes keep a bot from wedging: the lip is within [`JUMP_NOW_DIST`] (jump
/// now), or the gate is off (`frac <= 0`). `frac` is `rtx_jump_runup`.
fn jump_runup_ok(v_xy: Vec2, to_wp: Vec2, dist: f32, frac: f32, maxspeed: f32) -> bool {
    if frac <= 0.0 || dist < JUMP_NOW_DIST {
        return true;
    }
    v_xy.dot(to_wp.normalize_or_zero()) >= frac * maxspeed
}

/// How long a committed chained speed-jump leg's first velocity reading gets to settle before the
/// leg-hold chain-entry guard judges it (see [`chain_entry_hold_expired`]) — a landing carries real
/// speed the instant physics applies it, but the very first frame or two can read low from settling
/// noise. Well under [`STUCK_TIME`] (0.7s), the fastest existing watchdog that would otherwise fire
/// first on a chained leg held in place.
const CHAIN_ENTRY_GRACE: f32 = 0.3;

/// Whether the leg-hold chain-entry guard should divert *this tick*: a leg is committed (`commit`),
/// grounded (not mid-leap — diverting an airborne bot is meaningless, it's already committed to the
/// jump's physics), past its settling grace, and still [`NavGraph::chain_entry_blocked`] at the
/// bot's real speed (`blocked`, precomputed by the caller since it needs the graph).
///
/// The timing/state half of the check, split out from the graph lookup the same way
/// [`crate::navmesh::NavGraph::chain_entry_leg_ok`] splits the speed predicate from
/// `chain_entry_exclusions` — so the grace window and the ground/commit gating are directly
/// testable without a live `NavGraph`. See the call site for why this exists *in addition to* the
/// leg-transition guard: a chained jump committed at a borderline speed has no runway to build the
/// rest on, so without this it just holds until an existing watchdog eventually notices.
fn chain_entry_hold_expired(commit: Option<Commit>, now: f32, on_ground: bool, blocked: bool) -> bool {
    on_ground && blocked && commit.is_some_and(|c| now - c.since > CHAIN_ENTRY_GRACE)
}

/// Whether the curl too-slow abort should fire. With the grounded gate disabled, this preserves the
/// legacy speed-only predicate; with it enabled, airborne frames cannot abort the leg.
fn sj_abort_should_fire(on_ground: bool, gate: bool, predicted: f32, v_req: f32) -> bool {
    (!gate || on_ground) && predicted < v_req * 0.85
}

/// How many legs ahead the winding gate looks, and the total heading change (degrees) over them that
/// counts as "too tight to hop". A straight corridor reads ~0; a spiral staircase's curved treads
/// read tens of degrees per leg.
const WINDING_LOOKAHEAD: usize = 4;
const WINDING_LIMIT: f32 = 60.0;

/// Total absolute heading change (degrees) of the route over the next [`WINDING_LOOKAHEAD`] legs,
/// measured from `origin` through each leg's target cell. The conservative measure: it counts every
/// kink — including the bot's own approach onto the first target, which is what reads dm3's stair
/// crest (approach north, route west) before the hop is airborne — so it errs toward walking. Gates
/// bhop *entry* only: a chain not yet started loses nothing by waiting a few cells for a corner to
/// resolve, while a chain in flight is judged by the steadier [`route_turn`].
fn route_turn_sum(graph: &NavGraph, route: &[u32], pos: usize, origin: Vec3) -> f32 {
    let mut prev = origin.xy();
    let mut last_dir: Option<Vec2> = None;
    let mut total = 0.0f32;
    for &leg in route.iter().skip(pos).take(WINDING_LOOKAHEAD) {
        let tgt = graph.cell_origin(graph.link_target(leg)).xy();
        let dir = (tgt - prev).normalize_or_zero();
        if dir == Vec2::ZERO {
            continue;
        }
        if let Some(prev_dir) = last_dir {
            total += prev_dir.dot(dir).clamp(-1.0, 1.0).acos();
        }
        last_dir = Some(dir);
        prev = tgt;
    }
    total.to_degrees()
}

/// Largest heading deviation (degrees) of the route over the next [`WINDING_LOOKAHEAD`] legs: the
/// bot's travel direction and each inter-cell segment, measured against the first inter-cell
/// direction. Gates *sustaining* a live hop chain across a landing, where [`route_turn_sum`] is too
/// jumpy to be trusted: summing successive turns reads a lattice dogleg — the two 45° kinks where
/// A* reconciles grid columns on an arrow-straight runway — as a 90° hairpin, and that verdict
/// lands exactly when the 0.4s repath has re-anchored the route mid-run, dumping a full-speed hop
/// chain onto ground friction for nothing. Any single segment of the dogleg deviates at most 45°
/// from the corridor's own heading, while a hairpin (~180°), a zigzag weave (90°), and a spiral's
/// steadily rotating treads (past 60° within the window) all still trip the gate.
///
/// The approach term is the *velocity*, not origin→first-target: the cursor's current cell can sit
/// nearly abeam of a slaloming bot (9u ahead, 30u aside was measured live), and the line to it then
/// points sideways off a perfectly straight corridor. Velocity heading stays within the slalom's
/// ±45° envelope on a straight run, and still betrays a hairpin sitting right at the bot's feet
/// (the remaining window points back against the travel direction).
fn route_turn(graph: &NavGraph, route: &[u32], pos: usize, v_xy: Vec2, look: usize) -> f32 {
    // Only directions between two cell origins count — the origin-anchored first segment is the
    // abeam-noise this function exists to ignore, so it seeds `prev` and nothing else.
    let mut prev: Option<Vec2> = None;
    let mut reference: Option<Vec2> = None;
    let mut max_dev = 0.0f32;
    for &leg in route.iter().skip(pos).take(look) {
        let tgt = graph.cell_origin(graph.link_target(leg)).xy();
        let Some(p) = prev else {
            prev = Some(tgt);
            continue;
        };
        let dir = (tgt - p).normalize_or_zero();
        if dir == Vec2::ZERO {
            continue;
        }
        prev = Some(tgt);
        match reference {
            None => {
                reference = Some(dir);
                let travel = v_xy.normalize_or_zero();
                if travel != Vec2::ZERO {
                    max_dev = travel.dot(dir).clamp(-1.0, 1.0).acos();
                }
            }
            Some(r) => max_dev = max_dev.max(r.dot(dir).clamp(-1.0, 1.0).acos()),
        }
    }
    max_dev.to_degrees()
}
/// The per-frame constants every stall record shares, snapshotted once in [`steer`] so the five
/// watchdogs can log a firing by copying — no watchdog computes anything it wouldn't otherwise.
struct StallFrame {
    now: f32,
    origin: Vec3,
    cell: CellId,
    goal_cell: CellId,
    goal_dist: f32,
    speed: f32,
}

/// Park a watchdog firing on the bot (see [`StallRecord`]). Call it *before* clearing the route:
/// the record is meant to say which leg of which route failed, not what was left afterwards.
fn note_stall(
    bot: &mut BotState,
    f: &StallFrame,
    reason: &'static str,
    action: &'static str,
    link: Option<u32>,
    kind: Option<LinkKind>,
) {
    let (route_len, route_pos) = (bot.route.len() as u32, bot.route_pos as u32);
    bot.push_stall(StallRecord {
        t: f.now,
        reason,
        action,
        origin: f.origin,
        cell: f.cell,
        goal_cell: f.goal_cell,
        goal_dist: f.goal_dist,
        link,
        kind,
        speed: f.speed,
        route_len,
        route_pos,
    });
}

pub(super) fn steer(graph: &NavGraph, bot: &mut BotState, ctx: SteerCtx) -> SteerOut {
    let SteerCtx {
        s,
        o,
        costs,
        plat_status,
        gate_ready,
        bot_cell,
        goal_cell,
        race_line_ahead,
        weapons_hot,
        bsp,
    } = ctx;
    let Sense {
        host,
        now,
        frametime,
        origin,
        v_angle,
        client,
        weapon,
        on_ground,
        in_water,
        submerged,
        air_left,
        vz,
        air_jumped,
        enemy_seen_time,
        v_xy,
        speed,
        grapple_hook,
        has_grapple,
        hook_out,
        on_hook,
        anchor,
        reel_half_step,
        attack_finished,
        has_rl,
        ammo_rockets,
        health,
        armortype,
        armorvalue,
        quad,
        ..
    } = s;
    let Objective {
        hooking,
        on_sj,
        on_rj,
        enemy,
        chasing,
        polite,
        vigil,
        target_origin,
        watch_point,
        ..
    } = o;
    let gate_closed = costs.gate_closed;
    bot.replanned = false; // per-frame; the repath block below stamps it if one runs

    // Plain-jump commitment is normally pre-armed before objective resolution. Remember the first
    // physical airborne frame here; route kind/position is intentionally irrelevant to release.
    if !on_ground {
        if let Some(c) = bot.air.as_mut() {
            c.airborne = true;
        }
    }
    // Puppet rocket-jump order (test harness, see [`crate::control`]): pin the route to the single
    // ordered link so the repath / leg-advance / errand logic below can't clobber the one-leg route
    // the rocket-jump driver flies. Folded into `route_frozen` below, so every `!route_frozen` guard
    // also respects the pin. A RocketJump link never auto-advances (its driver advances on landing),
    // so the leg stays put until the attempt finishes and the control poller lifts the order. Goto/Hold
    // orders leave `order_link` None and route normally. Rebuilds only when the route isn't already it.
    let pinned = o.order_link.is_some();
    if let Some(link) = o.order_link {
        if bot.route.len() != 1 || bot.route.first() != Some(&link) {
            bot.route = vec![link];
            bot.route_bands = vec![0];
            bot.route_pos = 0;
            bot.goal_cell = Some(graph.link_target(link));
        }
    }
    // Incoming commitment (reads the route state *before* this frame's displacement handler): a >200u
    // jump while hooking, on a speed/rocket jump, riding a plain-jump arc, or pinned is that traversal
    // moving fast on purpose — not a teleport — so the handler below must leave the route alone.
    let frozen_pre = hooking || on_sj || on_rj || bot.air.is_some() || pinned;

    // A teleport (or any large instant displacement) invalidates the planned route — drop it and
    // re-path from where we landed. ~200u in one frame is far beyond running/falling. Skipped mid-hook:
    // the reel and the parabola move fast on purpose and must not clear the hook route.
    //
    // Exception — a *launch* teleporter: it flings you out airborne carrying the exit velocity, and the
    // ballistic arc lands on the far ledge the navmesh linked as the leg's target. Re-pathing from
    // mid-air instead localizes to whatever floor cell sits under the apex and air-steers off the ledge,
    // so the bot sails past the destination. When the leg we were walking into is a Teleport and we came
    // out airborne, commit to that target as an air arc (released on landing, like a jump leg) so the
    // air-strafe below curves us onto it. A teleport that drops you standing (`on_ground`) still clears
    // and re-paths, exactly as before.
    if !frozen_pre && bot.watchdog.last_origin != Vec3::ZERO && (origin - bot.watchdog.last_origin).length() > 200.0 {
        let launch = bot
            .route
            .get(bot.route_pos)
            .filter(|&&l| graph.link_kind(l) == LinkKind::Teleport && !on_ground)
            .map(|&l| (l, graph.link_target(l)));
        if let Some((leg, target)) = launch {
            bot.air = Some(AirCommit {
                leg,
                target,
                since: now,
                airborne: true,
            });
        } else {
            // A grounded teleport exit clears the route here. Stamp the just-ridden pad (and the reverse
            // pad by the exit) into the re-entry surcharge ring so a shuttle re-prices itself, and — under
            // debug — log it (two `tele` lines from one bot within a few seconds is a round-trip).
            if let Some(&leg) = bot.route.get(bot.route_pos) {
                if graph.link_kind(leg) == LinkKind::Teleport {
                    stamp_tele_reuse(bot, graph, leg, origin, now);
                    if host.cvar_bool(c"rtx_bot_debug") {
                        host.conprint(&cstring(&format!("rtx bot{client}: tele leg={leg}\n")));
                    }
                }
            }
            bot.route.clear();
            bot.repath_time = now;
        }
    }
    bot.watchdog.last_origin = origin;

    // Settle the commitment view for the rest of the frame. `on_air`/`route_frozen` now include a
    // launch-teleport arc just latched above, so the repath / gate / leg-advance logic all treat it as a
    // committed airborne traversal and won't yank the route out from under it. (A goal flip mid-arc must
    // not replace the route and turn the bot around.) Plain jumps used to be a collection of separate
    // `!on_air` guards, leaving holes such as gate errands; one ownership bit closes those seams.
    let on_air = bot.air.is_some();
    let route_frozen = hooking || on_sj || on_rj || on_air || pinned;
    // Read the LOD toggle once for the whole steer pass (errand reachability bound, repath corridor,
    // far gate pre-arm, errand route) — one engine cvar lookup, one consistent value across the frame.
    let lod = host.cvar_bool(c"rtx_bot_lod");

    // Gate errand: drop it once the gate's door has opened — or give up if we stop making progress
    // toward its button (stuck at a door whose button we can't actually reach), so we don't camp
    // there. Progress-based, not a flat timeout: a button that's simply far across the map (e.g.
    // when we spawned right next to the door) still gets reached. Suspended mid-hook.
    if !route_frozen {
        if let Some(errand) = bot.gate.errand {
            let gi = errand.index;
            let give_up = |bot: &mut BotState| {
                bot.gate.avoid = Some((gi, now + GATE_AVOID_TIME));
                bot.gate.errand = None;
                bot.route.clear();
                bot.repath_time = now;
            };
            if gate_closed.get(gi).copied() != Some(true) {
                bot.gate.errand = None; // door opened — done
                bot.route.clear();
                bot.repath_time = now;
            } else if now >= bot.repath_time && !button_reachable(graph, bot_cell, gi, &costs, lod) {
                // Re-verify the button is reachable without crossing this very gate (the arenazap
                // chicken-and-egg case) only at the repath cadence, not every frame: `button_reachable`
                // runs a whole-graph search, and the far pre-arm now routinely aims errands at distant
                // buttons. A door that opens is caught above every frame, and a bot that stops making
                // progress toward a now-unreachable button is caught by the give-up clock below, so a
                // ≤`REPATH_INTERVAL` lag on the topology-flip case is harmless.
                give_up(bot); // button is walled off behind this very gate — route around instead
            } else {
                let d = (graph.cell_origin(graph.gate(gi).button_cell).xy() - origin.xy()).length();
                if d < errand.best_dist - 4.0 {
                    let e = bot.gate.errand.as_mut().unwrap();
                    e.best_dist = d; // got closer — reset the give-up clock
                    e.since = now;
                } else if now - errand.since > GATE_GIVEUP_TIME {
                    give_up(bot); // no progress toward a reachable button — stuck; try elsewhere
                }
            }
        }
    }

    // Effective goal: the human, or — while opening a gate — that gate's button.
    let goal = match bot.gate.errand {
        Some(errand) => graph.gate(errand.index).button_cell,
        None => goal_cell,
    };

    // Re-path when the route is empty, the goal changed, or the timer elapsed. Frozen mid-hook, on a
    // speed/rocket jump, or committed to a plain jump arc, so the traversal keeps the route that put
    // it on that leg (a goal flip mid-air must not replace the route and turn the bot around).
    if !route_frozen && !on_air && (bot.route.is_empty() || bot.goal_cell != Some(goal) || now >= bot.repath_time) {
        // Speed-band planning credits the speed a bot carries between legs (chained speed jumps,
        // cheaper hot Walk legs) — gated on bhop being on (no speed-jump links otherwise) plus its
        // own escape-hatch cvar. `speed` seeds the start band, so a mid-run re-path keeps a hop
        // chain alive. Falls back to the plain cell A* (bands all-zero) when off.
        let use_bands = host.cvar_bool(c"rtx_bot_bhop") && host.cvar_bool(c"rtx_bot_bandplan");
        // Chain-entry gate (`rtx_bot_chain_entry_gate`, on by default): surcharge, for *this search
        // only*, any chained speed jump leaving `bot_cell` the bot's real speed can't carry (see
        // `NavGraph::chain_entry_exclusions` for why the banded planner's own feasibility check
        // misses exactly this case). Shadows `costs` for the rest of this repath block — the corridor,
        // banded/plain search and the priced-out fallback all see the exclusion; nothing outside this
        // block (the gate-errand route, the watchdogs) does. A bot that later reaches the same cell
        // carrying real speed gets the ordinary, unexcluded query.
        let chain_gate_penalties: Vec<(u32, f32)>;
        let costs = if host.cvar_bool(c"rtx_bot_chain_entry_gate") {
            let excluded: Vec<u32> = graph.chain_entry_exclusions(bot_cell, speed).collect();
            if excluded.is_empty() {
                costs
            } else {
                chain_gate_penalties = costs
                    .penalties
                    .iter()
                    .copied()
                    .chain(excluded.into_iter().map(|li| (li, CLOSED_GATE_PENALTY)))
                    .collect();
                LinkCosts {
                    penalties: &chain_gate_penalties,
                    ..costs
                }
            }
        } else {
            costs
        };
        // Where can we actually head? Unreachability is pure topology (every dynamic cost term is
        // finite — see `navmesh::reach`), so resolve the target *before* searching instead of
        // discovering a dead goal by watching a whole-graph search exhaust and then flooding to find
        // the nearest reachable cell. A goal behind a shut door with no way around, or in a
        // disconnected pocket, redirects to the reachable cell nearest it — the bot heads as far
        // toward the target as the graph allows (often enough for line of sight) rather than homing
        // into a wall.
        let target = if graph.reachable(bot_cell, goal) {
            goal
        } else {
            graph.nearest_reachable_to(bot_cell, goal).unwrap_or(goal)
        };
        // LOD steer corridor: for a far target, aim the fine search at an interim portal ~a few seconds
        // along the coarse route *and* restrict expansion to the corridor's clusters — so even a
        // band-infeasible exhaustion stays a local neighbourhood instead of draining the whole
        // cells×NBANDS space. The abstract corridor is a real fine path through those clusters, so a
        // route to the interim always exists in the window. The next repath advances it; `bot.goal_cell`
        // stays the true `goal`, so change detection and the gate/give-up logic are untouched. Off →
        // today's exact whole-graph search to the target.
        let corridor = corridor_to(graph, bot_cell, target, &costs, lod);
        // Commit to the interim once chosen, rather than accepting a fresh one each repath.
        //
        // `corridor` derives the interim from the bot's *current* cell, and on a cluster boundary the
        // two cells the bot is straddling hand back interims in opposite directions. The bot then
        // swims at the first, crosses back over the boundary, is handed the second, swims at that,
        // and so on: measured on dm3's pool as 4539/4537 alternating every ~0.7s for nine seconds,
        // the bot tracing a 136u box and arriving nowhere. A waypoint you re-pick faster than you can
        // reach it is not a waypoint.
        //
        // Held while it is still the same goal, still inside the fresh window (so the restricted
        // search can reach it), and not yet arrived at.
        let (search_target, window) = match &corridor {
            Some(c) => {
                // Deliberately *not* conditioned on the fresh window containing it: the window is
                // rebuilt from the bot's current cell, so it is exactly the thing that changes as the
                // bot straddles the boundary, and requiring membership drops the commitment on the
                // frames it exists to survive. `windowed_route` already falls back to an unrestricted
                // search when the window cannot reach the target.
                //
                // Released on *progress* as well as distance, and that second release matters more
                // than it looks. Held to the 48u test alone the corridor only advances once the bot is
                // almost on top of the interim, so the route it plans keeps shrinking to one or two
                // legs — and a route that short is shorter than the eyes look ahead, which drops the
                // view onto the distant objective over and over. Advancing the corridor while a few
                // legs still remain keeps the interim the few seconds ahead it was meant to be. It is
                // monotone in progress, so it cannot bring back the boundary flip the commitment
                // exists to prevent.
                //
                // **In water only**, and that scoping is the whole of it. The boundary flip this
                // settles is a swimmer's problem: two cells a few units apart in a layered pool whose
                // corridors point opposite ways. On land the same commitment is actively harmful, and
                // in three ways that all showed up as "movement weirdness". It caps the fine route at
                // the held interim, so the route shrinks toward a couple of legs — and `runway()`
                // measures over the route's leading ground legs, so the runway collapses and the bhop
                // controller never becomes eligible: the bot *runs* down corridors it should be
                // hopping. At hop speed a repath interval covers some 280 units against a 48-unit
                // release radius, so the bot sails past the thing it is holding and the route points
                // *behind* it — the turning round for no reason. And a route truncated across a jump's
                // legs starves the run-up and pre-arm sequencing that makes the jump work at all, which
                // is the fumbled gap jump on the way to the red armour. None of that is a tie needing
                // settling; it is a corridor being held still while the bot moves.
                let legs_left = bot.route.len().saturating_sub(bot.route_pos);
                let keep = bot.interim.filter(|&(g, i)| {
                    in_water
                        && g == target
                        && i != bot_cell
                        && (graph.cell_origin(i) - origin).length() > INTERIM_REACHED
                        && legs_left > EYE_LOOKAHEAD_LEGS
                });
                let interim = keep.map_or(c.interim, |(_, i)| i);
                bot.interim = Some((target, interim));
                (interim, Some(c.allowed.as_slice()))
            }
            None => {
                bot.interim = None;
                (target, None)
            }
        };
        // Flight recorder: a repath ran, this is the goal it was given, and this is what it actually
        // searched to after reachability redirection and any corridor truncation.
        bot.replanned = true;
        bot.route_goal = Some(goal);
        bot.route_target = Some(search_target);
        let banded = use_bands
            .then(|| match window {
                Some(w) => graph.find_path_banded_within(bot_cell, search_target, speed, &costs, w),
                None => graph.find_path_banded(bot_cell, search_target, speed, &costs),
            })
            .flatten();
        let (mut route, mut bands) = match banded {
            Some(r) => (r.links, r.bands),
            // Banded came back empty ⇒ band-infeasible (a route that only exists through a speed-jump
            // chain the carried speed can't satisfy) or bands off; the plain A* ignores bands and finds
            // the reachable target (windowed, with the unrestricted fallback — see [`windowed_route`]).
            None => (
                windowed_route(graph, bot_cell, search_target, &costs, window),
                Vec::new(),
            ),
        };
        // A window whose only answer crosses a capability wall the bot cannot pay is a window with
        // nothing this bot can walk — re-ask unrestricted, and at the *real* target rather than the
        // corridor's interim, since the interim is the choice the bad window made and reusing it walks
        // straight back into the trap. The banded search needs the same guard as the plain one: it is
        // the arm that actually runs, and it has no idea the driver will refuse what it planned.
        if priced_out(graph, &route, &costs) {
            route = graph.find_path(bot_cell, target, &costs).unwrap_or_default();
            bands = Vec::new();
        }
        // Keep `route_bands` parallel to `route`: zero-fill when unbanded (or on any length mismatch).
        if bands.len() != route.len() {
            bands = vec![0u8; route.len()];
        }
        // Hysteresis: a new plan has to be materially cheaper to displace the one in progress.
        //
        // Re-planning every `REPATH_INTERVAL` answers "what is best *from here*", and the answer can
        // change simply because the bot moved a cell. Where two routes are near-equal and leave in
        // opposite directions, each one's first leg carries the bot into the cell that prefers the
        // other, so the bot alternates plans a few times a second and travels nowhere. Measured on
        // dm3's pool: 8- and 9-leg routes swapping with cells 4381/4383, the bot swimming a 35u
        // back-and-forth for four seconds at a stretch.
        //
        // Nothing here overrides a *reason* to re-plan — a changed goal, a route we have walked off,
        // an emptied or priced-out plan all fail `route_still_ours` or leave `route` cheaper. This
        // only settles ties, and it settles them in favour of committing.
        //
        // **In water only.** Ties that near-equal are a property of swimming: a layered pool offers
        // many 3D paths of the same price, so the answer really does change under a bot that has
        // merely moved. Dry ground rarely does that — its plans differ by whole traversals — and the
        // ground watchdogs, run-up measurement and jump pre-arming are all built expecting the plan a
        // fresh search returns. Keeping a stale one there buys nothing and quietly breaks them.
        if in_water && !route.is_empty() && route_still_ours(graph, bot, origin, goal, &costs) {
            let fresh = remaining_cost(graph, origin, &route, &costs);
            let current = remaining_cost(graph, origin, &bot.route[bot.route_pos..], &costs);
            if fresh > current * ROUTE_SWITCH_GAIN {
                route = bot.route[bot.route_pos..].to_vec();
                bands = bot
                    .route_bands
                    .get(bot.route_pos..)
                    .map_or_else(Vec::new, <[u8]>::to_vec);
                if bands.len() != route.len() {
                    bands = vec![0u8; route.len()];
                }
            }
        }
        // Empty-route telemetry (C6): a resolved repath that came back with no legs while the bot isn't
        // already at its goal — the "parked in place" signature. `corr` shows whether a corridor was in
        // play (a same-cluster/near None vs a windowed search that found nothing).
        if host.cvar_bool(c"rtx_bot_debug") && route.is_empty() && bot_cell != target {
            host.conprint(&cstring(&format!(
                "rtx bot{client}: route=0 corr={} tgt_eq_goal={}\n",
                corridor.is_some(),
                target == goal
            )));
        }
        bot.route = route;
        bot.route_bands = bands;
        bot.route_pos = 0;
        bot.goal_cell = Some(goal);
        // Remember the gates the corridor crosses beyond the interim window (nearest first), so the
        // gate-errand block can pre-arm for a far shut door the truncated route won't reveal (see
        // [`GateState`]). Empty when there's no corridor, which also clears any previous list.
        bot.gate.corridor_gates = corridor.as_ref().map_or_else(Vec::new, |c| c.far_gates.clone());
        bot.repath_time = now + REPATH_INTERVAL;
        // Restart the progress watchdog against the new route (INFINITY ⇒ the first frame records the
        // real starting distance rather than reading as an instant stall on an old baseline); rebase
        // the climb baseline to here so a stale high-water mark from a previous route can't suppress it.
        bot.watchdog.progress_best = f32::INFINITY;
        bot.watchdog.climb_best = origin.z;
        bot.watchdog.progress_since = now;
    }
    // If we've fallen off the planned route (missed a jump, got shoved), re-localize next.
    if !route_frozen && !on_air && bot.route_pos >= bot.route.len() && bot_cell != goal && now >= bot.repath_time {
        bot.repath_time = now; // force a fresh path next frame
    }

    // Not on an errand yet? `find_path` already routes *around* a shut gate when it can (its links
    // are priced high), so if the chosen route still crosses one, there's no other way in — divert
    // to that gate's button. Skip a gate we recently gave up on (its button was unreachable) so we
    // don't immediately re-camp on it.
    if !route_frozen && !on_air && bot.gate.errand.is_none() {
        // Skip a gate we recently gave up on, while its avoid window is still open.
        let avoid = bot.gate.avoid.filter(|&(_, until)| now < until).map(|(gi, _)| gi);
        // A shut gate on the current route, or — under LOD, where the route stops at the interim — the
        // first still-shut gate the corridor crosses further along, in route order (the far pre-arm,
        // matching exact mode's full-route detection). `route_blocking_gate` takes priority (the more
        // precise near case). The far list is consulted only while lod is on, so a mid-game
        // `rtx_bot_lod 0` can't act on a stale corridor.
        let far = lod
            .then(|| {
                bot.gate
                    .corridor_gates
                    .iter()
                    .copied()
                    .find(|&gi| gate_closed.get(gi as usize).copied().unwrap_or(false) && Some(gi as usize) != avoid)
            })
            .flatten()
            .map(|gi| gi as usize);
        let block = route_blocking_gate(graph, &bot.route, bot.route_pos, gate_closed)
            .filter(|&gi| Some(gi) != avoid)
            .or(far);
        if let Some(gi) = block {
            if button_reachable(graph, bot_cell, gi, &costs, lod) {
                let button_cell = graph.gate(gi).button_cell;
                // first frame records the starting distance (best_dist starts at +inf)
                bot.gate.errand = Some(GateErrand {
                    index: gi,
                    best_dist: f32::INFINITY,
                    since: now,
                });
                // Bound this one-shot errand route the same way the main repath does (subsequent
                // repaths target the button as `goal` and corridor-bound it); a far button on a big
                // map would otherwise be one unbounded whole-graph A*.
                let corridor = corridor_to(graph, bot_cell, button_cell, &costs, lod);
                let (search_target, window) = match &corridor {
                    Some(c) => (c.interim, Some(c.allowed.as_slice())),
                    None => (button_cell, None),
                };
                bot.route = windowed_route(graph, bot_cell, search_target, &costs, window);
                bot.route_bands = vec![0u8; bot.route.len()]; // a walking errand, no carried speed
                bot.route_pos = 0;
                bot.goal_cell = Some(button_cell);
                bot.repath_time = now + REPATH_INTERVAL;
            } else {
                // Button is walled off behind this gate — don't chase it; avoid the gate so
                // route_blocking_gate stops re-selecting it and find_path routes around the pillar.
                bot.gate.avoid = Some((gi, now + GATE_AVOID_TIME));
            }
        }
    }

    // Advance past route legs we've already reached. A plat leg completes when we've *risen*
    // to the exit height (Z), not on XY arrival — we're standing still on the lift while it
    // carries us up, so XY barely changes.
    // A bunnyhopping bot covers ground fast enough to orbit a 24u waypoint, so widen the arrival gate.
    // Walk/Step additionally advances on a bounded crossing of the target plane: at 700+ ups a wide
    // lobe can pass a cell outside the old 64u radial fallback in one tick. Leaving that cell current
    // points the corridor behind the bot and turns an otherwise healthy slalom into a U-turn.
    let arrive_r = if bot.bhop.phase != bhop::Phase::Off || bot.sj.is_some() {
        ARRIVE_RADIUS.max(2.0 * speed * frametime)
    } else {
        ARRIVE_RADIUS
    };
    // While committed to a plain jump arc and still airborne, don't advance the leg: keep `kind` and
    // the waypoint pinned to the jump so steering stays on the landing point and the air-jump
    // undershoot recovery keeps firing (the leg advances naturally once we land). Like Hook/RocketJump,
    // whose drivers advance on landing, not on passing the target XY.
    // The leg timer belongs to the *current leg*, not the frame loop: any route change
    // (repath, gate errand, stall clear) swaps the leg out from under the clock, and the
    // stale age must not be billed to whichever leg comes next.
    let cur_leg = bot.route.get(bot.route_pos).copied();
    if cur_leg != bot.leg_timed {
        bot.leg_timed = cur_leg;
        bot.leg_age = 0.0;
    }
    if cur_leg.is_some() {
        bot.leg_age += frametime;
    }
    while (on_ground || (!on_air && !on_sj)) && bot.route_pos < bot.route.len() {
        let leg = bot.route[bot.route_pos];
        let target = graph.cell_origin(graph.link_target(leg));
        let arrived = match graph.link_kind(leg) {
            LinkKind::Plat => origin.z >= target.z - PLAT_RISE_TOL,
            // Depth counts on a swim leg, for the same reason it does on a plat: the climb from the
            // pool floor up to the surface ring is the *same column*, so an XY-only test calls it
            // arrived before the bot has risen an inch. The route then walks on along the rim
            // arrived before the bot has risen an inch. The route then walks on along the rim while the
            // bot is still on the bottom — at the wrong depth for every leg that follows, which is
            // exactly how a pillar the rim route goes cleanly around becomes something to swim into.
            LinkKind::Swim => (target - origin).length() <= arrive_r,
            // A hook leg never auto-advances on XY: a near-vertical pull-up passes the XY test while
            // still at the *bottom* of the swing. The hook driver advances it only once the parabola
            // has landed (see below).
            LinkKind::Hook => false,
            // Same for a rocket jump — its driver advances on landing, not on passing the target XY.
            LinkKind::RocketJump => false,
            // A speed jump crossing a big height difference must not advance on XY alone: from
            // an overhang the XY test passes while the bot is still a full storey above the
            // target, route_pos runs ahead of physics, and the replanner oscillates forever at
            // the lip (measured: dm3 cell 1373 -> link 48212, stuck at z=264 over a z=56 target).
            LinkKind::SpeedJump if (target.z - origin.z).abs() > 48.0 => false,
            LinkKind::Walk | LinkKind::Step if bot.bhop.phase != bhop::Phase::Off || bot.sj.is_some() => {
                let source = graph.cell_origin(graph.link_source(leg));
                ground_waypoint_arrived(origin.xy(), source.xy(), target.xy(), speed, frametime)
            }
            _ => {
                let to = target.xy() - origin.xy();
                let fast = bot.bhop.phase != bhop::Phase::Off || bot.sj.is_some();
                to.length() <= arrive_r || (fast && to.dot(v_xy) < 0.0 && to.length() <= 64.0)
            }
        };
        if arrived {
            // Execution feedback: how long the leg actually took. Ages beyond 30s are dropped —
            // beyond that something else owns the failure and the sample is noise.
            if bot.leg_age <= 30.0 {
                if bot.leg_times.len() >= 32 {
                    bot.leg_times.pop_front();
                }
                bot.leg_times.push_back((leg, bot.leg_age));
            }
            bot.leg_age = 0.0;
            bot.route_pos += 1;
        } else {
            break;
        }
    }

    // Current waypoint + how to traverse to it. Past the route's end, home straight in on the
    // human (final approach). A Plat and a *grounded* Teleport both aim at the leg's *source* cell
    // rather than its target, for the same reason from opposite ends: you don't walk toward where the
    // leg *sends* you, you stay in the thing that does the sending. A plat's exit ledge is across a gap
    // you can't reach until it lifts you; a teleporter's exit is across the map, often through a wall —
    // steer at it and the bot walks *out* of the trigger it needs to stand in, reaches nothing, and
    // turns around. Aim at the source and it walks *into* the trigger; touching it teleports.
    //
    // Once airborne on a launch teleporter's arc (the displacement handler above latched a teleport
    // AirCommit), the roles flip: the source is now across the map behind us and the *target* ledge is
    // where the arc must land, so aim there and let the air-strafe curve us onto it.
    let (waypoint, kind, final_leg, cur_leg) = if bot.route_pos < bot.route.len() {
        let leg = bot.route[bot.route_pos];
        let k = graph.link_kind(leg);
        let aim_source = matches!(k, LinkKind::Plat) || (k == LinkKind::Teleport && on_ground);
        let aim = if aim_source {
            graph.cell_origin(graph.link_source(leg))
        } else {
            graph.cell_origin(graph.link_target(leg))
        };
        (aim, Some(k), false, Some(leg))
    } else {
        (target_origin, None, true, None)
    };

    // Plat standoff. If an upcoming leg boards/rides a func_plat that isn't at its bottom, and we're
    // not already aboard it, walking to the board point would put us inside the lift's inner trigger
    // (the footprint shrunk 25u, spanning the full travel height) — and `plat_center_touch` resets
    // the lower-timer for any live player inside, so a bot waiting there would hold the lift raised
    // forever (and can wedge under a non-solid one). Instead hold a standoff outside the footprint
    // until it descends. The board leg itself may be a couple of Walk legs ahead, so scan a small
    // window and gate on proximity — the walk-in cells sit inside the full-height trigger too.
    let plat_hold: Option<usize> = bot
        .route
        .get(bot.route_pos..)
        .into_iter()
        .flatten()
        .take(PLAT_LOOKAHEAD)
        .find_map(|&l| graph.plat_of_link(l))
        .filter(|&pi| {
            let st = &plat_status[pi];
            let p = graph.plat(pi);
            let riding = origin.z > st.surface_z + 8.0 && in_footprint(origin.xy(), p.fp_min, p.fp_max, 0.0);
            !st.down && !riding && in_footprint(origin.xy(), p.fp_min, p.fp_max, PLAT_ENGAGE)
        });
    // Note there is deliberately no "the bot is loitering under a raised lift, walk it out" reflex here.
    // Standing in a shaft is not a state to detect and recover from on a timer — it is a state nothing
    // should ever choose. Every spot a bot comes to rest on is picked by a chooser that now refuses
    // shaft cells (`roam_target`, `vigil::pick_post`) and combat's footing demotes a dodge into one, so
    // a bot only ever *crosses* a shaft — and a crossing needs no permission to end. Anything else that
    // leaves a bot standing there is a bug in the chooser, and belongs fixed there rather than papered
    // over by a grace period that would, by construction, still stand under the lift for its duration.
    // While holding, steer to the standoff point and borrow the Plat leg's driver treatment (no
    // jump-press, no bhop entry, no air-latch, progress-watchdog exempt) by presenting `kind` as Plat.
    let (waypoint, kind) = match plat_hold {
        Some(pi) => {
            let p = graph.plat(pi);
            (plat_standoff(origin, p.fp_min, p.fp_max), Some(LinkKind::Plat))
        }
        None => (waypoint, kind),
    };

    // Waypoint magnetism: `resolve_objective` picked a desirable up item near the route; if it lies on
    // this leg's corridor, bend the immediate waypoint through it so the hull actually crosses the
    // trigger (a network-client bot has no generous pickup box — only the tight server-side overlap).
    // Only on a plain walk/step leg or the final approach (`None`) and never while airborne, bhopping,
    // holding off a plat, or running a gate errand — those own the feet and a side-step would wreck the
    // traversal. The bend is a lateral nudge of at most `MAGNET_LATERAL`; leg advancement still keys on
    // cell centers (untouched above), so this can't trip the progress watchdog. Left active under a
    // powerup commit on purpose: a ≤48u step costs far under the bridge slack, and grabbing armour on
    // the quad walk is the whole point.
    let magnet_bend = o.magnet.is_some_and(|item| {
        matches!(kind, Some(LinkKind::Walk | LinkKind::Step) | None)
            && !on_air
            && plat_hold.is_none()
            && bot.gate.errand.is_none()
            && bot.bhop.phase == bhop::Phase::Off
            && magnet_on_corridor(origin.xy(), waypoint.xy(), item.xy())
    });
    let waypoint = if magnet_bend {
        o.magnet.unwrap_or(waypoint)
    } else {
        waypoint
    };
    // Plat-wait timeout: keyed on the plat index (not the leg, which the 0.4s repath churn rebuilds),
    // give up on a lift that never descends — a camped one, or a targeted plat only its own trigger
    // lowers — by striking its ride link so this bot's A* diverts, then re-path.
    match plat_hold {
        Some(pi) => {
            if bot.plat_wait.map(|w| w.plat) != Some(pi) {
                bot.plat_wait = Some(PlatWait { plat: pi, since: now });
            } else if bot.plat_wait.is_some_and(|w| now - w.since > PLAT_WAIT_TIMEOUT) {
                let ride = bot.route[bot.route_pos..]
                    .iter()
                    .copied()
                    .find(|&l| graph.link_kind(l) == LinkKind::Plat && graph.plat_of_link(l) == Some(pi));
                if let Some(ride) = ride {
                    penalize_link(bot, ride, now);
                }
                bot.plat_wait = None;
                bot.route.clear();
                bot.repath_time = now;
            }
        }
        None => bot.plat_wait = None,
    }

    let hook_active = matches!(kind, Some(LinkKind::Hook)) || hooking;
    // Same for a rocket-jump leg: standing in stance and riding the blast arc must be exempt from the
    // stuck/progress watchdogs and the bhop veto, exactly like a hook leg.
    let rj_active = matches!(kind, Some(LinkKind::RocketJump)) || on_rj;
    // Where the *eyes* go while navigating: a couple of legs ahead of the feet (or the final
    // target when the route is short), so the view sweeps down the corridor instead of snapping
    // to every 32u grid cell the bot steps through. Steering still uses `waypoint`.
    //
    // But a Fight target we're *not* detouring on sets `target_origin` to the enemy's LIVE origin,
    // so aiming the eyes there while we can't see the enemy tracks it through walls — an aimbot
    // look. Once combat's 2s corner-hold lapses (or if we never saw them), look where we're
    // *travelling* instead. Non-combat targets — a human we follow, a committed item goal, or a
    // greedy detour (`chasing`) — are exactly where we want to look, so they keep `target_origin`.
    let combat_blind =
        enemy.is_some() && !chasing && (enemy_seen_time <= 0.0 || now - enemy_seen_time > LOOK_LOS_GRACE);
    let look_point = if vigil && bot.vigil.scan_point != Vec3::ZERO {
        // Standing vigil: sweep the eyes across the room (the scan point the aim spring pans to).
        // This drives the perception cone too (perception reads `bot.aim.angles`), so it's real scouting;
        // combat's `engage` still overrides the moment a target comes into sight.
        bot.vigil.scan_point
    } else if let Some(pi) = plat_hold {
        // Holding off a raised lift: watch it, so we notice it descend (and combat's `engage` still
        // overrides the instant a target comes into sight).
        let p = graph.plat(pi);
        Vec3::new(
            (p.fp_min.x + p.fp_max.x) * 0.5,
            (p.fp_min.y + p.fp_max.y) * 0.5,
            plat_status[pi].surface_z + 24.0,
        )
    } else if bot.route_pos + EYE_LOOKAHEAD_LEGS < bot.route.len() {
        // Two whole legs of margin, and *not* clamped to the last leg when the route is shorter.
        //
        // Clamping was tried and reverted. It looked like the fix for the view panning between the
        // corridor and a distant item, but the final leg's target is behind a bot that has just
        // reached it — and since the hop bearing follows the eyes, the bot turned round and ran back
        // the way it came. Measured as a U-turn on the climb up to the yellow armour, and the panning
        // it was aimed at turned out to be the route flip-flop that `swim::Sense::aim_trusted` fixed.
        graph.cell_origin(graph.link_target(bot.route[bot.route_pos + EYE_LOOKAHEAD_LEGS]))
    } else if combat_blind {
        // Past the route's end `waypoint` *is* `target_origin` (the enemy), so there fall through
        // to our actual travel heading rather than re-pointing the eyes at the hidden enemy.
        if final_leg && speed > 20.0 {
            origin + Vec3::new(v_xy.x, v_xy.y, 0.0)
        } else {
            waypoint
        }
    } else if bsp.is_none_or(|b| {
        b.hull0_trace(origin + Vec3::new(0.0, 0.0, 22.0), target_origin)
            .fraction
            >= 1.0
    }) {
        // The objective, but only when the eyes can actually reach it.
        //
        // Looking where you are going is right; looking *through a wall* at where you are going is
        // both an aimbot tell and a real cost, because the hop bearing follows the eyes — so a
        // wall-blocked stare is a U-turn waiting to happen, and it earned one on the climb from the
        // pool up to the yellow armour. `hull0_trace` is the engine's own `traceline`: a widthless ray,
        // which is the right question for line of sight.
        target_origin
    } else if speed > 20.0 {
        // Blocked: look where we are travelling, exactly as the combat-blind case does.
        origin + Vec3::new(v_xy.x, v_xy.y, 0.0)
    } else {
        waypoint
    };

    let goal_dist = (target_origin.xy() - origin.xy()).length();
    // Everything the watchdogs below report about a stall, gathered once from values this frame
    // already produced. See [`StallFrame`].
    let stall_frame = StallFrame {
        now,
        origin,
        cell: bot_cell,
        goal_cell,
        goal_dist,
        speed,
    };

    // Stuck detection. Suppressed mid-hook: standing in the throw stance, reeling, and riding the
    // parabola all look "stuck" to it, and a force-jump/repath there would wreck the traversal — the
    // hook driver's own per-phase timeouts are its stuck detection.
    let mut force_jump = false;
    if hook_active
        || rj_active
        || on_air
        || vigil
        || plat_hold.is_some()
        || (origin - bot.watchdog.stuck_origin).length() > STUCK_MOVE
    {
        bot.watchdog.stuck_origin = origin;
        bot.watchdog.stuck_since = now;
    } else if now - bot.watchdog.stuck_since > STUCK_TIME {
        // Force a jump to unwedge — but NOT toward a fatal edge. A bot stuck at a lava/pit lip (e.g.
        // wedged against a surcharged jump the router refuses to take) would otherwise force-jump
        // straight off it and burn. When the near-field sees a drop/hazard within a hop toward the
        // waypoint, hold the jump and let the penalize+repath below divert the route instead.
        let toward_edge = bot.near.as_ref().is_some_and(|nf| {
            let d = (waypoint.xy() - origin.xy()).normalize_or_zero();
            d.length_squared() > 0.5
                && nf.edge_ahead(origin, Vec3::new(d.x, d.y, 0.0), STUCK_JUMP_LOOK) < STUCK_JUMP_LOOK
        });
        force_jump = !toward_edge;
        // Penalize the leg we're wedged on so the forced re-path actually *diverts* — without this
        // the deterministic A* hands back the identical route and the bot re-wedges every 0.7s.
        // Both branches penalize+repath; the force-jump is the extra unwedge, so name it when it
        // actually fired.
        let action = if force_jump { "force_jump" } else { "penalize+repath" };
        note_stall(bot, &stall_frame, "displacement", action, cur_leg, kind);
        penalize_leg(bot, cur_leg, kind, now);
        bot.repath_time = now; // re-path next frame
        bot.watchdog.stuck_since = now;
    }

    // Path-progress watchdog: catches a bot that *is* moving (so the displacement detector above
    // stays satisfied) yet makes no headway toward the goal — orbiting a pillar, sliding along a
    // wall, riding a mis-linked jump back and forth. The metric is *remaining route length*, not the
    // straight-line XY distance to the goal: a helical climb (a spiral staircase whose top sits over
    // its own core) holds a near-constant XY distance to the goal while ascending correctly, which
    // false-tripped this watchdog into penalizing the only way up. Remaining arc-length shrinks as the
    // bot advances legs and still plateaus on a true orbit. Off-route (final approach, no legs) it
    // falls back to the direct goal distance. If it hasn't improved by `PROGRESS_EPS` for
    // `PROGRESS_STALL_TIME`, treat the current leg as failing: penalize it and re-path. Suspended
    // while hooking / on a committed speed-jump / riding a plat (all of which legitimately hold or
    // reverse progress for a while).
    let progress_metric = if bot.route.get(bot.route_pos).is_some() {
        route_remaining(graph, &bot.route, bot.route_pos, origin)
    } else {
        goal_dist
    };
    let plat_leg = matches!(kind, Some(LinkKind::Plat));
    if !hook_active && !rj_active && !on_sj && !on_air && !plat_leg && !vigil {
        // Gaining altitude is progress too. A helical staircase ascends correctly while its horizontal
        // orbit holds `route_remaining` near-constant, so a landing on a higher floor must reset the
        // stall timer or the watchdog penalizes and clears the only way up. Gate on `on_ground` so a
        // bhop's mid-air apex — which is *not* a floor gain — can't fake a climb.
        let climbed = on_ground && origin.z > bot.watchdog.climb_best + CLIMB_EPS;
        if progress_metric < bot.watchdog.progress_best - PROGRESS_EPS || climbed {
            bot.watchdog.progress_best = progress_metric.min(bot.watchdog.progress_best);
            if on_ground {
                bot.watchdog.climb_best = bot.watchdog.climb_best.max(origin.z);
            }
            bot.watchdog.progress_since = now;
        } else if progress_stalled(
            bot.watchdog.progress_best,
            bot.watchdog.progress_since,
            progress_metric,
            now,
        ) {
            note_stall(bot, &stall_frame, "progress", "penalize+repath", cur_leg, kind);
            penalize_leg(bot, cur_leg, kind, now);
            bot.route.clear();
            bot.repath_time = now;
            bot.watchdog.progress_best = progress_metric;
            bot.watchdog.climb_best = origin.z;
            bot.watchdog.progress_since = now;
        }
    } else {
        // Keep the baselines current so a stall isn't falsely flagged the instant we resume.
        bot.watchdog.progress_best = progress_metric;
        bot.watchdog.climb_best = origin.z;
        bot.watchdog.progress_since = now;
    }

    // Bunnyhop policy verdicts — everything that needs game state is judged here; *when* each
    // verdict may apply in the hop cycle (engage hysteresis, mid-hop commitment, landing-only
    // disengage) is `bhop::Bhop::step`'s job. The entry runway bar is deliberately fixed:
    // the old `speed·0.9` bar rose as the bot gained speed and cut runs short mid-air.
    let runway_dist = runway(graph, &bot.route, bot.route_pos, origin);
    // Combat only vetoes bhop while it *owns the view* — the enemy is in sight (or lost a moment
    // ago), when the eyes must aim, not sweep a strafe. A mere Fight target being chased across
    // the map is navigation, and navigation bunnyhops; in FFA every bot always has a target, so
    // gating on target existence kept bhop permanently off. The grace here is deliberately much
    // shorter than combat's 2s corner-hold: on a small open FFA map sight contact is frequent,
    // and a long window suppresses hopping almost everywhere.
    const BHOP_COMBAT_GRACE: f32 = 0.5;
    let combat_view = enemy.is_some() && enemy_seen_time > 0.0 && now - enemy_seen_time < BHOP_COMBAT_GRACE;
    // A navmesh cell flagged beside a fatal drop (a wall-hugging walkway over an open pit — a spiral
    // staircase's inner edge). It no longer vetoes the hop — the near-field hop bearing bends off the
    // inner edge and caps the leap at the lip, so a straight bhop line holds — but it still suppresses
    // the ground *zigzag*, whose lateral weave would carry a fast bot off the edge. The flag is
    // precomputed per cell, so unlike the near-field it stays valid while the bot is airborne mid-hop.
    // Exempt a jump run-up (the leg at hand, or the next, is a jump): the drop is the jump's landing.
    let is_jump = |l: u32| {
        matches!(
            graph.link_kind(l),
            LinkKind::JumpGap | LinkKind::DoubleJump | LinkKind::SpeedJump
        )
    };
    // ...but only when the leap lies along the travel direction. A jump leg that departs sideways
    // off the current run (dm3: the westbound RA-ramp run whose next leg is the southbound gap jump
    // onto the shelf) is not a run-up — the bot must first shed speed and turn, which is exactly the
    // room the ledge policy exists to make. Exempting it let the ground zigzag pump the run to 490
    // ups straight past a lip the runup gate (correctly) refused to jump perpendicular to.
    // For a speed jump the honest run-up direction is `from`→**takeoff**, not `from`→`to`: those are
    // the same line for a straight jump but diverge by the whole curl angle for a curl or side jump,
    // where the bot runs along a ledge and leaves off its flank. Measured against `to` a 60°+ side
    // jump fails this test *while the bot is flying its own certified run-up line* — which re-arms the
    // ledge brake on the approach and saps the very speed the leap needs. Plain jumps keep the
    // source→target chord: they have no separate takeoff point, and the sideways-departure case above
    // is exactly what their check is for.
    let jump_along_travel = |l: u32| {
        let src = graph.cell_origin(graph.link_source(l)).xy();
        let aim = match graph.speed_jump_of_link(l) {
            Some(tr) => tr.takeoff.xy(),
            None => graph.cell_origin(graph.link_target(l)).xy(),
        };
        let d = (aim - src).normalize_or_zero();
        let v = v_xy.normalize_or_zero();
        v == Vec2::ZERO || d == Vec2::ZERO || d.dot(v) > 0.5
    };
    let is_jump_at_hand = |l: u32| is_jump(l) && jump_along_travel(l);
    let jump_at_hand =
        cur_leg.is_some_and(&is_jump_at_hand) || bot.route.get(bot.route_pos + 1).is_some_and(|&l| is_jump_at_hand(l));
    let on_ledge = graph.is_ledge(bot_cell) && !jump_at_hand;
    let bhop_veto = !host.cvar_bool(c"rtx_bot_bhop")
        || combat_view
        || in_water // can't hop while swimming — the engine's pmove turns jumps into swim strokes
        || hook_active
        || rj_active
        // Spectating: a bhop cmd would overwrite the view yaw in `emit` and clobber the watch —
        // and a spectator strolling the stands shouldn't be bunnyhopping anyway.
        || watch_point.is_some()
        || bot.gate.errand.is_some()
        || bot.grenade.phase != GrenadePhase::Idle;
    // The banded planner's intent for this run: a band ≥ 1 on the current or next leg means the
    // route was planned to carry speed here, so admit bhop even on a short leg (the goal-distance
    // gates below exist to avoid hopping on trivial approaches — the plan overrides that judgment)
    // and tell the controller to hold the chain across the waypoint rather than disengage per leg.
    let planned_band = bot.route_bands.get(bot.route_pos).copied().unwrap_or(0);
    // An ascending Walk/Step leg (target more than a walk's worth above the source, i.e. a stair
    // riser) just ahead: a human runs up stairs, so don't let a planned carry hold the hop chain up
    // them — `runway`'s climb stop keeps *entry* off stairs, and this keeps *carry* from overriding it.
    let leg_ascends = |leg: u32| {
        matches!(graph.link_kind(leg), LinkKind::Walk | LinkKind::Step)
            && graph.cell_origin(graph.link_target(leg)).z - graph.cell_origin(graph.link_source(leg)).z > 8.0
    };
    let ascent_ahead =
        cur_leg.is_some_and(&leg_ascends) || bot.route.get(bot.route_pos + 1).is_some_and(|&l| leg_ascends(l));
    // A tightly winding corridor just ahead (the flat, curved treads of a spiral staircase between its
    // risers, or a hairpin): a hop at full speed overshoots the bend and weaves off the narrow path,
    // so drop to the walk — its near-field glide tracks the curve. `ascent_ahead` alone misses this,
    // since the winding legs are flat (no riser); the curvature gate catches them.
    // The carry gate must see as far as the bot needs to *stop* caring: at 490 ups four legs is a
    // quarter second, and a hairpin past that horizon is reached before friction can shed a single
    // band. A chain also needs a grounded stretch to die on: the last hop in flight is ~100u by
    // itself. Scale the window with speed (~0.85 s of travel at 32u legs); slow chains keep the tight
    // window that made the dogleg reading trustworthy.
    let turn_look = ((v_xy.length() * 0.85 / 32.0).ceil() as usize).clamp(WINDING_LOOKAHEAD, 20);
    let winding_ahead = route_turn(graph, &bot.route, bot.route_pos, v_xy, turn_look) > WINDING_LIMIT;
    let carry = (planned_band >= 1 || bot.route_bands.get(bot.route_pos + 1).copied().unwrap_or(0) >= 1)
        && !ascent_ahead
        && !winding_ahead;
    // Entry stays behind the conservative cumulative-turn reading: a chain not yet started loses
    // nothing by walking a few more cells while a corner resolves, and a hop begun *at* a corner
    // flies uncertified (the walk certifier is gated off on air frames). dm3's stair crest is the
    // measured case: entry admitted on the deviation measure alone hops the crest and corner-cuts
    // the notch beyond it.
    let winding_entry = winding_ahead || route_turn_sum(graph, &bot.route, bot.route_pos, origin) > WINDING_LIMIT;
    let bhop_entry = !final_leg
        && matches!(kind, Some(LinkKind::Walk | LinkKind::Step))
        && (goal_dist > 300.0 || planned_band >= 1)
        && runway_dist >= bhop::RUNWAY_ENGAGE
        && !winding_entry
        // Run up first: don't start the hop cycle from a standstill — accelerate on the ground until
        // we're actually moving, then leap into the circle-jump (a human never hops from a stop).
        && speed >= bhop::RUN_UP_SPEED;
    // Lenient continuation gate for taking *another* hop from a landing: leg kinds churn as the
    // route advances, and a run in progress shouldn't be dumped by the stricter entry conditions.
    // But never sustain the chain up an ascending stair run — `runway`'s climb stop keeps *entry* off
    // stairs, yet a chain carried onto a stairway (a wall-hugging spiral, say) would otherwise keep
    // hopping and weave off the treads. Drop to the walk, whose near-field glide tracks the steps.
    let bhop_sustain = matches!(kind, Some(LinkKind::Walk | LinkKind::Step))
        && (goal_dist > 150.0 || planned_band >= 1)
        && !ascent_ahead
        && !winding_ahead;
    // Ground zigzag: a corridor too short for a hop ([`bhop::RUNWAY_ENGAGE`]) but straight and long
    // enough ([`bhop::ZIGZAG_ENGAGE`]) to gain speed from the circle-strafe alone. The controller
    // hands off to the hop cycle if `bhop_entry` opens up mid-run, and `bhop_veto` (which includes
    // `!rtx_bot_bhop`) still gates it, so this is purely a sub-toggle on the same controller.
    let zigzag_ok = host.cvar_bool(c"rtx_bot_zigzag")
        && matches!(kind, Some(LinkKind::Walk | LinkKind::Step))
        && !final_leg
        && goal_dist > 150.0
        && runway_dist >= bhop::ZIGZAG_ENGAGE
        && !on_ledge;
    // A speed-jump leg is a *committed* bhop run-up + leap: engage bhop unconditionally (the link is a
    // pre-verified runway) and track it so the route stays frozen. Latch/clear `sj_leg` on the leg.
    let mut sj_active =
        matches!(kind, Some(LinkKind::SpeedJump)) && host.cvar_bool(c"rtx_bot_bhop") && !hook_active && !rj_active;
    if sj_active {
        if bot.sj.map(|c| c.leg) != cur_leg {
            // Leg-transition chain-entry guard (`rtx_bot_chain_entry_gate`): the plan-time exclusion
            // above only covers a route's *first* leg from the bot's cell at the moment it was
            // planned — a route whose leg 0 is an ordinary walk/step into the ledge cell and whose leg
            // 1 is the chained jump sails straight through it, because at plan time the walk hadn't
            // happened yet and the chained link wasn't adjacent to the bot's *then*-current cell. But
            // `sj_active` above engages the very instant this leg becomes current — "committed bhop
            // run-up + leap" — with no check at all that the bot actually arrived carrying speed, so a
            // bot that merely walked into the ledge (dm3 measured ~38 ups, well under either link's
            // v_req) commits anyway and only the 4s stall watchdog below ever notices. Catch it here,
            // once, at the exact frame the leg becomes current (not on every frame of an already-
            // engaged run — that would also catch a leg mid-flight, which is exactly the traffic this
            // must leave alone): a bot landing from a preceding jump already carries real speed at
            // this instant, so the check clears it naturally without any special-casing for "mid-chain".
            let chain_entry_ok =
                !host.cvar_bool(c"rtx_bot_chain_entry_gate") || graph.chain_entry_leg_ok(cur_leg, speed);
            if chain_entry_ok {
                bot.sj = cur_leg.map(|leg| Commit { leg, since: now });
            } else {
                // Same finite-penalty + immediate-replan mechanism the watchdogs below use — deliberately
                // *not* `note_stall`: nothing was attempted here to fail. Diverting before the takeoff is
                // the fix, not a stall to log.
                penalize_leg(bot, cur_leg, kind, now);
                bot.route.clear();
                bot.repath_time = now;
                sj_active = false;
            }
        }
    }
    // Leg-hold chain-entry guard: the transition-instant check above only catches a commit that was
    // *already* too slow. A chained jump committed at a borderline speed — enough to clear the loose
    // transition threshold, not enough to actually fly — has, by definition (no runway of its own),
    // nowhere to build the rest: `sj_hold` below just holds it on the ground circle-strafing in
    // place, and without this check nothing notices until either this fires or the generic 4s
    // `speedjump_stall` watchdog does. Measured live: a second dual-instrument capture on the
    // transition-only guard still found 21 ring stalls, 20 of them under half of v_req and 13 at
    // `route_pos == 1` — most of them `displacement` (the *generic* stuck watchdog, `STUCK_TIME` ==
    // 0.7s, firing first because a bot holding in place barely displaces) rather than the sj-specific
    // 4s one, which a chained hold essentially never survives to reach. So this has to run every
    // tick, not just at commit, and has to beat 0.7s.
    //
    // [`CHAIN_ENTRY_GRACE`] gives a landing's first velocity reading a moment to settle before this
    // judges it — the transition check has none, deliberately (an outright standstill needs no
    // grace) — and `on_ground` excludes the actual airborne leap: once launched the bot is committed
    // to the physics of the jump, and diverting mid-arc is meaningless. Fires at most once per
    // commit (clearing `bot.sj` takes it out of `sj_active` for the rest of this leg), and reuses the
    // same penalize+repath mechanism as every other watchdog here — no `note_stall`: this is still a
    // diversion before a takeoff was ever attempted, not a failure to log.
    if sj_active && host.cvar_bool(c"rtx_bot_chain_entry_gate") {
        let blocked = bot.sj.is_some_and(|c| !graph.chain_entry_leg_ok(Some(c.leg), speed));
        if chain_entry_hold_expired(bot.sj, now, on_ground, blocked) {
            penalize_leg(bot, cur_leg, kind, now);
            bot.sj = None;
            bot.route.clear();
            bot.repath_time = now;
            sj_active = false;
        }
    }
    if sj_active {
        // Watchdog: the route is frozen mid-leg, so if the run-up stalls (blocked, shoved, never
        // built speed) abandon it and re-path rather than wedging on the runway forever. Penalize the
        // leg so the deterministic A* actually diverts instead of handing back the same run-up.
        if bot.sj.is_some_and(|c| now - c.since > 4.0) {
            note_stall(bot, &stall_frame, "speedjump_stall", "penalize+repath", cur_leg, kind);
            penalize_leg(bot, cur_leg, kind, now);
            bot.sj = None;
            bot.route.clear();
            bot.repath_time = now;
            sj_active = false;
        }
    } else if bot.sj.is_some() {
        bot.sj = None;
    }
    // Fallback latch for a jump leg created by this frame's repath. Ordinarily `prearm_traversal`
    // installed it before objective resolution; this closes the first-frame route-build case.
    let on_jump_leg = matches!(kind, Some(LinkKind::JumpGap | LinkKind::DoubleJump));
    if on_jump_leg && bot.air.map(|c| c.leg) != cur_leg {
        bot.air = cur_leg.map(|leg| AirCommit {
            leg,
            target: graph.link_target(leg),
            since: now,
            airborne: !on_ground,
        });
    }
    if let Some(committed) = bot.air {
        match air_commit_decision(on_ground, committed.airborne, now - committed.since) {
            AirRelease::Keep => {}
            AirRelease::Land => {
                let leg_kind = graph.link_kind(committed.leg);
                let target = graph.cell_origin(committed.target);
                let on_target = (origin.xy() - target.xy()).length() <= 2.0 * ARRIVE_RADIUS;
                if !on_target {
                    note_stall(
                        bot,
                        &stall_frame,
                        "air_commit_off",
                        "penalize+repath",
                        Some(committed.leg),
                        Some(leg_kind),
                    );
                    penalize_leg(bot, Some(committed.leg), Some(leg_kind), now);
                    bot.route.clear();
                    bot.repath_time = now;
                }
                bot.air = None;
            }
            AirRelease::Timeout => {
                let leg_kind = graph.link_kind(committed.leg);
                note_stall(
                    bot,
                    &stall_frame,
                    "air_commit_timeout",
                    "penalize+repath",
                    Some(committed.leg),
                    Some(leg_kind),
                );
                penalize_leg(bot, Some(committed.leg), Some(leg_kind), now);
                bot.air = None;
                bot.route.clear();
                bot.repath_time = now;
            }
        }
    }
    // "Don't leap to your death": if we somehow reach the takeoff edge too slow to clear the gap,
    // hold the jump (keep accelerating) rather than launching short into it.
    let sj_takeoff = cur_leg
        .and_then(|l| graph.speed_jump_of_link(l))
        .map(|tr| (tr.takeoff, tr.v_req));
    // A curl speed jump carries a nonzero air-curl gain; a straight one carries 0 (keeps the slalom).
    let sj_curl_gain = cur_leg
        .and_then(|l| graph.speed_jump_of_link(l))
        .map(|tr| tr.curl_gain)
        .unwrap_or(0.0);
    let sj_curl = sj_active && sj_curl_gain > 0.0;
    // How wide the run-up's floor lets the speed-building weave swing (∞ = uncapped; a side jump off a
    // narrow ledge asks for less). See `SpeedJumpTraversal::weave_cap`.
    let sj_weave_cap = cur_leg
        .and_then(|l| graph.speed_jump_of_link(l))
        .map(|tr| tr.weave_cap)
        .unwrap_or(f32::INFINITY);
    // Signed along-corridor distance from the bot to a curl's takeoff (>0 behind the lip, <0 past it):
    // the run-up direction is the link's `from`→takeoff line. Used to trigger the leap on crossing the
    // takeoff *line* (not a radial ball the weave can skirt into a U-turn) and to gate the run-up aim.
    // Carries the run-up *axis* as well, because that axis is the heading the certifier proved the arc
    // from — see `sj_hold` below.
    let sj_run_up: Option<(f32, f32)> = if sj_curl {
        if let (Some((takeoff, _)), Some(leg)) = (sj_takeoff, cur_leg) {
            let dir = (takeoff.xy() - graph.cell_origin(graph.link_source(leg)).xy()).normalize_or_zero();
            Some(((takeoff.xy() - origin.xy()).dot(dir), yaw_of(dir)))
        } else {
            None
        }
    } else {
        None
    };
    let sj_progress: Option<f32> = sj_run_up.map(|(p, _)| p);
    // Curl too-slow abort: the bhop takeoff regime leaps a curl *unconditionally* at the lip, so if the
    // bot won't build `v_req` by the lip from where it is now (shoved, blocked, or dropped onto the leg
    // slow by a repath), bail the leg here rather than leap short into the pit. Predict the lip speed
    // from the current state via the ground-prestrafe oracle; abort (penalize + repath) when it falls
    // well short. Edge-avoidance — restored the moment `sj_active` clears — then keeps the bot off the
    // ledge. Left running (the run-up recovers a low *early* speed over the remaining distance).
    if let (true, Some((_, v_req)), Some(progress)) = (sj_curl, sj_takeoff, sj_progress) {
        let cv = |n: &std::ffi::CStr, d: f32| {
            let x = host.cvar(n);
            if x > 0.0 {
                x
            } else {
                d
            }
        };
        let predicted = crate::navmesh::prestrafe_delivered_from(
            speed,
            progress.max(0.0),
            cv(c"sv_accelerate", 10.0),
            cv(c"sv_maxspeed", 320.0),
            cv(c"sv_friction", 4.0),
            cv(c"sv_stopspeed", 100.0),
        );
        if sj_abort_should_fire(
            on_ground,
            host.cvar_bool(c"rtx_bot_sj_abort_grounded"),
            predicted,
            v_req,
        ) {
            note_stall(bot, &stall_frame, "prestrafe_deficit", "penalize+repath", cur_leg, kind);
            // A certified leg: strike it once so this repath prefers an alternative, but never let the
            // surcharge escalate — the build proved the jump flyable, so arriving slow is a transient
            // state to retry, not evidence the link is wrong for this bot.
            penalize_leg(bot, cur_leg, kind, now);
            bot.sj = None;
            bot.route.clear();
            bot.repath_time = now;
            sj_active = false;
        }
    }
    // Hold the leap until the takeoff is inside the envelope the build actually certified.
    //
    // `certify_curl` proves one arc from one *band*: a takeoff speed within `v_req · (1 ±
    // CURL_V_HOLD_TOL)` and a velocity within `CURL_PSI_TOL` of the run-up axis. The controller,
    // though, leaps a committed curl on geometry alone — the frame the bot crosses the takeoff line —
    // so whatever speed and heading the speed-building serpentine happens to be at becomes the
    // takeoff, certified or not. dm3's big gap (link 35450, `v_req` 443.8) shows what that costs: of
    // 31 live takeoffs, all 17 inside the envelope landed on the far shelf, and all 14 outside it fell
    // into the pit — 427-432 ups (under the band floor of 430) or 14-26° off the axis. A fall there is
    // not a small error: the bot climbs the whole spiral again, which is why the route timed out more
    // often than it arrived.
    //
    // So while any run-up is left (`p > 0`), stay on the ground and keep building. The wait is bounded
    // by construction — the bot is travelling toward the line, so `p` reaches 0 and it commits — and it
    // is cheap: ground prestrafe adds ~500 ups/s, so the last 28u of shelf is worth ~40 ups.
    let sj_hold = sj_active
        && match (sj_takeoff, sj_run_up) {
            // A curl: the full certified envelope.
            (Some((_, v_req)), Some((p, psi))) => {
                let in_band = speed >= v_req * (1.0 - CURL_V_HOLD_TOL);
                let on_axis = wrap180(psi - yaw_of(v_xy)).abs() <= CURL_PSI_TOL;
                p > 0.0 && !(in_band && on_axis)
            }
            // A straight speed jump: run-up and leap are collinear, so the heading takes care of itself
            // and the takeoff is a radial ball rather than a line. Hold only while still *approaching*
            // it — the run-up is what the hold is spending, so once the bot is at or past the takeoff
            // there is nothing left to buy and it must commit. (Holding past the edge is a hang, not a
            // wait: the bot circle-strafes on the spot, and a circle-strafe curves, so it wanders off
            // the corridor instead of leaping. This branch was unreachable until the controller began
            // honouring `hold_jump` for committed jumps, which is how it shipped with that hole.)
            (Some((takeoff, v_req)), None) => {
                let to_edge = takeoff.xy() - origin.xy();
                to_edge.dot(v_xy) > 0.0 && to_edge.length() < 48.0 && speed < v_req * 0.9
            }
            _ => false,
        };

    // Near-field ensure (see [`crate::nearfield`]): build/refresh the fine 8u clearance grid whenever
    // the bot is doing GROUNDED locomotion on a walk/step/approach leg — walking, zigzagging, OR
    // bhopping over ground — so the *fast-movement* hop bearing below can be steered clear of drop
    // edges and walls, not just the slow walk. Excluded on a committed ballistic arc (speed/rocket
    // jump, hook, launched air-commit), where the flight must commit to its landing and must not be
    // nudged. Built on grounded frames (the seed needs footing); the cache survives the brief airborne
    // phase of a hop (which rises well under the recenter height), so the bearing stays aware mid-hop.
    // The slow-walk edge margin below re-reads this same grid.
    let nf_locomotion = !on_air
        && !sj_active
        && !hook_active
        && !rj_active
        && matches!(kind, Some(LinkKind::Walk | LinkKind::Step) | None);
    let nf_active = nf_locomotion && host.cvar_bool(c"rtx_bot_nearfield");
    if nf_active && on_ground {
        if let Some(bsp) = bsp {
            // Low half of the key is gates, high half teleporters, so either changing forces a rebuild.
            // Both id spaces are small (single-digit gates; a handful of teleporters per map).
            let key = nearfield_gates(graph, gate_closed, origin).fold(0u32, |k, gi| k | (1u32 << gi.min(15)))
                | nearfield_teleports(graph, origin, waypoint).fold(0u32, |k, ti| k | (1u32 << (16 + ti.min(15))));
            if bot.near.as_ref().is_none_or(|nf| !nf.valid_for(origin, key)) {
                let boxes: Vec<(Vec3, Vec3)> = nearfield_gates(graph, gate_closed, origin)
                    .map(|gi| {
                        let g = graph.gate(gi);
                        (g.closed_min, g.closed_max)
                    })
                    .chain(nearfield_teleports(graph, origin, waypoint).map(|ti| graph.tele_volumes()[ti]))
                    .collect();
                // Liquid oracle: flush lava/slime is invisible to the clip hull, so classify it from the
                // render hull's `pointcontents` (our own parsed BSP — no syscall). Gated on the map having
                // any hazard cell at all, so the dry maps (the norm) pay nothing. A walkable column over
                // lava becomes a repelling `Col::Hazard`, so the walk margin and hop bearing steer off it.
                let has_haz = graph.has_hazards();
                let (lava, slime) = (crate::bsp::CONTENTS_LAVA, crate::bsp::CONTENTS_SLIME);
                let is_hazard = |p: Vec3| {
                    has_haz && {
                        let c = bsp.pointcontents(p);
                        c == lava || c == slime
                    }
                };
                bot.near = Some(nearfield::NearField::build(
                    &|p| bsp.is_solid(p),
                    &is_hazard,
                    origin,
                    &boxes,
                    key,
                ));
            }
        }
    }

    // Predictive hop planning (see `hopsim`). On a ledge corridor (a wall-hugging walkway over a drop
    // — a spiral staircase's inner edge) a bhop's chord sags over the void by more than the walkway is
    // wide, so no reactive edge test both keeps speed and stays on. Instead roll the pmove a hop ahead
    // under the guided policy the controller flies and take only hops whose *predicted* landing stays
    // on the route. Planned on a grounded frame from the live state, held across the flight, re-planned
    // each landing; `None` off ledge corridors or when boxed. Gated by `rtx_bot_hopplan`.
    let hop_mode = host.cvar_bool(c"rtx_bot_hopplan")
        && !bhop_veto
        && matches!(kind, Some(LinkKind::Walk | LinkKind::Step))
        && ledge_soon(graph, &bot.route, bot.route_pos, bot_cell);
    // Plan only on a grounded frame moving fast enough to actually hop — the rollout fan traces the
    // live BSP hull many times, so planning every crawling walk frame would blow the frame budget.
    // Mid-air the plan stays *latched* for the whole flight: the rollout committed to this landing, so
    // a leg-kind or ledge-flag flip as the route advances mid-hop must not strip the guidance and drop
    // the bot out of the air (which aborted the jump mid-arc and fell).
    if on_ground {
        bot.hop = if hop_mode && speed > 60.0 {
            bsp.and_then(|bsp| {
                let cvf = |name: &std::ffi::CStr, d: f32| {
                    let v = host.cvar(name);
                    if v > 0.0 {
                        v
                    } else {
                        d
                    }
                };
                let pm = crate::pmove_sim::PmParams {
                    gravity: cvf(c"sv_gravity", 800.0),
                    accel: cvf(c"sv_accelerate", 10.0),
                    friction: cvf(c"sv_friction", 4.0),
                    stopspeed: 100.0,
                    maxspeed: cvf(c"sv_maxspeed", 320.0),
                };
                // Route polyline from the bot outward, so plan arc-distances measure from here.
                let route_pts: Vec<Vec3> = std::iter::once(origin)
                    .chain(
                        bot.route
                            .get(bot.route_pos..)
                            .unwrap_or_default()
                            .iter()
                            .take(12)
                            .map(|&l| graph.cell_origin(graph.link_target(l))),
                    )
                    .collect();
                let st = crate::pmove_sim::PmState {
                    origin,
                    vel: Vec3::new(v_xy.x, v_xy.y, 0.0),
                    on_ground: true,
                    jump_held: false,
                };
                let has_haz = graph.has_hazards();
                let is_hazard = |p: Vec3| {
                    has_haz && {
                        let c = bsp.pointcontents(p);
                        c == crate::bsp::CONTENTS_LAVA || c == crate::bsp::CONTENTS_SLIME
                    }
                };
                hopsim::plan_hop(bsp, &is_hazard, &route_pts, st, &pm)
            })
        } else {
            None
        };
    }
    // Airborne frames leave `bot.hop` untouched — the plan stays latched for the whole flight.
    let hop_plan = bot.hop;
    let hop_bearing = hop_plan.map(|pl| yaw_of(pl.aim.xy() - origin.xy()));
    let hop_guide = hop_plan.map_or(0.0, |pl| pl.gain);

    // Drive the hop-cycle controller (see `bhop::Bhop`). On a speed jump the runway is the
    // run-up to the takeoff edge and the bearing aims straight at the landing so the leap goes
    // across the gap; otherwise steer toward the look-ahead corridor point (smoother than the 32u
    // next cell) with as much straight-ish corridor as the route offers. `mut` so the hazard-edge
    // brake below can null the hop and drive a reverse wish through `emit` instead.
    let mut bhop_cmd = {
        let dt = frametime.clamp(0.001, 0.05);
        let accel = host.cvar(c"sv_accelerate");
        let maxspeed = host.cvar(c"sv_maxspeed");
        let friction = host.cvar(c"sv_friction");
        let stopspeed = host.cvar(c"sv_stopspeed");
        let env = bhop::Env {
            dt,
            accel: if accel > 0.0 { accel } else { 10.0 },
            maxspeed: if maxspeed > 0.0 { maxspeed } else { 320.0 },
            friction: if friction > 0.0 { friction } else { 4.0 },
            stopspeed: if stopspeed > 0.0 { stopspeed } else { 100.0 },
            profile: crate::bot::human_profile::HumanMovementProfile::calibrated().safe(),
        };
        // A committed speed jump aims at its gap; otherwise steer toward the racing-line look-ahead
        // (race mode, when a line exists) or a *speed-scaled* corridor look-ahead — ~0.6 s of travel
        // ahead (clamped 96–448u) so a fast bot's bearing anticipates the corridor far enough to
        // start curving, rather than chasing the fixed ~2-legs `look_point` it has already overrun.
        let bhop_look = corridor_point(
            graph,
            &bot.route,
            bot.route_pos,
            origin,
            (speed * 0.6).clamp(96.0, 448.0),
        );
        let to_wp = waypoint.xy() - origin.xy();
        let ahead = match race_line_ahead {
            Some(lp) if !sj_active => lp.xy() - origin.xy(),
            // On a speed jump the run-up aims at the *takeoff* (follow the corridor to the lip), and
            // only once airborne does the bearing swing to the *landing* — so a curl jump (run-up and
            // leap not collinear) tracks its corridor instead of cutting across it and off the edge.
            // For a straight speed jump takeoff and target are collinear, so this is a no-op.
            _ if sj_active => {
                let aim = match (sj_takeoff, sj_progress) {
                    // Curl run-up: aim at the takeoff (follow the corridor) while still behind the lip —
                    // grounded *or* briefly airborne (a bumped or carried-airborne entry) — so it never
                    // curls toward the offset landing while still over the run-up and pulls off the edge.
                    // ...and keep aiming there while the leap is held for its envelope: the aim is what
                    // the speed-building weave centres on, so switching to the landing bearing mid-hold
                    // would turn the bot off the run-up axis — the very thing the hold is waiting for.
                    (Some((takeoff, _)), Some(p)) if p > bhop::LIP_REACH || sj_hold => takeoff,
                    // Straight speed jump on the ground: aim at the takeoff (collinear → no-op vs landing).
                    (Some((takeoff, _)), None) if on_ground => takeoff,
                    _ => waypoint,
                };
                aim.xy() - origin.xy()
            }
            // Normal corridor follow: aim at the speed-scaled look-ahead — but only while the straight
            // line to it stays on near-field-clear floor. On a wall-hugging spiral staircase the far
            // look-ahead wraps around the inner curve, so the chord to it cuts across the open centre;
            // certifying it (as the slow walk-glide path does) and otherwise dropping back to the near
            // waypoint keeps the fast bot on its current flight instead of steering off the inner edge.
            // Past the grid the chord passes — the route out there is trusted; the veto fires only on a
            // drop the near-field actually sees, so open corridors keep the full anticipatory look-ahead.
            _ => {
                let look_clear = bot
                    .near
                    .as_ref()
                    .filter(|_| nf_active)
                    .is_none_or(|nf| nf.chord_open(origin, bhop_look));
                if look_clear {
                    bhop_look.xy() - origin.xy()
                } else {
                    to_wp
                }
            }
        };
        let dir = if ahead.length() > 8.0 { ahead } else { to_wp };
        // Near-field-aware hop bearing: bend the heading off nearby drop edges and walls so a fast bot
        // (bhop or zigzag) holds the walkable line — e.g. tracking up a staircase — instead of weaving
        // straight off the edge toward the raw xy goal. `nf_active` already excludes committed jumps,
        // and a speed jump takes the `sj_active` branch above (so a gap leap still commits to its
        // landing). Inert on open ground, where the near-field push is zero.
        let dir = if hop_bearing.is_some() {
            dir // guided: the hop plan owns the bearing (set below); skip the reactive near-field bend
        } else {
            match bot
                .near
                .as_ref()
                .filter(|_| nf_active)
                .and_then(|nf| nf.steer_push(origin))
            {
                Some(push) => {
                    let bent = dir.normalize_or_zero() + push.xy() * NEARFIELD_BHOP_WEIGHT;
                    if bent.length() > 1e-3 {
                        bent * dir.length()
                    } else {
                        dir
                    }
                }
                None => dir,
            }
        };
        // A live hop plan supplies the bearing straight to its aim — the rollout certified that line.
        let bearing = hop_bearing.unwrap_or(yaw_of(dir));
        let bhop_runway = match (sj_takeoff, sj_progress) {
            // Curl: signed along-corridor distance to the takeoff (past-lip goes negative → leap).
            (_, Some(p)) => p,
            // Straight speed jump: radial distance to the takeoff edge (collinear run-up).
            (Some((takeoff, _)), None) if sj_active => (takeoff.xy() - origin.xy()).length(),
            _ => runway_dist,
        };
        // Forward wall probe: how far the bot can fly straight ahead before a wall — one hull trace
        // along the velocity out to a hop's flight. Feeds the controller's "don't leap at a wall,
        // carve when flying at one" logic. `INFINITY` (open) when there's no BSP, we're barely moving,
        // or the hop cycle isn't engaged/about to engage — so idle and plain-walking bots never trace.
        let clear = match bsp {
            Some(bsp) if speed > 1.0 && (bot.bhop.phase != bhop::Phase::Off || bhop_entry) => {
                let d = (speed * bhop::T_HOP).max(64.0);
                let end = origin + (v_xy.normalize_or_zero() * d).extend(0.0);
                let wall = bsp.hull1_trace(origin, end).fraction * d;
                // The hull trace sees only walls: a bot flying at the open centre of a wall-hugging
                // walkway (a spiral staircase's inner edge) traces clear and hops over the void. With a
                // live hop plan that leap is *intended* — the rollout certified its landing — so keep
                // the raw wall reach. Otherwise the near-field caps `clear` at the drop lip so the
                // controller carves/brakes on the ground at the edge rather than leaping off it.
                if hop_plan.is_some() {
                    wall
                } else {
                    let edge = bot
                        .near
                        .as_ref()
                        .filter(|_| nf_active)
                        .map_or(d, |nf| nf.edge_ahead(origin, v_xy.extend(0.0), d));
                    wall.min(edge)
                }
            }
            _ => f32::INFINITY,
        };
        let cmd = bot.bhop.step(
            &bhop::Input {
                v_xy,
                on_ground,
                bearing,
                runway: bhop_runway,
                eligible: bhop_entry,
                zigzag: zigzag_ok,
                // A live hop plan *is* the proof the chain belongs here — it certified a landing on the
                // route — so it keeps the chain alive across the ledge where `ascent_ahead`/`runway`
                // would otherwise drop to a walk.
                sustain: bhop_sustain || hop_plan.is_some(),
                veto: bhop_veto,
                committed: sj_active,
                carry: carry || hop_plan.is_some(),
                hold_jump: sj_hold,
                // The takeoff regime (hold ground prestrafe to the lip, leap once) is only for *curl*
                // jumps, which need a run-up the ground circle-strafe builds. A straight speed jump keeps
                // the pre-existing hop-chain takeoff — its air-strafe runway can exceed the ~490 prestrafe
                // ceiling, which the hold-to-lip regime would cap it below. So gate on the curl flag.
                takeoff_speed: match sj_takeoff {
                    Some((_, v_req)) if sj_active && sj_curl_gain > 0.0 => v_req,
                    _ => 0.0,
                },
                // Curl only jumps flagged as curls (straight speed jumps keep the slalom untouched). The
                // cvar, when set, overrides the link's baked gain for live tuning of the curl arc.
                curl_gain: if sj_active && sj_curl_gain > 0.0 {
                    let cv = host.cvar(c"rtx_jump_curl_gain");
                    if cv > 0.0 {
                        cv
                    } else {
                        sj_curl_gain
                    }
                } else {
                    0.0
                },
                // Only a committed jump leg carries a weave cap: off a jump leg the reactive edge
                // guards are live and own the bot's footing, so the weave stays uncapped there.
                weave_cap: if sj_active { sj_weave_cap } else { f32::INFINITY },
                guide_gain: hop_guide, // the live predictive hop plan's pursuit gain (0 = no plan)
                clear,
                now,
            },
            &env,
        );
        cmd
    };
    let bhop_active = bhop_cmd.is_some();

    // Steering: face the waypoint and run toward it.
    let to_wp = waypoint.xy() - origin.xy();
    let dist = to_wp.length();
    let yaw = yaw_of(to_wp);
    let mut angles = Vec3::new(0.0, yaw, 0.0);

    // Nav look target: eyes on the look-ahead point down the corridor (combat/gate may override
    // below). When the look point is basically on top of us (standing on the goal/waypoint), both it
    // and the steering yaw degenerate — `atan2` on a near-zero vector jitters frame to frame, which is
    // the source of the on-the-spot twitch — so hold the current smoothed view instead of chasing
    // noise. 48u guard (not 8) so a bot idling at a pickup doesn't re-solve a garbage angle.
    let eye = origin + Vec3::new(0.0, 0.0, 22.0);
    let to_look = look_point - eye;
    let mut look = if to_look.xy().length() > 48.0 {
        angles_to(eye, look_point)
    } else if dist > 8.0 {
        angles // steering yaw is still meaningful — look where we're walking
    } else if bot.aim.angles != Vec3::ZERO {
        bot.aim.angles // standing still on the point — hold the current view, don't snap to yaw 0
    } else {
        v_angle
    };

    // Grappling-hook leg driver: fly a LinkKind::Hook leg (select the grapple, settle the view on
    // the anchor, throw, reel to build speed, release into a parabola onto the target ledge). Its
    // whole state machine lives in `hook::drive_hook`; here we just feed it the frame snapshot and
    // apply the HookDrive it returns. The deferred `reset` (needs `&mut game`) is flushed later.
    let hook = hook::drive_hook(
        graph,
        bot,
        hook::HookCtx {
            hook_active,
            cur_leg,
            enemy,
            hook_out,
            on_hook,
            grapple_hook,
            has_grapple,
            now,
            weapon,
            origin,
            on_ground,
            anchor,
            reel_half_step,
            chasing,
        },
    );
    // Whether the hook is actively steering this frame (survives the abort branches above).
    let hook_engaged = bot.hook.phase != HookPhase::Idle;
    let hook_lock = matches!(
        bot.hook.phase,
        HookPhase::Flight | HookPhase::Reel | HookPhase::Ballistic
    );

    // Rocket-jump leg driver: walk to the launch cell with the RL out, settle the aim on the solved
    // fire angles, jump, fire after the solved delay, ride the blast arc onto the ledge. Same shape as
    // the hook driver — a snapshot in, an `RjDrive` out that the code below applies.
    let rj = rj::drive_rj(
        graph,
        bot,
        rj::RjCtx {
            rj_active,
            cur_leg,
            enemy,
            chasing,
            now,
            weapon,
            origin,
            on_ground,
            attack_finished,
            weapons_hot,
            has_rl,
            ammo_rockets,
            health,
            armortype,
            armorvalue,
            quad,
            knobs: s.rj_knobs,
        },
    );
    let rj_engaged = bot.rj.phase != RjPhase::Idle;
    let rj_lock = matches!(bot.rj.phase, RjPhase::Rise | RjPhase::Ballistic);

    if let Some(t) = hook.look_target {
        if (t - eye).xy().length() > 1.0 {
            look = angles_to(eye, t);
        }
    }
    // Rocket-jump look: Stance/Rise hold the solved fire *angles* directly (the shot flies along the
    // view); Ballistic looks at the landing *point* (reprojected like the hook's).
    if let Some(a) = rj.look_target_angles {
        look = a;
    } else if let Some(t) = rj.look_target {
        if (t - eye).xy().length() > 1.0 {
            look = angles_to(eye, t);
        }
    }
    // Audience watch (arena Spectate): eyes on the fighter the mode chose — already LOS-validated
    // there and held ~1-2s. Post-hoc like the hook/rj overrides, so bhop steering and the route
    // look-ahead stay untouched; the aim spring in `emit` turns it into a human pan and perception
    // follows through `bot.aim.angles`. Same 48u degenerate-angle guard as the nav look. Audience bots
    // have no grapple/RL, so the hook/rj guard is belt-and-braces.
    if !hook_engaged && !rj_engaged {
        if let Some(t) = watch_point {
            if (t - eye).xy().length() > 48.0 {
                look = angles_to(eye, t);
            }
        }
    }

    let (mut forward, mut side, mut buttons, mut impulse) = (0, 0, 0, 0);
    // Politely stop short only when tailing a human or roaming (`Objective::polite`). Everything
    // else walks all the way in: an item pickup needs its touch to fire, a race checkpoint is a
    // hull-sized touch box, and when hunting an enemy stopping short would halt the bot 64u out
    // — e.g. right at a door between it and its target (the combat layer manages the actual
    // fighting distance once it has line of sight). `polite` is never set alongside a chase or
    // a Fight intent, so it alone decides.
    // Arrival slowdown: when a grounded Walk/Step leg is about to hand off to a sharply-turning next
    // leg and continuing straight past the waypoint would run off a ledge, ease the wish down as we
    // close in so we arrive slow enough to make the turn instead of overshooting the lip. Double-gated
    // — a sharp turn AND a real drop straight ahead — so flat corners and the grid's 45° zigzag keep
    // full speed, and a thin balance path (no turn, or floor continuing past the waypoint) is untouched.
    let wish_scale = {
        let eligible = on_ground
            && !bhop_active
            && !sj_active
            && !hook_engaged
            && !rj_engaged
            && matches!(kind, Some(LinkKind::Walk | LinkKind::Step))
            && dist < TURN_SLOW_RADIUS;
        let cur_dir = to_wp.normalize_or_zero();
        let next_dir = bot
            .route
            .get(bot.route_pos + 1)
            .map(|&nl| (graph.cell_origin(graph.link_target(nl)).xy() - waypoint.xy()).normalize_or_zero());
        let sharp =
            cur_dir != Vec2::ZERO && next_dir.is_some_and(|nd| nd != Vec2::ZERO && cur_dir.dot(nd) < TURN_SLOW_COS);
        let over_ledge = eligible
            && sharp
            && bsp.is_some_and(|bsp| {
                let feet = waypoint - Vec3::new(0.0, 0.0, ORIGIN_TO_FEET);
                crate::hazard::ledge_ahead(&|p| bsp.is_solid(p), feet, Vec3::new(cur_dir.x, cur_dir.y, 0.0))
            });
        if over_ledge {
            (dist / TURN_SLOW_RADIUS).clamp(TURN_SLOW_MIN, 1.0)
        } else {
            1.0
        }
    };
    // Edge margin: on a grounded walk/step (or final-approach) leg while NOT bhopping, steer the
    // slow-walk wish away from a wall or drop beside the line of travel — the inner edge of an
    // open-cored spiral, a catwalk lip, a doorframe — instead of drifting into it while homing on the
    // next cell centre (which sits on the grid, up to a hull-width from the true edge). Bhop's own
    // bearing was made near-field-aware above, so this stays gated to the non-bhop walk.
    //
    // With `rtx_bot_nearfield` on, this reads the fine (8u) near-field clearance grid ensured before
    // the bhop block (see `nearfield`): wall-aware, self-cancelling through doorways/thin beams. When
    // the grid is off, absent, or the bot's been shoved off its own field, it falls back to the
    // drop-only `hazard::edge_bias` probe (Walk/Step only, as before — a final-approach `None` leg got
    // no push in that path historically).
    let nf_ground = on_ground
        && !on_air
        && bot.bhop.phase == bhop::Phase::Off
        && !bhop_active
        && !sj_active
        && !hook_engaged
        && !rj_engaged
        && dist > ARRIVE_RADIUS;
    // Certified walk tracking (see `walksim`) — the grounded sibling of the hop planner above. On a
    // walk/step leg, only a pursuit line the pmove rollout *proved* stays on the floor may own the
    // wish; while one does, the edge margin, the glide's chord veto and both ledge brakes below stand
    // down. They exist for the case the rollout reports boxed, and firing them under a proven line is
    // what made a bot brake mid-stride on a stair diagonal. Skipped whenever another driver owns the
    // feet (bhop/speed-jump/hook/rj/airborne), in water (pmove models none of it), or on a magnet
    // detour, which deliberately steps off the line the rollout would certify.
    let walk_corridor = host.cvar_bool(c"rtx_bot_walkplan")
        && !on_air
        && !in_water
        && bot.bhop.phase == bhop::Phase::Off
        && !bhop_active
        && !sj_active
        && !hook_engaged
        && !rj_engaged
        && !magnet_bend
        && matches!(kind, Some(LinkKind::Walk | LinkKind::Step));
    // The line the walk certifier, its freshness check and the aim point all share, built once so
    // they cannot disagree about where the route is.
    let route_line: Option<Vec<Vec3>> = walk_line_pts(graph, bot, cur_leg);
    if !walk_corridor {
        bot.walk = None; // another driver owns the frame, or this isn't ground to certify
    } else if on_ground {
        // The certificate holds while it's fresh, still describes the route we're on, and the bot is
        // still inside the tube the rollout enforced — being shoved off the line (a body, a blast)
        // voids it, because the policy was only ever proven from on it.
        let fresh = bot.walk.is_some_and(|w| {
            now - w.since <= WALK_RECERT
                && cur_leg.is_some_and(|l| w.legs[..w.n as usize].contains(&l))
                && route_line.as_ref().is_some_and(|pts| {
                    walksim::off_line(pts, origin)
                        .is_some_and(|off| off.lateral <= walksim::LATERAL_TOL && off.dz.abs() <= walksim::Z_TOL)
                })
        });
        if !fresh {
            bot.walk = None;
            // Too slow to be carried anywhere by a frame of wish, so nothing needs certifying (and
            // the brakes are inert down there too). Otherwise re-roll, unless a fan just came back
            // boxed and the back-off hasn't lapsed.
            if speed > LEDGE_MIN_SPEED && now >= bot.walk_retry {
                if let (Some(bsp), Some(route_pts)) = (bsp, route_line.clone()) {
                    let cvf = |name: &std::ffi::CStr, d: f32| {
                        let v = host.cvar(name);
                        if v > 0.0 {
                            v
                        } else {
                            d
                        }
                    };
                    let pm = crate::pmove_sim::PmParams {
                        gravity: cvf(c"sv_gravity", 800.0),
                        accel: cvf(c"sv_accelerate", 10.0),
                        friction: cvf(c"sv_friction", 4.0),
                        stopspeed: 100.0,
                        maxspeed: cvf(c"sv_maxspeed", 320.0),
                    };
                    // Shut gates and foreign teleport triggers are invisible to the clip hull, so the
                    // rollout would happily certify a walk straight through one. Same volumes the
                    // near-field stamps unwalkable, for the same reason.
                    let boxes: Vec<(Vec3, Vec3)> = nearfield_gates(graph, gate_closed, origin)
                        .map(|gi| {
                            let g = graph.gate(gi);
                            (g.closed_min, g.closed_max)
                        })
                        .chain(nearfield_teleports(graph, origin, waypoint).map(|ti| graph.tele_volumes()[ti]))
                        .collect();
                    let has_haz = graph.has_hazards();
                    let is_hazard = |p: Vec3| {
                        has_haz && {
                            let c = bsp.pointcontents(p);
                            c == crate::bsp::CONTENTS_LAVA || c == crate::bsp::CONTENTS_SLIME
                        }
                    };
                    let st = crate::pmove_sim::PmState {
                        origin,
                        vel: Vec3::new(v_xy.x, v_xy.y, 0.0),
                        on_ground: true,
                        jump_held: false,
                    };
                    match walksim::plan_walk(bsp, &is_hazard, &boxes, &route_pts, st, &pm) {
                        Some(plan) => {
                            let tail = &bot.route[bot.route_pos..];
                            let n = tail.len().min(state::WALK_LEGS);
                            let mut legs = [0u32; state::WALK_LEGS];
                            legs[..n].copy_from_slice(&tail[..n]);
                            bot.walk = Some(state::WalkGuide {
                                plan,
                                since: now,
                                legs,
                                n: n as u8,
                            });
                        }
                        // Boxed: nothing tracks from here, so the brakes own until the back-off lapses.
                        None => bot.walk_retry = now + WALK_RETRY,
                    }
                }
            }
        }
    }
    // Airborne frames inside the corridor leave the latch alone: walking down a staircase leaves the
    // floor for a few ticks a riser, and the rollout already certified those gaps.
    let walk_live = bot.walk.is_some();

    let edge_push = if nf_ground && !walk_live {
        let near_push = nf_active.then(|| bot.near.as_ref()?.steer_push(origin)).flatten();
        near_push.unwrap_or_else(|| {
            // No field → today's drop-only probe, which only runs on a real Walk/Step leg.
            if matches!(kind, Some(LinkKind::Walk | LinkKind::Step)) {
                bsp.map_or(Vec3::ZERO, |bsp| {
                    let feet = origin - Vec3::new(0.0, 0.0, ORIGIN_TO_FEET);
                    let travel = if speed > 40.0 {
                        v_xy.normalize_or_zero()
                    } else {
                        to_wp.normalize_or_zero()
                    };
                    crate::hazard::edge_bias(&|p| bsp.is_solid(p), feet, Vec3::new(travel.x, travel.y, 0.0))
                })
            } else {
                Vec3::ZERO
            }
        })
    } else {
        Vec3::ZERO
    };

    // Where the feet aim: the certified pursuit when one is live, else the near-field glide.
    let heading = if let Some((w, pts)) = bot.walk.zip(route_line.as_ref()) {
        // The certified policy, re-evaluated at the live state: project onto the same route-anchored
        // line the rollout used and take `aim_point` from there, so the bot pursues exactly the point
        // that was certified — including the lateral lane offset, which on a diagonal staircase is
        // what keeps the feet on tread instead of over the corner the cell-centre line cuts.
        // Deliberately raw: no `chord_clear` veto, because the rollout *is* the certificate, and a
        // stricter 8u-grid opinion about the same ground is what used to drop the smoothing exactly
        // where a stair needed it.
        let s = walksim::arc_at(&pts, origin);
        walksim::aim_point(&pts, s, w.plan).xy() - origin.xy()
    } else {
        // Glide look-ahead: on a grounded walk/step leg, if the near-field certifies a straight chord
        // to a point ~96u down the corridor stays on clear floor, aim the feet at *that* instead of the
        // raw 32u next cell — straightening the grid's constant 45° zigzag. Everything else still keys
        // on the raw waypoint (leg advancement, `wish_scale`, the magnet, the watchdogs): the chord
        // follows the leg polyline, so it passes within `ARRIVE_RADIUS` of each cell centre, exactly
        // the magnet's argument. Off on the final approach and whenever the chord isn't clear.
        let want_glide = nf_ground
            && nf_active
            && host.cvar_bool(c"rtx_bot_glide")
            && matches!(kind, Some(LinkKind::Walk | LinkKind::Step));
        let glide = want_glide.then_some(bot.near.as_ref()).flatten().and_then(|nf| {
            let g = corridor_point(graph, &bot.route, bot.route_pos, origin, NEAR_GLIDE_AHEAD);
            nf.chord_clear(origin, g, NEAR_GLIDE_MARGIN).then_some(g)
        });
        glide.map_or(to_wp, |g| g.xy() - origin.xy())
    };

    let close_enough = final_leg && polite && dist <= POLITE_DIST;
    if !close_enough {
        let (fwd, right, _) = angle_vectors(angles);
        let dir = (Vec3::new(heading.x, heading.y, 0.0).normalize_or_zero() + edge_push * EDGE_BIAS_WEIGHT)
            .normalize_or_zero();
        forward = (fwd.dot(dir) * MOVE_SPEED * wish_scale) as i32;
        side = (right.dot(dir) * MOVE_SPEED * wish_scale) as i32;
    }
    // Jump only while on the ground: QW pmove jumps once per press and needs the button
    // released (airborne) before it'll fire again. Gating on ground state pulses it correctly,
    // so a jump that falls short is retried on the next landing instead of the bot getting
    // stuck holding +jump against a ledge.
    // Curl-jump knobs for plain jump legs (see cvars): a run-up speed gate on the takeoff, plus the
    // in-air curl hold-fraction and gain applied below. All default to today's behavior.
    let jump_maxspeed = {
        let m = host.cvar(c"sv_maxspeed");
        if m > 0.0 {
            m
        } else {
            320.0
        }
    };
    let jump_runup = host.cvar(c"rtx_jump_runup").max(0.0);
    let curl_hold = host.cvar(c"rtx_jump_curl_hold").clamp(0.0, 0.95);
    let curl_gain = {
        let g = host.cvar(c"rtx_jump_curl_gain");
        if g > 0.0 {
            g
        } else {
            bhop::AIR_CORRECT_GAIN_DEFAULT
        }
    };
    // Run-up gate: on a plain jump leg, hold the takeoff jump until the bot carries speed *toward the
    // waypoint* (`jump_runup · maxspeed`), so it leaves the lip moving instead of jumping from a
    // standstill and air-accelerating into a stub arc. Escapes at the lip and when disabled keep it from
    // wedging; `force_jump` (the stuck detector) and the bhop controller bypass it too.
    let runup_ok = jump_runup_ok(v_xy, to_wp, dist, jump_runup, jump_maxspeed);
    let plain_jump_leg = matches!(kind, Some(LinkKind::JumpGap | LinkKind::DoubleJump));
    // How much floor is left along travel. The run-up gate is a per-frame test on a quantity the bot's
    // own motion changes — it holds the takeoff while the bot turns onto the jump line, and the turn
    // takes most of a short ledge — so the gate is in a race with the edge and can lose it by one
    // frame. `jump_runup_ok`'s own escape ("the lip is near, jump now") cannot settle that race: it
    // reads `dist`, the distance to the waypoint *across the gap*, which at takeoff is the whole jump
    // (110u on dm3's spiral crest) and only falls under `JUMP_NOW_DIST` mid-flight. Measure the floor
    // instead — see [`crate::hazard::lip_ahead`].
    let lip = if on_ground && plain_jump_leg && speed > LEDGE_MIN_SPEED {
        let vdir = v_xy.normalize_or_zero();
        bsp.filter(|_| vdir != Vec2::ZERO).and_then(|b| {
            crate::hazard::lip_ahead(
                &|p| b.is_solid(p),
                origin - Vec3::new(0.0, 0.0, ORIGIN_TO_FEET),
                Vec3::new(vdir.x, vdir.y, 0.0),
                LIP_LOOKAHEAD,
            )
        })
    } else {
        None
    };
    // Last-frame commitment: the floor runs out within a frame or two of travel, and the bot is going
    // roughly the way the leg wants (so this can't fire it off a side edge while it is still turning
    // in). Taking off with imperfect alignment beats walking into the gap — the arc has air control,
    // the walk has nothing.
    let lip_now = lip.is_some_and(|d| d <= speed * frametime * LIP_FRAMES + LIP_PAD)
        && v_xy.normalize_or_zero().dot(to_wp.normalize_or_zero()) > LIP_ALIGN_COS;
    // Record what the gate saw, not just its verdict: a takeoff that never fires and one that fires a
    // frame after the lip are the same trace without the leg kind, the carried speed and the floor left.
    bot.takeoff = state::TakeoffDiag {
        leg: kind,
        runup: v_xy.dot(to_wp.normalize_or_zero()),
        wp: dist,
        lip,
        ok: (runup_ok || lip_now) && plain_jump_leg,
        sj_held: sj_hold,
    };
    if on_ground && (force_jump || bhop_cmd.is_some_and(|c| c.jump) || (plain_jump_leg && (runup_ok || lip_now))) {
        buttons |= BUTTON_JUMP;
    }
    // Mid-air (double) jump: rtx grants one air jump per air travel. On a double-jump leg, spend it
    // near the apex (`vz` small) to restack the arc and clear the wider gap; on a plain jump leg,
    // spend it as a *recovery* only when we're descending short of a higher target (an undershoot).
    // `air_jumped` gates re-pressing it, and the engine ignores it when the floor's close (landing).
    if !on_ground && !air_jumped && vz <= 30.0 {
        let air_jump = match kind {
            Some(LinkKind::DoubleJump) => true,
            Some(LinkKind::JumpGap) => vz < 0.0 && waypoint.z > origin.z + 20.0,
            _ => false,
        };
        if air_jump {
            buttons |= BUTTON_JUMP;
        }
    }

    // Opening a gate's button: once at it, face it and push (walk in) or shoot it.
    if let Some(errand) = bot.gate.errand {
        let gi = errand.index;
        let g = graph.gate(gi);
        let at_button =
            bot.route_pos >= bot.route.len() || (origin.xy() - graph.cell_origin(g.button_cell).xy()).length() < 40.0;
        if at_button {
            angles = angles_to(eye, g.aim);
            let (pitch, yaw) = (angles.x, angles.y);
            look = angles; // the button needs a precise aim; the spring settles on it while parked
            buttons &= !BUTTON_JUMP;
            if g.shoot {
                // Switch to the shotgun and fire at the activator. If it's so high above us that
                // aiming would exceed the view-pitch limit (the shot lands under it), back
                // straight away first for a shallower angle — ground movement stays horizontal
                // regardless of look pitch, so we can keep aiming up while backpedalling. Only
                // fire while the activator is ready (not in its post-trigger cooldown).
                impulse = IMPULSE_SHOTGUN;
                if pitch < -68.0 {
                    forward = (-MOVE_SPEED) as i32;
                    side = 0;
                } else {
                    (forward, side) = (0, 0);
                    if weapon == Weapon::Shotgun && gate_ready[gi] {
                        buttons |= BUTTON_ATTACK;
                    }
                }
            } else {
                // Walk into the button to push it.
                let (fwd, right, _) = angle_vectors(Vec3::new(0.0, yaw, 0.0));
                let dir = (g.aim - origin).normalize_or_zero();
                forward = (fwd.dot(dir) * MOVE_SPEED) as i32;
                side = (right.dot(dir) * MOVE_SPEED) as i32;
            }
        }
    }

    // The frame's movement as a world-space velocity, decoupled from the view: smoothing the eyes
    // below can't change where the bot goes, and combat can steer independently of its aim.
    let (nf, nr, _) = angle_vectors(angles);
    let mut move_world = nf * forward as f32 + nr * side as f32;

    // Unified air steering (always on): a yaw-synced air-strafe wish toward a landing point, in
    // **world space** so the wish actually turns the velocity — a straight wish the 30-ups air-accel
    // cap all but ignores — while the eyes keep smoothing toward the target through the normal aim
    // spring (no raw-view channel, so the strafe never twitches the view). `None` when we're basically
    // on top of the target (keep whatever wish we had). See [`bhop::air_correct`].
    let air_wish = |target: Vec3, gain: f32| -> Option<Vec3> {
        let to = target.xy() - origin.xy();
        (to.length() > 24.0).then(|| {
            let dt = frametime.clamp(0.001, 0.05);
            let accel = host.cvar(c"sv_accelerate");
            let maxspeed = host.cvar(c"sv_maxspeed");
            let a_max = bhop::air_accel_max(
                if accel > 0.0 { accel } else { 10.0 },
                if maxspeed > 0.0 { maxspeed } else { 320.0 },
                dt,
            );
            let s = bhop::air_correct(v_xy, yaw_of(to), a_max, dt, gain);
            let w = bhop::wishdir_fs(s.view_yaw, s.forward, s.side);
            Vec3::new(w.x, w.y, 0.0) * MOVE_SPEED
        })
    };
    // Airborne on a plain jump leg: ride the arc toward the landing (the pinned waypoint — the
    // `on_air` gate keeps it on the link target) with the air-strafe wish. `look` stays as steered
    // above, so the eyes pan smoothly toward the landing while the strafe curves the trajectory.
    // Curl-hold: a jump link certifies only the straight source→target center line, but the bot took
    // off offset and homing back onto the target can sweep the arc into an edge wall. For the first
    // `curl_hold` fraction of the gap, hold the takeoff heading (steer along our own velocity — an
    // inert coast) so the near wall is cleared, then curl onto the target at `curl_gain`.
    if on_air && !on_ground {
        let held = curl_hold > 0.0
            && cur_leg.is_some_and(|leg| {
                let src = graph.cell_origin(graph.link_source(leg)).xy();
                let tgt = graph.cell_origin(graph.link_target(leg)).xy();
                let done = 1.0 - (tgt - origin.xy()).length() / (tgt - src).length().max(1.0);
                done < curl_hold
            });
        let wish = if held {
            air_wish(origin + Vec3::new(v_xy.x, v_xy.y, 0.0), curl_gain)
        } else {
            air_wish(waypoint, curl_gain)
        };
        if let Some(w) = wish {
            move_world = w;
        }
    }

    // Hazard-edge brake: if the near-field sees a fatal drop or lava edge close ahead along the
    // *velocity* — nearer than the bot can bleed its speed before the lip — hard-brake to a stop rather
    // than sliding or hopping into it. Unlike the geometric ledge brake below this is hazard-aware
    // (catches flush lava the clip hull can't see) and fires while *bhopping* too: a fast bot's
    // momentum, not its wish direction, is what carries it off, so the near-field bearing bend and the
    // leap-suppression above don't stop it — the speed itself must go. The hop is nulled for the frame
    // so the reverse wish actually drives (`emit` ignores `move_world` while a hop is). Off during a
    // jump run-up (`jump_at_hand`) — the bot needs that speed to clear the gap — and inert on open
    // ground, where `edge_ahead` finds no edge. `nf_active` already limits it to grounded walk/step/
    // approach legs (a Drop leg descends; a jump leg leaps), and the near-field grid is built there.
    // Also off while a walk plan is live: the rollout proved this line keeps its feet on the floor for
    // longer than the certificate is trusted, and the deviation check above re-arms this the very frame
    // that proof stops covering the ground under the bot.
    if nf_active && on_ground && !jump_at_hand && !walk_live && speed > LEDGE_MIN_SPEED {
        if let Some(nf) = bot.near.as_ref() {
            let vdir = v_xy.normalize_or_zero();
            let stop =
                (speed * BRAKE_REACT + speed * speed / (2.0 * BRAKE_DECEL)).clamp(nearfield::NEAR_RES, BRAKE_MAX_LOOK);
            let dir3 = Vec3::new(vdir.x, vdir.y, 0.0);
            if nf.edge_ahead(origin, dir3, stop + nearfield::NEAR_RES) <= stop {
                move_world = -dir3 * MOVE_SPEED;
                bhop_cmd = None;
            }
        }
    }

    // Ledge brake: a grounded bot on a Walk/Step leg whose *velocity* has drifted well off the corridor
    // to its waypoint (an overshot corner — e.g. run straight at a stair side) and is one stride from
    // running off the floor: kill the wish and thrust backward to stop before the lip. After the
    // navmesh's `ground_along` fix an *aligned* Walk/Step leg always has floor under it, so a drop
    // along velocity is unintended; and balancing along a thin wall-top keeps velocity aligned to the
    // waypoints, so the misalignment gate keeps this dead there. Dead too while airborne, bhopping,
    // speed-/rocket-jumping, or hooking — those own their motion (and the hook/rj overrides below win).
    if let Some(bsp) = bsp {
        // A certified pursuit cuts stair corners on purpose, so the misalignment this brake keys on is
        // exactly what a proven line looks like — it must not fire under one.
        let braking = on_ground
            && !on_air
            && bot.bhop.phase == bhop::Phase::Off
            && !bhop_active
            && !sj_active
            && !hook_engaged
            && !rj_engaged
            && !walk_live
            && matches!(kind, Some(LinkKind::Walk | LinkKind::Step))
            && speed > LEDGE_MIN_SPEED;
        if braking {
            let vdir = v_xy.normalize_or_zero();
            let aligned = vdir.dot(to_wp.normalize_or_zero()) >= LEDGE_ALIGN_COS;
            let vdir3 = Vec3::new(vdir.x, vdir.y, 0.0);
            let feet = origin - Vec3::new(0.0, 0.0, ORIGIN_TO_FEET);
            if !aligned && crate::hazard::ledge_ahead(&|p| bsp.is_solid(p), feet, vdir3) {
                move_world = -vdir3 * MOVE_SPEED;
            }
        }
    }

    // Hook override: stand still while reeling/flying (the pull owns velocity; ground input would
    // fight it or, airborne, break the frictionless arc), or walk toward the throw stance in Aim.
    if hook_engaged {
        move_world = match hook.approach {
            _ if hook.stand => Vec3::ZERO,
            Some(src) => Vec3::new(src.x - origin.x, src.y - origin.y, 0.0).normalize_or_zero() * MOVE_SPEED,
            None => Vec3::ZERO,
        };
        buttons &= !BUTTON_JUMP;
        if hook.select {
            impulse = IMPULSE_GRAPPLE;
        }
    }

    // Rocket-jump override: walk to the launch cell (Stance), stand and hold the aim (Rise), or ride
    // the arc with the world-space air-strafe wish toward the landing (Ballistic — the same in-flight
    // correction as a plain jump leg, curving the blast arc onto the target). The jump itself is
    // pressed post-spring in `emit` (via `rj.jump_ready`); the rocket fires on the driver's `rj.fire`.
    if rj_engaged {
        move_world = match rj.approach {
            _ if rj.stand => Vec3::ZERO,
            Some(src) => Vec3::new(src.x - origin.x, src.y - origin.y, 0.0).normalize_or_zero() * MOVE_SPEED,
            None => rj
                .air_correct
                .and_then(|t| air_wish(t, bhop::AIR_CORRECT_GAIN_DEFAULT))
                .unwrap_or(Vec3::ZERO),
        };
        buttons &= !BUTTON_JUMP; // the launch jump is pressed only via `emit`'s post-spring gate
        if rj.select {
            impulse = IMPULSE_ROCKET;
        }
        if rj.fire {
            buttons |= BUTTON_ATTACK;
        }
    }

    // Depth, once everything above has settled the horizontal wish.
    //
    // The navmesh is built from standable floor, so a route through water is a line of cells along
    // the *bottom* of it — followed literally, a crossing that drowns. `swim::vertical_wish` decides
    // depth from what the bot can perceive (where the route goes next in 3D, whether there is air
    // overhead, how much is left) and returns it as a velocity, so it composes into the same
    // world-space wish instead of being a separate mode. The result is a swimmer that moves
    // diagonally toward where it is going, which is what a person does.
    //
    // Applied last so it survives the hook/rj/reverse branches above, and only in water — out of it
    // the vertical term is meaningless and `wish::Regime::Ground` would drop it anyway.
    if in_water {
        let surface = bsp.and_then(|b| crate::hazard::surface_z(&|p| b.pointcontents(p), origin));
        // Depth aims at the top of the climb, not the next rung of it.
        //
        // The water is layered every 64 units, and `seek` is proportional — so aimed at the immediate
        // leg the upward wish fades to nothing as the bot reaches each layer, it hovers there until the
        // leg advances, then sets off again. That is an ascent that pulses once per layer, and it is
        // what "the bots shake a lot swimming up from the depths" looks like from the outside. A
        // swimmer heading for the surface is doing *one* climb, so give it one target: follow the route
        // up while it keeps being a swim and keeps rising, and seek the height it ends at. The
        // horizontal still comes from the current leg, so the path is unchanged — only the depth stops
        // being a staircase.
        //
        // Only where there is a surface to climb to, though. A flooded tunnel — dm3's pentagram
        // crossing — has no air overhead and no single ascent to make: its legs' heights *are* the
        // shape of the passage, and looking past them cuts the corner into the ceiling. Measured, the
        // crossing lost a second and a half each way. So the far aim is for open water, and confined
        // water is swum leg by leg exactly as the route describes it.
        let climb_z = {
            let mut z = waypoint.z;
            let mut i = if surface.is_some() {
                bot.route_pos + 1
            } else {
                bot.route.len()
            };
            while let Some(&l) = bot.route.get(i) {
                if graph.link_kind(l) != LinkKind::Swim {
                    break;
                }
                let tz = graph.cell_origin(graph.link_target(l)).z;
                if tz <= z {
                    break;
                }
                z = tz;
                i += 1;
            }
            z
        };
        move_world.z = swim::vertical_wish(
            &swim::Sense {
                submerged,
                surface,
                air_left,
                origin,
                aim: Vec3::new(waypoint.x, waypoint.y, climb_z),
                // A route leg's height always describes this water. So does a target directly
                // overhead — which is what the anti-drown override hands the bot, and what a bot
                // resolved *onto* its own goal cell has instead of legs, since `find_path(c, c)` is
                // empty. Distrusting that put a swimmer 26 units under the exit it was sent to and
                // made it swim down.
                aim_trusted: bot.route_pos < bot.route.len()
                    || (waypoint - origin).truncate().length() <= swim::AIM_TRUST_XY,
            },
            MOVE_SPEED,
        );

        // A corridor look-ahead for the *horizontal* was tried here and removed: averaging the next
        // few legs into one bearing did not settle the ascent (the zigzag count moved around without
        // trending), which means the swing is not the route's staircase being followed faithfully. It
        // is downstream of here. Left as a note so the same shape is not tried a third time.

        // Everything above composes the wish from the route, which knows nothing about what is in
        // the way. `PM_WaterMove` clips against the planes it hits, so a wish pressed square into a
        // corner nets zero and the bot hangs there at full effort. Deflect it around the solid.
        //
        // Before the exit latch on purpose: the haul-out's press *into* the bank is the whole
        // mechanism (`PM_CheckWaterJump` probes along it), and deflecting that would read the bank
        // as a wall and slide the bot off every climb it tried to make.
        if let Some(bsp) = bsp {
            let probe = |a, b| {
                let tr = bsp.hull1_trace(a, b);
                (tr.fraction, tr.plane_normal)
            };
            move_world = swim::deflect(&probe, origin, move_world, waypoint);
        }

        // Climbing out is a *committed* move, and it owns the eyes while it lasts.
        //
        // Leaving water in QuakeWorld is `PM_CheckWaterJump`, and it probes for the bank along
        // `pm_forward` — the view. A bot swimming past a ledge with its view down its onward path
        // probes into open water and is never granted the exit, however long it presses against the
        // side. That is the whole of "it swims along the bridge and never gets out": the route is
        // *along* the bank, so the look-ahead points along it too, and the one direction the bot
        // never faces is the one that would let it climb.
        //
        // So on an exit leg the bot turns square to the bank, tilts up, and drives up and forward —
        // and only then resumes its path. Yaw is what the engine's probe reads; the pitch and the
        // upward wish are what carry it over the lip once launched.
        //
        // *Committed* is load-bearing, and this used to re-decide it every frame instead. The arming
        // test asks whether the exit is above the bot — which is exactly the quantity the bot is
        // closing as it climbs, so a few units of bob flip it on and off and the view snaps between
        // the exit stance and the path look several times a second. Once begun, the haul-out is held
        // until the bot is out of the water, the route moves to another leg, or it has plainly
        // failed and the ordinary route steering should have its eyes back.
        //
        // A haul-out also needs somewhere to haul out *to*. `PM_CheckWaterJump` cannot lift a bot
        // through a roof, so under a bridge deck the stance is a bot holding a 45-degree view and a
        // full upward wish against the underside of the span until the timeout releases it — which is
        // the brief stick under dm3's bridge. No surface overhead, no climb: swim the route instead.
        // Which way out, found by asking the engine rather than inferring it.
        //
        // Two earlier versions guessed the bank: the bearing to the next cell, then the struck plane's
        // normal. Both are indirections, and both fail in the case that matters most — a bot that
        // fumbled a hop and dropped in beside a wall, whose route points somewhere else and whose
        // nearest surface is not the one it can climb. `swim::exit_yaw` mirrors `PM_CheckWaterJump`'s
        // probe and scans outward from the route's own bearing, so the answer is the most
        // forward-facing direction that genuinely works, and `None` means this is not a way out at all
        // — in which case the stance stands down and the route is swum rather than pressed.
        // Armed whenever the route is *on its way out*, not only on the one leg that rises.
        //
        // Requiring the current leg to climb is too narrow, and narrow in exactly the wrong place. At
        // the surface beside dm3's water bridge the current leg is usually a horizontal *rim* link, so
        // the stance never armed and the bot followed the rim — swimming the length of the bridge with
        // the bit it could have climbed passing by on its left. What decides that a bot wants out is
        // the route reaching dry land shortly, so that is the test: any leg within the next few whose
        // target is out of the water.
        //
        // Read the tail with `get`, not by indexing: a route can be *cleared* without `route_pos`
        // being rewound — that is the convention every other reader here follows — and several of
        // the failure paths above (progress watchdog, abandoned jump commitments, a lost plat) clear
        // it part-way through this very function. A swimming bot that hit one of those would index
        // a stale position into an empty slice, and since this runs inside an engine callback that
        // cannot unwind, the panic took the whole server with it rather than the frame.
        let leaving = bot
            .route
            .get(bot.route_pos..)
            .into_iter()
            .flatten()
            .take(EXIT_LEGS_AHEAD)
            .any(|&l| !graph.cell_in_water(graph.link_target(l)));
        let exit_leg = cur_leg.filter(|_| leaving && surface.is_some());
        let held = bot
            .water_exit
            .is_some_and(|c| Some(c.leg) == exit_leg && now - c.since < WATER_EXIT_MAX);
        if !held {
            let face =
                bsp.and_then(|b| swim::exit_yaw(&|p| b.pointcontents(p), origin, (waypoint - origin).truncate()));
            bot.water_exit = exit_leg
                .filter(|_| face.is_some())
                .map(|leg| Commit { leg, since: now });
            // Chosen once, with the commitment, and held with it. Which directions the engine grants
            // depends on the bot's own height, so a rising bot that re-asks every frame watches the set
            // change under it and swings between members of it. The grant only has to hold at the
            // instant of the jump, so choosing once is both steadier and no less correct.
            bot.water_exit_face = bot.water_exit.and(face);
        }
        match bot.water_exit_face.filter(|_| bot.water_exit.is_some()) {
            Some(out) => {
                look = Vec3::new(WATER_EXIT_PITCH, yaw_of(out), 0.0);
                move_world = Vec3::new(out.x, out.y, 0.0) * MOVE_SPEED + Vec3::Z * MOVE_SPEED;
            }
            // Not climbing out: leave the view to the eye chain above.
            //
            // Pointing it along the wish was tried, so that an ascending swimmer would look up rather
            // than flatly ahead. It looked worse, not better: the horizontal wish still swings, and
            // aiming the eyes straight down it removed the only smoothing in front of that swing. The
            // view should follow the swim, but only once the swim itself is steady.
            None => {}
        }
    } else {
        // Out of the water: the haul-out is over, however it ended.
        bot.water_exit = None;
        bot.water_exit_face = None;
    }

    // Bundle the frame's decisions into one command for the combat/grenade overlays to mutate.
    let cmd = BotCmd {
        look,
        move_world,
        buttons,
        impulse,
        shot: None,
    };

    // Traversal-critical legs lock out the combat/grenade overlays: `engage` owns movement and
    // clears +jump, which cancels the planner's route if done mid gap/double/speed jump.
    let traversal_lock = hook_lock
        || rj_lock
        || on_air
        || matches!(
            kind,
            Some(LinkKind::JumpGap | LinkKind::DoubleJump | LinkKind::SpeedJump)
        );
    let overlays_ok = !hook_engaged && !rj_engaged && !bhop_active && !traversal_lock;
    SteerOut {
        cmd,
        bhop_cmd,
        hook,
        rj,
        traversal_lock,
        overlays_ok,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fast_ground_waypoint_advances_across_its_forward_plane() {
        let source = Vec2::new(224.0, 1440.0);
        let target = Vec2::new(224.0, 1472.0);

        // A fast slalom may pass the 32u cell by more than the old 64u radial fallback. It is still
        // inside the directed corridor and must advance instead of steering back to the stale cell.
        assert!(ground_waypoint_arrived(
            Vec2::new(258.0, 1540.0),
            source,
            target,
            700.0,
            1.0 / 77.0,
        ));

        // Crossing the plane far outside the corridor is not progress along this path.
        assert!(!ground_waypoint_arrived(
            Vec2::new(400.0, 1540.0),
            source,
            target,
            700.0,
            1.0 / 77.0,
        ));

        // Being laterally close while still before the target does not skip it.
        assert!(!ground_waypoint_arrived(
            Vec2::new(250.0, 1460.0),
            source,
            target,
            700.0,
            1.0 / 77.0,
        ));
    }

    #[test]
    fn jump_runup_gate_wants_speed_toward_the_waypoint() {
        let fwd = Vec2::new(1.0, 0.0);
        // Standstill, far from the lip → blocked (no useless pogo).
        assert!(!jump_runup_ok(Vec2::ZERO, fwd, 200.0, 0.5, 320.0));
        // At the lip (< JUMP_NOW_DIST) → must jump now, whatever the speed.
        assert!(jump_runup_ok(Vec2::ZERO, fwd, 39.0, 0.5, 320.0));
        // Running toward the waypoint at 200 ups (> 0.5·320 = 160) → allowed.
        assert!(jump_runup_ok(Vec2::new(200.0, 0.0), fwd, 200.0, 0.5, 320.0));
        // Fast but perpendicular (no toward-component) → blocked.
        assert!(!jump_runup_ok(Vec2::new(0.0, 300.0), fwd, 200.0, 0.5, 320.0));
        // Gate disabled → always allowed (today's behavior).
        assert!(jump_runup_ok(Vec2::ZERO, fwd, 200.0, 0.0, 320.0));
    }

    /// Reproduces the leg-hold gap the transition-only guard left open: a chained speed jump
    /// committed at a borderline speed has no runway to build the rest on, so `sj_hold` just holds
    /// it grounded — and a live dual-instrument capture on the transition-only guard alone still
    /// found 21 ring stalls (20 under half of v_req, 13 at `route_pos == 1`), mostly `displacement`
    /// (the generic 0.7s stuck watchdog winning the race against the 4s sj-specific one). This is
    /// the every-tick check that closes it: fires only once its grace has passed, only while
    /// grounded (never mid-leap), and only when the leg is still genuinely blocked.
    #[test]
    fn chain_entry_hold_expired_fires_only_grounded_past_grace_and_blocked() {
        let commit = Some(Commit { leg: 7, since: 10.0 });
        // Still inside the settling grace → not yet.
        assert!(!chain_entry_hold_expired(commit, 10.1, true, true));
        // Past grace, but the bot actually has the speed now (or this isn't a chained link at all)
        // → nothing to divert.
        assert!(!chain_entry_hold_expired(commit, 10.4, true, false));
        // Past grace and blocked, but airborne — already committed to the leap's physics.
        assert!(!chain_entry_hold_expired(commit, 10.4, false, true));
        // Past grace, blocked, grounded → fires.
        assert!(chain_entry_hold_expired(commit, 10.4, true, true));
        // No commit at all (not on a speed-jump leg, or already diverted) → nothing to divert.
        assert!(!chain_entry_hold_expired(None, 10.4, true, true));
    }

    #[test]
    fn sj_abort_grounded_gate_controls_airborne_abort() {
        let v_req = 400.0;
        let low = 300.0;
        let high = 350.0;

        assert!(!sj_abort_should_fire(false, true, low, v_req));
        assert!(sj_abort_should_fire(false, false, low, v_req));
        assert!(sj_abort_should_fire(true, true, low, v_req));
        assert!(sj_abort_should_fire(true, false, low, v_req));
        assert!(!sj_abort_should_fire(true, true, high, v_req));
        assert!(!sj_abort_should_fire(true, false, high, v_req));
    }
}
