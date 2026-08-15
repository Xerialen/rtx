// SPDX-License-Identifier: AGPL-3.0-or-later

//! The typed MCP<->game control protocol, spoken as length-framed [msgpack].
//!
//! The bot-control channel used to be newline-delimited text with hand-built JSON replies. This crate
//! replaces that with a compact, typed wire: the MCP sends a [`Request`] (an `id` plus a [`Cmd`]) and
//! the game answers with a [`Msg`] — either a `Reply` correlated by `id` carrying a typed [`Resp`] (or
//! an error string), or an async [`Event`] emitted by a puppet order as it plays out. Both crates
//! depend on this schema, so it is single-sourced; the MCP decodes typed values and re-serializes them
//! as JSON for Claude, and the game builds typed values instead of formatting JSON strings.
//!
//! Frames are `[u32 little-endian byte length][msgpack payload]` (see [`to_frame`] / [`read_frame`]).
//! World positions are `[x, y, z]`; motion traces are [`TrajRow`]s. Descriptive
//! enum labels (weapon, link kind, hazard, oracle mode, …) travel as strings — they are display
//! labels, so the schema stays typed at the structural level without mirroring a dozen game enums.
//!
//! [msgpack]: https://msgpack.org/

use std::io::{self, Read};

use rtx_auditlog::AuditFrame;
use serde::{Deserialize, Serialize};

/// A 3D world position, `[x, y, z]`.
pub type Vec3 = [f32; 3];

/// One sampled frame of a bot's motion: `[t, x, y, z, vx, vy, vz, phase]`.
///
/// `phase` is the bunnyhop controller's state (0 off, 1 prestrafe, 2 hop, 3 zigzag). It is on the
/// wire because the interesting movement questions are all "which regime was it in when that
/// happened", and reconstructing that from positions is guesswork -- a bot grounded half the time
/// looks identical whether it chose to walk or failed to hop.
pub type TrajRow = [f32; 8];

// ---------------------------------------------------------------------------------------------------
// Framing codec
// ---------------------------------------------------------------------------------------------------

/// Encode `v` as msgpack behind a 4-byte little-endian length prefix — one wire frame.
pub fn to_frame<T: Serialize>(v: &T) -> Vec<u8> {
    let body = rmp_serde::to_vec_named(v).expect("msgpack encode");
    let mut frame = Vec::with_capacity(body.len() + 4);
    frame.extend_from_slice(&(body.len() as u32).to_le_bytes());
    frame.extend_from_slice(&body);
    frame
}

/// Largest frame payload we will allocate for. A guard against a corrupted or garbage length prefix
/// (a peer that died mid-frame, a desynced stream) blowing up into a multi-gigabyte allocation and
/// crashing the reader. Real frames — even a full status or a long trajectory — are far under this.
pub const MAX_FRAME: usize = 64 * 1024 * 1024;

/// Read one length-prefixed frame's payload bytes, or `Ok(None)` at a clean end of stream. A length
/// past [`MAX_FRAME`] is treated as a protocol error (so the caller reconnects) rather than allocated.
pub fn read_frame<R: Read>(r: &mut R) -> io::Result<Option<Vec<u8>>> {
    let mut len = [0u8; 4];
    match r.read_exact(&mut len) {
        Ok(()) => {}
        Err(e) if e.kind() == io::ErrorKind::UnexpectedEof => return Ok(None),
        Err(e) => return Err(e),
    }
    let n = u32::from_le_bytes(len) as usize;
    if n > MAX_FRAME {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "control frame length too large",
        ));
    }
    let mut body = vec![0u8; n];
    r.read_exact(&mut body)?;
    Ok(Some(body))
}

/// Decode a frame payload as `T`.
pub fn decode<T: for<'de> Deserialize<'de>>(bytes: &[u8]) -> Result<T, rmp_serde::decode::Error> {
    rmp_serde::from_slice(bytes)
}

// ---------------------------------------------------------------------------------------------------
// Requests (MCP -> game)
// ---------------------------------------------------------------------------------------------------

/// A request frame: a caller-chosen `id` echoed back on the matching [`Msg::Reply`], and the command.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Request {
    pub id: i64,
    pub cmd: Cmd,
}

