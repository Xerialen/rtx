// SPDX-License-Identifier: AGPL-3.0-or-later

//! Bot rocket jumps — the runtime side of the `navmesh::LinkKind::RocketJump` links.
//!
//! This phase supplies the **planning gate**: [`rocket_jump_extra`] tells the pathfinder how much to
//! surcharge rocket-jump links for a given bot, so one that can't currently fly a rocket jump (no
//! launcher, no rocket, too little health, or quad running) never plans a route through one. The
//! execution driver (a `HookPhase`-style machine) lands with the next phase.

use glam::{Vec3, Vec3Swizzles};

use super::state::{BotState, Driver, RjFire, RjOutcome, RjPhase, RjTelemetry};
use super::{ballistic_landing, Landing};
use crate::abi::EntVars;
use crate::defs::{Bits, Items, Weapon, BOT_MOVE_SPEED};
use crate::entity::EntId;
use crate::navmesh::{NavGraph, RJ_UNFIT_PENALTY};

/// Worst-case self-damage the planner budgets for: a point-blank floor rocket, unarmored (`120`
/// radius damage, `×0.5` self). Real solved links cost a touch less (~47–50, blast a few units out),
/// so gating on the worst case keeps a bot from planning a jump it lands too hurt to survive.
const RJ_WORST_SELF_DAMAGE: f32 = 60.0;
/// Health kept in reserve above the blast — a bot won't rocket-jump itself down to the wire, since it
/// often arrives into a fight (the conservative policy).
const RJ_HEALTH_MARGIN: f32 = 25.0;
/// A stationary solve is certified for ±16u of launch-position error. Stage toward its destination
/// inside that envelope: this avoids oscillating back across the nominal point while the view settles,
/// and gives mover-board landings clearance from the trigger-raised brush side.
const RJ_STATIONARY_STAGE_OFFSET: f32 = 12.0;
const RJ_PRECISE_STAGE_RADIUS: f32 = 3.0;

/// Health actually lost to a `dmg`-point blast after armor absorbs its share, mirroring `t_damage`:
/// `save = ceil(armortype·dmg)` clamped to `armorvalue`, and the knockback is *not* reduced.
fn effective_self_damage(dmg: f32, armortype: f32, armorvalue: f32) -> f32 {
    let save = (armortype * dmg).ceil().min(armorvalue);
    dmg - save
}

/// Target-side stationary release point and its settle radius. The offset plus radius never exceeds
/// the live stance/certification window.
fn stationary_stage(launch: Vec3, target: Vec3, stance: f32) -> (glam::Vec2, f32) {
    let radius = stance.min(RJ_PRECISE_STAGE_RADIUS).max(0.0);
    let offset = (stance - radius).clamp(0.0, RJ_STATIONARY_STAGE_OFFSET);
    let toward_target = (target - launch).xy().normalize_or_zero();
    (launch.xy() + toward_target * offset, radius)
}

/// `0.0` when this bot can fly a rocket-jump leg right now, else [`RJ_UNFIT_PENALTY`]. Unfit when it
/// lacks the rocket launcher or a rocket, has too little health for the worst-case self-blast (after
/// armor), or is running **quad** — `t_damage` applies quad *before* the mode split, so a self-rocket
/// under quad deals (and knocks back) 4×, which is both lethal and off-model for the solved arc.
pub(crate) fn rocket_jump_extra(v: &EntVars, quad_until: f32, now: f32) -> f32 {
    let effective = effective_self_damage(RJ_WORST_SELF_DAMAGE, v.armortype, v.armorvalue);
    let fit = v.items.has(Items::ROCKET_LAUNCHER)
        && v.ammo_rockets >= 1.0
        && quad_until <= now
        && v.health > effective + RJ_HEALTH_MARGIN;
    if fit {
        0.0
    } else {
        RJ_UNFIT_PENALTY
    }
}

