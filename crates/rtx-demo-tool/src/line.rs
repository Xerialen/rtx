// SPDX-License-Identifier: AGPL-3.0-or-later

//! Human reference lines, and scoring a bot run against one.
//!
//! The bot's movement has been judged, until now, by whether it arrived and how long it took. That
//! answers "did it work" but not "does it move like a player", and the second question is the one
//! that matters for a route the bot completes badly — grinding along a wall, braking into corners,
//! falling off and recovering. A demo of a human doing the *same traversal on the same geometry* is
//! the only honest reference for that, and a 4-on-4 MVD holds thousands of them.
//!
//! Two halves:
//!
//! * [`Track::traversals`] mines a demo for every time a player went from A to B — the discovery
//!   step. You give it two points off the navmesh and it hands back each run, with its time and
//!   speed profile. The *fastest* of those is what the bot should be measured against; the spread
//!   across them says how much of the difference is skill and how much is noise.
//! * [`LineScore`] compares one bot trajectory to one of those runs: not just elapsed time, but how
//!   far off the human's line it strayed, where it lost speed, and how much it fought its own
//!   steering.
//!
//! Everything here is pure — positions in, numbers out — so it can be unit-tested on synthetic
//! paths and reused by both the CLI and the MCP bridge without dragging a server in.

use glam::{Vec2, Vec3};

use crate::analysis::{Motion, Track};

/// One human run from A to B, lifted out of a demo.
#[derive(Clone, Debug)]
pub struct Traversal {
    /// Which player made the run.
    pub player: u8,
    /// Demo time it started and ended.
    pub start_time: f32,
    pub end_time: f32,
    /// The path itself, in file order.
    pub motions: Vec<Motion>,
}

impl Traversal {
    /// Seconds taken. This is the number a bot has to beat.
    pub fn duration(&self) -> f32 {
        self.end_time - self.start_time
    }

    /// Distance along the path — a wandering route is longer than the straight line, and the ratio
    /// of the two is how directly the player travelled.
    pub fn path_length(&self) -> f32 {
        self.motions
            .windows(2)
            .filter(|w| !w[1].warped)
            .map(|w| (w[1].origin - w[0].origin).truncate().length())
            .sum()
    }

    /// Straight-line displacement over path travelled: 1.0 is a straight run, 0 a closed loop.
    ///
    /// This decides whether "go from A to B" is the *same task* the human performed. Segments are
    /// cut by distance travelled, so a player who spent 640 units circling an item produces one
    /// whose endpoints are nearly the same point — and a bot told to reach the end simply cuts the
    /// chord. It then reads as three times faster than the human while hugging the reference line,
    /// because a doubled-back path is never far from any point on itself. Those movements are still
    /// worth being able to *execute*; they are just not comparable on time.
    pub fn directness(&self) -> f32 {
        let (Some(a), Some(b)) = (self.motions.first(), self.motions.last()) else {
            return 0.0;
        };
        let path = self.path_length();
        if path < 1.0 {
            0.0
        } else {
            (b.origin - a.origin).length() / path
        }
    }

    /// Mean speed along the path.
    pub fn mean_speed(&self) -> f32 {
        let d = self.duration();
        if d > 0.0 {
            self.path_length() / d
        } else {
            0.0
        }
    }

    /// The velocity the player was carrying when this movement began.
    ///
    /// A segment is a slice out of continuous play, so the human entered it already travelling —
    /// often at full speed. Anything reproducing the movement has to start from the same condition
    /// or it is measuring a standing start instead.
    ///
    /// Measured over [`ENTRY_WINDOW`] rather than off the first frame. An MVD carries no velocity
    /// field, so speed is differenced from positions, and a single frame divided by a
    /// millisecond-quantised delta can read hundreds of units per second high — on dm3's fastest
    /// segment, 1063 ups against a 700 ups mean. Averaging over a window is both robust to that and
    /// closer to what is being reproduced anyway, since no engine grants an instantaneous spike.
    pub fn entry_velocity(&self) -> Vec3 {
        let Some(first) = self.motions.first() else {
            return Vec3::ZERO;
        };
        let last = self
            .motions
            .iter()
            .take_while(|m| m.time - first.time <= ENTRY_WINDOW && !m.warped)
            .last()
            .unwrap_or(first);
        let dt = last.time - first.time;
        if dt <= 0.0 {
            return Vec3::ZERO;
        }
        (last.origin - first.origin) / dt
    }

    /// This run as `(time, position)` samples — the same shape a bot trajectory is scored from.
    ///
    /// Scoring a human against their own line is how the scale-free metrics get calibrated. Yaw
    /// jitter and wall events have no obvious "good" value in the abstract: strafe jumping *is*
    /// rapid yaw oscillation, and a fast player clips geometry too. Running [`LineScore`] over this
    /// gives what those counters read for a human doing the movement well, which is the only number
    /// a bot's should be judged against.
    pub fn samples(&self) -> Vec<(f32, Vec3)> {
        self.motions.iter().map(|m| (m.time, m.origin)).collect()
    }

    /// Turn this run into a reference line to score against.
    pub fn reference(&self, name: impl Into<String>) -> ReferenceLine {
        ReferenceLine {
            name: name.into(),
            player: self.player,
            duration: self.duration(),
            pts: self.motions.iter().map(|m| m.origin).collect(),
            speeds: self.motions.iter().map(|m| m.horizontal_speed).collect(),
        }
    }
}

