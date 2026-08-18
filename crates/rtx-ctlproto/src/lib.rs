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
    ///
    /// When `from` and `to` are set, dump a fresh A* search on the live graph instead of the
    /// bot's live route (`bot` may be 0). `mask_links` are treated as absent for that one
    /// search — the next-best path after the chosen route is masked. Omitted fields keep the
    /// historical `{ "Route": { "bot": N } }` frame working.
    Route {
        bot: u32,
        #[serde(default)]
        from: Option<u32>,
        #[serde(default)]
        to: Option<u32>,
        #[serde(default)]
        mask_links: Vec<u32>,
    },
    /// `fixa` — apply / dry-run / undo a stamp-pinned recipe through `apply_one` (not a second
    /// planter). `from`/`to` are optional A* endpoints for the receipt dump.
    Fixa {
        recipe: String,
        mode: String,
        #[serde(default)]
        from: Option<u32>,
        #[serde(default)]
        to: Option<u32>,
        /// Contents (or first token) of `~/lab/.rig-lock`. Required for apply/undo in the
        /// engine; dry-run may be empty. Serde-default keeps old frames decoding.
        #[serde(default)]
        lock_token: String,
    },
    /// Apply a whole composed recipe **atomically**: pin, ops in order with per-step identity
    /// checks, final identity, all or nothing.
    ///
    /// Replaces driving `fixa` once per op from the outside. That protocol needed the runner and the
    /// engine to agree about the graph between every step, and five separate interface bugs came out
    /// of the places where they could disagree — the last one being the runner's idea of the undo
    /// chain's top against the engine's actual stack top (DOM MONTERING-V296RAM-2 and the montering-5
    /// receipt). A composed recipe is one decision; making it one round trip removes the whole class
    /// rather than fixing its fifth instance.
    ///
    /// Every op runs on a private clone. Nothing is published until the last identity has been
    /// verified, so a refused komponat does not roll an intermediate state back — the intermediate
    /// state never existed anywhere the rig could see it.
    Komponat {
        /// The manifest's `recept_id`, echoed into the receipt.
        recept_id: String,
        /// The identity the live graph must have before anything runs.
        base: GraphIdent,
        steps: Vec<KomponatStep>,
        /// The identity the whole thing must produce.
        expect_final: GraphIdent,
        /// Rig-lock token — same gate as every other mutating verb.
        #[serde(default)]
        lock_token: String,
    },
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
        /// Rig-lock token, same gate as [`Cmd::Fixa`]'s `apply`/`undo`.
        ///
        /// A plant mutates the live graph exactly like a recipe apply does, and until DOM
        /// MONTERING-V296RAM-2 it did so without the lock: the composed recipe's first op could
        /// commit while its second was refused, and undoing the first was then blocked behind the
        /// very check that had just fired. That is a gate-coverage defect, not a lock defect —
        /// every mutating verb belongs behind the same gate.
        ///
        /// `default` keeps the wire backward compatible (an old sender still deserializes); the
        /// empty token it produces is then refused by the gate, which is the point.
        #[serde(default)]
        lock_token: String,
    },
    /// Hand-plant a standing cell at `pos` — a walkable surface the column carve's XY pitch cannot
    /// sample (see `NavGraph::plant_cell`). Inert on its own: nothing routes into it.
    PlanCell {
        pos: Vec3,
        /// Rig-lock token — see [`Cmd::PlanLink`].
        #[serde(default)]
        lock_token: String,
    },
    /// Hand-plant a `Drop` link from the cell nearest `from` to the cell nearest `to`, so a bot standing
    /// on a planted shelf has a way off it.
    PlanDrop {
        from: Vec3,
        to: Vec3,
        /// Rig-lock token — see [`Cmd::PlanLink`].
        #[serde(default)]
        lock_token: String,
    },
}

// ---------------------------------------------------------------------------------------------------
// Komponat: one composed recipe, applied atomically
// ---------------------------------------------------------------------------------------------------