/// The live `rtx_rj_*` tuning knobs (see [`crate::cvars`]), read once per frame in `sense` and
/// threaded through the driver (and `emit`'s aim-settle gate). All-Copy. Each default mirrors the
/// `RJ_*` constant it replaces, so an untouched server flies exactly as before — the knobs exist for
/// the tuning harness ([`crate::control`]) to sweep without a rebuild.
#[derive(Clone, Copy)]
pub(crate) struct RjKnobs {
    /// XY radius from the launch cell counted as "in stance" (`rtx_rj_stance`).
    pub stance: f32,
    /// Aim-settle tolerance before the jump presses, degrees (`rtx_rj_aim_tol`, used in `emit`).
    pub aim_tol: f32,
    /// Stance give-up timeout, seconds (`rtx_rj_stance_timeout`).
    pub stance_timeout: f32,
    /// Post-jump "still grounded ⇒ swallowed" timeout, seconds (`rtx_rj_liftoff_timeout`).
    pub liftoff_timeout: f32,
    /// Slack added to the solved airtime before the ballistic watchdog gives up (`rtx_rj_ballistic_slack`).
    pub ballistic_slack: f32,
    /// Added to the solved fire delay, seconds (`rtx_rj_delay_bias`; may be negative).
    pub delay_bias: f32,
    /// Added to the solved fire pitch, degrees (`rtx_rj_pitch_bias`; may be negative).
    pub pitch_bias: f32,
}

/// The rocket-jump driver's frame decisions, applied by `run_bot` after the graph/bot borrows end.
pub(crate) struct RjDrive {
    /// Stance/Rise: hold the view directly on these fire angles (not a look *point* — the shot flies
    /// straight along the view, and the timing matters more than a spring-settled point).
    pub look_target_angles: Option<Vec3>,
    /// Ballistic: look at the landing point (a natural travel look; the arc is already committed).
    pub look_target: Option<Vec3>,
    /// Hold ground still (Stance in-position / Rise).
    pub stand: bool,
    /// Stance: an explicit world-space ground wish. Keeping its magnitude lets the short-runway
    /// staging controller approach gently, brake, and then commit at full input.
    pub move_world: Option<Vec3>,
    /// Need to switch to the rocket launcher (impulse 7, re-sent every frame).
    pub select: bool,
    /// Stance→Rise trigger: press jump once the smoothed view has settled (resolved in `emit`).
    pub jump_ready: bool,
    /// Rise: fire the rocket this frame (pure timing — the aim was pre-settled in Stance).
    pub fire: bool,
    /// Ballistic: world-space wish toward the landing, for gentle in-flight air-strafe correction.
    pub air_correct: Option<Vec3>,
}

/// The per-frame snapshot the rocket-jump driver reads (all Copy). The fitness fields (`has_rl` …
/// `quad`) let it re-check at leg start that the bot can still fly the jump the planner chose.
pub(crate) struct RjCtx {
    pub rj_active: bool,
    pub cur_leg: Option<u32>,
    pub enemy: Option<EntId>,
    pub chasing: bool,
    pub now: f32,
    pub weapon: Weapon,
    pub origin: Vec3,
    pub velocity: Vec3,
    /// Live player-origin height on the mover for a compound platform RJ; `None` on static ground.
    pub platform_origin_z: Option<f32>,
    /// Live vertical mover velocity paired with `platform_origin_z`.
    pub platform_velocity_z: Option<f32>,
    pub on_ground: bool,
    pub attack_finished: f32,
    pub weapons_hot: bool,
    pub has_rl: bool,
    pub ammo_rockets: f32,
    pub health: f32,
    pub armortype: f32,
    pub armorvalue: f32,
    pub quad: bool,
    pub knobs: RjKnobs,
}

