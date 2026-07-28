// SPDX-License-Identifier: AGPL-3.0-or-later

//! `rtx-demo-tool` — read and analyze QuakeWorld demos, in both containers.
//!
//! A **`.qwd`** is what a client records: a flat log of timestamped records holding its own
//! `dem_cmd` inputs and the `dem_read` packets it received. A **`.mvd`** is what a *server* records
//! — the whole game, every player, addressed blocks, delta timestamps, and no single point of view
//! (see [`mvd`], which is where all the container differences live).
//!
//! Either way the framing is the *only* thing this crate parses: a block's payload is a recorded
//! message stream, decoded by [`rtx_proto::svc`], the same codec the live client speaks. So the
//! split is clean — the containers are read here, the wire is read there.
//!
//! [`parse_demo`] picks the container by content and hands back [`Frame`]s: one normalised player
//! state per update, whichever format it came from. The [`analysis`] module turns those into
//! per-player motion [`Track`](analysis::Track)s and the things worth asking of them — speed
//! percentiles, the jumps, turn rates. The `qwd` binary is the CLI: `players` lists a demo's
//! roster, `dump` emits CSV, `analyze` prints a movement report.
//!
//! What the two formats can and cannot tell you differs, and [`Frame`]'s `Option`s are where that
//! shows: an MVD carries no velocity (speeds are differenced from positions) and no ground flag,
//! while a `.qwd` carries both but only for the players its recorder could see.

use std::io::{Cursor, Seek, SeekFrom};
use std::path::{Path, PathBuf};

use binrw::{BinRead, BinReaderExt};
use glam::Vec3;
use rtx_proto::protocol::ProtoState;
use rtx_proto::svc::{self, MoveVars, SvcEvent, Usercmd};

pub mod analysis;
pub mod mvd;

pub use analysis::{Motion, Summary, Track};

/// The fixed `usercmd_t` embedded in a `dem_cmd` record — the local player's raw input, not a
/// delta. Byte-for-byte the engine's C layout: `msec`, 3 alignment bytes, `vec3_t` view angles,
/// three move shorts, then the button and impulse bytes.
#[derive(BinRead)]
#[br(little)]
struct RawUsercmd {
    msec: u8,
    #[br(pad_before = 3)]
    angles: [f32; 3],
    forward: i16,
    side: i16,
    up: i16,
    buttons: u8,
    impulse: u8,
}

impl RawUsercmd {
    fn into_usercmd(self) -> Usercmd {
        Usercmd {
            msec: self.msec,
            angles: Vec3::from_array(self.angles),
            forward: self.forward,
            side: self.side,
            up: self.up,
            buttons: self.buttons,
            impulse: self.impulse,
        }
    }
}

/// Each record's 5-byte header: a float demo time and the `dem_*` kind byte.
#[derive(BinRead)]
#[br(little)]
struct RecordHeader {
    time: f32,
    kind: u8,
}

/// The bytes of a QuakeWorld netchan sequence header (`incoming`/`incoming_acknowledged` longs)
/// that lead every recorded `dem_read` packet ahead of the svc message stream.
const NETCHAN_HEADER: usize = 8;

/// Which demo container a [`Demo`] was read from.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Format {
    /// A single client's recording: absolute timestamps, its own inputs, wire velocities.
    Qwd,
    /// A server-side multi-view recording: delta timestamps, every player, no velocities.
    Mvd,
}

impl Format {
    pub fn as_str(self) -> &'static str {
        match self {
            Format::Qwd => "qwd",
            Format::Mvd => "mvd",
        }
    }
}

/// The local client's own input for one frame, from a `dem_cmd` record.
#[derive(Clone, Copy, Debug)]
pub struct DemoCmd {
    /// Demo timestamp of the record.
    pub time: f32,
    /// The decoded command — view angles, moves, buttons, msec.
    pub cmd: Usercmd,
}

