// SPDX-License-Identifier: AGPL-3.0-or-later

//! Coordinate-free SNG megahealth acceptance trial.
//!
//! The external gate owns the SHA-bound map anchors. This module only validates those anchors,
//! creates a fresh stock life, forces the real item-goal stack through the mandatory rockets pickup,
//! and emits an authoritative typed result.

use glam::{Vec2, Vec3, Vec3Swizzles};
use rtx_ctlproto as proto;

use crate::bot::state::{BotState, ControlOrder, GoalCommit, ItemTrial, ItemTrialMoveFrame, ItemTrialSample};
use crate::defs::{Bits, Flags, Items, Solid, Weapon};
use crate::entity::{EntId, Think};
use crate::game::GameState;
use crate::navmesh::LinkKind;

use super::{a3, kind_name, send_event, valid_bot};

const FALL_DEPTH: f32 = 56.0;
const STALL_SECS: f32 = 1.0;
const MOVE_EPS: f32 = 16.0;
const TOUCH_GRACE: f32 = 0.35;
const WALL_PROBE: f32 = 24.0;
const CONTACT_SLOP: f32 = 0.75;
const SAMPLE_HZ: f32 = 100.0;
const SAMPLE_SLACK: usize = 2;
const SAMPLE_LIMIT_MAX: usize = 4096;
const CONTRACT_SLOP: f32 = 8.0;
const SPAWN_SLOP: f32 = 0.125;
/// Let a Hold usercmd cross the engine/program boundary before installing the measured fresh body.
/// This is part of every trial (not a discarded warm-up attempt), and measured time starts only
/// after the second reset at the end of this fence.
const ARM_SECS: f32 = 0.1;

fn scenario_label(scenario: proto::SngMegaScenario) -> &'static str {
    match scenario {
        proto::SngMegaScenario::West => "sng_mega_w",
        proto::SngMegaScenario::South => "sng_mega_s",
    }
}

pub(super) fn any_item_trial_active(game: &GameState) -> bool {
    game.entities.iter().any(|ent| ent.bot.puppet.item_trial.is_some())
}

fn sample_limit(max_secs: f32) -> usize {
    ((max_secs.max(0.0) * SAMPLE_HZ).ceil() as usize)
        .saturating_add(SAMPLE_SLACK)
        .min(SAMPLE_LIMIT_MAX)
}

fn reset_trial_bot_state(bot: &mut BotState, at: Vec3, now: f32) {
    let (is_bot, client) = (bot.is_bot, bot.client);
    *bot = BotState::default();
    bot.is_bot = is_bot;
    bot.client = client;
    bot.was_alive = true;
    bot.last_health = 100.0;
    bot.last_armor_value = 0.0;
    bot.watchdog.last_origin = at;
    bot.watchdog.stuck_origin = at;
    bot.watchdog.stuck_since = now;
    bot.repath_time = now;
}

fn configure_trial_body(game: &mut GameState, e: EntId, at: Vec3, angles: Vec3, now: f32) {
    game.configure_fresh_player_body(e);
    game.place_fresh_player_body_at(e, at, angles);
    reset_trial_bot_state(&mut game.entities[e].bot, at, now);
    {
        let ent = &mut game.entities[e];
        ent.v.armorvalue = 0.0;
        ent.v.armortype = 0.0;
        ent.v.items = (Items::AXE | Items::SHOTGUN).as_f32();
        ent.v.weapon = Weapon::Shotgun;
        ent.v.ammo_shells = 25.0;
        ent.v.ammo_nails = 0.0;
        ent.v.ammo_rockets = 0.0;
        ent.v.ammo_cells = 0.0;
    }
    game.w_set_current_ammo(e);
}

fn set_trial_waypoint_goal(state: &mut BotState, waypoint_item: u32, waypoint_terminal: u32, now: f32, deadline: f32) {
    state.goal.set_item(waypoint_item);
    state.goal.item_cell = waypoint_terminal;
    state.goal.commit = GoalCommit::Pickup;
    state.goal.since = now;
    state.goal.next_pick = deadline + 1.0;
    state.goal.magnet_item = 0;
}

