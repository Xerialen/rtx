// SPDX-License-Identifier: AGPL-3.0-or-later

//! Team-level analysis over a server-recorded [`Demo`](crate::Demo) — the report that says whether a
//! strategy change helped, and whether it overshot.
//!
//! [`analysis`](crate::analysis) asks how well a player *moves*. This module asks what a team *did*:
//! who scored, who died and where, how long each side stood in the rooms worth holding, and how much
//! of the match was spent standing still. Those are the quantities a change to goal pricing, area
//! control or pack discipline actually moves, and none of them are visible in a speed histogram.
//!
//! It is built for **A/B reading**, so nearly everything is reported per team. A split-team match —
//! one side running the new behaviour, the other the old — turns every number below into a paired
//! comparison with the map, the opponents and the clock held fixed.
//!
//! # What an MVD can and cannot support
//!
//! Positions, the dead flag, team names and the server's own scoreboard are all present and exact.
//! Item pickups are **not** directly represented, so this module never claims to count them; where a
//! question needs "who took the red armour", it is answered by proximity and dwell instead, and named
//! that way. See [`Zone`].

use std::collections::HashMap;

use glam::Vec3;

use crate::{Demo, Frame};

/// A named piece of the map to measure occupancy of — typically an item spawn worth controlling.
///
/// Zones are supplied by the caller rather than derived, because which rooms matter is a property of
/// the map and of the question being asked. The measurement behind the bot's area-control behaviour
/// priced dm3's red-armour room at ±1.75 team frags per minute and its quad room at ±1.6, so those
/// are the two a dm3 run should be given; on another map they would be different rooms entirely.
#[derive(Clone, Debug)]
pub struct Zone {
    pub name: String,
    pub center: Vec3,
    /// Occupancy radius, in map units. Measured in the xy-plane with a separate height band, because
    /// a room's floor and the walkway above it are different places despite sharing a footprint.
    pub radius: f32,
    /// Half-height of the band. A player further than this above or below the centre is somewhere
    /// else, however close in plan.
    pub half_height: f32,
}

impl Zone {
    pub fn contains(&self, p: Vec3) -> bool {
        (p.z - self.center.z).abs() <= self.half_height
            && (p.truncate() - self.center.truncate()).length() <= self.radius
    }
}

/// Speed below which a player is treated as **not going anywhere**.
///
/// A QuakeWorld player who is doing something is moving; competitive play is near-continuous motion,
/// with the reference 4-on-4 above 200 ups for 60–74% of the match. Sixty units per second is well
/// under a walk, so time spent below it is genuinely idle: waiting on a respawn, holding a corner, or
/// stuck. Distinguishing that from travel is the whole point — a bot that camps and a bot that
/// rotates can post identical average speeds.
pub const IDLE_SPEED: f32 = 60.0;

/// A continuous span one player spent inside one zone.
#[derive(Clone, Debug)]
pub struct Occupancy {
    pub player: u8,
    pub zone: usize,
    pub start: f32,
    pub end: f32,
    /// Seconds of this span spent below [`IDLE_SPEED`] — the part that is standing, not passing.
    pub idle: f32,
}

impl Occupancy {
    pub fn duration(&self) -> f32 {
        self.end - self.start
    }
}

/// One player's death, located.
///
/// Taken from the recorder's dead flag rather than from obituary text: the flag is a state the server
/// serialised, while the text is a string that has to be pattern-matched and silently miscounts
/// telefrags, world deaths and any name containing the wrong substring.
#[derive(Clone, Copy, Debug)]
pub struct Death {
    pub player: u8,
    pub time: f32,
    pub origin: Vec3,
}