/// Reduce a pile of segments to the distinct movements among them.
///
/// Eight players over twenty minutes produce thousands of segments, and most are the same movement
/// done again — the same corridor, the same jump, the same drop. Those are redundant as tests: a
/// bot that can execute one can execute the others. What is left after collapsing them is a
/// coverage suite, derived from what the map and the game actually produced rather than from
/// anyone's idea of which routes matter.
///
/// Redundancy is judged on **ground covered**, not on endpoints. Matching endpoints is the obvious
/// test and it does not work: segments are cut every so many units travelled, so two players running
/// the same corridor start their windows at different offsets along it and their endpoints land in
/// different cells even though the movement is identical. Instead a segment is dropped when
/// [`COVER_FRAC`] of the ground it covers is already covered by a kept segment *travelling the same
/// way* — which is what "these overlap, so one of them is redundant" actually means.
///
/// Direction is part of the key because it is part of the movement: running a staircase up and
/// running it down are different things to execute. Coverage is recorded for the travel octant and
/// its two neighbours, so a path that merely straddles an octant boundary is not counted as new.
///
/// Segments are considered **fastest first**, so the one that survives a group is the demanding one:
/// what a reference has to say is what is achievable, not what is typical — the slower runs over the
/// same ground are usually a player doing something else on the way (fighting, collecting, waiting).
///
/// Deterministic: the order segments are considered in is a total order on (speed, player, time),
/// coverage keys are integers, and the output is in that same considered order.
pub fn distinct(mut segments: Vec<Traversal>, bucket: f32) -> Vec<Traversal> {
    use std::collections::HashSet;
    let bucket = bucket.max(1.0);

    // Fastest first; the rest of the ordering only has to be total and stable across runs.
    segments.sort_by(|a, b| {
        b.mean_speed()
            .total_cmp(&a.mean_speed())
            .then(a.player.cmp(&b.player))
            .then(a.start_time.total_cmp(&b.start_time))
    });

    let mut covered: HashSet<Cell> = HashSet::new();
    let mut out = Vec::new();
    for s in segments {
        let cells = cover_cells(&s, bucket);
        if cells.is_empty() {
            continue;
        }
        let known = cells.iter().filter(|c| covered.contains(c)).count();
        if known as f32 / cells.len() as f32 >= COVER_FRAC {
            continue;
        }
        for &(x, y, z, oct) in &cells {
            // Claim the neighbouring octants too: the *test* is exact, so a later path along the
            // same line but a degree the other side of a boundary still reads as covered.
            for d in -1..=1 {
                covered.insert((x, y, z, (oct + d).rem_euclid(8)));
            }
        }
        out.push(s);
    }
    out
}

/// How much of the start of a movement [`Traversal::entry_velocity`] averages over. Long enough
/// (several frames at 72 Hz) to survive one badly-timed delta, short enough to still be the entry.
pub const ENTRY_WINDOW: f32 = 0.1;

/// A quantised position plus the octant of travel through it.
type Cell = (i32, i32, i32, i32);

/// The whole coverage suite for a demo: every player's movement, cut into pieces and deduped.
///
/// This is the one entry point callers should use — the CLI and the live harness must build the
/// same suite from the same demo or a baseline means nothing, and the segment *index* is only a
/// stable name for a movement because both go through here with the same parameters.
pub fn suite(demo: &crate::Demo, min_path: f32, max_path: f32, max_secs: f32, bucket: f32) -> Vec<Traversal> {
    let mut all = Vec::new();
    for p in crate::analysis::players(demo) {
        all.extend(crate::analysis::track(demo, p).segments(min_path, max_path, max_secs));
    }
    distinct(all, bucket)
}

/// The default suite parameters, so every caller names the same movements by the same indices.
pub const SUITE_MIN_PATH: f32 = 192.0;
pub const SUITE_MAX_PATH: f32 = 640.0;
pub const SUITE_MAX_SECS: f32 = 4.0;
pub const SUITE_BUCKET: f32 = 128.0;

/// How much of a segment's ground has to be already covered for it to count as redundant. Not 1.0:
/// two runs of the same corridor differ at the ends, where one player entered a step earlier.
pub const COVER_FRAC: f32 = 0.85;

/// The cells a segment covers, resampled at half the cell size so the spacing of the demo's frames
/// does not decide how much ground a fast run appears to touch.
fn cover_cells(s: &Traversal, bucket: f32) -> Vec<Cell> {
    let step = bucket * 0.5;
    let mut out: Vec<Cell> = Vec::new();
    for w in s.motions.windows(2) {
        if w[1].warped {
            continue;
        }
        let (a, b) = (w[0].origin, w[1].origin);
        let len = (b - a).truncate().length();
        if len < 1e-3 {
            continue;
        }
        let dir = (b - a).truncate() / len;
        let oct = (dir.y.atan2(dir.x).to_degrees() / 45.0).round().rem_euclid(8.0) as i32;
        for i in 0..=(len / step).floor() as i32 {
            let p = a + (b - a) * (i as f32 * step / len).min(1.0);
            out.push((
                (p.x / bucket).floor() as i32,
                (p.y / bucket).floor() as i32,
                (p.z / bucket).floor() as i32,
                oct,
            ));
        }
    }
    out.sort_unstable();
    out.dedup();
    out
}

/// A human path, prepared for comparison.
#[derive(Clone, Debug)]
pub struct ReferenceLine {
    pub name: String,
    /// The player whose run this was.
    pub player: u8,
    /// How long the human took.
    pub duration: f32,
    /// Positions along the run.
    pub pts: Vec<Vec3>,
    /// Horizontal speed at each position.
    pub speeds: Vec<f32>,
}