fn exact_item(game: &GameState, classname: &str, contract: Vec3, label: &str) -> Result<EntId, String> {
    if !contract.is_finite() {
        return Err(format!("{label} contract must be finite"));
    }
    let item = game
        .find_by_classname(classname)
        .min_by(|&a, &b| {
            (game.entities[a].v.origin - contract)
                .length_squared()
                .total_cmp(&(game.entities[b].v.origin - contract).length_squared())
        })
        .ok_or_else(|| format!("{label} {classname} not found"))?;
    let actual = game.entities[item].v.origin;
    if (actual - contract).length() > CONTRACT_SLOP {
        return Err(format!(
            "{label} {classname} at {actual:?}, outside manifest contract {contract:?}"
        ));
    }
    Ok(item)
}

fn best_terminal(game: &GameState, item: EntId, travel: &[f32], label: &str) -> Result<u32, String> {
    let graph = game.nav.graph.as_ref().ok_or("navmesh not ready")?;
    game.nav
        .goals
        .iter()
        .filter_map(|&(goal_item, cell)| (goal_item == item.0).then_some(cell))
        .filter(|&cell| crate::bot::item_terminal_touches(graph.cell_origin(cell), &game.entities[item]))
        .filter(|&cell| travel[cell as usize].is_finite())
        .min_by(|&a, &b| travel[a as usize].total_cmp(&travel[b as usize]))
        .ok_or_else(|| format!("{label} has no reachable touch-valid terminal"))
}

fn physical_wall_contact_frame(drive: Vec3, delta: Vec3, trace_fraction: f32, plane_normal: Vec3) -> bool {
    let dir = drive.xy().normalize_or_zero();
    let normal = plane_normal.xy().normalize_or_zero();
    let impact_distance = trace_fraction * WALL_PROBE;
    let clearance = (delta.xy() - dir * impact_distance).dot(normal);
    normal != Vec2::ZERO && -drive.xy().dot(normal) >= 64.0 && trace_fraction < 0.99 && clearance <= CONTACT_SLOP
}

fn blocked_drive_frame(drive: Vec3, delta: Vec3, trace_fraction: f32, plane_normal: Vec3) -> bool {
    let normal = plane_normal.xy().normalize_or_zero();
    physical_wall_contact_frame(drive, delta, trace_fraction, plane_normal) && -delta.xy().dot(normal) < 0.5
}

#[derive(Default)]
struct StaticWallFrame {
    contact: bool,
    push: bool,
    normal: Vec3,
}

impl StaticWallFrame {
    fn observe(
        &mut self,
        drive: Vec3,
        delta: Vec3,
        trace_fraction: f32,
        plane_normal: Vec3,
        ascending_step_riser: bool,
    ) {
        let contact = physical_wall_contact_frame(drive, delta, trace_fraction, plane_normal) && !ascending_step_riser;
        if !contact {
            return;
        }
        let push = blocked_drive_frame(drive, delta, trace_fraction, plane_normal);
        if !self.contact || (push && !self.push) {
            self.normal = plane_normal;
        }
        self.contact = true;
        self.push |= push;
    }
}

fn ascending_step_riser(kind: LinkKind, source: Vec3, target: Vec3, plane_normal: Vec3) -> bool {
    let travel = (target.xy() - source.xy()).normalize_or_zero();
    let normal = plane_normal.xy().normalize_or_zero();
    matches!(kind, LinkKind::Walk | LinkKind::Step)
        && target.z > source.z + 0.5
        && travel != Vec2::ZERO
        && normal != Vec2::ZERO
        && -normal.dot(travel) >= 0.7
}

