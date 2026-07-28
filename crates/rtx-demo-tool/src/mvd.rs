// SPDX-License-Identifier: AGPL-3.0-or-later

//! The multi-view demo (`.mvd`) container.
//!
//! An MVD is what a QuakeWorld *server* records: not one client's session but the whole game, every
//! player's state every frame, with no single point of view. That difference shows up in the
//! framing, and getting any of it wrong reads plausible-looking rubbish rather than failing:
//!
//! * **Time is a one-byte millisecond delta**, not an absolute float. It saturates at 255 and the
//!   recorder writes 0 for anything under 2 ms, so demo time is a sum of quantized steps — see
//!   [`Reader::time`]. A paused server writes zero-length steps, so demo time is game time, not
//!   wall time.
//! * **Every block is addressed.** A `.qwd` block is implicitly "to me"; an MVD block names its
//!   audience — everyone, one player, or a bitmask of players. The audience is packed into the tag
//!   byte for the single-player kinds, so the tag is not simply the kind.
//! * **The payload has no netchan header.** A `.qwd` records whole datagrams, sequence numbers and
//!   all; an MVD records only the message stream.
//! * **`dem_multiple` addressed to nobody is not a message stream at all** — it is the hidden-data
//!   channel, carrying things the format grew later. Feeding it to an svc parser desynchronises the
//!   rest of the block. One of those hidden kinds is the raw per-player `usercmd`, which is the
//!   highest-fidelity movement signal in the file: real keys and float angles at the client's own
//!   rate, where `svc_playerinfo` gives 1/8-unit positions at the recorder's.
//!
//! There is no file header and no magic, in either format — an MVD is designed to be an append-only
//! stream you can join late (that is what QTV does with the identical framing). Detection is
//! therefore structural; see [`looks_like_mvd`].

/// Demo record kinds (`dem_*`). The first three are shared with `.qwd`; the rest are MVD-only.
pub mod dem {
    /// The recording client's own `usercmd`. `.qwd` only — an MVD has no local client.
    pub const CMD: u8 = 0;
    /// A recorded server→client packet. `.qwd` only; MVD uses the addressed kinds below.
    pub const READ: u8 = 1;
    /// Netchan sequence seeds, once at the start.
    pub const SET: u8 = 2;
    /// Addressed to the players in a following 32-bit mask.
    pub const MULTIPLE: u8 = 3;
    /// Addressed to one player, named in the tag byte's high bits.
    pub const SINGLE: u8 = 4;
    /// A stats update for the one player named in the tag byte's high bits.
    pub const STATS: u8 = 5;
    /// Addressed to everyone. Carries the per-frame `svc_playerinfo` run.
    pub const ALL: u8 = 6;
    /// The kind occupies the low three bits of the tag byte.
    pub const MASK: u8 = 7;
}

/// Hidden message kinds carried inside a `dem_multiple` block addressed to nobody.
pub mod hidden {
    /// `<byte player> <byte dropnum> <byte msec> <3 floats angles> <3 shorts moves> <byte buttons>
    /// <byte impulse>` — the player's actual input.
    pub const USERCMD: u16 = 0x0001;
    /// Escape marker: the type is repeated while it reads as this.
    pub const EXTENDED: u16 = 0xFFFF;
}

/// Who a block is addressed to.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum To {
    /// `dem_all` — every viewer.
    All,
    /// `dem_single` / `dem_stats` — one player slot.
    Single(u8),
    /// `dem_multiple` — the set of slots whose bit is set.
    Mask(u32),
}

impl To {
    /// Whether a viewer tracking `slot` would have received this block. A reader that wants one
    /// player's view — rather than the whole game — filters on this.
    pub fn includes(self, slot: u8) -> bool {
        match self {
            To::All => true,
            To::Single(s) => s == slot,
            To::Mask(m) => m & (1u32 << slot) != 0,
        }
    }

    /// A `dem_multiple` addressed to no one at all: the hidden-data channel, whose payload is not
    /// an svc stream.
    pub fn is_hidden_channel(self) -> bool {
        self == To::Mask(0)
    }
}

