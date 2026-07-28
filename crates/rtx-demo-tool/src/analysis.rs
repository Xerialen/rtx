// SPDX-License-Identifier: AGPL-3.0-or-later

//! Movement analysis over a parsed [`Demo`](crate::Demo).
//!
//! A demo's `svc_playerinfo` frames are positions sampled every server frame; the interesting
//! quantities for movement work — how fast a player was going, how much height a climb gained, the
//! shape of a route — are differences between them. [`track`] turns one player's frames into a
//! [`Track`] of [`Motion`]s (position plus the speed to reach it), and [`Track::summary`] reduces
//! that to the numbers you'd otherwise recompute by hand for every demo.

use glam::Vec3;

use crate::Demo;

/// Implied speed above which a step between frames is a **discontinuity**, not motion.
///
/// Positions are differenced, so a teleport, a respawn, or a map change all read as one enormous
/// stride. QuakeWorld clamps real velocity at `sv_maxvelocity` (2000 by default) and even a
/// quad-boosted rocket jump stays far below this, so anything past it is the player being *moved*
/// rather than moving. Without the guard a single dm3 teleport puts a 200,000 ups peak in the
/// summary and adds its own length to the path. (ezquake draws the same line as a flat 150-unit
/// step; a speed keeps it right when the recorder's frame rate drops.)
pub const TELEPORT_SPEED: f32 = 3000.0;

/// One player's motion at a single frame: where they were, and how fast they got there from the
/// previous frame (the first frame of a track reads zero).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Motion {
    /// Demo timestamp of the frame.
    pub time: f32,
    /// Position.
    pub origin: Vec3,
    /// Horizontal (xy-plane) speed in units/sec — the one that matters for bhop/run pace.
    pub horizontal_speed: f32,
    /// Vertical (z) speed in units/sec; positive is upward.
    pub vertical_speed: f32,
    /// Direction of travel in the xy-plane, degrees. `None` while too slow to have a meaningful
    /// heading.
    pub heading: Option<f32>,
    /// Change of heading since the previous frame, degrees/sec — how hard the player is turning.
    /// This is the signal a "did they arc or did they corner" question is asked of.
    pub turn_rate: f32,
    /// The player was *moved* into this position (teleport, respawn) rather than travelling there,
    /// so the speeds above are zeroed and the step contributes no path length.
    pub warped: bool,
    /// The player was dead this frame. A corpse slides, so these frames carry motion that is not
    /// the player travelling.
    pub dead: bool,
}

/// One player's motion across a whole demo, in ascending time.
#[derive(Clone, Debug, PartialEq)]
pub struct Track {
    /// The player slot this track follows.
    pub player: u8,
    /// Per-frame motion, in file (time) order.
    pub motions: Vec<Motion>,
}

/// One airborne span, found by [`Track::jumps`].
///
/// Everything here is measured, not modelled: a demo says where the player was, and a jump is the
/// arc between leaving the ground and arriving back on it. `takeoff_speed` against `distance` is
/// the pair that says whether a crossing was comfortable or at the limit.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Jump {
    pub takeoff_time: f32,
    pub landing_time: f32,
    /// Seconds off the ground.
    pub airtime: f32,
    pub takeoff: Vec3,
    pub landing: Vec3,
    /// Horizontal distance covered, in units — the gap that was cleared.
    pub distance: f32,
    /// Net height change over the jump; negative for a drop.
    pub rise: f32,
    /// Height gained above the takeoff at the top of the arc. A standing QuakeWorld jump peaks
    /// ~45 units, so anything far above that had help (a lift, a ramp, a rocket).
    pub apex: f32,
    /// Horizontal speed at the moment of leaving the ground.
    pub takeoff_speed: f32,
    /// Fastest horizontal speed at any point in the flight — a strafe jump *gains* speed in the
    /// air, so this exceeding the takeoff speed is the signature of one.
    pub peak_speed: f32,
}

/// Reduced stats for a [`Track`] — the headline numbers a movement report shows.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Summary {
    /// The player slot.
    pub player: u8,
    /// Number of frames in the track.
    pub frames: usize,
    /// Wall-clock span from first to last frame, in seconds.
    pub duration: f32,
    /// First position.
    pub start: Vec3,
    /// Last position.
    pub end: Vec3,
    /// Fastest horizontal speed reached, in units/sec.
    pub peak_speed: f32,
    /// Horizontal path length divided by duration, in units/sec.
    pub mean_speed: f32,
    /// Total horizontal distance travelled along the path, in units.
    pub path_length: f32,
    /// Lowest z reached.
    pub min_z: f32,
    /// Highest z reached.
    pub max_z: f32,
    /// Net height change, `end.z - start.z` (negative for a descent).
    pub height_gain: f32,
}