impl ReferenceLine {
    /// Cumulative horizontal arc length at each point — the x-axis every comparison is made
    /// against, because two runs of the same route are only comparable by *progress*, not by time.
    pub fn arc(&self) -> Vec<f32> {
        let mut out = Vec::with_capacity(self.pts.len());
        let mut s = 0.0;
        for (i, p) in self.pts.iter().enumerate() {
            if i > 0 {
                s += (*p - self.pts[i - 1]).truncate().length();
            }
            out.push(s);
        }
        out
    }

    /// Total horizontal length of the line.
    pub fn length(&self) -> f32 {
        self.arc().last().copied().unwrap_or(0.0)
    }

    /// Distance from `p` to the line, and how far along the line the nearest point sits.
    ///
    /// Segment-wise rather than point-wise: at 72 Hz a human's samples are ~5 units apart, but a
    /// bot's may not be, and comparing to the nearest *sample* would report a sawtooth that is an
    /// artefact of sampling rather than of the bot's line.
    pub fn nearest(&self, p: Vec3) -> (f32, f32) {
        let arc = self.arc();
        let (mut best_d, mut best_s) = (f32::MAX, 0.0);
        for i in 1..self.pts.len() {
            let (a, b) = (self.pts[i - 1], self.pts[i]);
            let ab = (b - a).truncate();
            let len = ab.length();
            let t = if len > 1e-3 {
                ((p - a).truncate().dot(ab) / (len * len)).clamp(0.0, 1.0)
            } else {
                0.0
            };
            let closest = a.truncate() + ab * t;
            let d = (p.truncate() - closest).length();
            if d < best_d {
                best_d = d;
                best_s = arc[i - 1] + len * t;
            }
        }
        (best_d, best_s)
    }
}

/// How a bot run compares to a human one.
///
/// Time alone hides the interesting failures: a bot can match the clock while scraping every wall,
/// or lose two seconds to a single bad corner. These are the numbers that tell those apart.
#[derive(Clone, Debug, PartialEq)]
pub struct LineScore {
    /// Whether the bot finished within the arrival radius of the line's end.
    pub arrived: bool,
    /// Seconds the bot took, and what the human took for the same route.
    pub time: f32,
    pub reference_time: f32,
    /// Distance from the human's line: the median is how differently the bot routes, the 95th and
    /// the max are where it went somewhere else entirely.
    pub cross_track_p50: f32,
    pub cross_track_p95: f32,
    pub cross_track_max: f32,
    /// Bot speed as a fraction of the human's, in 20 bins of progress along the line. Where this
    /// dips is *where* the bot is slow, which a single mean can never say.
    pub speed_ratio: [f32; 20],
    /// Mean of the above over bins the bot actually reached.
    pub mean_speed_ratio: f32,
    /// The same over the **back half** of the line only.
    ///
    /// Read this, not `mean_speed_ratio`, when asking whether the bot *executes* a movement well. A
    /// reference is lifted out of a match where the human entered it already at speed, and a bot
    /// placed at its start begins at rest — on a 640-unit segment the standing start is a large part
    /// of the run, and it would otherwise swamp everything the movement is actually about. By the
    /// back half the bot has whatever speed it is going to get, so this is the honest comparison.
    pub late_speed_ratio: f32,
    /// 95th percentile of per-frame heading change, degrees/sec. High means the bot is sawing at
    /// its own steering rather than holding a line.
    pub yaw_jitter_p95: f32,
    /// Frames spent travelling *away* from the next point on the line.
    pub reverse_frames: u32,
    /// Ground the bot actually covered, and what the human covered on the same reference.
    ///
    /// The pair is what says whether the two runs are the *same journey*. Segments are cut every so
    /// many units travelled, so a human who spent that circling produces a reference whose endpoints
    /// nearly coincide; a bot told to reach the end cuts the chord, covers a third of the distance,
    /// and finishes in a third of the time while never straying from a line that doubles back past
    /// itself. Comparing those two clocks is meaningless — see [`LineScore::comparable`].
    pub path_length: f32,
    pub reference_path: f32,
    /// Grounded frames that lost more than [`WALL_LOSS`] of speed in one step — running into
    /// geometry. The count that "stops bumping into things" has to move.
    pub wall_events: u32,
    /// How often the turn *reverses direction*, per second of travel.
    ///
    /// This is the honest "is the movement smooth" number, and [`LineScore::yaw_jitter_p95`] is not.
    /// A strafe jump is a wide swinging arc, so a good run turns hard and continuously — the peak
    /// turn *rate* of excellent movement and of a bot sawing at its own steering look the same. What
    /// separates them is that the arc holds its direction while the saw keeps changing its mind.
    ///
    /// Counted with a [`TURN_DEADBAND`] on both sides of the flip, so the noise either side of
    /// straight-line travel does not register as a decision.
    pub yaw_reversals: f32,
}

/// Speed lost in a single frame that reads as hitting something rather than braking. Friction and
/// deliberate stopping are far gentler; a wall takes most of the speed at once.
pub const WALL_LOSS: f32 = 60.0;

/// Arrival radius for "the bot finished the route".
pub const ARRIVE_RADIUS: f32 = 64.0;

/// Speed below which a frame's heading says nothing about steering. Matches the gate the corridor
/// metrics use for the same reason.
pub const YAW_MIN_SPEED: f32 = 100.0;

/// A run has to cover at least this share of the human's ground for the two clocks to mean the same
/// thing — see [`LineScore::comparable`].
///
/// One-sided on purpose: a bot that travels *further* than the human is simply worse at the route,
/// and timing that is exactly the point. Only travelling much less makes the comparison invalid.
/// 0.6 leaves room for the weave a strafe-jumping human adds (5-15%) without admitting a chord.
pub const PATH_COMPARABLE: f32 = 0.6;

