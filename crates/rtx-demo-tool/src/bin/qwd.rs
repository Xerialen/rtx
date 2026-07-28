// SPDX-License-Identifier: AGPL-3.0-or-later

//! `qwd` — inspect QuakeWorld demos, single-client `.qwd` and multi-view `.mvd` alike (the
//! container is chosen by content, since neither has a magic number and demos get renamed).
//!
//! - `qwd players` lists a demo's roster. The first thing to run on a file you haven't seen: a
//!   multi-view recording holds a whole team game across thirty-two slots, and the other
//!   subcommands need a slot or a name to talk about.
//! - `qwd dump` writes position, velocity, and usercmd fields as CSV (the old `qwd_dump.py`
//!   output): *combined* rows (one per player-state update, paired with its usercmd) by default,
//!   or `--raw` events interleaved by time with a leading `movevars` row.
//! - `qwd analyze` prints a per-player movement report — duration, speed percentiles, climb, path
//!   length, the jumps it found, and an optional down-sampled waypoint table.
//!
//! The name is historical: it predates MVD support and is kept because scripts call it.

use std::borrow::Cow;
use std::io::{self, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use rtx_demo_tool::analysis::{self, Motion};
use rtx_demo_tool::{parse_demo, Demo, DemoCmd, Frame};
use rtx_proto::svc::{MoveVars, Usercmd};

const USAGE: &str = "\
usage: qwd <command> [options] [FILE...]

Inspect QuakeWorld demos: single-client .qwd and server-side multi-view .mvd, told
apart by content. With no FILE, reads *.qwd and *.mvd in the current directory.

A .qwd records one client — its own inputs and the velocities the server sent it.
An .mvd records the whole game: every player, no velocities (speeds are derived
from successive positions), and inputs only where the recorder embedded them.

commands:
  players   list the players in a demo
  dump      write position/velocity/usercmd fields as CSV
  analyze   print a per-player movement report

dump options:
  --raw           emit raw dem_cmd and playerinfo events (plus a movevars row)
  --player P      one player, by slot number or (substring of) name; defaults to
                  the recording client, or to everyone in an .mvd
  --all-players   include every player
  --no-header     omit the CSV header

analyze options:
  --player P      one player, by slot number or (substring of) name
  --all-players   report every player
  --waypoints N   also print N evenly-spaced trajectory waypoints (default: none)

  -h, --help      show this help
";

fn main() -> ExitCode {
    let mut argv = std::env::args().skip(1);
    match argv.next().as_deref() {
        Some("players") => run_players(argv),
        Some("dump") => run_dump(argv),
        Some("analyze") => run_analyze(argv),
        None | Some("-h") | Some("--help") => {
            print!("{USAGE}");
            ExitCode::SUCCESS
        }
        Some(other) => {
            eprintln!("qwd: unknown command {other:?} (try `qwd --help`)");
            ExitCode::from(2)
        }
    }
}

/// Resolve the file list: the given paths, or `*.qwd` in the current directory.
fn resolve_paths(files: Vec<PathBuf>) -> Vec<PathBuf> {
    if !files.is_empty() {
        return files;
    }
    let mut paths: Vec<PathBuf> = std::fs::read_dir(".")
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            p.extension()
                .is_some_and(|x| x.eq_ignore_ascii_case("qwd") || x.eq_ignore_ascii_case("mvd"))
        })
        .collect();
    paths.sort();
    paths
}

// ── players ───────────────────────────────────────────────────────────────────────────────────

