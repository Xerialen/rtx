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

    /// Speed distribution over the track, as `[p0, p50, p90, p99, max]` of horizontal speed across
    /// frames the player was actually moving. Percentiles rather than a mean because a 20-minute
    /// game is mostly standing still, waiting, and dying — the mean of that says nothing about how
    /// fast the player *travels*.
    pub fn speed_percentiles(&self) -> [f32; 5] {
        let mut v: Vec<f32> = self
            .motions
            .iter()
            .filter(|m| !m.warped && m.horizontal_speed > 1.0)
            .map(|m| m.horizontal_speed)
            .collect();
        if v.is_empty() {
            return [0.0; 5];
        }
        v.sort_by(f32::total_cmp);
        let at = |q: f32| v[((v.len() - 1) as f32 * q) as usize];
        [at(0.0), at(0.5), at(0.9), at(0.99), v[v.len() - 1]]
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