/// One player's state at one instant — the common currency of both containers.
///
/// A `.qwd` and a `.mvd` say very different things on the wire (see [`mvd`]), and normalising here
/// is what lets [`analysis`] and the CLI treat them alike. The cost is that a field absent from one
/// format is `Option` for both: an MVD carries no velocity at all, and a `.qwd` records only the
/// players its recorder could see.
#[derive(Clone, Debug)]
pub struct Frame {
    /// Demo timestamp in seconds from the start of the file.
    pub time: f32,
    /// Player slot, 0..32. Stable across the file; [`Demo::players`] names it.
    pub player: u8,
    pub origin: Vec3,
    /// View angles in degrees. An MVD stores pitch pre-multiplied by −3 (a recorder quirk) and
    /// this undoes it, so pitch means the same thing in both formats.
    pub angles: Vec3,
    /// Velocity **as the wire stated it**. `None` for MVD, which never carries any — derive it
    /// from successive origins instead ([`analysis::Track`] does).
    pub velocity: Option<Vec3>,
    /// The player's own input, when the format carries it: `PF_COMMAND` in a `.qwd`, or a hidden
    /// `usercmd` block in an MVD.
    pub command: Option<Usercmd>,
    /// Whether the recorder marked this player dead this frame.
    pub dead: bool,
    /// Ground contact, when the protocol carried it (`Z_EXT_PF_ONGROUND`). `None` in an MVD.
    pub on_ground: Option<bool>,
    /// Animation frame of the player's weapon — a usable proxy for firing.
    pub weaponframe: Option<u8>,
}

/// What a demo knows about one player slot.
#[derive(Clone, Debug, Default)]
pub struct PlayerSlot {
    /// `\name\` from the slot's userinfo.
    pub name: String,
    /// `\team\` from the slot's userinfo.
    pub team: String,
    /// Whether the slot joined as a spectator (`\*spectator\`).
    pub spectator: bool,
    /// The raw `\k\v\` userinfo string, last seen.
    pub userinfo: String,
    /// How many frames this slot appeared in — the cheap way to tell players from empty slots.
    pub frames: usize,
}

impl PlayerSlot {
    /// Whether this slot ever carried a player. Empty slots exist in every demo; a 4-on-4 fills
    /// eight of the thirty-two.
    pub fn present(&self) -> bool {
        self.frames > 0 || !self.name.is_empty()
    }

    /// The slot's display name, falling back to its number when userinfo never arrived.
    pub fn label(&self, slot: u8) -> String {
        if self.name.is_empty() {
            format!("#{slot}")
        } else {
            self.name.clone()
        }
    }
}

/// Pull `key` out of a `\k\v\k\v` userinfo string.
fn info_value(userinfo: &str, key: &str) -> String {
    let mut it = userinfo.split('\\').skip(1);
    while let (Some(k), Some(v)) = (it.next(), it.next()) {
        if k == key {
            return v.to_string();
        }
    }
    String::new()
}

/// Everything one demo yields: the framing-derived context plus the two event streams.
#[derive(Clone, Debug)]
pub struct Demo {
    /// The file this came from.
    pub path: PathBuf,
    /// The negotiated protocol state at end of file (coord/angle widths, extension masks).
    pub proto: ProtoState,
    /// Which container this came from — the two carry different information, and a consumer that
    /// silently assumes velocity or usercmds exist will read zeros off an MVD.
    pub format: Format,
    /// The recording client's own player slot, from the last `svc_serverdata`. `None` for an MVD,
    /// which records the whole game rather than one client's view.
    pub local_player: Option<u8>,
    /// What each of the 32 slots was — name, team, spectator flag.
    pub players: Vec<PlayerSlot>,
    /// The map, from `svc_serverdata`.
    pub levelname: String,
    /// The server's physics constants, from the last `svc_serverdata`.
    pub movevars: Option<MoveVars>,
    /// The local player's own `dem_cmd` inputs, in file order (ascending time).
    pub demo_cmds: Vec<DemoCmd>,
    /// Every `svc_playerinfo` seen, in file order.
    pub frames: Vec<Frame>,
    /// Non-fatal per-packet decode failures (offset + reason). A malformed tail packet lands here
    /// rather than aborting the whole file.
    pub warnings: Vec<String>,
}