/// Everything the report knows about one player.
#[derive(Clone, Debug)]
pub struct PlayerReport {
    pub slot: u8,
    pub name: String,
    pub team: String,
    /// Frags scored **during this recording** — see [`segment_frags`].
    pub frags: i16,
    pub deaths: usize,
    /// Seconds the player was alive and being tracked.
    pub alive_secs: f32,
    /// Seconds alive and below [`IDLE_SPEED`].
    pub idle_secs: f32,
    /// Mean horizontal speed over live frames.
    pub mean_speed: f32,
    /// Per-zone seconds occupied, and of those, seconds standing still.
    pub zone_secs: Vec<f32>,
    pub zone_idle_secs: Vec<f32>,
    /// The longest single unbroken stay in any zone, and which. The headline camping number: a
    /// rotation through the red-armour room reads a few seconds, living there reads tens.
    pub longest_stay: f32,
    pub longest_stay_zone: Option<usize>,
}

/// Aggregated per-team figures. Every field is a sum or mean over the team's players.
#[derive(Clone, Debug)]
pub struct TeamReport {
    pub team: String,
    pub players: usize,
    pub frags: i16,
    pub deaths: usize,
    pub alive_secs: f32,
    pub idle_secs: f32,
    pub mean_speed: f32,
    pub zone_secs: Vec<f32>,
    pub zone_idle_secs: Vec<f32>,
    /// Deaths that happened inside each zone — where a side is losing its fights.
    pub zone_deaths: Vec<usize>,
    /// Longest single stay by any member of the team, and which zone.
    pub longest_stay: f32,
    pub longest_stay_zone: Option<usize>,
    /// Mean pairwise distance between living teammates, sampled over the match. Low means the team
    /// moves as a clump, high means it is scattered; neither is automatically right, but a large
    /// change between two arms of an A/B is a real behavioural difference.
    pub mean_spread: f32,
}

/// The whole report.
#[derive(Clone, Debug)]
pub struct Report {
    pub zones: Vec<Zone>,
    pub players: Vec<PlayerReport>,
    pub teams: Vec<TeamReport>,
    pub duration: f32,
    /// Spans in zones, for callers that want the raw distribution rather than the summary.
    pub occupancies: Vec<Occupancy>,
    pub deaths: Vec<Death>,
    /// Reasons this recording should not be read as a fair match. Never empty-checked away: a
    /// contaminated run that *looks* clean is worse than no run, and the whole point of an A/B is
    /// that the two sides differ only in the thing under test.
    pub warnings: Vec<String>,
}

/// Fraction of the match a player must be alive for the sides to be considered evenly manned.
///
/// Well below anything a real player would post, because this is a tripwire for a broken slot rather
/// than a judgement about play. It exists because a bot seated past `maxclients` is skipped by every
/// `1..=maxclients` loop — respawn included — and sits as a permanent corpse with its frags frozen.
/// The match is then silently N-on-(N−1), and the short-handed side loses in a way that reads
/// exactly like the strategy change under test.
const PRESENT_FRAC: f32 = 0.2;

/// Find the reasons a recording is not a fair fight.
fn contamination(players: &[PlayerReport], teams: &[TeamReport], duration: f32) -> Vec<String> {
    let mut out = Vec::new();
    for p in players {
        if duration > 0.0 && p.alive_secs < PRESENT_FRAC * duration {
            out.push(format!(
                "{} ({}) was alive for {:.0}s of {:.0}s — {} frags, {} deaths. A slot that never \
                 respawns is usually a bot seated past maxclients; the match was short-handed.",
                p.name,
                if p.team.is_empty() { "no team" } else { &p.team },
                p.alive_secs,
                duration,
                p.frags,
                p.deaths
            ));
        }
    }
    let sizes: Vec<usize> = teams.iter().map(|t| t.players).collect();
    if let (Some(&lo), Some(&hi)) = (sizes.iter().min(), sizes.iter().max()) {
        if lo != hi {
            out.push(format!("teams are uneven: {sizes:?} players"));
        }
    }
    out
}

/// Per-frame derived state for one player, in time order.
struct Walk {
    time: f32,
    origin: Vec3,
    speed: f32,
    dead: bool,
    dt: f32,
}