pub(super) fn start_sng_mega(
    game: &mut GameState,
    request_id: i64,
    bot: u32,
    scenario: proto::SngMegaScenario,
    start: Vec3,
    mega_contract: Vec3,
    rockets_contract: Vec3,
    max_secs: f32,
) -> Result<proto::SngMegaResp, String> {
    if game.host().is_client() {
        return Err("sng_mega requires the authoritative server backend".into());
    }
    if any_item_trial_active(game) {
        return Err("another item trial is active; wait for SngMegaResult".into());
    }
    if !max_secs.is_finite() || !(1.0..=30.0).contains(&max_secs) {
        return Err("max_secs must be finite and in [1,30]".into());
    }
    if !start.is_finite() {
        return Err("start contract must be finite".into());
    }
    let e = valid_bot(game, bot)?;
    if !game.level.mapname.eq_ignore_ascii_case("dm3") {
        return Err(format!("sng_mega requires dm3 (current map {})", game.level.mapname));
    }
    let now = game.time();
    let mega = exact_item(game, "item_health", mega_contract, "mega")?;
    let rockets = exact_item(game, "item_rockets", rockets_contract, "waypoint")?;
    let spawn = game
        .find_by_classname("info_player_deathmatch")
        .find(|&candidate| (game.entities[candidate].v.origin - start).length() <= SPAWN_SLOP)
        .ok_or_else(|| format!("info_player_deathmatch missing at manifest start {start:?}"))?;

    let start_cell = game
        .nav
        .graph
        .as_ref()
        .ok_or("navmesh not ready")?
        .nearest(start)
        .ok_or("no navmesh cell at trial start")?;
    let pricing = game.bot_item_trial_link_pricing(e, now);
    let (terminal, waypoint_terminal, planned_route) = {
        let graph = game.nav.graph.as_ref().unwrap();
        let route_costs = pricing.costs(e.0);
        let travel = graph.costs_from(start_cell, &pricing.costs(0));
        let terminal = best_terminal(game, mega, &travel, "mega")?;
        let waypoint_terminal = best_terminal(game, rockets, &travel, "rockets waypoint")?;
        let use_bands = game.host.cvar_bool(c"rtx_bot_bhop") && game.host.cvar_bool(c"rtx_bot_bandplan");
        let route = if use_bands {
            graph
                .find_path_banded(start_cell, waypoint_terminal, 0.0, &route_costs)
                .map(|route| route.links)
        } else {
            graph.find_path(start_cell, waypoint_terminal, &route_costs)
        }
        .ok_or("rockets waypoint became unreachable under production planner")?;
        (terminal, waypoint_terminal, route)
    };

    // All fallible validation is complete. Restore both pickups to a fresh, deterministic state.
    for item in [mega, rockets] {
        if game.entities[item].v.solid != Solid::Trigger {
            game.sub_regen(item);
        }
        game.entities[item].think = Think::None;
        game.entities[item].v.nextthink = 0.0;
    }

    let at = start + Vec3::new(0.0, 0.0, 1.0);
    let start_angles = game.entities[spawn].v.angles;
    configure_trial_body(game, e, at, start_angles, now);

    let deadline = now + max_secs;
    let limit = sample_limit(max_secs);
    {
        let state = &mut game.entities[e].bot;
        set_trial_waypoint_goal(state, rockets.0, waypoint_terminal, now, deadline);
        // `set_bot_cmd` controls the following engine movement step. Hold across that seam before
        // the measured reset so a command emitted before this request cannot contaminate attempt 1.
        state.puppet.order = Some(ControlOrder::Hold);
        state.puppet.item_trial = Some(ItemTrial {
            request_id,
            item: mega.0,
            terminal,
            waypoint_item: rockets.0,
            waypoint_done: false,
            scenario: scenario_label(scenario),
            start_hint: start,
            arm_at: now + ARM_SECS,
            start_angles,
            started: now,
            deadline,
            start_origin: at,
            initial_armor: 0.0,
            wish: Vec3::ZERO,
            pending_wish: Vec3::ZERO,
            buttons: 0,
            pending_buttons: 0,
            move_frame: ItemTrialMoveFrame {
                route_pos: 0,
                link: u32::MAX,
                terminal: waypoint_terminal,
            },
            last_origin: at,
            last_velocity: Vec3::ZERO,
            last_t: now,
            motion_anchor: at,
            motion_since: now,
            wall_run: 0.0,
            wall_max: 0.0,
            wall_contacts: 0,
            wall_normal: Vec3::ZERO,
            ground_z: at.z,
            terminal_touch_since: None,
            goal_lost: false,
            min_z: at.z,
            peak_speed: 0.0,
            initial_route: planned_route,
            route_captured: false,
            sample_limit: limit,
            samples: Vec::with_capacity(limit),
            samples_truncated: false,
        });
    }

    Ok(proto::SngMegaResp {
        bot,
        scenario,
        start: a3(at),
        start_cell,
        item: mega.0,
        terminal,
        waypoint: rockets.0,
        max_secs,
    })
}