/// Why a demo couldn't be framed. Failures *inside* a packet are collected as
/// [`Demo::warnings`] instead; these are the container-level errors that stop parsing.
#[derive(Debug)]
pub enum Error {
    /// Reading the file failed.
    Io(std::io::Error),
    /// A record ran off the end of the file.
    Truncated {
        /// What was being read.
        what: &'static str,
        /// Byte offset it started at.
        at: u64,
    },
    /// A `dem_read` declared a length past the end of the file.
    LengthOverflow {
        /// Byte offset of the length field.
        at: u64,
    },
    /// A record tag that isn't `dem_cmd`/`dem_read`/`dem_set`.
    UnknownRecord {
        /// The tag byte.
        kind: u8,
        /// Byte offset it appeared at.
        at: u64,
    },
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Error::Io(e) => write!(f, "{e}"),
            Error::Truncated { what, at } => write!(f, "truncated {what} at offset {at}"),
            Error::LengthOverflow { at } => write!(f, "dem_read length exceeds file at offset {at}"),
            Error::UnknownRecord { kind, at } => write!(f, "unknown demo record type {kind} at offset {at}"),
        }
    }
}

impl std::error::Error for Error {}

impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Error::Io(e)
    }
}

type Result<T> = std::result::Result<T, Error>;

/// Accumulates decoded messages into a [`Demo`], for either container.
///
/// Both formats reduce to "here is a message stream, and here is when it happened", so everything
/// downstream of the framing lives here. The MVD-only state is the per-player carry-forward: those
/// `svc_playerinfo` deltas are relative to that player's *last* update, so the resolver has to hold
/// one absolute state per slot and patch it.
struct Feed {
    proto: ProtoState,
    format: Format,
    local_player: Option<u8>,
    movevars: Option<MoveVars>,
    levelname: String,
    players: Vec<PlayerSlot>,
    frames: Vec<Frame>,
    warnings: Vec<String>,
    /// Per-slot absolute state, for resolving MVD deltas. Origin and angles only — those are the
    /// fields the format deltas.
    last: Vec<(Vec3, Vec3)>,
    /// Hidden-channel usercmds, keyed by slot, awaiting the frame they belong to.
    pending_cmds: Vec<Option<Usercmd>>,
}

/// A recorder quirk: MVD pitch is written pre-multiplied by −3 (it packs a wider useful range into
/// the byte), so it has to be undone to mean degrees again. Roll is always written as zero.
const MVD_PITCH_SCALE: f32 = -3.0;

impl Feed {
    fn new(format: Format) -> Self {
        Feed {
            proto: if format == Format::Mvd {
                ProtoState::new_mvd()
            } else {
                ProtoState::new()
            },
            format,
            local_player: None,
            movevars: None,
            levelname: String::new(),
            players: vec![PlayerSlot::default(); 32],
            frames: Vec::new(),
            warnings: Vec::new(),
            last: vec![(Vec3::ZERO, Vec3::ZERO); 32],
            pending_cmds: vec![None; 32],
        }
    }