/// A graph identity at both levels, as the sealed manifest writes it.
///
/// `graph_stamp` is a decimal string because the nivå-1 FNV is a u64 and can pass 2^53 — the
/// contract (`WORK_LOGS/graphstamp-kontrakt.md` §4) says decimal string everywhere outside the
/// engine's own arithmetic.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct GraphIdent {
    pub cells: u32,
    pub links: u32,
    pub rj_links: u32,
    /// Nivå-1 FNV-1a-64 as a decimal string.
    pub graph_stamp: String,
    /// Nivå-2 SHA-256 hex over the canonical inventory — the **params-free** one.
    ///
    /// The manifest carries two: params-bearing (the recipe's own derivation, which hashes
    /// `carried`/`v_req`/`gain`) and params-free. Only the params-free one describes a graph the
    /// engine can have, because `carried` is fixture metadata that never reaches `Cmd::PlanLink`.
    /// Sending the wrong one makes every step mismatch.
    pub graph_content_hash: String,
}

/// One mutation in a composed recipe.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub enum KomponatOp {
    /// A registered recipe from the engine's own table, applied by name.
    ///
    /// The payload deliberately is *not* on the wire: `fixa` is the only apply path and the table is
    /// the only planter. A composed recipe naming `ram-rail-v2` gets the sealed table entry, not
    /// whatever cells a caller felt like sending.
    Recipe { name: String },
    /// A hand-planted speed jump — the V296 class, which has no `ShelfPatch` to name.
    PlanLink {
        from: Vec3,
        takeoff: Vec3,
        tgt: Vec3,
        v_req: f32,
        #[serde(default)]
        gain: Option<f32>,
    },
    /// Remove existing links by id, for a diagnosis that has no table entry.
    ///
    /// The table stays the only *planter*: this op cannot create anything. It exists because a
    /// composed recipe may need to close an edge the sealed table has no recipe for, and the
    /// alternative — adding a `ShelfPatch` per diagnosis — would mean a rebuild and a new binary
    /// for every experiment.
    ///
    /// A raw link id is meaningless across graphs, so each spec carries its anchor and the engine
    /// refuses unless the live link matches `from`/`to`/`kind` exactly. That is the same gate
    /// `apply_one` applies to a recipe's `remove_links`, and it is what makes an id on the wire
    /// safe: the id says *where to look*, the anchor says *what must be there*.
    RemoveLinks { links: Vec<RemoveLinkSpec> },
}

/// One link a [`KomponatOp::RemoveLinks`] takes out, with the anchor that proves it is the right one.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct RemoveLinkSpec {
    /// Live link index in the graph the step's `expect_before` pins.
    pub id: u32,
    pub from: u32,
    pub to: u32,
    /// Link kind token as the dump writes it (`walk`, `jump`, `drop`, …), lowercase.
    pub kind: String,
}

/// One step of a composed recipe, with the identities the manifest derived for it.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct KomponatStep {
    /// The manifest's name for this op, echoed into the receipt.
    pub name: String,
    pub op: KomponatOp,
    /// What the graph must be *before* this step. Replaces the recipe's own base pin: a chained op
    /// runs against an intermediate graph, so its standalone pin cannot hold and the composed
    /// recipe's own derivation is the pin instead.
    pub expect_before: GraphIdent,
    /// What the graph must be *after* it.
    pub expect_after: GraphIdent,
}

/// One step's observed outcome.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct KomponatStepResult {
    pub name: String,
    /// `"ok"`, or `"refused"` for the step that stopped the transaction. Steps after it are absent —
    /// they never ran.
    pub outcome: String,
    pub reason: Option<String>,
    /// What the graph actually became. `None` when the step was refused before it mutated anything.
    pub observed: Option<GraphIdent>,
    /// Link id, for a `PlanLink` step.
    pub link: Option<u32>,
}