/// List who is in a demo. The first thing you want from a file you haven't seen: a multi-view
/// recording of a 4-on-4 holds eight players plus spectators across thirty-two slots, and every
/// other subcommand needs a slot or a name to talk about.
fn run_players(argv: impl Iterator<Item = String>) -> ExitCode {
    let mut files = Vec::new();
    for arg in argv {
        match arg.as_str() {
            "-h" | "--help" => {
                print!("{USAGE}");
                return ExitCode::SUCCESS;
            }
            _ if arg.starts_with('-') && arg != "-" => return usage_err(&format!("unknown option {arg}")),
            _ => files.push(PathBuf::from(arg)),
        }
    }
    let paths = resolve_paths(files);
    if paths.is_empty() {
        eprintln!("qwd: no .qwd or .mvd files found");
        return ExitCode::FAILURE;
    }
    let mut had_error = false;
    for path in &paths {
        let demo = match parse_demo(path) {
            Ok(d) => d,
            Err(e) => {
                eprintln!("qwd: {}: {e}", path.display());
                had_error = true;
                continue;
            }
        };
        let span = demo
            .frames
            .last()
            .zip(demo.frames.first())
            .map_or(0.0, |(l, f)| l.time - f.time);
        println!(
            "{} [{}] {} — {:.1}s, {} frames",
            basename(&demo.path),
            demo.format.as_str(),
            if demo.levelname.is_empty() {
                "?"
            } else {
                &demo.levelname
            },
            span,
            demo.frames.len()
        );
        for (slot, p) in demo.players.iter().enumerate() {
            if !p.present() {
                continue;
            }
            let mut tags = Vec::new();
            if p.spectator {
                tags.push("spectator".to_string());
            }
            if !p.team.is_empty() {
                tags.push(format!("team {}", p.team));
            }
            if demo.local_player == Some(slot as u8) {
                tags.push("recorder".to_string());
            }
            println!(
                "  {slot:>2}  {:<20} {:>7} frames{}{}",
                p.label(slot as u8),
                p.frames,
                if tags.is_empty() { "" } else { "  " },
                tags.join(", ")
            );
        }
    }
    exit_code(had_error)
}

// ── dump ──────────────────────────────────────────────────────────────────────────────────────

/// The combined-mode columns (also the body of every raw row).
const HEADER: &[&str] = &[
    "file",
    "time",
    "player",
    "x",
    "y",
    "z",
    "velocity_present",
    "vx",
    "vy",
    "vz",
    "cmd_source",
    "cmd_time",
    "msec",
    "forwardmove",
    "sidemove",
    "upmove",
    "buttons",
    "pitch",
    "yaw",
];

/// The ten movevars, named to match the old tool's `mv_*` raw-CSV tail so both stay diffable.
const MOVEVAR_FIELDS: &[&str] = &[
    "mv_gravity",
    "mv_stopspeed",
    "mv_maxspeed",
    "mv_spectatormaxspeed",
    "mv_accelerate",
    "mv_airaccelerate",
    "mv_wateraccelerate",
    "mv_friction",
    "mv_waterfriction",
    "mv_entgravity",
];

/// Which stream a resolved usercmd came from — the `cmd_source` column.
#[derive(Clone, Copy)]
enum CmdSource {
    /// The usercmd the server echoed inside the `svc_playerinfo`.
    PlayerInfo,
    /// The local player's nearest `dem_cmd`, matched by time.
    DemCmd,
}

impl CmdSource {
    fn as_str(self) -> &'static str {
        match self {
            CmdSource::PlayerInfo => "playerinfo",
            CmdSource::DemCmd => "dem_cmd",
        }
    }
}

/// A usercmd resolved for a row, carrying which stream it came from and the time to report.
struct ResolvedCmd {
    source: CmdSource,
    time: f32,
    cmd: Usercmd,
}

/// Parsed `qwd dump` options.
struct DumpArgs {
    files: Vec<PathBuf>,
    raw: bool,
    player: Option<String>,
    all_players: bool,
    no_header: bool,
    help: bool,
}

fn run_dump(argv: impl Iterator<Item = String>) -> ExitCode {
    let mut a = DumpArgs {
        files: Vec::new(),
        raw: false,
        player: None,
        all_players: false,
        no_header: false,
        help: false,
    };
    let mut it = argv;
    while let Some(arg) = it.next() {
        match arg.as_str() {
            "--raw" => a.raw = true,
            "--all-players" => a.all_players = true,
            "--no-header" => a.no_header = true,
            "-h" | "--help" => a.help = true,
            "--player" => match it.next() {
                Some(v) => a.player = Some(v),
                None => return usage_err("--player needs a slot number or name"),
            },
            _ if arg.starts_with("--player=") => a.player = Some(arg["--player=".len()..].to_string()),
            "--" => a.files.extend(it.by_ref().map(PathBuf::from)),
            _ if arg.starts_with('-') && arg != "-" => return usage_err(&format!("unknown option {arg}")),
            _ => a.files.push(PathBuf::from(arg)),
        }
    }
    if a.help {
        print!("{USAGE}");
        return ExitCode::SUCCESS;
    }

    let paths = resolve_paths(a.files);
    if paths.is_empty() {
        eprintln!("qwd: no .qwd or .mvd files found");
        return ExitCode::FAILURE;
    }

    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let mut had_error = false;

    if !a.no_header {
        let header: Vec<&str> = if a.raw {
            std::iter::once("event")
                .chain(HEADER.iter().copied())
                .chain(MOVEVAR_FIELDS.iter().copied())
                .collect()
        } else {
            HEADER.to_vec()
        };
        if write_row(&mut out, &header).is_err() {
            return ExitCode::SUCCESS; // downstream closed the pipe
        }
    }

    for path in &paths {
        let demo = match parse_demo(path) {
            Ok(demo) => demo,
            Err(e) => {
                eprintln!("qwd: {}: {e}", path.display());
                had_error = true;
                continue;
            }
        };
        for w in &demo.warnings {
            eprintln!("qwd: {}: {w}", path.display());
            had_error = true;
        }

        let slot = match a.player.as_deref().map(|v| resolve_player(&demo, v)) {
            Some(Ok(s)) => Some(s),
            Some(Err(e)) => {
                eprintln!("qwd: {}: {e}", path.display());
                had_error = true;
                continue;
            }
            None => None,
        };
        let rows = if a.raw {
            raw_rows(&demo, slot, a.all_players)
        } else {
            combined_rows(&demo, slot, a.all_players)
        };
        for row in rows {
            let cells: Vec<&str> = row.iter().map(String::as_str).collect();
            if write_row(&mut out, &cells).is_err() {
                return if had_error {
                    ExitCode::FAILURE
                } else {
                    ExitCode::SUCCESS
                };
            }
        }
    }

    let _ = out.flush();
    exit_code(had_error)
}