    /// Decode one svc message stream and fold what it says into the demo.
    fn packet(&mut self, time: f32, msg: &[u8], at: usize) {
        let events = match svc::parse(&mut self.proto, msg) {
            Ok(evs) => evs,
            Err(e) => {
                self.warnings.push(format!("packet at offset {at}: {e}"));
                return;
            }
        };
        for ev in events {
            match ev {
                SvcEvent::PlayerInfo(info) => {
                    let velocity_present = (0..3).any(|i| info.flags & (svc::pf::VELOCITY1 << i) != 0);
                    let slot = info.player as usize;
                    if let Some(p) = self.players.get_mut(slot) {
                        p.frames += 1;
                    }
                    self.frames.push(Frame {
                        time,
                        player: info.player,
                        origin: info.origin,
                        // A `.qwd` has no angle field of its own — a player's facing rides on the
                        // usercmd the server echoed back, so it is present exactly when that is.
                        angles: info.command.map_or(Vec3::ZERO, |c| c.angles),
                        velocity: velocity_present.then_some(info.velocity),
                        command: info.command,
                        dead: info.dead(),
                        on_ground: self
                            .proto
                            .has_z_ext(rtx_proto::protocol::z_ext::PF_ONGROUND)
                            .then(|| info.on_ground()),
                        weaponframe: info.weaponframe,
                    });
                }
                SvcEvent::MvdPlayerInfo(info) => {
                    let slot = info.player as usize;
                    if slot >= self.last.len() {
                        continue;
                    }
                    // Resolve the delta: a field the recorder omitted is unchanged, not zero.
                    let (origin, angles) = &mut self.last[slot];
                    for i in 0..3 {
                        if let Some(v) = info.origin[i] {
                            origin[i] = v;
                        }
                        if let Some(a) = info.angles[i] {
                            angles[i] = if i == 0 { a / MVD_PITCH_SCALE } else { a };
                        }
                    }
                    let (origin, angles) = (*origin, *angles);
                    self.players[slot].frames += 1;
                    self.frames.push(Frame {
                        time,
                        player: info.player,
                        origin,
                        angles,
                        velocity: None, // an MVD carries none; differencing is the only source
                        command: self.pending_cmds[slot].take(),
                        dead: info.dead(),
                        on_ground: None,
                        weaponframe: info.weaponframe,
                    });
                }
                SvcEvent::ServerData(sd) => {
                    if self.format == Format::Qwd {
                        self.local_player = Some(sd.playernum);
                    }
                    self.movevars = Some(sd.movevars);
                    self.levelname = sd.levelname.clone();
                }
                SvcEvent::UpdateUserinfo { player, userinfo, .. } => self.set_userinfo(player, userinfo),
                SvcEvent::SetInfo { player, key, value } => {
                    if let Some(p) = self.players.get_mut(player as usize) {
                        match key.as_str() {
                            "name" => p.name = value,
                            "team" => p.team = value,
                            "*spectator" => p.spectator = !value.is_empty() && value != "0",
                            _ => {}
                        }
                    }
                }
                _ => {}
            }
        }
    }

    fn set_userinfo(&mut self, player: u8, userinfo: String) {
        let Some(p) = self.players.get_mut(player as usize) else {
            return;
        };
        p.name = info_value(&userinfo, "name");
        p.team = info_value(&userinfo, "team");
        p.spectator = !info_value(&userinfo, "*spectator").is_empty();
        p.userinfo = userinfo;
    }

    fn finish(self, path: PathBuf, demo_cmds: Vec<DemoCmd>) -> Demo {
        Demo {
            path,
            proto: self.proto,
            format: self.format,
            local_player: self.local_player,
            players: self.players,
            levelname: self.levelname,
            movevars: self.movevars,
            demo_cmds,
            frames: self.frames,
            warnings: self.warnings,
        }
    }
}

/// Parse a demo file, choosing the container by content.
///
/// Neither format has a magic number, so the choice is structural — see [`mvd::looks_like_mvd`].
/// Content beats the extension deliberately: demos get renamed, and misreading the container is
/// silent rather than loud.
pub fn parse_demo(path: impl AsRef<Path>) -> Result<Demo> {
    let path = path.as_ref().to_path_buf();
    let data = std::fs::read(&path)?;
    if mvd::looks_like_mvd(&data) {
        Ok(parse_mvd_bytes(&data, path))
    } else {
        parse_qwd_bytes(&data, path)
    }
}