/// Every bot-control verb.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub enum Cmd {
    /// Server + strategy status (map, navmesh, match, oracle, per-bot state).
    Status,
    /// Queue the team-match start.
    MatchStart,
    /// Every rocket-jump link the navmesh generated.
    Links,
    /// The map's bot-goal items.
    Items,
    /// Make a bot fit for a rocket-jump test.
    Prep { bot: u32, health: f32, rockets: f32 },
    /// Place a bot at a world point carrying `vel`.
    ///
    /// Velocity is part of placement because for a movement test it is part of the *starting
    /// condition*: a human reference is lifted out of a match where the player entered the movement
    /// already at speed, so dropping a bot at the same point from rest measures its standing start
    /// rather than the movement. `[0, 0, 0]` is the plain "put it here" placement.
    Teleport { bot: u32, pos: Vec3, vel: Vec3 },
    /// Order a bot to run to a world point (emits `Arrived` / `GotoStall`).
    Goto { bot: u32, pos: Vec3 },
    /// Order a bot to fly a rocket-jump link (emits `RjResult`).
    Rj { bot: u32, link: u32 },
    /// Order a bot to fly a non-RJ link (emits `FlyResult`).
    Fly { bot: u32, link: u32 },
    /// Park a bot (clear any order).
    Hold { bot: u32 },
    /// Stop a bot and clear its puppet state.
    Stop { bot: u32 },
    /// Set a live server cvar.
    Set { name: String, value: String },
    /// Read a cvar's string and float value.
    Get { name: String },
    /// Run a raw console command.
    RunCmd { raw: String },
    /// Inspect the navmesh cell nearest a world point.
    Cell { pos: Vec3 },
    /// Inspect a navmesh cell by id — the other direction from [`Cmd::Cell`], so a cell named in a
    /// route, a link listing or an earlier reply can be looked up without first knowing where it is.
    CellById { cell: u32 },
    /// Dump a bot's current A* route.
    Route { bot: u32 },
    /// Dump the tail of a bot's `rtx_bot_debug` audit ring.
    Audit { bot: u32, lines: u32 },
    /// List generated speed-jump links. Curls (`gain > 0`) *and* straight/chained speed jumps —
    /// otherwise the straight family is listed by nothing and can't be fly-tested by id.
    Curls,
    /// Fetch the current map's raw BSP file, so a viewer can render the world without a local copy.
    Bsp,
    /// Every map the server could load — loose `.bsp` plus the ones inside its `.pak`s. Lets a client
    /// offer a map picker without guessing where the server's gamedirs live on disk.
    Maps,
    /// Probe the build-time curl certifier.
    Probe {
        takeoff: Vec3,
        tgt: Vec3,
        psi0: f32,
        runway: f32,
    },
    /// Search the offline sim for a speed-curl jump.
    Curl { src: Vec3, tgt: Vec3 },
    /// Hand-plant a SpeedJump link into the live graph. `gain` overrides the air-curl gain the link is
    /// baked with (default: the `rtx_jump_curl_gain` cvar, else 12) — the parameter a side-jump sweep
    /// varies, so it has to travel with the plant rather than through a server-wide cvar.
    PlanLink {
        from: Vec3,
        takeoff: Vec3,
        tgt: Vec3,
        v_req: f32,
        #[serde(default)]
        gain: Option<f32>,
    },
    /// Hand-plant a standing cell at `pos` — a walkable surface the column carve's XY pitch cannot
    /// sample (see `NavGraph::plant_cell`). Inert on its own: nothing routes into it.
    PlanCell { pos: Vec3 },
    /// Hand-plant a `Drop` link from the cell nearest `from` to the cell nearest `to`, so a bot standing
    /// on a planted shelf has a way off it.
    PlanDrop { from: Vec3, to: Vec3 },
}

// ---------------------------------------------------------------------------------------------------
// Messages (game -> MCP): replies and async events
// ---------------------------------------------------------------------------------------------------

/// One outbound frame from the game: a reply to a request, or an async puppet event.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub enum Msg {
    /// Answer to the request with this `id`. `Err` carries the failure message.
    Reply { id: i64, result: Result<Resp, String> },
    /// A puppet order's lifecycle event (not correlated to a request).
    Event(Event),
}

/// A typed command result. `Ack`/`Queued` cover the verbs whose reply is just a confirmation.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub enum Resp {
    Status(Box<StatusResp>),
    /// A verb that only queues work (`MatchStart`, `RunCmd`).
    Queued,
    Links(Vec<RjLink>),
    Items(Vec<ItemInfo>),
    Prep {
        bot: u32,
        health: f32,
        rockets: f32,
    },
    Teleport {
        bot: u32,
        origin: Vec3,
    },
    Goto {
        bot: u32,
        target: Vec3,
    },
    Rj {
        bot: u32,
        link: u32,
    },
    Fly {
        bot: u32,
        link: u32,
    },
    /// `Hold` / `Stop` — just the bot id.
    Ack {
        bot: u32,
    },
    Set {
        name: String,
        value: String,
    },
    Get {
        name: String,
        string: String,
        value: f32,
    },
    Cell(CellResp),
    Route(RouteResp),
    Audit(AuditResp),
    Curls(Vec<CurlLink>),
    Probe(ProbeResp),
    Curl(CurlResp),
    PlanLink(PlanLinkResp),
    PlanCell(PlanCellResp),
    PlanDrop(PlanDropResp),
    Bsp(Box<BspResp>),
    /// Loadable map names, lowercased and sorted (see [`Cmd::Maps`]).
    Maps(Vec<String>),
}

/// The current map's raw BSP file plus its name, so a viewer can parse the render lumps and draw the
/// world without needing a local copy of the map. `bytes` travels as a msgpack `bin` (not an int
/// array) via `serde_bytes`, so a multi-MB map stays compact on the wire.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BspResp {
    pub map: String,
    #[serde(with = "serde_bytes")]
    pub bytes: Vec<u8>,
}

// --- status -----------------------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct StatusResp {
    pub map: String,
    pub time: f32,
    /// `"ready"`, `"building"`, or `"none"`.
    pub navmesh: String,
    pub cells: u32,
    pub links: u32,
    pub rj_links: u32,
    pub match_: MatchInfo,
    pub oracle: OracleInfo,
    pub bots: Vec<BotStatus>,
    /// Connected human clients. A separate array from `bots` on purpose: every existing
    /// consumer that iterates `bots` keeps its bots-only contract, and a movement-lab tool
    /// that wants the human reads this one. Empty on a server nobody has joined.
    #[serde(default)]
    pub players: Vec<PlayerStatus>,
}