/// Which player's frames to emit: an explicit slot, the local player, or all of them (`None`).
///
/// A `.qwd` has an obvious default — the client that recorded it. A multi-view demo does not: it is
/// a recording of everybody, so defaulting to a slot would silently report one of eight players as
/// if it were *the* player. There, no selection means all of them.
fn selected_player(demo: &Demo, explicit: Option<u8>, all: bool) -> Option<u8> {
    if all {
        None
    } else if explicit.is_some() {
        explicit
    } else {
        demo.local_player
    }
}

/// Resolve a `--player` argument that may name a slot *or* a player. Names are matched
/// case-insensitively and by substring, because QuakeWorld names are full of punctuation and
/// colour codes that are miserable to type exactly.
fn resolve_player(demo: &Demo, arg: &str) -> Result<u8, String> {
    if let Ok(slot) = arg.parse::<u8>() {
        return Ok(slot);
    }
    let needle = arg.to_ascii_lowercase();
    let hits: Vec<u8> = demo
        .players
        .iter()
        .enumerate()
        .filter(|(_, p)| p.present() && p.name.to_ascii_lowercase().contains(&needle))
        .map(|(i, _)| i as u8)
        .collect();
    match hits.as_slice() {
        [one] => Ok(*one),
        [] => Err(format!("no player matching {arg:?}")),
        many => {
            let names: Vec<String> = many.iter().map(|&s| demo.players[s as usize].label(s)).collect();
            Err(format!("{arg:?} matches several players: {}", names.join(", ")))
        }
    }
}

/// The nearest `dem_cmd` to `time`, by absolute time difference — the local player's input for a
/// frame the server didn't echo a command for.
fn nearest_demo_cmd(cmds: &[DemoCmd], time: f32) -> Option<&DemoCmd> {
    if cmds.is_empty() {
        return None;
    }
    let idx = cmds.partition_point(|c| c.time < time);
    let mut best: Option<&DemoCmd> = None;
    for &i in &[idx, idx.wrapping_sub(1)] {
        if let Some(c) = cmds.get(i) {
            if best.is_none_or(|b| (c.time - time).abs() < (b.time - time).abs()) {
                best = Some(c);
            }
        }
    }
    best
}

/// The usercmd to report for one frame: the echoed command if present, else the nearest `dem_cmd`
/// — but only for the local player (or every player when the demo never told us who that is).
fn command_for_frame(demo: &Demo, frame: &Frame) -> Option<ResolvedCmd> {
    if let Some(cmd) = frame.command {
        return Some(ResolvedCmd {
            source: CmdSource::PlayerInfo,
            time: frame.time,
            cmd,
        });
    }
    let use_demo = match demo.local_player {
        None => true,
        Some(local) => frame.player == local,
    };
    if use_demo {
        nearest_demo_cmd(&demo.demo_cmds, frame.time).map(|c| ResolvedCmd {
            source: CmdSource::DemCmd,
            time: c.time,
            cmd: c.cmd,
        })
    } else {
        None
    }
}