impl LineScore {
    /// Whether this run's time can be compared to the human's at all.
    ///
    /// False when the bot covered far less ground — it found a shorter journey between the same two
    /// points than the human took, so the clocks measure different things. That is a finding, not a
    /// failure, and the run is still scored on arrival and on how cleanly it moved; it just does not
    /// belong in a time distribution.
    ///
    /// Measured rather than predicted. A reference's own directness ([`Traversal::directness`])
    /// tells you a chord *might* be much shorter, but a single right-angle corridor turn scores 0.71
    /// on it and is a perfectly fair A-to-B task, because the walls give the bot no chord to take.
    /// What the bot actually travelled settles it either way.
    pub fn comparable(&self) -> bool {
        self.reference_path < 1.0 || self.path_length >= PATH_COMPARABLE * self.reference_path
    }

    /// Score a bot trajectory — `(time, origin)` samples — against a human line.
    pub fn score(samples: &[(f32, Vec3)], reference: &ReferenceLine) -> LineScore {
        let mut cross = Vec::with_capacity(samples.len());
        let mut yaw_steps: Vec<f32> = Vec::new();
        let mut bins: [(f32, u32); 20] = [(0.0, 0); 20];
        let (mut reverse_frames, mut wall_events) = (0u32, 0u32);
        let mut path_length = 0.0f32;
        let len = reference.length().max(1.0);

        /// Carried between samples: everything the next step is measured against.
        struct Prev {
            time: f32,
            pos: Vec3,
            /// Unit heading, zero until the bot has moved far enough to have one.
            heading: Vec2,
            /// Progress along the reference, monotonic — so a bot that stalls doesn't churn bins.
            progress: f32,
            speed: f32,
        }
        let mut prev: Option<Prev> = None;
        // Signed turn rate of the previous step, and how long has been spent travelling fast enough
        // for a turn to mean anything — the denominator that makes reversals comparable across
        // movements of different length.
        let mut turn_prev: Option<f32> = None;
        let mut reversals = 0u32;
        let mut turning_secs = 0.0f32;

        for &(time, pos) in samples {
            let (dist, progress) = reference.nearest(pos);
            cross.push(dist);
            let mut heading = prev.as_ref().map_or(Vec2::ZERO, |p| p.heading);
            let mut speed = 0.0;
            if let Some(p) = &prev {
                let dt = time - p.time;
                if dt > 0.0 {
                    let step = (pos - p.pos).truncate();
                    path_length += step.length();
                    speed = step.length() / dt;
                    bins[((progress / len).clamp(0.0, 0.999) * 20.0) as usize].0 += speed;
                    bins[((progress / len).clamp(0.0, 0.999) * 20.0) as usize].1 += 1;
                    // Backwards *along the route*, which is different from being off it.
                    if progress < p.progress - 1.0 {
                        reverse_frames += 1;
                    }
                    // Losing most of the speed in one grounded step is running into something;
                    // friction and deliberate braking are far gentler, and a drop is not a wall.
                    if p.speed - speed > WALL_LOSS && (pos.z - p.pos.z).abs() < 8.0 {
                        wall_events += 1;
                    }
                    // Steering is only meaningful while actually travelling. Below this a step is
                    // short enough that its direction is mostly quantisation, and differencing it
                    // against a frame time yields nonsense — a crawling bot was reading 5600 deg/s,
                    // fifteen revolutions a second, which is not a fact about its steering.
                    if speed >= YAW_MIN_SPEED {
                        let h = step.normalize();
                        if p.heading.length_squared() > 0.0 {
                            // Signed, so the *direction* of the turn survives: a swinging arc holds
                            // its sign for many frames, a saw flips it. The magnitude alone cannot
                            // tell those apart, and excellent movement has plenty of magnitude.
                            let rate = p.heading.perp_dot(h).atan2(p.heading.dot(h)).to_degrees() / dt;
                            yaw_steps.push(rate.abs());
                            if let Some(last) = turn_prev {
                                if last.abs() > TURN_DEADBAND
                                    && rate.abs() > TURN_DEADBAND
                                    && last.signum() != rate.signum()
                                {
                                    reversals += 1;
                                }
                            }
                            turn_prev = Some(rate);
                            turning_secs += dt;
                        }
                        heading = h;
                    }
                }
            }
            prev = Some(Prev {
                time,
                pos,
                heading,
                progress: prev.as_ref().map_or(progress, |p| progress.max(p.progress)),
                speed,
            });
        }

        cross.sort_by(f32::total_cmp);
        yaw_steps.sort_by(f32::total_cmp);
        let pct = |v: &[f32], q: f32| {
            if v.is_empty() {
                0.0
            } else {
                v[(((v.len() - 1) as f32) * q) as usize]
            }
        };

        // Reference speed per progress bin, to divide by.
        let ref_arc = reference.arc();
        let mut ref_bins: [(f32, u32); 20] = [(0.0, 0); 20];
        for (i, sp) in reference.speeds.iter().enumerate() {
            let bin = ((ref_arc[i] / len).clamp(0.0, 0.999) * 20.0) as usize;
            ref_bins[bin].0 += sp;
            ref_bins[bin].1 += 1;
        }
        let mut speed_ratio = [0.0f32; 20];
        let (mut sum, mut n) = (0.0f32, 0u32);
        let (mut late_sum, mut late_n) = (0.0f32, 0u32);
        for i in 0..20 {
            let bot = if bins[i].1 > 0 {
                bins[i].0 / bins[i].1 as f32
            } else {
                0.0
            };
            let human = if ref_bins[i].1 > 0 {
                ref_bins[i].0 / ref_bins[i].1 as f32
            } else {
                0.0
            };
            if human > 1.0 && bins[i].1 > 0 {
                speed_ratio[i] = bot / human;
                sum += speed_ratio[i];
                n += 1;
                if i >= 10 {
                    late_sum += speed_ratio[i];
                    late_n += 1;
                }
            }
        }

        let end = reference.pts.last().copied().unwrap_or(Vec3::ZERO);
        let arrived = samples
            .last()
            .is_some_and(|&(_, p)| (p - end).truncate().length() <= ARRIVE_RADIUS);
        let time = match (samples.first(), samples.last()) {
            (Some(a), Some(b)) => b.0 - a.0,
            _ => 0.0,
        };

        LineScore {
            arrived,
            time,
            reference_time: reference.duration,
            cross_track_p50: pct(&cross, 0.5),
            cross_track_p95: pct(&cross, 0.95),
            cross_track_max: cross.last().copied().unwrap_or(0.0),
            speed_ratio,
            mean_speed_ratio: if n > 0 { sum / n as f32 } else { 0.0 },
            late_speed_ratio: if late_n > 0 { late_sum / late_n as f32 } else { 0.0 },
            yaw_jitter_p95: pct(&yaw_steps, 0.95),
            reverse_frames,
            wall_events,
            path_length,
            reference_path: len,
            yaw_reversals: if turning_secs > 0.0 {
                reversals as f32 / turning_secs
            } else {
                0.0
            },
        }
    }
}