pub(super) fn poll_sng_mega(game: &mut GameState, e: EntId, bot: u32, now: f32) {
    let Some(mut trial) = game.entities[e].bot.puppet.item_trial.take() else {
        return;
    };

    if trial.arm_at > 0.0 {
        if now < trial.arm_at {
            game.entities[e].bot.puppet.item_trial = Some(trial);
            return;
        }

        // The Hold usercmd has crossed the engine boundary. Install a second fresh body at the
        // contract pose and start the measured interval now; this is still the same requested
        // attempt, with no route execution or result discarded during the fence.
        let max_secs = trial.deadline - trial.started;
        let waypoint_terminal = trial.move_frame.terminal;
        configure_trial_body(game, e, trial.start_origin, trial.start_angles, now);
        trial.arm_at = 0.0;
        trial.started = now;
        trial.deadline = now + max_secs;
        trial.last_origin = trial.start_origin;
        trial.last_velocity = Vec3::ZERO;
        trial.last_t = now;
        trial.motion_anchor = trial.start_origin;
        trial.motion_since = now;
        set_trial_waypoint_goal(
            &mut game.entities[e].bot,
            trial.waypoint_item,
            waypoint_terminal,
            now,
            trial.deadline,
        );
        game.entities[e].bot.puppet.item_trial = Some(trial);
        return;
    }

    let origin = game.entities[e].v.origin;
    let velocity = game.entities[e].v.velocity;
    let on_ground = game.entities[e].v.flags.has(Flags::ONGROUND);
    let alive = game.entities[e].is_alive();
    let health = game.entities[e].v.health;
    let items = game.entities[e].v.items;
    let item_solid = game.entities[EntId(trial.item)].v.solid;
    let touching = crate::bot::item_terminal_touches(origin, &game.entities[EntId(trial.item)]);

    // The rockets are a hard first leg, acknowledged only by both world state and this bot's ammo.
    if !trial.waypoint_done {
        let waypoint_solid = game.entities[EntId(trial.waypoint_item)].v.solid;
        if waypoint_solid != Solid::Trigger && game.entities[e].v.ammo_rockets > 0.0 {
            trial.waypoint_done = true;
            let state = &mut game.entities[e].bot;
            state.goal.set_item(trial.item);
            state.goal.item_cell = trial.terminal;
            state.goal.commit = GoalCommit::Pickup;
            state.goal.since = now;
            state.goal.next_pick = trial.deadline + 1.0;
            state.goal.magnet_item = 0;
        } else if game.entities[e].bot.goal.item == trial.waypoint_item {
            // This is an explicit trial waypoint, not an ordinary selector choice. Its governing
            // leash is the caller's bounded trial deadline; refreshing the production goal clock
            // prevents the normal ten-second ammo-item give-up from cancelling a still-moving,
            // mandatory first leg before that contract expires.
            game.entities[e].bot.goal.since = now;
        }
    }

    let (goal_item, selected_terminal, route_pos, current_link) = {
        let state = &game.entities[e].bot;
        (
            state.goal.item,
            state.goal.item_cell,
            state.route_pos,
            state.route.get(state.route_pos).copied().unwrap_or(u32::MAX),
        )
    };
    let dt = (now - trial.last_t).clamp(0.0, 0.1);
    let delta = origin - trial.last_origin;
    trial.min_z = trial.min_z.min(origin.z);
    trial.peak_speed = trial.peak_speed.max(velocity.xy().length());

    let realized_velocity = if dt > f32::EPSILON { delta / dt } else { Vec3::ZERO };
    let mut wall_frame = StaticWallFrame::default();
    for drive in [trial.wish, trial.last_velocity, realized_velocity] {
        if drive.xy().length() < 64.0 {
            continue;
        }
        let dir = drive.xy().normalize_or_zero();
        let end = trial.last_origin + Vec3::new(dir.x, dir.y, 0.0) * WALL_PROBE;
        if let Some(trace) = game.nav.bsp.as_ref().map(|bsp| bsp.hull1_trace(trial.last_origin, end)) {
            let riser = game.nav.graph.as_ref().is_some_and(|graph| {
                let link = trial.move_frame.link;
                (link as usize) < graph.links.len()
                    && ascending_step_riser(
                        graph.link_kind(link),
                        graph.cell_origin(graph.link_source(link)),
                        graph.cell_origin(graph.link_target(link)),
                        trace.plane_normal,
                    )
            });
            wall_frame.observe(drive, delta, trace.fraction, trace.plane_normal, riser);
        }
    }
    if wall_frame.contact {
        trial.wall_contacts = trial.wall_contacts.saturating_add(1);
        trial.wall_normal = wall_frame.normal;
    }
    if wall_frame.push {
        trial.wall_run += dt;
        trial.wall_max = trial.wall_max.max(trial.wall_run);
    } else {
        trial.wall_run = 0.0;
    }
    if (origin - trial.motion_anchor).length() >= MOVE_EPS {
        trial.motion_anchor = origin;
        trial.motion_since = now;
    }
    if touching {
        trial.terminal_touch_since.get_or_insert(now);
    } else {
        trial.terminal_touch_since = None;
    }

    let expected_item = if trial.waypoint_done {
        trial.item
    } else {
        trial.waypoint_item
    };
    if goal_item == expected_item && !trial.route_captured && !game.entities[e].bot.route.is_empty() {
        trial.initial_route = game.entities[e].bot.route.clone();
        trial.route_captured = true;
    }

    if trial.samples.len() < trial.sample_limit {
        trial.samples.push(ItemTrialSample {
            t: now,
            origin,
            velocity,
            wish: trial.wish,
            buttons: trial.buttons,
            on_ground,
            wall: wall_frame.contact,
            route_pos: trial.move_frame.route_pos,
            link: trial.move_frame.link,
            terminal: trial.move_frame.terminal,
        });
    } else {
        trial.samples_truncated = true;
    }

    let fell = origin.z < trial.ground_z - FALL_DEPTH;
    if on_ground && !fell {
        trial.ground_z = origin.z;
    }
    let pickup = trial.waypoint_done && health > 100.0 && items.has(Items::SUPERHEALTH) && item_solid != Solid::Trigger;
    if goal_item != expected_item && !pickup {
        trial.goal_lost = true;
    }
    let stalled = trial.wish.xy().length() >= 64.0 && now - trial.motion_since >= STALL_SECS;
    let outcome = if !alive {
        Some((false, "dead"))
    } else if fell {
        Some((false, "fall"))
    } else if trial
        .terminal_touch_since
        .is_some_and(|since| now - since >= TOUCH_GRACE)
    {
        Some((false, "no_pickup"))
    } else if item_solid != Solid::Trigger && !pickup {
        Some((false, "item_taken_before_waypoint"))
    } else if trial.goal_lost {
        Some((false, "goal_lost"))
    } else if stalled {
        Some((false, "stall"))
    } else if now >= trial.deadline {
        Some((false, "timeout"))
    } else if pickup {
        Some((true, "pickup"))
    } else {
        None
    };

    if let Some((ok, reason)) = outcome {
        finish_sng_mega(game, e, bot, now, trial, ok, reason);
        return;
    }

    trial.last_origin = origin;
    trial.last_velocity = velocity;
    trial.last_t = now;
    trial.wish = trial.pending_wish;
    trial.buttons = trial.pending_buttons;
    trial.move_frame = ItemTrialMoveFrame {
        route_pos,
        link: current_link,
        terminal: selected_terminal,
    };
    game.entities[e].bot.puppet.item_trial = Some(trial);
}