/// One connected human client, as much of it as a movement lab needs to attribute a position
/// to a navmesh cell. Deliberately much smaller than [`BotStatus`]: no goal, no route, no
/// puppet order — a human has none of those.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PlayerStatus {
    pub ent: u32,
    pub name: String,
    pub origin: Vec3,
    pub health: f32,
    pub on_ground: bool,
    pub alive: bool,
    pub speed: f32,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct MatchInfo {
    pub mode: String,
    pub format: String,
    pub phase: String,
    pub teams: u32,
    pub size: u32,
    pub teamplay: i32,
    pub timelimit: f32,
    pub fraglimit: f32,
    pub live_until: f32,
    pub scores: Vec<i32>,
    pub roster: Vec<RosterEntry>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RosterEntry {
    pub name: String,
    pub team: u32,
}

/// A referenced entity (enemy, goal item), or absent.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EntRef {
    pub ent: u32,
    pub name: String,
    pub classname: String,
    pub origin: Vec3,
    pub solid: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Ammo {
    pub shells: i32,
    pub nails: i32,
    pub rockets: i32,
    pub cells: i32,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BotGoal {
    /// Times this bot has swapped one live item goal for another — the goal-churn tripwire for a
    /// change to item valuation. Defaulted so an older server still parses.
    #[serde(default)]
    pub switches: u32,
    pub item: Option<EntRef>,
    pub commit: String,
    pub since: f32,
    pub next_item: Option<EntRef>,
    pub hold_item: Option<EntRef>,
    pub hold_for: Option<EntRef>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RouteHead {
    pub pos: u32,
    pub len: u32,
    pub next: Option<RouteNext>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RouteNext {
    pub link: u32,
    pub kind: String,
    pub target: Vec3,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BotStatus {
    pub ent: u32,
    pub client: i32,
    pub name: String,
    pub team: i32,
    pub team_name: String,
    pub frags: i32,
    pub origin: Vec3,
    pub health: f32,
    pub armor: f32,
    pub armor_type: f32,
    pub weapon: String,
    pub items: String,
    pub ammo: Ammo,
    pub on_ground: bool,
    pub alive: bool,
    pub order: String,
    pub posture: String,
    pub known_enemy: Option<EntRef>,
    pub goal: BotGoal,
    pub route: RouteHead,
    pub rj_phase: String,
    pub speed: f32,
    pub bhop: String,
    pub bhop_peak: f32,
    /// Pack-economy tallies for this bot. Defaulted so an older server still parses.
    #[serde(default)]
    pub packs: PackStats,
}

/// What this bot did in the weapon-transfer market: how often it holstered the big gun with no
/// fight on, how many claimed packs it collected, and how many times it died holding a big weapon
/// and handed one over. The scoreboard doesn't show any of this, and it is worth about half of
/// every armed kill.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct PackStats {
    pub sg_swaps: u32,
    /// Claims handed out — the denominator for `secured`. Defaulted so an older server still parses.
    #[serde(default)]
    pub hinted: u32,
    pub secured: u32,
    pub fed: u32,
}

// --- oracle -----------------------------------------------------------------------------------------

/// The seven evaluation counters, shared by the top-level summary and each nugget-kind breakdown.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct EvalCounts {
    pub treated: u32,
    pub treated_success: u32,
    pub controls: u32,
    pub control_success: u32,
    pub applied: u32,
    pub invalidated: u32,
    pub pending: u32,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct EpisodeEval {
    pub counts: EvalCounts,
    pub by_kind: Vec<(String, EvalCounts)>,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Eval {
    pub counts: EvalCounts,
    pub by_kind: Vec<(String, EvalCounts)>,
    pub episodes: EpisodeEval,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Communication {
    pub proposed: u32,
    pub communicated: u32,
    pub refreshed: u32,
    pub suppressed: u32,
    pub superseded: u32,
    pub arm_clears: u32,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Nugget {
    pub recipient: i32,
    pub kind: String,
    pub target_cell: u32,
    pub subject: i32,
    pub confidence: f32,
    pub decision_at: f32,
    pub evidence_at: f32,
    pub expires_at: f32,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PlanTeam {
    pub team: u32,
    pub mode: String,
    pub control: String,
    /// Our measured power total minus the believed enemy total, corpses counted as fresh spawns.
    /// One unit is worth about one team frag over the next minute, so this reads directly as "how
    /// far ahead are we, on equipment" — and it is what the control state is decided from.
    #[serde(default)]
    pub power_gap: f32,
    pub nuggets: Vec<Nugget>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Plan {
    pub generation: u64,
    pub at: f32,
    pub teams: Vec<PlanTeam>,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct OracleInfo {
    pub running: bool,
    pub epoch: u64,
    pub last_output: f32,
    pub plan: Option<Plan>,
    pub communication: Communication,
    pub eval: Eval,
}

// --- rj / items / cell / route / audit / curl -------------------------------------------------------

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RjLink {
    pub link: u32,
    pub src: Vec3,
    pub tgt: Vec3,
    pub fire_pitch: f32,
    pub fire_yaw: f32,
    pub fire_delay: f32,
    pub airtime: f32,
    pub self_damage: f32,
    pub v0: Vec3,
    pub blast: Vec3,
    pub pos_blast: Vec3,
    pub land: Vec3,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct NavCell {
    pub cell: u32,
    pub origin: Vec3,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ItemInfo {
    pub ent: u32,
    pub classname: String,
    pub origin: Vec3,
    pub available: bool,
    pub nav: Option<NavCell>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CellLinkOut {
    pub link: u32,
    pub kind: String,
    /// The cell this link lands on, so the graph can be walked by id rather than re-resolved by point.
    pub to_cell: u32,
    pub to: Vec3,
    pub cost: f32,
    pub tgt_hazard: String,
    pub hazard_hp: f32,
    pub water_extra: f32,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CellLinkIn {
    pub link: u32,
    pub kind: String,
    /// The cell this link leaves from — see [`CellLinkOut::to_cell`].
    pub from_cell: u32,
    pub from: Vec3,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CellResp {
    pub cell: u32,
    pub origin: Vec3,
    pub hazard: String,
    /// Whether the cell sits beside a fatal drop — the edge lane of a staircase or walkway. Purely
    /// descriptive: it does not price the cell's links. What it *does* drive is the runtime movement
    /// policy, which drops off the bunnyhop and hands the leg to the predictive planners here, so this
    /// is what explains a bot walking a stretch it would otherwise hop.
    pub ledge: bool,
    pub out: Vec<CellLinkOut>,
    pub incoming: Vec<CellLinkIn>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RouteLeg {
    pub i: u32,
    pub link: u32,
    pub kind: String,
    /// Cells the leg runs between, so a route reads as ids and not only as coordinates.
    pub src_cell: u32,
    pub tgt_cell: u32,
    pub src: Vec3,
    pub tgt: Vec3,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RouteResp {
    pub bot: u32,
    pub route_pos: u32,
    pub origin: Vec3,
    pub legs: Vec<RouteLeg>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AuditResp {
    pub bot: u32,
    pub count: u32,
    pub frames: Vec<AuditFrame>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CurlLink {
    pub link: u32,
    pub from: Vec3,
    pub takeoff: Vec3,
    pub tgt: Vec3,
    pub v_req: f32,
    /// Air-curl gain. `0` means a straight speed jump (no `air_correct` curl), not a missing value.
    pub gain: f32,
    /// A chained jump takes off from the ledge itself and needs a prior jump to deliver `v_req` —
    /// it has no runway of its own, so flying one from a standing start will fail by design.
    #[serde(default)]
    pub chained: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct Cert {
    pub v_req: f32,
    pub gain: f32,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ProbeGain {
    pub gain: f32,
    pub land: Vec3,
    pub miss_xy: f32,
    pub miss_z: f32,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ProbeResp {
    pub v_deliver: f32,
    pub certified: Option<Cert>,
    pub gains: Vec<ProbeGain>,
}

/// A curl-jump search result. When `found` is false only `chord` is meaningful.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CurlResp {
    pub found: bool,
    pub chord: f32,
    pub v0: f32,
    pub psi0: f32,
    pub gain: f32,
    pub miss_xy: f32,
    pub land: Vec3,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PlanLinkResp {
    pub link: u32,
    pub from_cell: u32,
    pub to_cell: u32,
    pub from: Vec3,
    pub tgt: Vec3,
    pub takeoff: Vec3,
    pub v_req: f32,
    pub airtime: f32,
    pub cost: f32,
}

/// A planted standing cell: its new id and the origin it was indexed at.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct PlanCellResp {
    pub cell: u32,
    /// Where it actually landed after the floor snap — not the `pos` that was asked for.
    pub origin: Vec3,
    /// Walk/step links wired to neighbours at the same height, both ways. `0` is normal for a shelf
    /// that has nothing at its own level; the way off such a cell is a `PlanDrop`.
    pub links_created: u32,
}

/// A planted `Drop`: the new link plus the cells it actually resolved to, so the caller can verify it
/// attached to the shelf it meant and not to a floor underneath it.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct PlanDropResp {
    pub link: u32,
    pub from_cell: u32,
    pub to_cell: u32,
    pub from: Vec3,
    pub tgt: Vec3,
    pub cost: f32,
}

// ---------------------------------------------------------------------------------------------------
// Events (game -> MCP, async)
// ---------------------------------------------------------------------------------------------------

/// A puppet order's lifecycle event, emitted as the order plays out over frames.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub enum Event {
    /// A `Goto` reached its target.
    Arrived {
        bot: u32,
        t: f32,
        origin: Vec3,
        target: Vec3,
        dist: f32,
        traj: Vec<TrajRow>,
    },
    /// A `Goto` stalled (no progress) — the source is (currently) inaccessible.
    GotoStall {
        bot: u32,
        t: f32,
        origin: Vec3,
        target: Vec3,
        dist: f32,
        best: f32,
        secs: f32,
        traj: Vec<TrajRow>,
    },
    /// A rocket-jump attempt finished (any terminal outcome).
    RjResult(Box<RjResult>),
    /// A fly-link attempt finished (landed, timed out, …).
    FlyResult(FlyResult),
    /// One server frame of authoritative player movement, while `rtx_telemetry` is on.
    ///
    /// This is the movement lab's input: engine-truth ground flag, the full 3-D velocity and
    /// the ground entity, at the rate the server actually runs — none of which a 15 Hz
    /// `status` poll can reconstruct. Emitted even with zero players: the empty event is the
    /// consumer's heartbeat, proof this build supports telemetry, so a tool started before
    /// the human connects does not fall back to polling.
    Pmove(PmoveEvent),
    /// A steering watchdog fired: the bot noticed it is not getting anywhere on its current leg.
    ///
    /// One event per firing, from every watchdog in the bot's steering core (displacement,
    /// route progress, the speed-jump run-up, the air commitment, the curl prestrafe deficit).
    /// Everything here is a copy of what the watchdog already had in hand, so a consumer can
    /// attribute a stall to a map spot, a route leg and a link kind without a follow-up query.
    BotStall {
        bot: u32,
        t: f32,
        /// Which watchdog fired: `displacement`, `progress`, `speedjump_stall`,
        /// `air_commit_off`, `air_commit_timeout`, `prestrafe_deficit`.
        reason: String,
        origin: Vec3,
        /// The nav cell the bot stands on, and the one it is routing toward.
        cell: u32,
        goal_cell: u32,
        goal_dist: f32,
        /// The route leg (link index) in force when the watchdog fired; `u32::MAX` when off-route.
        link: u32,
        /// That leg's `LinkKind`, as its `Debug` name; empty when off-route.
        kind: String,
        speed: f32,
        /// The route as it stood *before* the watchdog cleared it, and the bot's leg within it.
        route_len: u32,
        route_pos: u32,
        /// What the watchdog did about it: `force_jump` or `penalize+repath`.
        action: String,
    },
    /// One second of life from one `rtx-client` seat.
    ///
    /// A seat that connected and then went nowhere is indistinguishable from a working one in
    /// every other line a client prints. This is the periodic proof of the opposite: how far it
    /// has actually travelled, how the link is behaving, how stale its last snapshot is, and
    /// whether it is far enough along to be playing at all.
    SeatHeartbeat {
        /// The name the seat connected under — the same string the server's scoreboard shows.
        seat: String,
        t: f32,
        /// Distance covered since the seat connected. Flat across heartbeats = a stuck seat.
        travelled: f32,
        rtt_ms: f32,
        /// Frames the server withheld to stay inside our rate, cumulative.
        chokes: u32,
        /// Seconds since this connection's entity snapshot last advanced.
        snapshot_age: f32,
        /// How far through connecting the seat is (`Active` once it is playing).
        signon: String,
        /// Whether the navmesh finished building. Without it the brain does nothing at all, so a
        /// seat that stands still with `nav_ready: false` is waiting, not broken.
        nav_ready: bool,
        alive: bool,
    },
    /// One bot's planning decision for one tick — see [`PlanTick`].
    ///
    /// Boxed because it is by far the largest payload here and every other variant would otherwise
    /// pay its size on the stack.
    PlanTick(Box<PlanTick>),
    /// Which graph the `PlanTick` stream is measured against — see [`PlanContract`].
    PlanContract(PlanContract),
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PmoveEvent {
    pub t: f32,
    pub players: Vec<PmovePlayer>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PmovePlayer {
    pub ent: u32,
    pub origin: Vec3,
    pub vel: Vec3,
    pub on_ground: bool,
    /// The entity being stood on, or -1 for none/invalid.
    pub ground_ent: i32,
}

/// The contract name and version every [`PlanTick`] is written against.
pub const PLAN_SCHEMA: &str = "qw-nav-graph/1";

/// Sentinel for "no cell / no link" in a [`PlanTick`].
///
/// Plan telemetry is the *row* layer, and rows carry sentinels, never the string `unknown` — that
/// belongs to the verdict layer downstream (attribution, clustering, action classes). The two mean
/// different things and merging them loses a fact: `link == PLAN_NONE` says the bot was demonstrably
/// off-route, where `unknown` in a verdict says nobody could tell.
pub const PLAN_NONE: u32 = u32::MAX;

/// Sentinel for "no planned speed band" in [`PlanTick::band`].
pub const PLAN_NO_BAND: u8 = u8::MAX;

/// Sentinel for a float the engine did not measure this tick (an unprobed lip, an uncapped weave).
pub const PLAN_UNSET: f32 = -1.0;

/// What the planner decided for one bot on one tick, and the controller state it decided it in.
///
/// The stall stream ([`Event::BotStall`]) says a bot noticed it was going nowhere; this says what it
/// was *trying* to do every tick, whether or not anything went wrong. That difference is the point:
/// a failure that never trips a watchdog — a route silently re-priced onto a longer way, a jump the
/// bot declined to attempt — leaves no trace at all in the stall stream.
///
/// Emitted only with both `rtx_telemetry` and `rtx_plan_telemetry` set; see `rtx-game`'s `control`.
///
/// **No field is ever absent or null.** Missing values are the sentinels above, so a consumer never
/// has to distinguish "key not present" from "value not known", and an old dataset run through an
/// adapter stays byte-comparable with a fresh one.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PlanTick {
    // --- identity and joining ---------------------------------------------------------------
    /// [`PLAN_SCHEMA`] — pinned on every row so a line is self-describing without a side channel.
    pub schema: String,
    /// Which navmesh instance these numbers mean anything against; expanded by [`PlanContract`].
    /// Two rows with different stamps must never be compared.
    pub graph_stamp: u64,
    /// Engine client number (`1..=maxclients`), the same index the rest of the harness stamps.
    pub bot: u32,
    /// Server time. The join key against the harness row, alongside `bot`: both are read from the
    /// same `game.time()` in the same `frame_end`, so they match exactly rather than approximately.
    pub t: f32,
    /// Per-bot monotone counter. Events are droppable under backlog (by design — replies are not),
    /// so a gap here is the only way a consumer can tell "the planner did nothing" from "the row
    /// never made it". A run with gaps must be reported as such, not read as an absence of events.
    pub seq: u32,

    // --- the planner's decision -------------------------------------------------------------
    /// The cell the bot resolved itself to this planning frame ([`PLAN_NONE`] if none).
    pub cell: u32,
    /// The cell it is routing toward ([`PLAN_NONE`] if none).
    pub goal_cell: u32,
    pub route_len: u32,
    pub route_pos: u32,
    /// **The active leg** — the link index the bot is steering along right now ([`PLAN_NONE`] when
    /// off-route or arrived). This is the decision the whole event exists to record.
    pub link: u32,
    /// That leg's `LinkKind` as its `Debug` name; empty when off-route.
    pub kind: String,
    /// The active leg's source and target cells ([`PLAN_NONE`] when off-route). Carried so an
    /// attribution pass never has to re-open the graph to resolve a link index.
    pub link_from: u32,
    pub link_to: u32,
    /// The banded planner's planned *entry* speed band for this leg ([`PLAN_NO_BAND`] if none).
    pub band: u8,
    /// A repath ran this tick.
    pub replanned: bool,
    /// The goal the last repath was handed, and the cell A\* actually searched to after
    /// reachability redirection and LOD truncation ([`PLAN_NONE`] if none).
    pub route_goal: u32,
    pub route_target: u32,
    /// The banded planner's total cost for the committed route, in seconds ([`PLAN_UNSET`] when the
    /// route came from an unbanded search or none was committed).
    pub plan_cost: f32,
    /// What finishing the current plan costs from where the bot actually is, in the seconds A\*
    /// minimises.
    pub remaining_cost: f32,
    /// Why the last repath produced nothing: empty when it succeeded, else `no_path`, `priced_out`
    /// or `unreachable`.
    ///
    /// This is the field that separates a *structurally missing link* from an *execution failure* —
    /// the distinction the IN-ring oracle turns on. A bot failing repeatedly with `plan_fail` empty
    /// had a route and could not fly it; one with `no_path` was never offered a way at all, and no
    /// amount of steering work would have helped.
    pub plan_fail: String,

    // --- what the active leg cost, term by term ---------------------------------------------
    /// The leg's static cost, and each dynamic term A\* charged on top of it this tick, in seconds.
    ///
    /// A sum cannot be acted on: 100000.4s is a shut gate plus jitter, or an unfit rocket jump plus
    /// a failed-link strike, and those want opposite fixes. Split, the reason is readable directly.
    /// New terms may be added as further `p_*` fields — a consumer that meets one it does not know
    /// must ignore it, so instrumenting a fork's extra pricing is an extension, not a break.
    pub p_base: f32,
    pub p_gate: f32,
    pub p_penalty: f32,
    pub p_jitter: f32,
    pub p_rj: f32,
    pub p_water: f32,
    pub p_hazard: f32,
    pub p_chained: f32,
    /// What A\* actually paid: the sum of the `p_*` terms present on this row.
    pub p_total: f32,

    // --- speed, recorded but never a cause ---------------------------------------------------
    /// Required takeoff speed for a committed speed jump (`0` = not a speed jump), and the speed
    /// actually carried.
    ///
    /// **Logged, never a causal label.** The v_req-gap reading of V296 was investigated and
    /// falsified; these fields exist so that hypothesis can be re-tested and re-refuted, not so a
    /// consumer can classify a failure from them. Cause comes from the controller state below.
    pub v_req: f32,
    /// Horizontal speed.
    pub speed: f32,
    pub vz: f32,
    /// This speed jump has no run-up of its own and depends on carried entry speed.
    pub chained: bool,
    /// Air-curl gain (`0` = a straight speed jump) and the run-up weave half-angle in degrees
    /// ([`PLAN_UNSET`] when uncapped).
    pub curl_gain: f32,
    pub weave_cap: f32,

    // --- controller state: the V296 oracle ----------------------------------------------------
    /// `FL_ONGROUND` this tick.
    pub on_ground: bool,
    /// The hop controller's phase: `Off`, `Prestrafe`, `Hop` or `Zigzag`.
    pub phase: String,
    /// Straight corridor remaining — on a speed jump, the run-up left to the takeoff.
    pub runway: f32,
    /// The active speed jump's source cell — where the takeoff is measured from ([`PLAN_NONE`] when
    /// the leg is not a speed jump).
    pub takeoff_cell: u32,
    /// Speed carried toward the steering waypoint, and the distance to it.
    pub runup: f32,
    pub wp: f32,
    /// Floor left ahead before it falls away ([`PLAN_UNSET`] when not probed). What the takeoff is
    /// really racing.
    pub lip: f32,
    /// The run-up gate's verdict, and whether a speed jump's leap is held for its envelope.
    pub takeoff_ok: bool,
    pub sj_held: bool,
    /// At the edge but too slow to clear the gap: keep building, do not leap.
    pub hold_jump: bool,
    /// Whether `+jump` is set in the usercmd **actually sent** this tick.
    ///
    /// Read after the whole button chain, including the late clears — an intention that never
    /// reached the engine is exactly the jump-cmd-on-ground race this telemetry exists to catch, so
    /// recording the wish instead of the command would hide the bug it is here to expose.
    pub jump_cmd: bool,
    /// Vertical speed on the first airborne tick of the current hop (`0` before any takeoff). The
    /// leap's actual outcome, as opposed to the gate's prediction of it.
    pub first_air_vz: f32,
    /// Hops taken in this engagement, and why the last one ended (`veto`, `runway`, `leg`; empty if
    /// still engaged or never engaged).
    pub hops: u32,
    pub off_reason: String,
}

/// Which graph a run of [`PlanTick`]s was measured against.
///
/// Sent once when plan telemetry is switched on and again whenever the navmesh is rebuilt, so a
/// capture stands on its own: [`PlanTick::graph_stamp`] is a bare integer, and this is what it means.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PlanContract {
    /// [`PLAN_SCHEMA`].
    pub schema: String,
    /// The stamp every [`PlanTick`] in this run carries.
    pub graph_stamp: u64,
    pub map: String,
    /// The graph's shape — the same counts the harness records, so engine and harness can be checked
    /// against each other on the same graph.
    pub cells: u32,
    pub links: u32,
    pub rj_links: u32,
    /// The engine build the numbers came from (its package version — the engine carries no git
    /// commit; run identity is the harness's to record).
    pub build: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct RjSolved {
    pub pitch: f32,
    pub yaw: f32,
    pub delay: f32,
    pub airtime: f32,
    pub self_damage: f32,
    pub v0: Vec3,
    pub blast: Vec3,
    pub pos_blast: Vec3,
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct RjBias {
    pub delay: f32,
    pub pitch: f32,
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct RjPress {
    pub t: f32,
    pub origin: Vec3,
    pub view: [f32; 2],
    pub aim_err: f32,
    pub stance_off_xy: f32,
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct RjFire {
    pub t: f32,
    pub delay: f32,
    pub origin: Vec3,
    pub view: [f32; 2],
    pub pitch_err: f32,
    pub yaw_err: f32,
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct RjLand {
    pub t: f32,
    pub origin: Vec3,
    pub miss_xy: f32,
    pub miss_z: f32,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RjResult {
    pub bot: u32,
    pub link: u32,
    /// Terminal outcome label (`landed`, `landed_off`, `overran`, `stance_timeout`, …).
    pub outcome: String,
    pub src: Vec3,
    pub tgt: Vec3,
    pub solved: RjSolved,
    pub bias: RjBias,
    pub press: Option<RjPress>,
    pub fire: Option<RjFire>,
    pub land: Option<RjLand>,
    pub traj: Vec<TrajRow>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct FlyResult {
    pub bot: u32,
    pub link: u32,
    pub on_target: bool,
    pub timeout: bool,
    pub land: Vec3,
    pub target: Vec3,
    pub miss_xy: f32,
    pub miss_z: f32,
    pub takeoff_speed: f32,
    pub peak: f32,
    pub traj: Vec<TrajRow>,
}

#[cfg(test)]
mod tests {

    #[test]
    fn plan_cell_cmd_roundtrips() {
        let cmd = Cmd::PlanCell {
            pos: [-880.0, -42.0, 88.0],
        };
        let bytes = rmp_serde::to_vec_named(&cmd).unwrap();
        assert_eq!(rmp_serde::from_slice::<Cmd>(&bytes).unwrap(), cmd);
    }

    #[test]
    fn plan_drop_cmd_roundtrips() {
        let cmd = Cmd::PlanDrop {
            from: [-880.0, -42.0, 88.0],
            to: [-864.0, -32.0, -16.0],
        };
        let bytes = rmp_serde::to_vec_named(&cmd).unwrap();
        assert_eq!(rmp_serde::from_slice::<Cmd>(&bytes).unwrap(), cmd);
    }

    #[test]
    fn plan_cell_and_drop_resps_roundtrip() {
        let cell = Resp::PlanCell(PlanCellResp {
            cell: 4602,
            origin: [-880.0, -42.0, 88.0],
            links_created: 0,
        });
        let bytes = rmp_serde::to_vec_named(&cell).unwrap();
        assert_eq!(rmp_serde::from_slice::<Resp>(&bytes).unwrap(), cell);

        let drop = Resp::PlanDrop(PlanDropResp {
            link: 37600,
            from_cell: 4602,
            to_cell: 109,
            from: [-880.0, -42.0, 88.0],
            tgt: [-864.0, -32.0, -16.0],
            cost: 0.62,
        });
        let bytes = rmp_serde::to_vec_named(&drop).unwrap();
        assert_eq!(rmp_serde::from_slice::<Resp>(&bytes).unwrap(), drop);
    }

    use super::*;

    fn roundtrip<T>(v: &T) -> T
    where
        T: Serialize + for<'de> Deserialize<'de>,
    {
        let frame = to_frame(v);
        // The length prefix matches the payload, and the payload decodes back to an equal value.
        let n = u32::from_le_bytes(frame[..4].try_into().unwrap()) as usize;
        assert_eq!(n, frame.len() - 4);
        decode(&frame[4..]).unwrap()
    }

    #[test]
    fn pmove_event_roundtrips() {
        let msg = Msg::Event(Event::Pmove(PmoveEvent {
            t: 12.5,
            players: vec![PmovePlayer {
                ent: 1,
                origin: [1.0, 2.0, 3.0],
                vel: [191.0, -197.0, 62.0],
                on_ground: false,
                ground_ent: -1,
            }],
        }));
        assert_eq!(roundtrip(&msg), msg);
    }

    #[test]
    fn empty_pmove_event_roundtrips() {
        // The zero-player heartbeat is load-bearing: a consumer started before anyone
        // connects uses it to learn the build supports telemetry at all.
        let msg = Msg::Event(Event::Pmove(PmoveEvent {
            t: 12.5,
            players: Vec::new(),
        }));
        assert_eq!(roundtrip(&msg), msg);
    }

    #[test]
    fn bot_stall_event_roundtrips() {
        let msg = Msg::Event(Event::BotStall {
            bot: 3,
            t: 91.25,
            reason: "displacement".to_string(),
            origin: [1874.5, -32.6, -127.0],
            cell: 412,
            goal_cell: 77,
            goal_dist: 638.5,
            link: 1901,
            kind: "SpeedJump".to_string(),
            speed: 118.0,
            route_len: 9,
            route_pos: 4,
            action: "penalize+repath".to_string(),
        });
        assert_eq!(roundtrip(&msg), msg);
    }

    #[test]
    fn off_route_bot_stall_roundtrips() {
        // Off-route the watchdog has no leg to name: `u32::MAX` link and an empty kind must
        // survive the wire, so a consumer can tell "no leg" from "leg 0".
        let msg = Msg::Event(Event::BotStall {
            bot: 1,
            t: 4.0,
            reason: "progress".to_string(),
            origin: [0.0, 0.0, 0.0],
            cell: 0,
            goal_cell: 0,
            goal_dist: 0.0,
            link: u32::MAX,
            kind: String::new(),
            speed: 0.0,
            route_len: 0,
            route_pos: 0,
            action: "penalize+repath".to_string(),
        });
        assert_eq!(roundtrip(&msg), msg);
    }

    #[test]
    fn seat_heartbeat_roundtrips() {
        let msg = Msg::Event(Event::SeatHeartbeat {
            seat: "bot\u{2022}rex".to_string(),
            t: 128.0,
            travelled: 14203.5,
            rtt_ms: 12.5,
            chokes: 3,
            snapshot_age: 0.013,
            signon: "Active".to_string(),
            nav_ready: true,
            alive: true,
        });
        assert_eq!(roundtrip(&msg), msg);
    }

    #[test]
    fn status_players_roundtrip() {
        let players = vec![PlayerStatus {
            ent: 1,
            name: "Xerial".to_string(),
            origin: [1874.5, -32.6, -127.0],
            health: 100.0,
            on_ground: false,
            alive: true,
            speed: 274.9,
        }];
        assert_eq!(roundtrip(&players), players);
    }

    #[test]
    fn request_roundtrips() {
        let r = Request {
            id: 7,
            cmd: Cmd::Goto {
                bot: 2,
                pos: [1.0, 2.0, 3.0],
            },
        };
        assert_eq!(roundtrip(&r), r);
    }

    #[test]
    fn reply_ok_and_err_roundtrip() {
        let ok = Msg::Reply {
            id: 3,
            result: Ok(Resp::Ack { bot: 2 }),
        };
        assert_eq!(roundtrip(&ok), ok);
        let err = Msg::Reply {
            id: 4,
            result: Err("no such bot 9".to_string()),
        };
        assert_eq!(roundtrip(&err), err);
    }

    #[test]
    fn audit_reply_roundtrips_frames() {
        let mut f = AuditFrame::default();
        f.speed = 812.0;
        f.bhop = rtx_auditlog::Bhop::Hop;
        let msg = Msg::Reply {
            id: 1,
            result: Ok(Resp::Audit(AuditResp {
                bot: 2,
                count: 1,
                frames: vec![f],
            })),
        };
        let back = roundtrip(&msg);
        assert_eq!(back, msg);
    }

    #[test]
    fn bsp_reply_roundtrips_bytes() {
        // The BSP payload rides as raw `serde_bytes` — verify a non-UTF-8 blob survives intact.
        let msg = Msg::Reply {
            id: 8,
            result: Ok(Resp::Bsp(Box::new(BspResp {
                map: "dm3".to_string(),
                bytes: vec![0x1d, 0x00, 0xff, 0x80, 0x01, 0x02, 0x03],
            }))),
        };
        assert_eq!(roundtrip(&msg), msg);
    }

    #[test]
    fn event_roundtrips() {
        let ev = Msg::Event(Event::FlyResult(FlyResult {
            bot: 2,
            link: 5,
            on_target: true,
            timeout: false,
            land: [10.0, 20.0, 30.0],
            target: [11.0, 21.0, 31.0],
            miss_xy: 1.4,
            miss_z: 1.0,
            takeoff_speed: 500.0,
            peak: 812.0,
            traj: vec![[0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 2.0]],
        }));
        assert_eq!(roundtrip(&ev), ev);
    }

    #[test]
    fn two_frames_read_back_in_order() {
        let a = to_frame(&Request {
            id: 1,
            cmd: Cmd::Status,
        });
        let b = to_frame(&Request {
            id: 2,
            cmd: Cmd::Audit { bot: 2, lines: 50 },
        });
        let mut stream: Vec<u8> = Vec::new();
        stream.extend_from_slice(&a);
        stream.extend_from_slice(&b);
        let mut cur = std::io::Cursor::new(stream);
        let f1 = read_frame(&mut cur).unwrap().unwrap();
        let f2 = read_frame(&mut cur).unwrap().unwrap();
        assert!(read_frame(&mut cur).unwrap().is_none());
        assert_eq!(decode::<Request>(&f1).unwrap().id, 1);
        assert_eq!(decode::<Request>(&f2).unwrap().cmd, Cmd::Audit { bot: 2, lines: 50 });
    }

    /// A `PlanTick` survives the wire exactly as sent.
    ///
    /// Plan rows are the record later analysis reasons from, and they go out as msgpack behind a
    /// length prefix like everything else. A field that silently failed to round-trip would not show
    /// up as an error anywhere — it would show up as a wrong conclusion about a bot, months later.
    #[test]
    fn plan_tick_round_trips() {
        let tick = PlanTick {
            schema: PLAN_SCHEMA.to_string(),
            graph_stamp: 0xdead_beef_1234_5678,
            bot: 3,
            t: 12.345,
            seq: 918,
            cell: 503,
            goal_cell: 194,
            route_len: 9,
            route_pos: 2,
            link: 1373,
            kind: "SpeedJump".to_string(),
            link_from: 56,
            link_to: 99,
            band: 2,
            replanned: true,
            route_goal: 194,
            route_target: 188,
            plan_cost: 4.25,
            remaining_cost: 3.5,
            plan_fail: String::new(),
            p_base: 1.5,
            p_gate: 0.0,
            p_penalty: 2.5,
            p_jitter: 0.125,
            p_rj: 0.0,
            p_water: 0.37,
            p_hazard: 0.0,
            p_chained: 0.0,
            p_total: 4.495,
            v_req: 320.0,
            speed: 287.5,
            vz: -12.0,
            chained: true,
            curl_gain: 0.35,
            weave_cap: -1.0,
            on_ground: false,
            phase: "Hop".to_string(),
            runway: 96.0,
            takeoff_cell: 56,
            runup: 301.0,
            wp: 48.0,
            lip: -1.0,
            takeoff_ok: true,
            sj_held: true,
            hold_jump: false,
            jump_cmd: true,
            first_air_vz: 270.0,
            hops: 4,
            off_reason: String::new(),
        };
        let frame = to_frame(&Msg::Event(Event::PlanTick(Box::new(tick.clone()))));
        let mut cursor = &frame[..];
        let body = read_frame(&mut cursor).expect("frame reads back").expect("a whole frame was there");
        match decode::<Msg>(&body).expect("decodes") {
            Msg::Event(Event::PlanTick(got)) => assert_eq!(*got, tick),
            other => panic!("wrong message back: {other:?}"),
        }
    }

    /// The sentinels are the row layer's only way of saying "nothing here", so they must survive the
    /// wire as themselves — and stay distinct from the zeros that mean a real measured zero.
    #[test]
    fn plan_sentinels_are_distinct_from_zero() {
        assert_eq!(PLAN_NONE, u32::MAX);
        assert_eq!(PLAN_NO_BAND, u8::MAX);
        assert_eq!(PLAN_UNSET, -1.0);
        assert_ne!(PLAN_NONE, 0, "a missing link must not read as link 0");
        assert_ne!(PLAN_NO_BAND, 0, "a missing band must not read as band 0");
        assert!(PLAN_UNSET < 0.0, "an unprobed float must not read as a measured 0.0");
    }

    /// The contract round-trips too — a capture whose stamp cannot be expanded is a capture nobody
    /// can safely compare against another.
    #[test]
    fn plan_contract_round_trips() {
        let c = PlanContract {
            schema: PLAN_SCHEMA.to_string(),
            graph_stamp: 0xdead_beef_1234_5678,
            map: "dm3".to_string(),
            cells: 4581,
            links: 19822,
            rj_links: 311,
            build: "0.1.0".to_string(),
        };
        let frame = to_frame(&Msg::Event(Event::PlanContract(c.clone())));
        let mut cursor = &frame[..];
        let body = read_frame(&mut cursor).expect("frame reads back").expect("a whole frame was there");
        match decode::<Msg>(&body).expect("decodes") {
            Msg::Event(Event::PlanContract(got)) => assert_eq!(got, c),
            other => panic!("wrong message back: {other:?}"),
        }
    }
}