fn combined_rows(demo: &Demo, explicit: Option<u8>, all: bool) -> Vec<Vec<String>> {
    let player = selected_player(demo, explicit, all);
    demo.frames
        .iter()
        .filter(|f| player.is_none_or(|p| f.player == p))
        .map(|f| row_for_frame(demo, f, command_for_frame(demo, f).as_ref()))
        .collect()
}

fn raw_rows(demo: &Demo, explicit: Option<u8>, all: bool) -> Vec<Vec<String>> {
    let player = selected_player(demo, explicit, all);
    let pad = vec![String::new(); MOVEVAR_FIELDS.len()];
    let mut rows: Vec<Vec<String>> = Vec::new();

    // Movevars first, so a reader has the physics params before any frame.
    if let Some(mv) = demo.movevars {
        let mut row = vec!["movevars".to_string()];
        row.extend(std::iter::repeat_n(String::new(), HEADER.len()));
        row.extend(movevar_cells(&mv));
        rows.push(row);
    }

    // dem_cmd (key i*2+1) and playerinfo (key j*2) events, ordered by (time, key) — the key breaks
    // ties deterministically and interleaves the two independent index spaces exactly as before.
    let mut events: Vec<(f32, usize, Vec<String>)> = Vec::new();
    for (i, c) in demo.demo_cmds.iter().enumerate() {
        let mut row = vec!["dem_cmd".to_string()];
        row.extend(row_for_cmd(&demo.path, c));
        row.extend(pad.iter().cloned());
        events.push((c.time, i * 2 + 1, row));
    }
    for (j, f) in demo.frames.iter().enumerate() {
        if player.is_some_and(|p| f.player != p) {
            continue;
        }
        let resolved = f.command.map(|cmd| ResolvedCmd {
            source: CmdSource::PlayerInfo,
            time: f.time,
            cmd,
        });
        let mut row = vec!["playerinfo".to_string()];
        row.extend(row_for_frame(demo, f, resolved.as_ref()));
        row.extend(pad.iter().cloned());
        events.push((f.time, j * 2, row));
    }
    events.sort_by(|a, b| {
        a.0.partial_cmp(&b.0)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(a.1.cmp(&b.1))
    });
    rows.extend(events.into_iter().map(|(_, _, row)| row));
    rows
}

/// A combined/playerinfo row: the frame's position and velocity, plus a resolved usercmd's fields.
fn row_for_frame(demo: &Demo, frame: &Frame, cmd: Option<&ResolvedCmd>) -> Vec<String> {
    // `velocity_present` says whether the *wire* carried a velocity, not whether one can be had:
    // an MVD never does, and its speeds come from differencing successive origins instead.
    let v = frame.velocity;
    vec![
        basename(&demo.path),
        fmt_float(frame.time),
        frame.player.to_string(),
        fmt_float(frame.origin.x),
        fmt_float(frame.origin.y),
        fmt_float(frame.origin.z),
        if v.is_some() { "1" } else { "0" }.to_string(),
        opt_int(v.map(|v| v.x as i64)),
        opt_int(v.map(|v| v.y as i64)),
        opt_int(v.map(|v| v.z as i64)),
        cmd.map_or(String::new(), |c| c.source.as_str().to_string()),
        cmd.map_or(String::new(), |c| fmt_float(c.time)),
        cmd.map_or(String::new(), |c| c.cmd.msec.to_string()),
        cmd.map_or(String::new(), |c| c.cmd.forward.to_string()),
        cmd.map_or(String::new(), |c| c.cmd.side.to_string()),
        cmd.map_or(String::new(), |c| c.cmd.up.to_string()),
        cmd.map_or(String::new(), |c| c.cmd.buttons.to_string()),
        cmd.map_or(String::new(), |c| fmt_float(c.cmd.angles.x)),
        cmd.map_or(String::new(), |c| fmt_float(c.cmd.angles.y)),
    ]
}

/// A raw `dem_cmd` row: no position/velocity, just the local input.
fn row_for_cmd(path: &Path, c: &DemoCmd) -> Vec<String> {
    vec![
        basename(path),
        fmt_float(c.time),
        String::new(),
        String::new(),
        String::new(),
        String::new(),
        "0".to_string(),
        String::new(),
        String::new(),
        String::new(),
        CmdSource::DemCmd.as_str().to_string(),
        fmt_float(c.time),
        c.cmd.msec.to_string(),
        c.cmd.forward.to_string(),
        c.cmd.side.to_string(),
        c.cmd.up.to_string(),
        c.cmd.buttons.to_string(),
        fmt_float(c.cmd.angles.x),
        fmt_float(c.cmd.angles.y),
    ]
}