/// One framed block: when it happened, who it was for, and its payload.
#[derive(Clone, Copy, Debug)]
pub struct Block<'a> {
    /// Accumulated demo time in seconds, from summing the millisecond deltas.
    pub time: f32,
    /// `dem_*` kind, already stripped of the packed audience bits.
    pub kind: u8,
    /// Who the recorder addressed it to.
    pub to: To,
    /// The bytes after the header — an svc message stream, unless [`To::is_hidden_channel`].
    pub body: &'a [u8],
    /// Offset of the block's first byte, for error reporting.
    pub at: usize,
}

/// A block-at-a-time reader over MVD bytes.
///
/// Yields blocks in file order with a running clock. A truncated final block ends iteration rather
/// than erroring: demos are commonly cut short by a crashed server or an interrupted download, and
/// the ~14 MB before the tear is still perfectly good data.
pub struct Reader<'a> {
    data: &'a [u8],
    pos: usize,
    time_ms: u64,
}

impl<'a> Reader<'a> {
    pub fn new(data: &'a [u8]) -> Self {
        Reader {
            data,
            pos: 0,
            time_ms: 0,
        }
    }

    /// Accumulated demo time in seconds. Starts at zero on the first block.
    pub fn time(&self) -> f32 {
        self.time_ms as f32 / 1000.0
    }

    /// Byte offset of the next unread block.
    pub fn pos(&self) -> usize {
        self.pos
    }

    fn u8_at(&self, p: usize) -> Option<u8> {
        self.data.get(p).copied()
    }

    fn u32_at(&self, p: usize) -> Option<u32> {
        self.data
            .get(p..p + 4)
            .map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
    }

    /// The next block, or `None` at end of file or on a truncated tail.
    pub fn next_block(&mut self) -> Option<Block<'a>> {
        let at = self.pos;
        let msec = self.u8_at(at)?;
        let tag = self.u8_at(at + 1)?;
        let kind = tag & dem::MASK;

        // `dem_set` is the one fixed-size block: two sequence longs, no length prefix.
        if kind == dem::SET {
            if self.data.len() < at + 10 {
                return None;
            }
            self.time_ms += msec as u64;
            self.pos = at + 10;
            return Some(Block {
                time: self.time(),
                kind,
                to: To::All,
                body: &[],
                at,
            });
        }

        // Everything else is `[audience?][u32 length][body]`. Only `dem_multiple` spends bytes on
        // its audience; `dem_single`/`dem_stats` pack the slot into the tag's upper five bits,
        // which is why the tag is not simply the kind.
        let (to, len_at) = match kind {
            dem::MULTIPLE => (To::Mask(self.u32_at(at + 2)?), at + 6),
            dem::SINGLE | dem::STATS => (To::Single(tag >> 3), at + 2),
            _ => (To::All, at + 2),
        };
        let len = self.u32_at(len_at)? as usize;
        // A length past the end is either a torn tail or corruption; either way there is nothing
        // more to read. `MAX_MVD_SIZE` in the engine is the same sanity bound.
        let body_at = len_at + 4;
        let body = self.data.get(body_at..body_at + len)?;

        self.time_ms += msec as u64;
        self.pos = body_at + len;
        Some(Block {
            time: self.time(),
            kind,
            to,
            body,
            at,
        })
    }
}

/// Whether these bytes frame as an MVD.
///
/// Both containers are header-less, so this walks the framing and asks whether it stays in step: a
/// `.qwd` read as an MVD almost immediately yields an out-of-range kind or a length that overruns
/// the file, because its leading float timestamp is nothing like a `msec`/tag pair. Requiring
/// several MVD-only kinds is what makes it decisive rather than a coin toss — mirrors the same
/// heuristic in ezquake's demo prober.
pub fn looks_like_mvd(data: &[u8]) -> bool {
    let mut r = Reader::new(data);
    let (mut blocks, mut mvd_only) = (0u32, 0u32);
    while let Some(b) = r.next_block() {
        blocks += 1;
        if matches!(b.kind, dem::MULTIPLE | dem::SINGLE | dem::STATS | dem::ALL) {
            mvd_only += 1;
        } else if b.kind != dem::SET {
            return false; // dem_cmd / dem_read never appear in an MVD
        }
        if mvd_only >= 4 {
            return true;
        }
        if blocks > 64 {
            break;
        }
    }
    false
}