/// A copy of `demo` holding only what happened in `[from, to)`.
///
/// Cheap and obviously-correct rather than threading a window through every reader, and it makes the
/// slice a real [`Demo`] so [`report`] treats it exactly like a short recording — including deriving
/// frags from the first and last update *inside the window*.
///
/// Slicing exists because a single window is not the result. The first 22 minutes of a split-team
/// match read 1.49:1 to the treated side, the next 9 read 0.58:1, and quoting either alone is
/// wrong. Anything drawing a conclusion from one of these reports should look at the slices first
/// and only then at the total.
pub fn slice(demo: &Demo, from: f32, to: f32) -> Demo {
    let mut d = demo.clone();
    d.frames.retain(|f| f.time >= from && f.time < to);
    d.frag_updates.retain(|u| u.time >= from && u.time < to);
    d.prints.retain(|p| p.time >= from && p.time < to);
    d
}

/// Build the per-player frame walk, with speeds differenced from positions and warps excluded.
fn walk(demo: &Demo, player: u8) -> Vec<Walk> {
    let track = crate::analysis::track(demo, player);
    let mut out = Vec::with_capacity(track.motions.len());
    let mut prev_time: Option<f32> = None;
    for m in &track.motions {
        let dt = prev_time.map_or(0.0, |p| (m.time - p).max(0.0));
        prev_time = Some(m.time);
        out.push(Walk {
            time: m.time,
            origin: m.origin,
            // A warped frame's differenced speed is a teleport artefact, not motion; treating it as
            // zero would also wrongly count as idle, so those frames are skipped by the callers.
            speed: if m.warped { f32::NAN } else { m.horizontal_speed },
            dead: m.dead,
            dt: if dt > 1.0 { 0.0 } else { dt },
        });
    }
    out
}

/// Frags **scored during this recording**, per slot.
///
/// `svc_updatefrags` carries a running total, not an increment, so the last value is the player's
/// score for the whole match — which is the wrong number whenever the recording starts mid-match.
/// Deaths here are counted from dead-flag transitions inside the demo and so are always segment
/// local; pairing a cumulative score with segment-local deaths silently inflates K/D by however much
/// of the match happened before `record`. Taking last-minus-first puts both on the same clock.
///
/// A recording that starts at map load sees every player's first update at zero, so this is exactly
/// the final score there — the common case is unaffected.
fn segment_frags(demo: &Demo) -> HashMap<u8, i16> {
    let mut first: HashMap<u8, i16> = HashMap::new();
    let mut last: HashMap<u8, i16> = HashMap::new();
    for u in &demo.frag_updates {
        first.entry(u.player).or_insert(u.frags);
        last.insert(u.player, u.frags);
    }
    last.into_iter()
        .map(|(slot, end)| (slot, end - first.get(&slot).copied().unwrap_or(0)))
        .collect()
}