/// Build the motion track for `player`: their `svc_playerinfo` origins in time order, each carrying
/// the horizontal and vertical speed to reach it from the previous frame.
pub fn track(demo: &Demo, player: u8) -> Track {
    /// Below this, a heading is noise rather than a direction.
    const HEADING_MIN_SPEED: f32 = 20.0;

    let mut motions: Vec<Motion> = Vec::new();
    let mut prev: Option<(f32, Vec3)> = None;
    for frame in demo.frames.iter().filter(|f| f.player == player) {
        let origin = frame.origin;
        let (mut horizontal_speed, mut vertical_speed) = (0.0, 0.0);
        let mut warped = false;
        if let Some((pt, po)) = prev {
            if frame.time > pt {
                let dt = frame.time - pt;
                let d = origin - po;
                let (h, v) = (d.truncate().length() / dt, d.z / dt);
                // A step no player could have travelled is one they were moved through.
                if h > TELEPORT_SPEED || v.abs() > TELEPORT_SPEED {
                    warped = true;
                } else {
                    (horizontal_speed, vertical_speed) = (h, v);
                }
            }
        }
        let heading = (horizontal_speed > HEADING_MIN_SPEED)
            .then(|| {
                prev.map(|(_, po)| {
                    let d = (origin - po).truncate();
                    d.y.atan2(d.x).to_degrees()
                })
            })
            .flatten();
        // Turn rate needs a heading on both sides and a real interval between them.
        let turn_rate = match (heading, motions.last()) {
            (Some(h), Some(m)) if !warped => match (m.heading, frame.time - m.time) {
                (Some(ph), dt) if dt > 0.0 => wrap180(h - ph) / dt,
                _ => 0.0,
            },
            _ => 0.0,
        };
        motions.push(Motion {
            time: frame.time,
            origin,
            horizontal_speed,
            vertical_speed,
            heading,
            turn_rate,
            warped,
            dead: frame.dead,
        });
        prev = Some((frame.time, origin));
    }
    Track { player, motions }
}

/// Fold an angle difference into ±180°, so a sweep past due-north reads as a small turn.
fn wrap180(deg: f32) -> f32 {
    let mut d = deg % 360.0;
    if d > 180.0 {
        d -= 360.0;
    } else if d < -180.0 {
        d += 360.0;
    }
    d
}

/// Every player slot that appears in the demo's frames, ascending.
pub fn players(demo: &Demo) -> Vec<u8> {
    let mut ps: Vec<u8> = demo.frames.iter().map(|f| f.player).collect();
    ps.sort_unstable();
    ps.dedup();
    ps
}

impl Track {
    /// Reduce the track to its [`Summary`] stats.
    pub fn summary(&self) -> Summary {
        let first = self.motions.first();
        let last = self.motions.last();
        let start = first.map_or(Vec3::ZERO, |m| m.origin);
        let end = last.map_or(Vec3::ZERO, |m| m.origin);
        let duration = match (first, last) {
            (Some(f), Some(l)) => l.time - f.time,
            _ => 0.0,
        };
        // Path length sums the horizontal step distances (speed * dt is exactly that step).
        let mut path_length = 0.0;
        let mut peak_speed = 0.0f32;
        let mut min_z = start.z;
        let mut max_z = start.z;
        for (i, m) in self.motions.iter().enumerate() {
            peak_speed = peak_speed.max(m.horizontal_speed);
            min_z = min_z.min(m.origin.z);
            max_z = max_z.max(m.origin.z);
            // A warped step is a jump in space, not distance covered — counting it would add a
            // teleport's length to how far the player is said to have run.
            if i > 0 && !m.warped {
                path_length += (m.origin - self.motions[i - 1].origin).truncate().length();
            }
        }
        let mean_speed = if duration > 0.0 { path_length / duration } else { 0.0 };
        Summary {
            player: self.player,
            frames: self.motions.len(),
            duration,
            start,
            end,
            peak_speed,
            mean_speed,
            path_length,
            min_z,
            max_z,
            height_gain: end.z - start.z,
        }
    }