/// Turn rate below which a step's direction is not a steering decision — the wobble either side of
/// running straight. Both steps have to clear it for a sign change to count as a reversal.
pub const TURN_DEADBAND: f32 = 60.0;

impl Track {
    /// Cut this player's whole track into continuous movement segments.
    ///
    /// The point of this, rather than [`Track::traversals`], is that **no route should be
    /// hardcoded**. Which paths exist is a property of the map and of what the game asked for at
    /// that moment; a curated A-to-B list smuggles in an assumption about where players *ought* to
    /// go. What actually matters is whether the bot can *execute* each piece of movement a human
    /// performed. So: take everything, cut it where the player stopped being one continuous piece
    /// of motion, and let the map decide what the segments are.
    ///
    /// A segment ends at a genuine break in continuity — death, a teleport, or standing still long
    /// enough that whatever follows is a new movement rather than the same one.
    ///
    /// Those breaks alone are not enough, though, and the reason is the thing that makes this game
    /// unusual: players essentially never stop. Continuous motion runs for tens of seconds, so
    /// cutting only at stops yields a handful of enormous spans that share no endpoints and
    /// therefore never collapse against each other. `max_path` is what makes the suite work — it
    /// cuts travel into pieces of comparable size, so the same corridor run twice produces segments
    /// that land in the same endpoint buckets and dedupe. `max_secs` remains as a backstop for slow
    /// movement (water, a lift) that would otherwise run long.
    ///
    /// Segments shorter than `min_path` are dropped: that is manoeuvring, not travel.
    pub fn segments(&self, min_path: f32, max_path: f32, max_secs: f32) -> Vec<Traversal> {
        /// Standing still for longer than this ends a segment. Short enough that a pause to shoot
        /// separates two movements; long enough that quantisation stalls (see `TELEPORT_SPEED`'s
        /// note on 1/8-unit rounding) do not.
        const STOP_SECS: f32 = 0.5;
        /// Below this a player is not travelling.
        const MOVING_UPS: f32 = 40.0;

        let mut out = Vec::new();
        let mut start: Option<usize> = None;
        let mut stopped_since: Option<f32> = None;
        let mut travelled = 0.0f32;
        let mut flush = |start: &mut Option<usize>, end: usize, motions: &[Motion]| {
            if let Some(s) = start.take() {
                if end > s {
                    let run = Traversal {
                        player: self.player,
                        start_time: motions[s].time,
                        end_time: motions[end].time,
                        motions: motions[s..=end].to_vec(),
                    };
                    if run.path_length() >= min_path {
                        out.push(run);
                    }
                }
            }
        };

        for (i, m) in self.motions.iter().enumerate() {
            if m.warped || m.dead {
                flush(&mut start, i.saturating_sub(1), &self.motions);
                stopped_since = None;
                continue;
            }
            if m.horizontal_speed < MOVING_UPS {
                // A pause only breaks the segment once it has lasted; a momentary dip is still the
                // same movement (landing, a step, a rounded-to-zero slow frame).
                let since = *stopped_since.get_or_insert(m.time);
                if m.time - since >= STOP_SECS {
                    flush(&mut start, i, &self.motions);
                }
                continue;
            }
            stopped_since = None;
            match start {
                None => {
                    start = Some(i);
                    travelled = 0.0;
                }
                Some(s) => {
                    travelled += (m.origin - self.motions[i - 1].origin).truncate().length();
                    if travelled >= max_path || m.time - self.motions[s].time >= max_secs {
                        flush(&mut start, i, &self.motions);
                        // The next segment begins where this one ended, so the suite tiles the
                        // player's path rather than sampling disconnected pieces of it.
                        start = Some(i);
                        travelled = 0.0;
                    }
                }
            }
        }
        flush(&mut start, self.motions.len().saturating_sub(1), &self.motions);
        out
    }