/// Analyse a demo against a set of zones.
pub fn report(demo: &Demo, zones: Vec<Zone>) -> Report {
    let frags = segment_frags(demo);
    let mut players = Vec::new();
    let mut occupancies = Vec::new();
    let mut deaths = Vec::new();
    let mut duration: f32 = 0.0;

    for (slot, info) in demo.players.iter().enumerate() {
        if !info.present() || info.spectator {
            continue;
        }
        let slot = slot as u8;
        let w = walk(demo, slot);
        if w.is_empty() {
            continue;
        }
        duration = duration.max(w.last().map_or(0.0, |x| x.time));

        let mut alive_secs = 0.0;
        let mut idle_secs = 0.0;
        let mut speed_sum = 0.0;
        let mut speed_n = 0.0;
        let mut zone_secs = vec![0.0; zones.len()];
        let mut zone_idle = vec![0.0; zones.len()];
        // One open span per zone, so overlapping zones each get their own accounting.
        let mut open: Vec<Option<Occupancy>> = (0..zones.len()).map(|_| None).collect();
        let mut was_dead = w[0].dead;

        for step in &w {
            if step.dead && !was_dead {
                deaths.push(Death {
                    player: slot,
                    time: step.time,
                    origin: step.origin,
                });
            }
            was_dead = step.dead;

            let moving = step.speed.is_finite();
            let idle = moving && step.speed < IDLE_SPEED;
            if !step.dead {
                alive_secs += step.dt;
                if idle {
                    idle_secs += step.dt;
                }
                if moving {
                    speed_sum += step.speed;
                    speed_n += 1.0;
                }
            }

            for (zi, zone) in zones.iter().enumerate() {
                // A corpse is not holding a room. Closing the span on death is also what keeps
                // "longest stay" honest — otherwise dying in the armour room reads as holding it.
                let inside = !step.dead && zone.contains(step.origin);
                if inside {
                    zone_secs[zi] += step.dt;
                    if idle {
                        zone_idle[zi] += step.dt;
                    }
                    match &mut open[zi] {
                        Some(span) => {
                            span.end = step.time;
                            if idle {
                                span.idle += step.dt;
                            }
                        }
                        none => {
                            *none = Some(Occupancy {
                                player: slot,
                                zone: zi,
                                start: step.time,
                                end: step.time,
                                idle: if idle { step.dt } else { 0.0 },
                            })
                        }
                    }
                } else if let Some(span) = open[zi].take() {
                    occupancies.push(span);
                }
            }
        }
        for span in open.into_iter().flatten() {
            occupancies.push(span);
        }

        let mine: Vec<&Occupancy> = occupancies.iter().filter(|o| o.player == slot).collect();
        let longest = mine.iter().max_by(|a, b| a.duration().total_cmp(&b.duration()));
        players.push(PlayerReport {
            slot,
            name: info.label(slot),
            team: info.team.clone(),
            frags: frags.get(&slot).copied().unwrap_or(0),
            deaths: deaths.iter().filter(|d| d.player == slot).count(),
            alive_secs,
            idle_secs,
            mean_speed: if speed_n > 0.0 { speed_sum / speed_n } else { 0.0 },
            zone_secs,
            zone_idle_secs: zone_idle,
            longest_stay: longest.map_or(0.0, |o| o.duration()),
            longest_stay_zone: longest.map(|o| o.zone),
        });
    }

    let teams = aggregate_teams(demo, &players, &zones, &deaths);
    let warnings = contamination(&players, &teams, duration);
    Report {
        zones,
        players,
        teams,
        duration,
        occupancies,
        deaths,
        warnings,
    }
}

fn aggregate_teams(demo: &Demo, players: &[PlayerReport], zones: &[Zone], deaths: &[Death]) -> Vec<TeamReport> {
    let mut names: Vec<String> = Vec::new();
    for p in players {
        if !names.contains(&p.team) {
            names.push(p.team.clone());
        }
    }
    names.sort();

    names
        .into_iter()
        .map(|team| {
            let members: Vec<&PlayerReport> = players.iter().filter(|p| p.team == team).collect();
            let slots: Vec<u8> = members.iter().map(|m| m.slot).collect();
            let mut zone_secs = vec![0.0; zones.len()];
            let mut zone_idle = vec![0.0; zones.len()];
            let mut zone_deaths = vec![0usize; zones.len()];
            for m in &members {
                for zi in 0..zones.len() {
                    zone_secs[zi] += m.zone_secs[zi];
                    zone_idle[zi] += m.zone_idle_secs[zi];
                }
            }
            for d in deaths.iter().filter(|d| slots.contains(&d.player)) {
                for (zi, zone) in zones.iter().enumerate() {
                    if zone.contains(d.origin) {
                        zone_deaths[zi] += 1;
                    }
                }
            }
            let longest = members.iter().max_by(|a, b| a.longest_stay.total_cmp(&b.longest_stay));
            let alive: f32 = members.iter().map(|m| m.alive_secs).sum();
            TeamReport {
                team: team.clone(),
                players: members.len(),
                frags: members.iter().map(|m| m.frags).sum(),
                deaths: members.iter().map(|m| m.deaths).sum(),
                alive_secs: alive,
                idle_secs: members.iter().map(|m| m.idle_secs).sum(),
                mean_speed: if members.is_empty() {
                    0.0
                } else {
                    members.iter().map(|m| m.mean_speed).sum::<f32>() / members.len() as f32
                },
                zone_secs,
                zone_idle_secs: zone_idle,
                zone_deaths,
                longest_stay: longest.map_or(0.0, |m| m.longest_stay),
                longest_stay_zone: longest.and_then(|m| m.longest_stay_zone),
                mean_spread: mean_spread(demo, &slots),
            }
        })
        .collect()
}