    /// Split the track into airborne spans — the demo's jumps.
    ///
    /// A multi-view demo carries no ground flag, so "airborne" has to be inferred from the shape of
    /// the height trace. The rule is deliberately conservative: a span begins where the player
    /// starts rising fast enough that only a jump explains it, ends where the height settles again,
    /// and is kept only if it lasted long enough to be a jump rather than a stair-step or the
    /// 1/8-unit quantisation of the wire jittering. Ramps and lifts move a player upward too, which
    /// is why the *rate* matters rather than the height alone.
    ///
    /// This is the general form of what the tool used to be pointed at by hand: instead of reading
    /// out one jump you already know the timestamps of, every jump in a 20-minute 4-on-4 falls out.
    pub fn jumps(&self) -> Vec<Jump> {
        /// Upward speed that means "left the ground under their own power" — a walked ramp or a
        /// lift is far slower, a jump starts at `JUMP_VZ` = 270.
        const TAKEOFF_VZ: f32 = 150.0;
        /// Shortest span worth calling a jump. A full-height jump hangs ~0.68 s; this admits the
        /// clipped ones (hopping up a step) while rejecting single-frame noise.
        const MIN_AIRTIME: f32 = 0.15;
        /// Longest span still called a jump. Past this the player is *falling* — off a ledge, down
        /// a shaft — or swimming, none of which is a jump even though all are airborne. A jump from
        /// dm3's highest reachable point lands well inside this.
        const MAX_AIRTIME: f32 = 2.0;

        let mut out = Vec::new();
        let mut open: Option<usize> = None;
        for (i, m) in self.motions.iter().enumerate() {
            if m.warped {
                open = None; // a teleport mid-flight is not a landing
                continue;
            }
            match open {
                None if m.vertical_speed > TAKEOFF_VZ => open = Some(i.saturating_sub(1)),
                // Airborne until the player is descending no more and has stopped moving down —
                // i.e. the frame where the fall arrests is the landing.
                Some(start) if m.vertical_speed >= 0.0 && self.motions[i - 1].vertical_speed < 0.0 => {
                    let (a, b) = (&self.motions[start], m);
                    let airtime = b.time - a.time;
                    if (MIN_AIRTIME..=MAX_AIRTIME).contains(&airtime) {
                        let peak_z = self.motions[start..=i].iter().fold(f32::MIN, |z, k| z.max(k.origin.z));
                        out.push(Jump {
                            takeoff_time: a.time,
                            landing_time: b.time,
                            airtime,
                            takeoff: a.origin,
                            landing: b.origin,
                            distance: (b.origin - a.origin).truncate().length(),
                            rise: b.origin.z - a.origin.z,
                            apex: peak_z - a.origin.z,
                            takeoff_speed: a.horizontal_speed,
                            peak_speed: self.motions[start..=i]
                                .iter()
                                .fold(0.0f32, |s, k| s.max(k.horizontal_speed)),
                        });
                    }
                    open = None;
                }
                _ => {}
            }
        }
        out
    }

    /// Speed distribution over the track, as `[p0, p50, p90, p99, max]` of horizontal speed.
    ///
    /// Over every live frame — the only exclusions are warps (see [`TELEPORT_SPEED`]) and frames
    /// the player was dead for, neither of which is that player travelling. There is deliberately
    /// no "is he moving" threshold: measured on a real 4-on-4, one costs about 2% (p50 327 → 334)
    /// while quietly biasing the answer upward, and what it removes is mostly not standing still
    /// anyway — at 1/8-unit positions and 72 Hz, a slow step rounds to zero, and 58% of apparently
    /// stationary runs last under a tenth of a second.
    ///
    /// Percentiles rather than a mean because the distribution is wide and skewed, not because
    /// there is much idling to discount: a competitive player is above 200 ups for roughly 70% of
    /// the match.
    pub fn speed_percentiles(&self) -> [f32; 5] {
        let mut v: Vec<f32> = self.live().map(|m| m.horizontal_speed).collect();
        if v.is_empty() {
            return [0.0; 5];
        }
        v.sort_by(f32::total_cmp);
        let at = |q: f32| v[((v.len() - 1) as f32 * q) as usize];
        [at(0.0), at(0.5), at(0.9), at(0.99), v[v.len() - 1]]
    }

    /// Frames this player was alive and actually travelling through — the honest denominator for
    /// any "how fast does this player move" question.
    pub fn live(&self) -> impl Iterator<Item = &Motion> {
        self.motions.iter().filter(|m| !m.warped && !m.dead)
    }