/// A raw `usercmd` lifted from a hidden `dem_multiple(0)` block — the player's real input.
#[derive(Clone, Copy, Debug)]
pub struct HiddenUsercmd {
    pub player: u8,
    /// How many commands the server dropped before this one.
    pub dropnum: u8,
    pub msec: u8,
    /// View angles in degrees, at full float precision (unlike the quantized `svc_playerinfo`).
    pub angles: [f32; 3],
    pub forward: i16,
    pub side: i16,
    pub up: i16,
    pub buttons: u8,
    pub impulse: u8,
}

/// Decode the hidden messages in a `dem_multiple(0)` body, returning any `usercmd`s among them.
///
/// The channel is a run of `[u32 length][u16 type][length bytes]` records, where the type repeats
/// while it reads as [`hidden::EXTENDED`]. Unknown kinds are skipped by their length — the whole
/// point of the length prefix — so a demo from a newer server stays readable.
pub fn hidden_usercmds(body: &[u8], out: &mut Vec<HiddenUsercmd>) {
    let mut p = 0usize;
    let rd_u32 = |p: usize| -> Option<u32> { body.get(p..p + 4).map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]])) };
    let rd_u16 = |p: usize| -> Option<u16> { body.get(p..p + 2).map(|b| u16::from_le_bytes([b[0], b[1]])) };
    while p + 6 <= body.len() {
        let Some(size) = rd_u32(p) else { return };
        let mut q = p + 4;
        let mut kind = match rd_u16(q) {
            Some(k) => k,
            None => return,
        };
        q += 2;
        while kind == hidden::EXTENDED {
            match rd_u16(q) {
                Some(k) => {
                    kind = k;
                    q += 2;
                }
                None => return,
            }
        }
        let size = size as usize;
        let Some(payload) = body.get(q..q + size) else { return };
        if kind == hidden::USERCMD && payload.len() >= 21 {
            let f32_at = |i: usize| f32::from_le_bytes([payload[i], payload[i + 1], payload[i + 2], payload[i + 3]]);
            let i16_at = |i: usize| i16::from_le_bytes([payload[i], payload[i + 1]]);
            out.push(HiddenUsercmd {
                player: payload[0],
                dropnum: payload[1],
                msec: payload[2],
                angles: [f32_at(3), f32_at(7), f32_at(11)],
                forward: i16_at(15),
                side: i16_at(17),
                up: i16_at(19),
                buttons: payload.get(21).copied().unwrap_or(0),
                impulse: payload.get(22).copied().unwrap_or(0),
            });
        }
        p = q + size;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Build one block: `[msec][tag][audience?][len][body]`.
    fn block(msec: u8, tag: u8, audience: Option<u32>, body: &[u8]) -> Vec<u8> {
        let mut v = vec![msec, tag];
        if let Some(a) = audience {
            v.extend_from_slice(&a.to_le_bytes());
        }
        v.extend_from_slice(&(body.len() as u32).to_le_bytes());
        v.extend_from_slice(body);
        v
    }

    #[test]
    fn accumulates_delta_time_and_strips_the_packed_audience() {
        let mut d = block(0, dem::ALL, None, &[0x0b]);
        d.extend(block(33, dem::ALL, None, &[1, 2]));
        // dem_single to slot 5 — the slot rides in the tag's upper bits, not in the body.
        d.extend(block(17, (5 << 3) | dem::SINGLE, None, &[9]));
        d.extend(block(0, dem::MULTIPLE, Some(0b1010), &[7]));

        let mut r = Reader::new(&d);
        let b0 = r.next_block().unwrap();
        assert_eq!((b0.time, b0.kind, b0.to), (0.0, dem::ALL, To::All));
        let b1 = r.next_block().unwrap();
        assert_eq!(b1.time, 0.033);
        assert_eq!(b1.body, &[1, 2]);
        let b2 = r.next_block().unwrap();
        assert_eq!(b2.to, To::Single(5), "the audience is packed into the tag byte");
        assert_eq!(b2.kind, dem::SINGLE);
        assert!((b2.time - 0.050).abs() < 1e-6, "time is a running sum of the deltas");
        let b3 = r.next_block().unwrap();
        assert_eq!(b3.to, To::Mask(0b1010));
        assert!(b3.to.includes(1) && b3.to.includes(3) && !b3.to.includes(2));
        assert!(r.next_block().is_none());
    }

    #[test]
    fn dem_set_is_fixed_width_and_carries_no_body() {
        let mut d = vec![0, dem::SET];
        d.extend_from_slice(&[0; 8]);
        d.extend(block(10, dem::ALL, None, &[42]));
        let mut r = Reader::new(&d);
        assert_eq!(r.next_block().unwrap().kind, dem::SET);
        let b = r.next_block().unwrap();
        assert_eq!(
            b.body,
            &[42],
            "dem_set has no length prefix; mis-sizing it desyncs the file"
        );
    }

    #[test]
    fn a_torn_tail_ends_iteration_rather_than_erroring() {
        let mut d = block(0, dem::ALL, None, &[1, 2, 3]);
        d.extend_from_slice(&[5, dem::ALL, 0xff, 0xff]); // a length that never arrives
        let mut r = Reader::new(&d);
        assert!(r.next_block().is_some());
        assert!(
            r.next_block().is_none(),
            "a cut-short demo keeps everything before the tear"
        );
    }

    #[test]
    fn mask_zero_is_the_hidden_channel_not_an_svc_stream() {
        assert!(To::Mask(0).is_hidden_channel());
        assert!(!To::All.is_hidden_channel());
        assert!(!To::Mask(1).is_hidden_channel());
    }

    #[test]
    fn detects_mvd_framing_and_rejects_qwd() {
        let mut mvd = Vec::new();
        for _ in 0..5 {
            mvd.extend(block(13, dem::ALL, None, &[0x0b, 0x00]));
        }
        assert!(looks_like_mvd(&mvd));

        // A .qwd record: float time, then dem_read, then a length. Read as MVD the float's bytes
        // become a bogus msec/tag pair.
        let mut qwd = Vec::new();
        qwd.extend_from_slice(&1.5f32.to_le_bytes());
        qwd.push(1); // dem_read
        qwd.extend_from_slice(&4u32.to_le_bytes());
        qwd.extend_from_slice(&[0; 4]);
        assert!(!looks_like_mvd(&qwd));
    }

    #[test]
    fn reads_a_hidden_usercmd_and_skips_unknown_kinds() {
        let mut body = Vec::new();
        // An unknown kind, skipped by its length.
        body.extend_from_slice(&3u32.to_le_bytes());
        body.extend_from_slice(&0x1234u16.to_le_bytes());
        body.extend_from_slice(&[1, 2, 3]);
        // A usercmd.
        let mut cmd = vec![7u8, 0, 13];
        cmd.extend_from_slice(&10.0f32.to_le_bytes());
        cmd.extend_from_slice(&200.0f32.to_le_bytes());
        cmd.extend_from_slice(&0.0f32.to_le_bytes());
        cmd.extend_from_slice(&400i16.to_le_bytes());
        cmd.extend_from_slice(&(-400i16).to_le_bytes());
        cmd.extend_from_slice(&0i16.to_le_bytes());
        cmd.push(2);
        cmd.push(0);
        body.extend_from_slice(&(cmd.len() as u32).to_le_bytes());
        body.extend_from_slice(&hidden::USERCMD.to_le_bytes());
        body.extend_from_slice(&cmd);

        let mut out = Vec::new();
        hidden_usercmds(&body, &mut out);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].player, 7);
        assert_eq!(out[0].angles[1], 200.0);
        assert_eq!((out[0].forward, out[0].side), (400, -400));
        assert_eq!(out[0].buttons, 2, "jump");
    }
}