/// Mean pairwise distance between living teammates, sampled on a coarse grid.
///
/// Sampled rather than computed per frame because MVD updates for different players land on
/// different frames: pairing them requires a common clock, and a half-second grid is finer than any
/// tactical movement while keeping the cost linear.
fn mean_spread(demo: &Demo, slots: &[u8]) -> f32 {
    const SAMPLE: f32 = 0.5;
    if slots.len() < 2 {
        return 0.0;
    }
    let mut latest: HashMap<u8, (Vec3, bool)> = HashMap::new();
    let mut next_sample = f32::NEG_INFINITY;
    let (mut total, mut n) = (0.0f32, 0u32);
    let mut frames: Vec<&Frame> = demo.frames.iter().filter(|f| slots.contains(&f.player)).collect();
    frames.sort_by(|a, b| a.time.total_cmp(&b.time));
    for f in frames {
        latest.insert(f.player, (f.origin, f.dead));
        if f.time < next_sample {
            continue;
        }
        next_sample = f.time + SAMPLE;
        let live: Vec<Vec3> = latest
            .values()
            .filter(|(_, dead)| !dead)
            .map(|(origin, _)| *origin)
            .collect();
        if live.len() < 2 {
            continue;
        }
        let (mut sum, mut pairs) = (0.0f32, 0u32);
        for i in 0..live.len() {
            for j in (i + 1)..live.len() {
                sum += (live[i] - live[j]).length();
                pairs += 1;
            }
        }
        total += sum / pairs as f32;
        n += 1;
    }
    if n == 0 {
        0.0
    } else {
        total / n as f32
    }
}