    /// Share of live frames spent above `speed`, in 0..1.
    ///
    /// More informative than any single percentile for this population, and the number that says
    /// plainly how wrong "mostly standing still" is: on the reference 4-on-4 every player is above
    /// 200 ups for about 70% of the match.
    pub fn share_above(&self, speed: f32) -> f32 {
        let (mut n, mut hit) = (0u32, 0u32);
        for m in self.live() {
            n += 1;
            if m.horizontal_speed > speed {
                hit += 1;
            }
        }
        if n == 0 {
            0.0
        } else {
            hit as f32 / n as f32
        }
    }

    /// Share of frames the player was dead, in 0..1.
    pub fn dead_share(&self) -> f32 {
        if self.motions.is_empty() {
            return 0.0;
        }
        self.motions.iter().filter(|m| m.dead).count() as f32 / self.motions.len() as f32
    }

    /// Down-sample the track to at most `n` roughly evenly spaced waypoints, always keeping the
    /// first and last frame. `n < 2` (or a shorter track) returns every frame unchanged.
    pub fn waypoints(&self, n: usize) -> Vec<Motion> {
        let len = self.motions.len();
        if n < 2 || len <= n {
            return self.motions.clone();
        }
        (0..n).map(|i| self.motions[i * (len - 1) / (n - 1)]).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Demo, Format, Frame, PlayerSlot};
    use rtx_proto::protocol::ProtoState;

    fn frame(time: f32, player: u8, origin: Vec3) -> Frame {
        Frame {
            time,
            player,
            origin,
            angles: Vec3::ZERO,
            velocity: None,
            command: None,
            dead: false,
            on_ground: None,
            weaponframe: None,
        }
    }

    fn demo(frames: Vec<Frame>) -> Demo {
        Demo {
            path: "test.qwd".into(),
            proto: ProtoState::new(),
            format: Format::Qwd,
            local_player: Some(0),
            players: vec![PlayerSlot::default(); 32],
            levelname: String::new(),
            movevars: None,
            demo_cmds: Vec::new(),
            frames,
            warnings: Vec::new(),
        }
    }

    /// A straight 200-unit horizontal hop over 0.1s is 2000 ups, and a climb shows as height gain —
    /// with a second player's frames interleaved but not mixed into the track.
    #[test]
    fn track_speed_and_climb() {
        let d = demo(vec![
            frame(0.0, 0, Vec3::new(0.0, 0.0, 0.0)),
            frame(0.05, 1, Vec3::new(999.0, 0.0, 0.0)), // other player, ignored
            frame(0.1, 0, Vec3::new(200.0, 0.0, 16.0)),
            frame(0.2, 0, Vec3::new(200.0, 0.0, 48.0)), // straight up: no horizontal move
        ]);
        let t = track(&d, 0);
        assert_eq!(t.motions.len(), 3);
        assert!((t.motions[1].horizontal_speed - 2000.0).abs() < 0.5);
        assert!((t.motions[1].vertical_speed - 160.0).abs() < 0.5);
        assert!((t.motions[2].horizontal_speed - 0.0).abs() < 0.5);

        let s = t.summary();
        assert_eq!(s.frames, 3);
        assert!((s.duration - 0.2).abs() < 1e-6);
        assert!((s.peak_speed - 2000.0).abs() < 0.5);
        assert!((s.height_gain - 48.0).abs() < 1e-4);
        assert!((s.max_z - 48.0).abs() < 1e-4);
        assert_eq!(players(&d), vec![0, 1]);
    }

    /// A teleport is a jump in *space*, and differencing positions cannot tell that from motion —
    /// so it must be excluded explicitly, or one dm3 teleporter reports a five-figure speed and
    /// adds its own length to how far the player is said to have run.
    #[test]
    fn a_teleport_is_not_motion() {
        let d = demo(vec![
            frame(0.0, 0, Vec3::new(0.0, 0.0, 0.0)),
            frame(0.1, 0, Vec3::new(30.0, 0.0, 0.0)),   // 300 ups: real running
            frame(0.2, 0, Vec3::new(2000.0, 0.0, 0.0)), // across the map between frames
            frame(0.3, 0, Vec3::new(2030.0, 0.0, 0.0)), // running again at the far end
        ]);
        let t = track(&d, 0);
        assert!(!t.motions[1].warped);
        assert!(t.motions[2].warped, "19,700 ups is a teleport, not a sprint");
        assert_eq!(t.motions[2].horizontal_speed, 0.0);
        assert!(!t.motions[3].warped);

        let s = t.summary();
        assert!((s.peak_speed - 300.0).abs() < 1.0, "the teleport must not set the peak");
        assert!(
            (s.path_length - 60.0).abs() < 1.0,
            "path counts the two 30u runs, not the 1970u warp: {}",
            s.path_length
        );
    }