/// The receipt for a composed recipe.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct KomponatResp {
    pub recept_id: String,
    /// `"applied"` or `"refused"`. There is no third outcome: a refused komponat leaves the live
    /// graph bit-for-bit as it was.
    pub outcome: String,
    pub reason: Option<String>,
    /// The identity the live graph had when the transaction started — and still has, if it refused.
    pub base: GraphIdent,
    /// The live graph's identity now.
    pub observed_final: GraphIdent,
    pub steps: Vec<KomponatStepResult>,
    /// What `fixa --undo` takes to roll the whole komponat back in one move.
    pub undo_name: String,
    pub audit: String,
}

// ---------------------------------------------------------------------------------------------------
// Rig-lock: the token contract shared by everything that mutates the graph
// ---------------------------------------------------------------------------------------------------

/// The campaign token a rig-lock body declares on its `token=` line, if it has one.
///
/// Lives here, with the rest of the control-channel contract, because the lock file has more than
/// one reader: the engine gates every mutating verb on it, the MCP bridge has to pick a token to
/// send, and the deploy runner parses the same eight fields in Python. When those readers disagreed
/// the rig half-mounted — the form that satisfied the runner (eight `key=value` lines) left the
/// engine looking at `owner=fable` as the first field and refusing everything (DOM
/// MONTERING-V296RAM-2). One definition, mirrored deliberately, instead of three that drift.
///
/// `None` when there is no `token=` line, when its value is empty, or when two lines declare
/// *different* tokens — a file that contradicts itself does not say what it means.
pub fn rig_lock_declared_token(body: &str) -> Option<&str> {
    let mut found: Option<&str> = None;
    for line in body.lines() {
        if let Some(v) = line.trim().strip_prefix("token=") {
            let v = v.trim();
            match found {
                Some(prev) if prev != v => return None,
                _ => found = Some(v),
            }
        }
    }
    found.filter(|v| !v.is_empty())
}

/// Whether `token` opens a rig-lock whose file body is `body`.
///
/// Accepted forms, in order of authority:
///
/// 1. **the declared `token=`** — when the file names its token, that is the token and nothing else
///    is accepted. A lock that names itself must not also be openable by whatever sits first in it.
/// 2. **the whole trimmed body**, or **its first whitespace field** — the old single-line locks
///    (`fable 1`). Only consulted when there is no `token=` line.
///
/// An empty token never opens anything, and neither does an empty body.
///
/// A file that *tries* to declare a token but fails to — an empty value, or two lines disagreeing —
/// opens nothing at all. It does not fall back to rule 2: falling back would let `owner=fable` open
/// a lock whose token field is broken, which is the loose behaviour this whole contract exists to
/// remove.
pub fn rig_lock_accepts(body: &str, token: &str) -> bool {
    let (body, token) = (body.trim(), token.trim());
    if body.is_empty() || token.is_empty() {
        return false;
    }
    if body.lines().any(|l| l.trim().starts_with("token=")) {
        return rig_lock_declared_token(body) == Some(token);
    }
    token == body || token == body.split_whitespace().next().unwrap_or("")
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
    Fixa(FixaResp),
    /// A whole composed recipe's outcome — boxed because the receipt carries every step.
    Komponat(Box<KomponatResp>),
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
    /// Present when this reply is a from→to A* query (GAP 3). Absent on a live-bot dump.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub astar: Option<AstarDump>,
}

/// A* identity for a [`Cmd::Route`] from→to query: whether a path existed, its priced cost,
/// the endpoints, and which links were masked for the next-best rescan.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AstarDump {
    pub found: bool,
    pub cost: f32,
    pub start_cell: u32,
    pub goal_cell: u32,
    pub mask_links: Vec<u32>,
}

/// One A* dump on a `fixa` reply (cells + links + cost).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct FixaPath {
    pub found: bool,
    pub cost: f32,
    pub cells: Vec<u32>,
    pub links: Vec<u32>,
    pub mask_links: Vec<u32>,
}