/// Fly a `LinkKind::RocketJump` leg: walk to the launch cell with the RL out and the view settled on
/// the solved fire angles, jump, fire the rocket after the solved delay, then ride the blast arc onto
/// the target ledge. The Stance→Rise jump and the aim settle are resolved post-spring in `emit`; the
/// fire is pure timing (the aim was prepaid in Stance). Per-phase timeouts are the stuck detection.
pub(crate) fn drive_rj(graph: &NavGraph, bot: &mut BotState, c: RjCtx) -> RjDrive {
    let RjCtx {
        rj_active,
        cur_leg,
        enemy,
        chasing,
        now,
        weapon,
        origin,
        velocity,
        platform_origin_z,
        platform_velocity_z,
        on_ground,
        attack_finished,
        weapons_hot,
        has_rl,
        ammo_rockets,
        health,
        armortype,
        armorvalue,
        quad,
        knobs: k,
    } = c;
    // The solved fire pitch, biased by the knob — the settle gate in `emit` measures the smoothed view
    // against exactly this, so bias and gate stay consistent.
    let pitch_bias = Vec3::new(k.pitch_bias, 0.0, 0.0);

    let mut look_target_angles = None;
    let mut look_target = None;
    let mut stand = false;
    let mut move_world = None;
    let mut select = false;
    let mut jump_ready = false;
    let mut fire = false;
    let air_correct = None;
    let mut failed = false;

    if rj_active {
        if let Some((leg, tr)) = cur_leg.and_then(|l| graph.rocket_jump_of_link(l).copied().map(|t| (l, t))) {
            let src = graph.cell_origin(graph.link_source(leg));
            let tgt = graph.cell_origin(graph.link_target(leg));
            let launch = tr.launch;
            // An enemy while not yet committed (Idle/Stance) → let combat win; abort cleanly. (Never
            // fires under a puppet order, which forces `enemy = None`; the telemetry is defensive.)
            if enemy.is_some() && matches!(bot.rj.phase, RjPhase::Idle | RjPhase::Stance) {
                bot.rj.phase = RjPhase::Idle;
                bot.rj.telem.outcome = Some(RjOutcome::EnemyAbort);
            } else {
                if bot.rj.phase == RjPhase::Idle {
                    // Fitness pre-check on arrival: the bot's state can change between plan and here,
                    // so verify it can still afford the specific leg's blast before committing.
                    let effective = effective_self_damage(tr.self_damage, armortype, armorvalue);
                    let fit =
                        has_rl && ammo_rockets >= 1.0 && weapons_hot && !quad && health > effective + RJ_HEALTH_MARGIN;
                    if !fit {
                        // Record enough for the harness to see *which* leg was unfit before we bail.
                        bot.rj.telem.link = leg;
                        bot.rj.telem.src = src;
                        bot.rj.telem.launch = launch;
                        bot.rj.telem.tgt = tgt;
                        bot.rj.telem.outcome = Some(RjOutcome::Unfit);
                        failed = true;
                    } else {
                        bot.rj.phase = RjPhase::Stance;
                        bot.rj.link = leg;
                        bot.rj.started = now;
                        bot.rj.run_staged = false;
                        // Snapshot the plan + knob biases for this attempt (clears any prior outcome).
                        bot.rj.telem = RjTelemetry {
                            link: leg,
                            src,
                            launch,
                            tgt,
                            solved_angles: tr.fire_angles,
                            solved_delay: tr.fire_delay,
                            airtime: tr.airtime,
                            self_damage: tr.self_damage,
                            run_velocity: tr.run_velocity,
                            delay_bias: k.delay_bias,
                            pitch_bias: k.pitch_bias,
                            press: None,
                            fire: None,
                            outcome: None,
                        };
                    }
                }
                match bot.rj.phase {
                    RjPhase::Stance => {
                        look_target_angles = Some(tr.fire_angles + pitch_bias);
                        if weapon != Weapon::RocketLauncher {
                            select = true; // impulse 7, re-sent (swallowed until the current cooldown ends)
                        }
                        let run = tr.run_velocity.xy();
                        let running = run.length_squared() > f32::EPSILON;
                        let run_dir = run.normalize_or_zero();
                        let start_offset = origin.xy() - tr.run_start.xy();
                        let offset = origin.xy() - launch.xy();
                        let ground_speed = velocity.xy().length();
                        let along = offset.dot(run_dir);
                        let lateral = (offset - run_dir * along).length();
                        let stance = if platform_origin_z.is_some() {
                            k.stance.min(8.0)
                        } else {
                            k.stance
                        };
                        // The solver's entry speed assumes the complete runway from `run_start`.
                        // The normal stance radius is intentionally generous for stationary jumps,
                        // but accepting it here can discard half of a 32u mover runway and delay full
                        // speed until after `launch`. Stage close enough that the remaining position
                        // error stays well inside the solver's certified launch perturbation.
                        let run_start_stance = k.stance.min(3.0);
                        let launch_window = running && lateral <= stance && (-stance..=stance * 2.0).contains(&along);
                        let run_ready = running && velocity.xy().dot(run_dir) >= run.length() * 0.95;
                        let lift_ready = platform_origin_z.is_none_or(|z| (z - launch.z).abs() <= 4.0);
                        let lift_run_ready = platform_origin_z.is_none_or(|z| {
                            let vz = platform_velocity_z.unwrap_or(0.0);
                            let lead = vz.max(0.0) * tr.run_time;
                            vz > 0.0 && (launch.z - lead - 3.0..=launch.z + 4.0).contains(&z)
                        });
                        let (stationary_point, stationary_radius) = stationary_stage(launch, tgt, k.stance);
                        let stationary_offset = origin.xy() - stationary_point;
                        let stationary_ready =
                            !running && stationary_offset.length() <= stationary_radius && ground_speed <= 20.0;

                        if stationary_ready {
                            stand = true;
                        } else if running && weapon == Weapon::RocketLauncher && now >= attack_finished {
                            if !bot.rj.run_staged {
                                let start_distance = start_offset.length();
                                if start_distance <= run_start_stance && ground_speed <= 20.0 {
                                    // The offline ground run starts from rest. Settle the same state
                                    // before committing so arrival momentum cannot spend or extend a
                                    // one-body-wide runway.
                                    bot.rj.run_staged = true;
                                } else {
                                    let to_start = -start_offset;
                                    // Ground acceleration is ~3200 u/s² at stock settings. Brake
                                    // early enough to spend the carried arrival speed before the
                                    // runway start; a fixed 7u trigger overshoots badly after a
                                    // chained landing, while v²/2a also stays gentle from rest.
                                    let brake_distance =
                                        run_start_stance + ground_speed * ground_speed / (2.0 * 3200.0);
                                    if ground_speed > 20.0
                                        && (start_distance <= brake_distance
                                            || velocity.xy().dot(to_start.normalize_or_zero()) < 0.0)
                                    {
                                        // Active counter-steer stops the full-speed point approach
                                        // before it oscillates across the tiny mover.
                                        let brake = -velocity.xy().normalize_or_zero() * BOT_MOVE_SPEED;
                                        move_world = Some(Vec3::new(brake.x, brake.y, 0.0));
                                    } else {
                                        let speed = if start_distance > 7.0 { BOT_MOVE_SPEED } else { 80.0 };
                                        let wish = to_start.normalize_or_zero() * speed;
                                        move_world = Some(Vec3::new(wish.x, wish.y, 0.0));
                                    }
                                }
                            }
                            if bot.rj.run_staged {
                                if lift_run_ready {
                                    // Keep the stock ground run through the launch point while the
                                    // eyes stay on the backward/downward shot. The command is
                                    // world-space, so view smoothing cannot bend the run-up.
                                    move_world = Some(Vec3::new(run_dir.x, run_dir.y, 0.0) * BOT_MOVE_SPEED);
                                } else {
                                    // Staged on the mover: wait for its live surface to enter the
                                    // phase-height window certified by the offline solve.
                                    stand = true;
                                }
                            }
                        } else if running {
                            stand = true; // pay weapon selection/cooldown before spending the short runway
                        } else {
                            let to_stage = -stationary_offset;
                            let distance = to_stage.length();
                            let direction = to_stage.normalize_or_zero();
                            let brake_distance = stationary_radius + ground_speed * ground_speed / (2.0 * 3200.0);
                            let wish = if ground_speed > 20.0
                                && (distance <= brake_distance || velocity.xy().dot(direction) < 0.0)
                            {
                                -velocity.xy().normalize_or_zero() * BOT_MOVE_SPEED
                            } else {
                                direction * if distance > 7.0 { BOT_MOVE_SPEED } else { 80.0 }
                            };
                            move_world = Some(Vec3::new(wish.x, wish.y, 0.0));
                        }
                        // Ready to jump once the RL is in hand, on the ground, off cooldown, the aim is
                        // settling, and a running solve has actually delivered its certified entry speed.
                        if (stationary_ready || (launch_window && run_ready && lift_ready))
                            && weapon == Weapon::RocketLauncher
                            && on_ground
                            && now >= attack_finished
                            && enemy.is_none()
                        {
                            jump_ready = true; // pressed post-spring once the view is inside the certified aim
                        }
                        if now - bot.rj.started > k.stance_timeout {
                            bot.rj.telem.outcome = Some(RjOutcome::StanceTimeout);
                            failed = true;
                        }
                    }
                    RjPhase::Rise => {
                        look_target_angles = Some(tr.fire_angles + pitch_bias); // keep holding the settled aim
                        stand = true;
                        if on_ground && now - bot.rj.jump_time > k.liftoff_timeout {
                            bot.rj.telem.outcome = Some(RjOutcome::LiftoffTimeout);
                            failed = true; // the jump was swallowed — never left the ground
                        } else if now - bot.rj.jump_time >= tr.fire_delay + k.delay_bias {
                            fire = true; // fire this frame (aim already held since Stance)
                                         // The post-spring view sent with +attack is filled in by `emit`.
                            bot.rj.telem.fire = Some(RjFire {
                                t: now,
                                actual_delay: now - bot.rj.jump_time,
                                origin,
                                view: Vec3::ZERO,
                            });
                            bot.rj.phase = RjPhase::Ballistic;
                            bot.rj.started = now;
                        }
                    }
                    RjPhase::Ballistic => {
                        look_target = Some(tgt);
                        // Coast the certified continuation exactly. QW air-strafe can add speed even
                        // for a tiny bearing correction; on a 500-ups running RJ that moved an
                        // otherwise identical touchdown by more than a nav cell. The perturb sweep
                        // certifies launch/aim/timing error, while adding post-blast input is an
                        // entirely different (and unmodelled) trajectory.
                        let elapsed = now - bot.rj.started;
                        match ballistic_landing(origin, tgt, on_ground, elapsed, tr.airtime + k.ballistic_slack) {
                            Landing::Down { on_target } => {
                                let target_cell = graph.link_target(leg);
                                let exact_target = on_target && (origin.z - tgt.z).abs() <= 24.0;
                                let adjacent_target = graph.nearest(origin).is_some_and(|landed_cell| {
                                    let landed = graph.cell_origin(landed_cell);
                                    (origin.xy() - landed.xy()).length() <= 48.0
                                        && (origin.z - landed.z).abs() <= 24.0
                                        && graph.same_ground_patch(landed_cell, target_cell)
                                });
                                let on_target = exact_target || adjacent_target;
                                if on_target {
                                    bot.rj.fails = 0;
                                }
                                bot.rj.telem.outcome = Some(RjOutcome::Landed {
                                    on_target,
                                    origin,
                                    t: now,
                                });
                                bot.route_pos += 1; // clear the leg; repath from the landing
                                bot.rj.phase = RjPhase::Idle;
                                bot.repath_time = now;
                            }
                            Landing::Overran => {
                                bot.rj.telem.outcome = Some(RjOutcome::Overran { origin, t: now });
                                bot.rj.phase = RjPhase::Idle; // never landed cleanly — repath
                                bot.repath_time = now;
                            }
                            Landing::Riding => {}
                        }
                    }
                    RjPhase::Idle => {}
                }
            }
        } else {
            // The pinned/current leg isn't a solvable rocket jump — abort. Under a puppet order this
            // means the graph was rebuilt out from under the attempt (link ids aren't stable).
            if bot.rj.phase != RjPhase::Idle {
                bot.rj.telem.outcome = Some(RjOutcome::LegVanished);
            }
            bot.rj.phase = RjPhase::Idle;
        }
    }
    if failed {
        bot.rj.phase = RjPhase::Idle;
        bot.traversal_failed(Driver::RocketJump, chasing, now);
    }

    RjDrive {
        look_target_angles,
        look_target,
        stand,
        move_world,
        select,
        jump_ready,
        fire,
        air_correct,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn vars(items: Items, rockets: f32, health: f32, armortype: f32, armorvalue: f32) -> EntVars {
        EntVars {
            items: items.as_f32(),
            ammo_rockets: rockets,
            health,
            armortype,
            armorvalue,
            ..Default::default()
        }
    }

    #[test]
    fn fit_and_unfit_cases() {
        let rl = Items::ROCKET_LAUNCHER;
        // Healthy, armed, no quad → fit.
        assert_eq!(rocket_jump_extra(&vars(rl, 5.0, 100.0, 0.0, 0.0), 0.0, 1.0), 0.0);
        // No launcher → unfit.
        assert_eq!(
            rocket_jump_extra(&vars(Items::empty(), 5.0, 100.0, 0.0, 0.0), 0.0, 1.0),
            RJ_UNFIT_PENALTY
        );
        // No rocket → unfit.
        assert_eq!(
            rocket_jump_extra(&vars(rl, 0.0, 100.0, 0.0, 0.0), 0.0, 1.0),
            RJ_UNFIT_PENALTY
        );
        // Too little health unarmored (needs > 60 + 25 = 85) → 80 unfit, 90 fit.
        assert_eq!(
            rocket_jump_extra(&vars(rl, 5.0, 80.0, 0.0, 0.0), 0.0, 1.0),
            RJ_UNFIT_PENALTY
        );
        assert_eq!(rocket_jump_extra(&vars(rl, 5.0, 90.0, 0.0, 0.0), 0.0, 1.0), 0.0);
        // Quad running → unfit even when otherwise healthy.
        assert_eq!(
            rocket_jump_extra(&vars(rl, 5.0, 100.0, 0.0, 0.0), 5.0, 1.0),
            RJ_UNFIT_PENALTY
        );
    }

    #[test]
    fn armor_lowers_the_health_bar() {
        let rl = Items::ROCKET_LAUNCHER;
        // Yellow armor (0.6, plenty of value): save = ceil(0.6·60) = 36 → effective 24, bar 24+25=49.
        // So 50 health is fit, 45 is not — armor makes rocket jumps viable at lower health.
        assert_eq!(rocket_jump_extra(&vars(rl, 5.0, 50.0, 0.6, 100.0), 0.0, 1.0), 0.0);
        assert_eq!(
            rocket_jump_extra(&vars(rl, 5.0, 45.0, 0.6, 100.0), 0.0, 1.0),
            RJ_UNFIT_PENALTY
        );
    }

    #[test]
    fn stationary_stage_leans_targetward_inside_the_certified_window() {
        let launch = Vec3::ZERO;
        let (point, radius) = stationary_stage(launch, Vec3::new(-100.0, 0.0, 160.0), 16.0);
        assert_eq!(point, glam::vec2(-12.0, 0.0));
        assert_eq!(radius, 3.0);
        assert!(point.distance(launch.xy()) + radius <= 16.0);

        let (tight_point, tight_radius) = stationary_stage(launch, Vec3::X, 2.0);
        assert_eq!(tight_point, launch.xy(), "a narrow live stance leaves no offset budget");
        assert_eq!(tight_radius, 2.0);
    }
}