    /// A jump is an airborne span between a takeoff and a landing, and the numbers that matter are
    /// how far it went and whether speed rose in the air — the signature of a strafe jump.
    #[test]
    fn finds_a_jump_and_its_speed_gain() {
        // A 0.6s arc: up, over, down. Speed climbs 300 -> 400 ups across the flight.
        let mut frames = vec![frame(0.0, 0, Vec3::new(0.0, 0.0, 0.0))];
        let zs = [20.0, 36.0, 44.0, 40.0, 24.0, 0.0];
        for (i, z) in zs.iter().enumerate() {
            let t = 0.1 * (i + 1) as f32;
            frames.push(frame(t, 0, Vec3::new(30.0 + 34.0 * i as f32, 0.0, *z)));
        }
        frames.push(frame(0.7, 0, Vec3::new(234.0, 0.0, 0.0))); // grounded again
        let t = track(&demo(frames), 0);

        let jumps = t.jumps();
        assert_eq!(jumps.len(), 1, "one arc, one jump: {jumps:?}");
        let j = jumps[0];
        assert!((j.apex - 44.0).abs() < 1.0, "apex is measured above the takeoff");
        assert!(j.airtime > 0.4 && j.airtime < 0.8, "airtime {}", j.airtime);
        assert!(j.distance > 150.0, "distance {}", j.distance);
        assert!(
            j.peak_speed > j.takeoff_speed,
            "this arc accelerates in the air ({} -> {})",
            j.takeoff_speed,
            j.peak_speed
        );
    }

    /// Standing still is not a jump, and neither is a long fall.
    #[test]
    fn rejects_non_jumps() {
        // Flat ground, no vertical motion at all.
        let flat: Vec<Frame> = (0..12)
            .map(|i| frame(0.1 * i as f32, 0, Vec3::new(30.0 * i as f32, 0.0, 0.0)))
            .collect();
        assert!(track(&demo(flat), 0).jumps().is_empty(), "level running is not a jump");

        // A 3-second descent: airborne, but falling rather than jumping.
        let mut fall = vec![frame(0.0, 0, Vec3::new(0.0, 0.0, 1000.0))];
        for i in 1..=30 {
            let t = 0.1 * i as f32;
            fall.push(frame(
                t,
                0,
                Vec3::new(20.0 * i as f32, 0.0, 1000.0 - 10.0 * (i * i) as f32),
            ));
        }
        fall.push(frame(3.2, 0, Vec3::new(620.0, 0.0, -8000.0)));
        assert!(
            track(&demo(fall), 0).jumps().is_empty(),
            "a multi-second plunge is a fall, not a jump"
        );
    }

    /// Percentiles run over every live frame, parked ones included — there is no "is he moving"
    /// threshold, because on real data one costs ~2% and biases the answer upward.
    #[test]
    fn speed_percentiles_cover_all_live_frames() {
        let mut frames = vec![frame(0.0, 0, Vec3::ZERO)];
        // 20 frames parked, then 5 moving at ~400 ups.
        for i in 1..=20 {
            frames.push(frame(0.1 * i as f32, 0, Vec3::ZERO));
        }
        for i in 1..=5 {
            frames.push(frame(2.0 + 0.1 * i as f32, 0, Vec3::new(40.0 * i as f32, 0.0, 0.0)));
        }
        let p = track(&demo(frames), 0).speed_percentiles();
        assert!((p[4] - 400.0).abs() < 1.0, "max {}", p[4]);
        assert!(
            p[1] < 100.0,
            "p50 over all live frames is dragged down by the parked ones: {}",
            p[1]
        );
    }

    /// Down-sampling keeps the endpoints and the requested count.
    #[test]
    fn waypoints_keep_endpoints() {
        let frames: Vec<Frame> = (0..10)
            .map(|i| frame(i as f32, 0, Vec3::new(i as f32, 0.0, 0.0)))
            .collect();
        let t = track(&demo(frames), 0);
        let wp = t.waypoints(4);
        assert_eq!(wp.len(), 4);
        assert_eq!(wp.first().unwrap().origin.x, 0.0);
        assert_eq!(wp.last().unwrap().origin.x, 9.0);
    }
}