/// Result of `Cmd::Fixa`. `outcome` is `dry_run_ok` / `applied` / `already_meshed` /
/// `undone` / `failed`. Stamps are decimal strings.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct FixaResp {
    pub recipe: String,
    pub mode: String,
    pub outcome: String,
    pub reason: Option<String>,
    pub map: String,
    pub cells: u32,
    pub links: u32,
    pub rj_links: u32,
    pub stamp: String,
    pub content_hash: String,
    pub stamp_before: Option<String>,
    pub stamp_after: Option<String>,
    pub astar_before: Option<FixaPath>,
    pub astar_after: Option<FixaPath>,
    pub astar_next_best: Option<FixaPath>,
    pub audit: String,
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

    /// An old sender's frame has no `lock_token`. It must still decode — the field is additive —
    /// and it must decode to the empty token, which the engine's gate refuses. Wire compatibility
    /// and the gate are two different promises and both have to hold.
    #[test]
    fn plant_cmds_from_an_old_sender_still_decode_with_an_empty_token() {
        #[derive(serde::Serialize)]
        enum GammalCmd {
            PlanCell { pos: Vec3 },
            PlanDrop { from: Vec3, to: Vec3 },
            PlanLink { from: Vec3, takeoff: Vec3, tgt: Vec3, v_req: f32 },
        }
        let gamla = [
            rmp_serde::to_vec_named(&GammalCmd::PlanCell {
                pos: [-880.0, -42.0, 88.0],
            })
            .unwrap(),
            rmp_serde::to_vec_named(&GammalCmd::PlanDrop {
                from: [-880.0, -42.0, 88.0],
                to: [-864.0, -32.0, -16.0],
            })
            .unwrap(),
            rmp_serde::to_vec_named(&GammalCmd::PlanLink {
                from: [107.0, -582.0, 296.0],
                takeoff: [92.0, -588.0, 296.0],
                tgt: [138.1, -701.0, 328.0],
                v_req: 320.0,
            })
            .unwrap(),
        ];
        for bytes in gamla {
            let cmd: Cmd = rmp_serde::from_slice(&bytes).expect("gammal ram måste avkodas");
            let token = match &cmd {
                Cmd::PlanCell { lock_token, .. }
                | Cmd::PlanDrop { lock_token, .. }
                | Cmd::PlanLink { lock_token, .. } => lock_token.as_str(),
                other => panic!("fel variant: {other:?}"),
            };
            assert_eq!(token, "", "saknat fält blir tom token");
            assert!(!rig_lock_accepts("t20m-abc\ntoken=t20m-abc", token), "och grinden vägrar den");
        }
    }

    #[test]
    fn rig_lock_declared_token_wins_over_everything_else_in_the_file() {
        let campaign = "owner=fable\nunit=tbx-d1\ntoken=t20m-abc\nts=2026-08-17T08:16:02Z";
        assert!(rig_lock_accepts(campaign, "t20m-abc"));
        // Det var precis de här två som öppnade låset förr och stängde motorn nu.
        assert!(!rig_lock_accepts(campaign, "owner=fable"));
        assert!(!rig_lock_accepts(campaign, campaign));
    }

    #[test]
    fn rig_lock_bridge_and_legacy_forms_both_open() {
        let bridge = "t20m-abc\nowner=fable\ntoken=t20m-abc\nts=x";
        assert!(rig_lock_accepts(bridge, "t20m-abc"));
        assert!(!rig_lock_accepts(bridge, "owner=fable"));

        let legacy = "fable 1\n";
        assert!(rig_lock_accepts(legacy, "fable 1"), "hela kroppen");
        assert!(rig_lock_accepts(legacy, "fable"), "första fältet");
        assert!(!rig_lock_accepts(legacy, "1"));
    }

    #[test]
    fn rig_lock_that_contradicts_itself_opens_nothing() {
        let bad = "owner=fable\ntoken=a\ntoken=b";
        for t in ["a", "b", "owner=fable", bad] {
            assert!(!rig_lock_accepts(bad, t), "{t:?}");
        }
        // Ett tomt token=-värde är också ett misslyckat försök att deklarera, inte en frånvaro.
        assert!(!rig_lock_accepts("owner=fable\ntoken=", "owner=fable"));
    }

    #[test]
    fn rig_lock_empty_inputs_open_nothing() {
        assert!(!rig_lock_accepts("", "x"));
        assert!(!rig_lock_accepts("   \n", "x"));
        assert!(!rig_lock_accepts("fable 1", ""));
        assert!(!rig_lock_accepts("fable 1", "   "));
    }

    #[test]
    fn rig_lock_line_endings_match_str_lines() {
        // Rust str::lines() splits on \n / \r\n only. Lone \r is not a break.
        // Python splitlines() would give declared="a" / contradiction here.
        assert_eq!(rig_lock_declared_token("token=a\rb"), Some("a\rb"));
        assert_eq!(
            rig_lock_declared_token("token=a\rtoken=b"),
            Some("a\rtoken=b")
        );
        assert!(rig_lock_accepts("token=a\rb", "a\rb"));
        assert!(!rig_lock_accepts("token=a\rb", "a"));
        assert!(rig_lock_accepts("token=a\rtoken=b", "a\rtoken=b"));

        let crlf = "owner=fable\r\ntoken=t20m-abc\r\nts=x\r\n";
        assert_eq!(rig_lock_declared_token(crlf), Some("t20m-abc"));
        assert!(rig_lock_accepts(crlf, "t20m-abc"));
        assert!(!rig_lock_accepts(crlf, "owner=fable"));
        assert_eq!(rig_lock_declared_token("token=abc  \r\n"), Some("abc"));
    }

    #[test]
    fn plan_cell_cmd_roundtrips() {
        let cmd = Cmd::PlanCell {
            pos: [-880.0, -42.0, 88.0],
            lock_token: "t20m-0000".into(),
        };
        let bytes = rmp_serde::to_vec_named(&cmd).unwrap();
        assert_eq!(rmp_serde::from_slice::<Cmd>(&bytes).unwrap(), cmd);
    }

    #[test]
    fn plan_drop_cmd_roundtrips() {
        let cmd = Cmd::PlanDrop {
            from: [-880.0, -42.0, 88.0],
            to: [-864.0, -32.0, -16.0],
            lock_token: "t20m-0000".into(),
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

    /// Historical Route frames only carried `bot`. GAP 3 added from/to/mask_links with serde
    /// defaults so those frames must still decode.
    #[test]
    fn route_frame_without_query_fields_still_decodes() {
        #[derive(Serialize)]
        enum OldRoute {
            Route { bot: u32 },
        }
        let bytes = rmp_serde::to_vec_named(&OldRoute::Route { bot: 3 }).unwrap();
        assert_eq!(
            rmp_serde::from_slice::<Cmd>(&bytes).unwrap(),
            Cmd::Route {
                bot: 3,
                from: None,
                to: None,
                mask_links: vec![],
            }
        );
    }

    #[test]
    fn fixa_frame_without_lock_token_defaults_empty() {
        #[derive(Serialize)]
        enum OldFixa {
            Fixa { recipe: String, mode: String },
        }
        let bytes = rmp_serde::to_vec_named(&OldFixa::Fixa {
            recipe: "west-shelf".into(),
            mode: "apply".into(),
        })
        .unwrap();
        assert_eq!(
            rmp_serde::from_slice::<Cmd>(&bytes).unwrap(),
            Cmd::Fixa {
                recipe: "west-shelf".into(),
                mode: "apply".into(),
                from: None,
                to: None,
                lock_token: String::new(),
            }
        );
    }
}