/// Parse multi-view demo bytes. Framing problems end the file rather than failing it — a demo cut
/// short by a crashed server is normal, and everything before the tear is good.
pub fn parse_mvd_bytes(data: &[u8], path: PathBuf) -> Demo {
    let mut feed = Feed::new(Format::Mvd);
    let mut r = mvd::Reader::new(data);
    let mut cmds = Vec::new();
    while let Some(b) = r.next_block() {
        match b.kind {
            mvd::dem::SET => {}
            // Not an svc stream — the hidden channel, which is where the real usercmds live.
            _ if b.to.is_hidden_channel() => {
                cmds.clear();
                mvd::hidden_usercmds(b.body, &mut cmds);
                for c in cmds.drain(..) {
                    if let Some(slot) = feed.pending_cmds.get_mut(c.player as usize) {
                        *slot = Some(Usercmd {
                            msec: c.msec,
                            angles: Vec3::from_array(c.angles),
                            forward: c.forward,
                            side: c.side,
                            up: c.up,
                            buttons: c.buttons,
                            impulse: c.impulse,
                        });
                    }
                }
            }
            _ => feed.packet(b.time, b.body, b.at),
        }
    }
    feed.finish(path, Vec::new())
}

/// Parse `.qwd` bytes: a single client's recording.
///
/// Framing errors (a truncated record, a bad length, an unknown tag) return `Err` — unlike an MVD,
/// a `.qwd` is not a joinable stream, so a broken record means the file is wrong rather than cut.
/// A packet whose svc stream fails to decode is recorded in [`Demo::warnings`] and skipped, so one
/// corrupt frame near the end doesn't cost you the whole demo.
pub fn parse_qwd_bytes(data: &[u8], path: PathBuf) -> Result<Demo> {
    let total = data.len() as u64;
    let mut cur = Cursor::new(data);
    let mut feed = Feed::new(Format::Qwd);
    let mut demo_cmds = Vec::new();

    // Require `n` more bytes before a read, so a truncated record is a clear error rather than a
    // binrw underflow with no context.
    let require = |pos: u64, n: u64, what: &'static str| -> Result<()> {
        if total - pos < n {
            Err(Error::Truncated { what, at: pos })
        } else {
            Ok(())
        }
    };

    while cur.position() < total {
        let rec_at = cur.position();
        require(rec_at, 5, "record header")?;
        let header: RecordHeader = cur.read_le().map_err(|_| Error::Truncated {
            what: "record header",
            at: rec_at,
        })?;

        match header.kind {
            mvd::dem::CMD => {
                let body_at = cur.position();
                require(body_at, (USERCMD_BYTES + VIEWANGLES_BYTES) as u64, "dem_cmd")?;
                let raw: RawUsercmd = cur.read_le().map_err(|_| Error::Truncated {
                    what: "dem_cmd",
                    at: body_at,
                })?;
                cur.seek(SeekFrom::Current(VIEWANGLES_BYTES as i64))?; // skip the smoothed view angles
                demo_cmds.push(DemoCmd {
                    time: header.time,
                    cmd: raw.into_usercmd(),
                });
            }
            mvd::dem::READ => {
                let len_at = cur.position();
                require(len_at, 4, "dem_read length")?;
                let length: u32 = cur.read_le().map_err(|_| Error::Truncated {
                    what: "dem_read length",
                    at: len_at,
                })?;
                let start = cur.position() as usize;
                let end = start + length as usize;
                if end > data.len() {
                    return Err(Error::LengthOverflow { at: len_at });
                }
                let packet = &data[start..end];
                cur.set_position(end as u64);
                // A connectionless (`0xffffffff`) packet is out-of-band, not a message stream — the
                // trailing "EndOfDemo" marker is one. It carries no frame data, so skip it rather
                // than feed its bytes to the svc parser as if they were opcodes.
                if packet.starts_with(&rtx_proto::protocol::CONNECTIONLESS) {
                    continue;
                }
                // A `.qwd` records whole datagrams, so the svc stream starts after the netchan
                // sequence header. (An MVD records only the stream — no header to skip.)
                let msg = &packet[NETCHAN_HEADER.min(packet.len())..];
                feed.packet(header.time, msg, start);
            }
            mvd::dem::SET => {
                require(cur.position(), 8, "dem_set")?;
                cur.seek(SeekFrom::Current(8))?;
            }
            kind => return Err(Error::UnknownRecord { kind, at: rec_at }),
        }
    }

    Ok(feed.finish(path, demo_cmds))
}