fn movevar_cells(mv: &MoveVars) -> Vec<String> {
    [
        mv.gravity,
        mv.stopspeed,
        mv.maxspeed,
        mv.spectatormaxspeed,
        mv.accelerate,
        mv.airaccelerate,
        mv.wateraccelerate,
        mv.friction,
        mv.waterfriction,
        mv.entgravity,
    ]
    .iter()
    .map(|v| fmt_float(*v))
    .collect()
}

/// Format a float to at most six decimals, trailing zeros (and a bare point) trimmed — matching the
/// old tool's `fmt_float`, so the CSVs stay comparable.
fn fmt_float(v: f32) -> String {
    let s = format!("{v:.6}");
    let trimmed = s.trim_end_matches('0').trim_end_matches('.');
    if trimmed.is_empty() {
        "0".to_string()
    } else {
        trimmed.to_string()
    }
}

fn opt_int(v: Option<i64>) -> String {
    v.map_or(String::new(), |n| n.to_string())
}

/// Write one CSV row (`,`-separated, `\n`-terminated, minimal quoting).
fn write_row(out: &mut impl Write, cells: &[&str]) -> io::Result<()> {
    for (i, cell) in cells.iter().enumerate() {
        if i > 0 {
            out.write_all(b",")?;
        }
        out.write_all(csv_escape(cell).as_bytes())?;
    }
    out.write_all(b"\n")
}

/// Quote a field only if it contains a comma, quote, or newline (RFC 4180 minimal quoting).
fn csv_escape(s: &str) -> Cow<'_, str> {
    if s.contains([',', '"', '\n', '\r']) {
        Cow::Owned(format!("\"{}\"", s.replace('"', "\"\"")))
    } else {
        Cow::Borrowed(s)
    }
}

// ── analyze ───────────────────────────────────────────────────────────────────────────────────

/// Parsed `qwd analyze` options.
struct AnalyzeArgs {
    files: Vec<PathBuf>,
    player: Option<String>,
    all_players: bool,
    waypoints: usize,
    help: bool,
}

fn run_analyze(argv: impl Iterator<Item = String>) -> ExitCode {
    let mut a = AnalyzeArgs {
        files: Vec::new(),
        player: None,
        all_players: false,
        waypoints: 0,
        help: false,
    };
    let mut it = argv;
    while let Some(arg) = it.next() {
        match arg.as_str() {
            "--all-players" => a.all_players = true,
            "-h" | "--help" => a.help = true,
            "--player" => match it.next() {
                Some(v) => a.player = Some(v),
                None => return usage_err("--player needs a slot number or name"),
            },
            _ if arg.starts_with("--player=") => a.player = Some(arg["--player=".len()..].to_string()),
            "--waypoints" => match it.next().and_then(|v| v.parse().ok()) {
                Some(n) => a.waypoints = n,
                None => return usage_err("--waypoints needs a count"),
            },
            _ if arg.starts_with("--waypoints=") => match arg["--waypoints=".len()..].parse() {
                Ok(n) => a.waypoints = n,
                Err(_) => return usage_err("--waypoints needs a count"),
            },
            "--" => a.files.extend(it.by_ref().map(PathBuf::from)),
            _ if arg.starts_with('-') && arg != "-" => return usage_err(&format!("unknown option {arg}")),
            _ => a.files.push(PathBuf::from(arg)),
        }
    }
    if a.help {
        print!("{USAGE}");
        return ExitCode::SUCCESS;
    }

    let paths = resolve_paths(a.files);
    if paths.is_empty() {
        eprintln!("qwd: no .qwd or .mvd files found");
        return ExitCode::FAILURE;
    }

    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let mut had_error = false;

    for path in &paths {
        let demo = match parse_demo(path) {
            Ok(demo) => demo,
            Err(e) => {
                eprintln!("qwd: {}: {e}", path.display());
                had_error = true;
                continue;
            }
        };
        for w in &demo.warnings {
            eprintln!("qwd: {}: {w}", path.display());
            had_error = true;
        }

        // With no `--player`, report the recording client — or, in a multi-view demo that has no
        // such thing, everyone. Picking a slot arbitrarily there would report one of eight players
        // as though the file were about them.
        let players = match (a.all_players, a.player.as_deref()) {
            (true, _) => analysis::players(&demo),
            (_, Some(v)) => match resolve_player(&demo, v) {
                Ok(s) => vec![s],
                Err(e) => {
                    eprintln!("qwd: {}: {e}", path.display());
                    had_error = true;
                    continue;
                }
            },
            (_, None) => match demo.local_player {
                Some(p) => vec![p],
                None => analysis::players(&demo),
            },
        };
        let _ = writeln!(out, "{}", basename(&demo.path));
        for p in players {
            if report_player(&mut out, &demo, p, a.waypoints).is_err() {
                return exit_code(had_error);
            }
        }
    }

    let _ = out.flush();
    exit_code(had_error)
}