/// The dm3 rooms the stack measurement priced, as zones.
///
/// Coordinates are the item spawns themselves; the radii are room-sized rather than pickup-sized,
/// because the question is who held the *area*, which is what was measured to be worth ±1.75 (red
/// armour) and ±1.6 (quad) team frags per minute.
pub fn dm3_zones() -> Vec<Zone> {
    vec![
        Zone {
            name: "RA".into(),
            center: Vec3::new(256.0, -704.0, 304.0),
            radius: 320.0,
            half_height: 96.0,
        },
        Zone {
            name: "Quad".into(),
            center: Vec3::new(952.0, 296.0, 56.0),
            radius: 320.0,
            half_height: 96.0,
        },
        Zone {
            name: "YA".into(),
            center: Vec3::new(1232.0, -904.0, -48.0),
            radius: 320.0,
            half_height: 96.0,
        },
        Zone {
            name: "Pent".into(),
            center: Vec3::new(1008.0, 800.0, -296.0),
            radius: 320.0,
            half_height: 96.0,
        },
        Zone {
            name: "RL".into(),
            center: Vec3::new(1520.0, 496.0, -112.0),
            radius: 320.0,
            half_height: 96.0,
        },
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_zone_is_a_room_not_a_column() {
        let z = Zone {
            name: "RA".into(),
            center: Vec3::new(0.0, 0.0, 0.0),
            radius: 100.0,
            half_height: 50.0,
        };
        assert!(z.contains(Vec3::new(50.0, 50.0, 10.0)));
        // Outside in plan.
        assert!(!z.contains(Vec3::new(200.0, 0.0, 0.0)));
        // Inside in plan but on another floor — the walkway above the armour is not the armour room.
        assert!(!z.contains(Vec3::new(0.0, 0.0, 200.0)));
    }

    /// The tripwire that would have caught a silently short-handed match.
    #[test]
    fn a_slot_that_never_lives_is_reported_as_contamination() {
        let player = |name: &str, team: &str, alive_secs: f32, frags: i16| PlayerReport {
            slot: 0,
            name: name.into(),
            team: team.into(),
            frags,
            deaths: 0,
            alive_secs,
            idle_secs: 0.0,
            mean_speed: 0.0,
            zone_secs: vec![],
            zone_idle_secs: vec![],
            longest_stay: 0.0,
            longest_stay_zone: None,
        };
        let team = |name: &str, players: usize| TeamReport {
            team: name.into(),
            players,
            frags: 0,
            deaths: 0,
            alive_secs: 0.0,
            idle_secs: 0.0,
            mean_speed: 0.0,
            zone_secs: vec![],
            zone_idle_secs: vec![],
            zone_deaths: vec![],
            longest_stay: 0.0,
            longest_stay_zone: None,
            mean_spread: 0.0,
        };

        // A bot seated past maxclients: never respawns, frags frozen at whatever it had when it
        // died. Four of these went unnoticed for a whole match, and the short-handed side lost by
        // 2:1 in a way that read exactly like the change under test working.
        let corpse = vec![player("Klesk", "blue", 0.0, 3), player("Sarge", "blue", 700.0, 40)];
        let teams = vec![team("blue", 2)];
        let w = contamination(&corpse, &teams, 740.0);
        assert_eq!(w.len(), 1, "the dead slot must be named: {w:?}");
        assert!(w[0].contains("Klesk"));

        // A fair match says nothing.
        let fair = vec![player("A", "red", 700.0, 40), player("B", "blue", 690.0, 38)];
        let even = vec![team("red", 1), team("blue", 1)];
        assert!(contamination(&fair, &even, 740.0).is_empty());

        // Uneven rosters are flagged even when everyone present is playing normally.
        let lopsided = vec![team("red", 4), team("blue", 3)];
        assert!(!contamination(&fair, &lopsided, 740.0).is_empty());
    }

    /// Frags are a running total on the wire; deaths are counted in-demo. Both must be on the same
    /// clock or a mid-match recording reports a K/D built from a whole match's kills and one
    /// segment's deaths.
    #[test]
    fn frags_are_scored_in_the_segment_not_carried_in() {
        use crate::{Format, FragUpdate, PlayerSlot};
        let demo = Demo {
            path: "t.mvd".into(),
            proto: rtx_proto::protocol::ProtoState::new_mvd(),
            format: Format::Mvd,
            local_player: None,
            players: vec![PlayerSlot::default(); 32],
            levelname: String::new(),
            movevars: None,
            demo_cmds: Vec::new(),
            frames: Vec::new(),
            frag_updates: vec![
                // Joined the recording already on 200 and finished on 230: scored 30 here.
                FragUpdate {
                    time: 0.0,
                    player: 0,
                    frags: 200,
                },
                FragUpdate {
                    time: 5.0,
                    player: 0,
                    frags: 230,
                },
                // Recorded from zero: the whole score is this segment's.
                FragUpdate {
                    time: 0.0,
                    player: 1,
                    frags: 0,
                },
                FragUpdate {
                    time: 5.0,
                    player: 1,
                    frags: 12,
                },
            ],
            prints: Vec::new(),
            warnings: Vec::new(),
        };
        let f = segment_frags(&demo);
        assert_eq!(f.get(&0), Some(&30), "a carried-in total is not this segment's score");
        assert_eq!(f.get(&1), Some(&12), "recording from map load is unaffected");
    }

    #[test]
    fn idle_threshold_sits_below_a_walk() {
        // A QuakeWorld player who is playing is moving; the reference match is above 200 ups for most
        // of its length. The threshold has to be low enough that ordinary travel never reads as idle.
        assert!(IDLE_SPEED < 200.0);
        assert!(IDLE_SPEED > 0.0);
    }
}