/// Size of the embedded `usercmd_t` in a `dem_cmd` record.
const USERCMD_BYTES: usize = 24;
/// Size of the view-angle vector that trails it, which we skip.
const VIEWANGLES_BYTES: usize = 12;

#[cfg(test)]
mod tests {
    use super::*;

    /// Hand-build a minimal demo (one `dem_cmd`, one `dem_set`) and check the framing and the
    /// fixed `usercmd_t` decode — the parts this crate owns, exercised without a demo file on disk.
    #[test]
    fn frames_a_dem_cmd_and_skips_a_dem_set() {
        let mut buf = Vec::new();
        // dem_cmd record: time=1.5, kind=0, then the 24-byte usercmd + 12 view-angle bytes.
        buf.extend_from_slice(&1.5f32.to_le_bytes());
        buf.push(mvd::dem::CMD);
        buf.push(13); // msec
        buf.extend_from_slice(&[0, 0, 0]); // alignment padding
        buf.extend_from_slice(&10.0f32.to_le_bytes()); // pitch
        buf.extend_from_slice(&(-20.0f32).to_le_bytes()); // yaw
        buf.extend_from_slice(&0.0f32.to_le_bytes()); // roll
        buf.extend_from_slice(&800i16.to_le_bytes()); // forward
        buf.extend_from_slice(&(-400i16).to_le_bytes()); // side
        buf.extend_from_slice(&0i16.to_le_bytes()); // up
        buf.push(0b11); // buttons: attack | jump
        buf.push(7); // impulse
        buf.extend_from_slice(&[0u8; VIEWANGLES_BYTES]); // trailing view angles, skipped
                                                         // dem_set record: time=1.5, kind=2, 8 payload bytes.
        buf.extend_from_slice(&1.5f32.to_le_bytes());
        buf.push(mvd::dem::SET);
        buf.extend_from_slice(&[0u8; 8]);

        let dir = std::env::temp_dir();
        let path = dir.join("rtx_qwd_parse_frames_test.qwd");
        std::fs::write(&path, &buf).unwrap();
        let demo = parse_demo(&path).unwrap();
        std::fs::remove_file(&path).ok();

        assert_eq!(demo.demo_cmds.len(), 1);
        assert!(demo.frames.is_empty());
        let c = demo.demo_cmds[0];
        assert_eq!(c.time, 1.5);
        assert_eq!(c.cmd.msec, 13);
        assert_eq!(c.cmd.forward, 800);
        assert_eq!(c.cmd.side, -400);
        assert_eq!(c.cmd.buttons, 0b11);
        assert_eq!(c.cmd.impulse, 7);
        assert_eq!(c.cmd.angles.x, 10.0);
        assert_eq!(c.cmd.angles.y, -20.0);
    }

    /// A record tag that isn't one of the three demo kinds is a hard framing error.
    #[test]
    fn rejects_an_unknown_record_tag() {
        let mut buf = Vec::new();
        buf.extend_from_slice(&0.0f32.to_le_bytes());
        buf.push(9); // not dem_cmd/read/set
        let dir = std::env::temp_dir();
        let path = dir.join("rtx_qwd_parse_badtag_test.qwd");
        std::fs::write(&path, &buf).unwrap();
        let err = parse_demo(&path).unwrap_err();
        std::fs::remove_file(&path).ok();
        assert!(matches!(err, Error::UnknownRecord { kind: 9, .. }));
    }
}