fn scenario_from_label(label: &str) -> proto::SngMegaScenario {
    if label == "sng_mega_s" {
        proto::SngMegaScenario::South
    } else {
        proto::SngMegaScenario::West
    }
}

fn route_legs(game: &GameState, route: &[u32]) -> Vec<proto::RouteLeg> {
    let Some(graph) = game.nav.graph.as_ref() else {
        return Vec::new();
    };
    route
        .iter()
        .enumerate()
        .filter(|&(_, &link)| (link as usize) < graph.links.len())
        .map(|(i, &link)| proto::RouteLeg {
            i: i as u32,
            link,
            kind: kind_name(graph.link_kind(link)).to_string(),
            src: a3(graph.cell_origin(graph.link_source(link))),
            tgt: a3(graph.cell_origin(graph.link_target(link))),
        })
        .collect()
}

fn finish_sng_mega(game: &mut GameState, e: EntId, bot: u32, now: f32, trial: ItemTrial, ok: bool, reason: &str) {
    let ent = &game.entities[e];
    let current_link = ent.bot.route.get(ent.bot.route_pos).copied();
    let item = &game.entities[EntId(trial.item)];
    let terminal_origin = game
        .nav
        .graph
        .as_ref()
        .map(|graph| a3(graph.cell_origin(trial.terminal)))
        .unwrap_or([0.0; 3]);
    let initial_route = route_legs(game, &trial.initial_route);
    let samples = trial
        .samples
        .iter()
        .map(|sample| proto::ItemTrialSample {
            t: sample.t,
            origin: a3(sample.origin),
            velocity: a3(sample.velocity),
            wish: a3(sample.wish),
            buttons: sample.buttons,
            on_ground: sample.on_ground,
            wall: sample.wall,
            route_pos: sample.route_pos as u32,
            link: (sample.link != u32::MAX).then_some(sample.link),
            terminal: sample.terminal,
        })
        .collect();
    let result = proto::SngMegaResult {
        request_id: trial.request_id,
        map: game.level.mapname.clone(),
        bot,
        client: ent.bot.client,
        ok,
        reason: reason.to_string(),
        scenario: scenario_from_label(trial.scenario),
        waypoint_item: trial.waypoint_item,
        waypoint_done: trial.waypoint_done,
        started: trial.started,
        ended: now,
        elapsed: now - trial.started,
        max_secs: trial.deadline - trial.started,
        start: a3(trial.start_origin),
        origin: a3(ent.v.origin),
        velocity: a3(ent.v.velocity),
        wish: a3(trial.wish),
        buttons: trial.buttons,
        on_ground: ent.v.flags.has(Flags::ONGROUND),
        alive: ent.is_alive(),
        item: trial.item,
        item_origin: a3(item.v.origin),
        terminal: trial.terminal,
        terminal_origin,
        selected_item: ent.bot.goal.item,
        selected_terminal: ent.bot.goal.item_cell,
        route_pos: ent.bot.route_pos as u32,
        current_link,
        health: ent.v.health,
        items: ent.v.items,
        item_available: item.v.solid == Solid::Trigger,
        min_z: trial.min_z,
        peak_speed: trial.peak_speed,
        wall_secs: trial.wall_max,
        wall_contacts: trial.wall_contacts,
        wall_normal: a3(trial.wall_normal),
        route_captured: trial.route_captured,
        initial_route,
        samples,
        samples_truncated: trial.samples_truncated,
    };

    let state = &mut game.entities[e].bot;
    state.puppet.order = Some(ControlOrder::Hold);
    state.route.clear();
    state.route_bands.clear();
    state.route_pos = 0;
    state.goal.set_item(0);
    state.goal.commit = GoalCommit::None;
    send_event(game, proto::Event::SngMegaResult(Box::new(result)));
}