    /// Every run this player made from near `from` to near `to`, within `max_secs`.
    ///
    /// The discovery step: given two points off the navmesh, find where a human actually did that
    /// traversal so the bot has something to be measured against. A 20-minute team game crosses the
    /// same ground dozens of times, and the spread over those runs is as informative as the best of
    /// them — it says how much of a bot's shortfall is a real gap and how much is variance.
    ///
    /// A run ends at the *first* arrival at `to`, and runs cannot overlap: leaving `from` again
    /// starts a new candidate. Warped frames abort the run in progress, since a player who
    /// teleported did not travel the route.
    ///
    /// `max_detour` is what makes the result a *route* rather than a coincidence. In a real match a
    /// player is constantly at A and later at B without having travelled between them — they fought,
    /// took a detour for an item, died elsewhere. Bounding path length against the straight-line
    /// distance keeps only runs that actually went there: on dm3, a stairs-to-armour pair that
    /// yields one 5279-unit "run" over an 868-unit gap yields nothing once this is applied, which is
    /// the honest answer.
    pub fn traversals(&self, from: Vec3, to: Vec3, radius: f32, max_secs: f32, max_detour: f32) -> Vec<Traversal> {
        // A run is clocked from the *edge* of the start zone to the edge of the end zone, so the
        // ground it can possibly cover is the gap minus both radii. Comparing its path against the
        // full centre-to-centre distance would make every short route look implausibly direct — on
        // dm3's 278-unit balcony crossing with a 64-unit radius, a perfect run measures 0.6x.
        let reachable = ((to - from).length() - 2.0 * radius).max(1.0);
        let near = |a: Vec3, b: Vec3| (a - b).length() <= radius;
        let mut out = Vec::new();
        let mut start: Option<usize> = None;
        for (i, m) in self.motions.iter().enumerate() {
            if m.warped || m.dead {
                start = None;
                continue;
            }
            if near(m.origin, from) {
                // Restart the clock while still in the start zone, so the run measures the journey
                // rather than the loitering before it.
                start = Some(i);
                continue;
            }
            let Some(s) = start else { continue };
            if m.time - self.motions[s].time > max_secs {
                start = None;
                continue;
            }
            if near(m.origin, to) {
                let run = Traversal {
                    player: self.player,
                    start_time: self.motions[s].time,
                    end_time: m.time,
                    motions: self.motions[s..=i].to_vec(),
                };
                if run.path_length() <= reachable * max_detour {
                    out.push(run);
                }
                start = None;
            }
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::analysis::track;
    use crate::{Demo, Format, Frame, PlayerSlot};

    fn demo_of(pts: &[(f32, Vec3)]) -> Demo {
        Demo {
            path: "t.mvd".into(),
            proto: rtx_proto::protocol::ProtoState::new_mvd(),
            format: Format::Mvd,
            local_player: None,
            players: vec![PlayerSlot::default(); 32],
            levelname: String::new(),
            movevars: None,
            demo_cmds: Vec::new(),
            frames: pts
                .iter()
                .map(|&(time, origin)| Frame {
                    time,
                    player: 0,
                    origin,
                    angles: Vec3::ZERO,
                    velocity: None,
                    command: None,
                    dead: false,
                    on_ground: None,
                    weaponframe: None,
                })
                .collect(),
            warnings: Vec::new(),
        }
    }

    /// A straight run out and back finds exactly one A→B traversal, timed from the last frame in
    /// the start zone rather than the first.
    #[test]
    fn finds_one_traversal_per_trip() {
        let mut pts = Vec::new();
        // Loiter at A, run to B, come back.
        for i in 0..5 {
            pts.push((0.05 * i as f32, Vec3::new(0.0, 0.0, 0.0)));
        }
        for i in 1..=10 {
            pts.push((0.25 + 0.05 * i as f32, Vec3::new(50.0 * i as f32, 0.0, 0.0)));
        }
        for i in 1..=10 {
            pts.push((0.75 + 0.05 * i as f32, Vec3::new(500.0 - 50.0 * i as f32, 0.0, 0.0)));
        }
        let t = track(&demo_of(&pts), 0);
        let runs = t.traversals(Vec3::ZERO, Vec3::new(500.0, 0.0, 0.0), 32.0, 10.0, 2.0);
        assert_eq!(runs.len(), 1, "one out-and-back is one A->B run");
        let r = &runs[0];
        assert!((r.duration() - 0.5).abs() < 0.06, "duration {}", r.duration());
        assert!(r.path_length() > 450.0, "path {}", r.path_length());
        assert!(r.mean_speed() > 800.0, "mean {}", r.mean_speed());
    }

    /// Cross-track is measured to the line, not to its samples — a bot running the same route at a
    /// different sample rate must not read as wandering.
    #[test]
    fn cross_track_is_segment_wise() {
        let line = ReferenceLine {
            name: "l".into(),
            player: 0,
            duration: 1.0,
            pts: vec![Vec3::ZERO, Vec3::new(100.0, 0.0, 0.0), Vec3::new(200.0, 0.0, 0.0)],
            speeds: vec![300.0, 300.0, 300.0],
        };
        // A point halfway along the first segment, dead on the line.
        let (d, s) = line.nearest(Vec3::new(50.0, 0.0, 0.0));
        assert!(d < 0.01, "on the line, got {d}");
        assert!((s - 50.0).abs() < 0.01, "progress {s}");
        // And one 20 units to the side.
        let (d, _) = line.nearest(Vec3::new(50.0, 20.0, 0.0));
        assert!((d - 20.0).abs() < 0.01, "off the line, got {d}");
        assert!((line.length() - 200.0).abs() < 0.01);
    }

    /// The score separates "slower" from "went somewhere else" from "hit a wall".
    #[test]
    fn scores_a_slow_but_accurate_run() {
        let line = ReferenceLine {
            name: "l".into(),
            player: 0,
            duration: 1.0,
            pts: (0..=10).map(|i| Vec3::new(50.0 * i as f32, 0.0, 0.0)).collect(),
            speeds: vec![500.0; 11],
        };
        // Same path, half the speed: on the line, but every bin at ~0.5.
        let samples: Vec<(f32, Vec3)> = (0..=10)
            .map(|i| (0.2 * i as f32, Vec3::new(50.0 * i as f32, 0.0, 0.0)))
            .collect();
        let s = LineScore::score(&samples, &line);
        assert!(s.arrived);
        assert!(s.cross_track_p95 < 1.0, "stayed on the line: {}", s.cross_track_p95);
        assert!(
            (s.mean_speed_ratio - 0.5).abs() < 0.05,
            "half speed: {}",
            s.mean_speed_ratio
        );
        assert_eq!(s.reverse_frames, 0);
        assert_eq!(s.wall_events, 0);
    }

    /// A run that detours off the line is caught by cross-track even when it arrives on time.
    #[test]
    fn catches_a_detour() {
        let line = ReferenceLine {
            name: "l".into(),
            player: 0,
            duration: 1.0,
            pts: (0..=10).map(|i| Vec3::new(50.0 * i as f32, 0.0, 0.0)).collect(),
            speeds: vec![500.0; 11],
        };
        // Bulges 120 units off the line in the middle.
        let samples: Vec<(f32, Vec3)> = (0..=10)
            .map(|i| {
                let bulge = if (3..=7).contains(&i) { 120.0 } else { 0.0 };
                (0.1 * i as f32, Vec3::new(50.0 * i as f32, bulge, 0.0))
            })
            .collect();
        let s = LineScore::score(&samples, &line);
        assert!(s.arrived, "it still gets there");
        assert!(s.cross_track_max > 100.0, "the detour shows: {}", s.cross_track_max);
        assert!(
            s.cross_track_p95 > 100.0,
            "and across enough of the run to clear p95: {}",
            s.cross_track_p95
        );
        // The median stays near zero, and that is right: most of this run *was* on the line. It is
        // why the tails are reported too — a route that is mostly fine with one bad excursion is a
        // different problem from one that is uniformly off, and p50 alone cannot tell them apart.
        assert!(s.cross_track_p50 < 1.0, "p50 {}", s.cross_track_p50);
    }

    /// A run of `n` frames along +X at `step` units apart, starting at `from`, `dt` per frame.
    fn run_along(from: Vec3, step: Vec3, n: usize, dt: f32) -> Vec<(f32, Vec3)> {
        (0..n).map(|i| (dt * i as f32, from + step * i as f32)).collect()
    }

    /// Uninterrupted movement is cut by distance travelled, and the pieces tile: each begins where
    /// the last ended. This is the rule that matters, because a match almost never gives us a stop
    /// to cut at.
    #[test]
    fn segments_cut_by_distance_and_tile() {
        // 2000 units of continuous running at ~400 ups.
        let pts = run_along(Vec3::ZERO, Vec3::new(5.0, 0.0, 0.0), 401, 0.0125);
        let segs = track(&demo_of(&pts), 0).segments(192.0, 640.0, 60.0);
        assert_eq!(segs.len(), 3, "2000u at 640u a piece, last remainder dropped");
        for s in &segs {
            assert!(
                (s.path_length() - 640.0).abs() < 10.0,
                "cut at the distance, not the clock: {}",
                s.path_length()
            );
        }
        for w in segs.windows(2) {
            let end = w[0].motions.last().unwrap().origin;
            let start = w[1].motions[0].origin;
            assert_eq!(end, start, "segments tile rather than sample");
        }
    }

    /// A doubled-back reference is not a fair time comparison, and it hides that fact well: the bot
    /// cutting the chord *also* reads as hugging the line, because a path that returns on itself is
    /// never far from any point on itself. Only the ground actually covered gives it away.
    #[test]
    fn a_shortcut_is_not_a_faster_run() {
        // Out 400 units and back, so start and end are 8 units apart over 800 of path.
        let mut pts = run_along(Vec3::ZERO, Vec3::new(8.0, 0.0, 0.0), 51, 0.0125);
        let t0 = pts.last().unwrap().0;
        pts.extend((1..=50).map(|i| (t0 + 0.0125 * i as f32, Vec3::new(400.0 - 8.0 * i as f32, 0.0, 0.0))));
        let seg = track(&demo_of(&pts), 0).segments(192.0, 4096.0, 60.0).remove(0);
        assert!(seg.directness() < 0.1, "a loop is indirect: {}", seg.directness());

        // A bot that barely moves is "on the line" the whole time and lands within the arrival
        // radius of the end — which is why arrival, cross-track and the clock *all* flatter it.
        let samples: Vec<(f32, Vec3)> = (0..=10).map(|i| (0.05 * i as f32, Vec3::new(4.0, 0.0, 0.0))).collect();
        let s = LineScore::score(&samples, &seg.reference("loop"));
        assert!(s.arrived, "the chord of a loop is no distance at all");
        assert!(
            s.cross_track_p95 < 1.0,
            "and it never leaves the line: {}",
            s.cross_track_p95
        );
        assert!(s.time < s.reference_time, "so the clock flatters it");
        assert!(!s.comparable(), "only the ground covered catches it");

        // The same reference, run properly: covering the human's ground makes it timeable — even
        // though the reference is exactly as indirect as before.
        let full: Vec<(f32, Vec3)> = pts.iter().map(|&(t, p)| (t * 2.0, p)).collect();
        let s = LineScore::score(&full, &seg.reference("loop"));
        assert!(s.comparable(), "it went where the human went");
        assert!(s.time > s.reference_time, "at half the speed: {:.2}s", s.time);

        // And a bot that takes a *longer* route is still timed — that is the finding, not an excuse.
        let straight = track(
            &demo_of(&run_along(Vec3::ZERO, Vec3::new(8.0, 0.0, 0.0), 100, 0.0125)),
            0,
        )
        .segments(192.0, 4096.0, 60.0)
        .remove(0);
        assert!(straight.directness() > 0.99, "{}", straight.directness());
        let detour: Vec<(f32, Vec3)> = (0..=100)
            .map(|i| {
                let x = 7.9 * i as f32;
                (
                    0.02 * i as f32,
                    Vec3::new(x, if i % 2 == 0 { 40.0 } else { -40.0 }, 0.0),
                )
            })
            .collect();
        let s = LineScore::score(&detour, &straight.reference("straight"));
        assert!(s.comparable(), "a longer path is comparable: {:.0}u", s.path_length);
    }

    /// A wide smooth arc and a bot sawing at its own steering reach the *same* peak turn rate — a
    /// strafe jump is a hard continuous turn, and excellent movement has plenty of magnitude. Only
    /// the reversal count separates them, which is why it is the number that matters.
    #[test]
    fn an_arc_and_a_saw_differ_by_reversals_not_by_rate() {
        let line = ReferenceLine {
            name: "l".into(),
            player: 0,
            duration: 1.0,
            pts: (0..=60).map(|i| Vec3::new(10.0 * i as f32, 0.0, 0.0)).collect(),
            speeds: vec![600.0; 61],
        };
        // A steady arc: heading rotates one way throughout, ~600 ups.
        let dt = 1.0 / 72.0;
        let mut p = Vec3::ZERO;
        let arc: Vec<(f32, Vec3)> = (0..60)
            .map(|i| {
                let a = (i as f32 * 1.5).to_radians();
                p += Vec3::new(a.cos(), a.sin(), 0.0) * 600.0 * dt;
                (dt * i as f32, p)
            })
            .collect();
        // A saw: same speed, heading alternates either side of straight by the same angle.
        let mut q = Vec3::ZERO;
        let saw: Vec<(f32, Vec3)> = (0..60)
            .map(|i| {
                let a = if i % 2 == 0 { 22.0f32 } else { -22.0f32 }.to_radians();
                q += Vec3::new(a.cos(), a.sin(), 0.0) * 600.0 * dt;
                (dt * i as f32, q)
            })
            .collect();

        let (a, s) = (
            LineScore::score(&arc, &line).clone(),
            LineScore::score(&saw, &line).clone(),
        );
        // The old metric cannot tell them apart — the saw's peak rate is if anything *higher*.
        assert!(
            s.yaw_jitter_p95 >= a.yaw_jitter_p95,
            "peak rate does not separate them: arc {} saw {}",
            a.yaw_jitter_p95,
            s.yaw_jitter_p95
        );
        // The reversal count does, decisively.
        assert!(a.yaw_reversals < 1.0, "an arc holds its direction: {}", a.yaw_reversals);
        assert!(s.yaw_reversals > 20.0, "a saw keeps changing it: {}", s.yaw_reversals);
    }

    /// Redundancy is about ground covered, not endpoints — the case endpoint bucketing gets wrong,
    /// since the cutter starts two players' windows at different offsets along the same corridor.
    #[test]
    fn distinct_collapses_by_ground_covered() {
        let mk = |player: u8, from: Vec3, dir: Vec3, dt: f32| {
            let pts = run_along(from, dir, 100, dt);
            let mut t = track(&demo_of(&pts), 0);
            t.player = player;
            t.segments(192.0, 4096.0, 60.0).remove(0)
        };
        // 792 units of corridor, and a second run 64 units out of phase with it. No endpoint
        // matches, but 92% of the ground does, so it is the same movement.
        let a = mk(0, Vec3::ZERO, Vec3::new(8.0, 0.0, 0.0), 0.0125);
        let shifted = mk(1, Vec3::new(64.0, 0.0, 0.0), Vec3::new(8.0, 0.0, 0.0), 0.0125);
        assert_eq!(distinct(vec![a.clone(), shifted], 128.0).len(), 1, "same ground");

        // Far enough out of phase and it is no longer the same movement: 300 units of this run is
        // ground nothing has covered, and a bot that can do the first half has not shown it can do
        // the second. The threshold is [`COVER_FRAC`], and it is a coverage rule, not a phase fix.
        let past = mk(2, Vec3::new(300.0, 0.0, 0.0), Vec3::new(8.0, 0.0, 0.0), 0.0125);
        assert_eq!(distinct(vec![a.clone(), past], 128.0).len(), 2, "new ground survives");

        // The reverse direction is a different movement over identical ground, and must survive.
        let back = mk(3, Vec3::new(792.0, 0.0, 0.0), Vec3::new(-8.0, 0.0, 0.0), 0.0125);
        assert_eq!(distinct(vec![a.clone(), back], 128.0).len(), 2, "direction counts");

        // And of two runs over the same ground, the faster one is what is kept.
        let slow = mk(4, Vec3::ZERO, Vec3::new(8.0, 0.0, 0.0), 0.05);
        let kept = distinct(vec![slow, a.clone()], 128.0);
        assert_eq!(kept.len(), 1);
        assert!(kept[0].mean_speed() > 600.0, "kept {} ups", kept[0].mean_speed());
    }
}