/// Print one player's movement report: the summary block, then an optional waypoint table.
fn report_player(out: &mut impl Write, demo: &Demo, player: u8, waypoints: usize) -> io::Result<()> {
    let track = analysis::track(demo, player);
    let s = track.summary();
    if s.frames == 0 {
        return writeln!(out, "  player {player}: no frames");
    }
    let label = demo
        .players
        .get(player as usize)
        .map_or_else(|| format!("#{player}"), |p| p.label(player));
    writeln!(
        out,
        "  player {player} ({label}): {} frames over {:.2}s",
        s.frames, s.duration
    )?;
    writeln!(
        out,
        "    start ({:.0}, {:.0}, {:.0})  ->  end ({:.0}, {:.0}, {:.0})   climb {:+.0}",
        s.start.x, s.start.y, s.start.z, s.end.x, s.end.y, s.end.z, s.height_gain,
    )?;
    writeln!(
        out,
        "    horizontal speed: peak {:.0}  mean {:.0} ups   path {:.0}u   z {:.0}..{:.0}",
        s.peak_speed, s.mean_speed, s.path_length, s.min_z, s.max_z,
    )?;
    // Percentiles over moving frames: a whole match is mostly standing still, so a mean says very
    // little about how fast the player actually travels.
    let [_, p50, p90, p99, max] = track.speed_percentiles();
    writeln!(
        out,
        "    while moving: p50 {p50:.0}  p90 {p90:.0}  p99 {p99:.0}  max {max:.0} ups",
    )?;
    let jumps = track.jumps();
    if !jumps.is_empty() {
        let gained = jumps.iter().filter(|j| j.peak_speed > j.takeoff_speed + 5.0).count();
        let longest = jumps.iter().fold(None::<&analysis::Jump>, |b, j| {
            Some(match b {
                Some(b) if b.distance >= j.distance => b,
                _ => j,
            })
        });
        writeln!(
            out,
            "    jumps: {} ({} gained speed in the air)   mean {:.0}u / {:.2}s",
            jumps.len(),
            gained,
            jumps.iter().map(|j| j.distance).sum::<f32>() / jumps.len() as f32,
            jumps.iter().map(|j| j.airtime).sum::<f32>() / jumps.len() as f32,
        )?;
        if let Some(j) = longest {
            writeln!(
                out,
                "      longest {:.0}u at t={:.1}s: ({:.0}, {:.0}, {:.0}) -> ({:.0}, {:.0}, {:.0})  \
                 takeoff {:.0} peak {:.0} ups, apex {:+.0}, {:.2}s",
                j.distance,
                j.takeoff_time,
                j.takeoff.x,
                j.takeoff.y,
                j.takeoff.z,
                j.landing.x,
                j.landing.y,
                j.landing.z,
                j.takeoff_speed,
                j.peak_speed,
                j.apex,
                j.airtime,
            )?;
        }
    }
    if waypoints >= 2 {
        writeln!(out, "    waypoints (t  x  y  z  hspeed  vspeed):")?;
        let t0 = track.motions.first().map_or(0.0, |m| m.time);
        for Motion {
            time,
            origin,
            horizontal_speed,
            vertical_speed,
            ..
        } in track.waypoints(waypoints)
        {
            writeln!(
                out,
                "      {:6.2}  {:6.0} {:6.0} {:6.0}   {:5.0}  {:+5.0}",
                time - t0,
                origin.x,
                origin.y,
                origin.z,
                horizontal_speed,
                vertical_speed,
            )?;
        }
    }
    Ok(())
}

// ── shared ────────────────────────────────────────────────────────────────────────────────────

fn basename(path: &Path) -> String {
    path.file_name()
        .map_or_else(|| path.display().to_string(), |n| n.to_string_lossy().into_owned())
}

fn usage_err(msg: &str) -> ExitCode {
    eprintln!("qwd: {msg}");
    ExitCode::from(2)
}

fn exit_code(had_error: bool) -> ExitCode {
    if had_error {
        ExitCode::FAILURE
    } else {
        ExitCode::SUCCESS
    }
}
