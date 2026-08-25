"""T3 branch-vs-reference match on a KTX server.

The runner does not manage the match server: the operator prepares a dedicated
mvdsv+KTX instance whose default usermode matches [t3].seats_per_side and whose
timelimit equals [t3].duration_s — both are verified against the server's
serverinfo before anything launches. The runner only launches the two client
processes, gates readiness, observes, and reads the score back.
"""
from __future__ import annotations

import hashlib
import math
import re
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from . import combat_lock as combat_lock_mod
from . import evidence as evidence_mod
from . import team_damage
from .control import Control
from .runlib import (
    RigLifecycle,
    RigLock,
    RunRecorder,
    config_path,
    select_match_demo,
    wait_for_demo_flush,
)
from .t2 import _mean, _summarize_cells

TEAM_BY_SIDE = {"branch": "brch", "reference": "ref"}
COLORS_BY_SIDE = {"branch": ("4", "4"), "reference": ("13", "13")}
SIDES = ("branch", "reference")


def _udp_serverinfo(host: str, port: int, timeout: float = 3.0) -> dict[str, str]:
    """One QW status query, returning the serverinfo key/value line."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        sock.sendto(b"\xff\xff\xff\xffstatus\n", (host, port))
        data, _ = sock.recvfrom(8192)
    finally:
        sock.close()
    text = data.decode("latin1")
    if not text.startswith("\xff\xff\xff\xffn"):
        raise RuntimeError(f"unexpected status reply from {host}:{port}")
    info_line = text[5:].split("\n", 1)[0]
    tokens = info_line.strip("\\").split("\\")
    return dict(zip(tokens[0::2], tokens[1::2]))


def _match_server(config: dict[str, Any]) -> tuple[str, int]:
    raw = config["t3"]["match_server"]
    host, _, port_text = raw.rpartition(":")
    if not host or not port_text.isdigit():
        raise RuntimeError(f"t3.match_server must be host:port, got {raw!r}")
    return host, int(port_text)


def _md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:8]


def _client_binary(config: dict[str, Any], side: str) -> Path:
    key = "branch_client" if side == "branch" else "reference_client"
    value = config["t3"][key]
    if not value:
        raise RuntimeError(f"t3.{key} is required for T3")
    path = config_path(config, value)
    if not path.is_file():
        raise RuntimeError(f"t3.{key}: {path} does not exist")
    return path


class _Side:
    """One client process plus its control connection and running measurement."""

    #: The longest gap between two polls that still counts as one measured
    #: stretch of a bot's movement. T3's own loop runs at ~4 Hz, so 0.6 s is
    #: 2.4x its period and the default keeps T3's numbers exactly where they
    #: were. It is a parameter because T4 samples at 1.0 s: with the constant
    #: hardcoded at 0.6 every T4 sample fell outside the window, `still_s`
    #: stayed at 0.0 for a match in which a bot never moved, and gate (c)
    #: reported the *best possible* value where the truth was the worst
    #: possible (QA, 2026-08-24, punkt 3). A caller that samples slower than
    #: the window measures nothing at all, so the window has to follow the
    #: caller's period rather than the other way round.
    DEFAULT_SAMPLE_WINDOW_S = 0.6

    def __init__(
        self,
        side: str,
        binary: Path,
        control_port: int,
        sample_window_s: float = DEFAULT_SAMPLE_WINDOW_S,
    ):
        self.side = side
        self.binary = binary
        self.control_port = control_port
        self.sample_window_s = float(sample_window_s)
        self.process: subprocess.Popen | None = None
        self.control: Control | None = None
        self.stalls: list[dict[str, Any]] = []
        self.per_second: list[float] = []
        self.still_s = 0.0
        self.bots_seen = 0
        self.polls = 0
        self.frags: int | None = None
        self._previous: dict[int, tuple[list[float], float]] = {}
        self._accumulator: dict[int, list[float]] = {}
        self._last_second = time.monotonic()
        self._last_telemetry = 0.0

    def launch(
        self,
        server: str,
        basedir: str,
        bots: int,
        soak_s: int,
        log_dir: Path,
        log_prefix: str = "t3",
    ) -> None:
        colors = COLORS_BY_SIDE[self.side]
        command = [
            str(self.binary),
            "--server", server,
            "--basedir", basedir,
            "--bots", str(bots),
            "--team", TEAM_BY_SIDE[self.side],
            # A squad under --name gets numbered labels (brch1..brchN), which
            # keeps every scoreboard name unique across both processes — the
            # MVD analysis attributes damage by name and needs that.
            "--name", TEAM_BY_SIDE[self.side],
            "--colors", colors[0], colors[1],
            # Both sides play at max client skill: identical, and the extra
            # engagements give the MVD analysis (combat lock) real signal.
            "--skill", "7",
            "--control-port", str(self.control_port),
            "--soak", str(soak_s),
        ]
        log_path = log_dir / f"{log_prefix}-{self.side}.log"
        self.log = open(log_path, "w", encoding="utf-8")
        self.process = subprocess.Popen(
            command, stdout=self.log, stderr=subprocess.STDOUT
        )

    def connect(self, deadline: float) -> None:
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise RuntimeError(
                    f"{self.side} client exited before its control port opened "
                    f"(code {self.process.returncode})"
                )
            try:
                self.control = Control("127.0.0.1", self.control_port, timeout=10.0)
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.5)
        raise RuntimeError(
            f"{self.side} client control port {self.control_port} never opened: "
            f"{last_error}"
        )

    def status_bots(self) -> list[dict[str, Any]]:
        assert self.control is not None
        return self.control.request("status", timeout=8.0)["data"].get("bots", [])

    def sample(self, now: float | None = None) -> None:
        """One measurement poll: origins, per-second speed, stillness, stalls.

        `now` is injectable so the caller's clock and this one are the same
        reading — and so the stillness arithmetic can be driven over a whole
        simulated match offline. It was not testable before, which is how a
        sampling period that measured nothing shipped.
        """
        assert self.control is not None
        now = time.monotonic() if now is None else now
        try:
            bots = self.status_bots()
        except Exception:
            return
        self.polls += 1
        alive = [bot for bot in bots if bot.get("alive")]
        self.bots_seen = max(self.bots_seen, len(alive))
        frag_values = [bot.get("frags") for bot in bots]
        if frag_values and all(isinstance(value, int) for value in frag_values):
            self.frags = sum(frag_values)
        for bot in bots:
            entity = int(bot["ent"])
            origin = bot["origin"]
            previous = self._previous.get(entity)
            if bot.get("alive") and previous is not None:
                elapsed = now - previous[1]
                if 0.01 < elapsed < self.sample_window_s:
                    speed = math.hypot(
                        origin[0] - previous[0][0], origin[1] - previous[0][1]
                    ) / elapsed
                    if speed < 1500:
                        accumulator = self._accumulator.setdefault(entity, [0.0, 0])
                        accumulator[0] += speed * elapsed
                        accumulator[1] += 1
                        if speed < 16:
                            self.still_s += elapsed
            self._previous[entity] = (origin, now)
        if now - self._last_second >= 1.0:
            elapsed = now - self._last_second
            for distance, samples in self._accumulator.values():
                if samples:
                    self.per_second.append(float(distance) / elapsed)
            self._accumulator = {}
            self._last_second = now
        if now - self._last_telemetry >= 10.0:
            try:
                self.control.request("set rtx_telemetry 1", timeout=4.0)
            except Exception:
                pass
            self._last_telemetry = now
        for event in self.control.events:
            if event.get("ev") == "bot_stall":
                self.stalls.append(event)
        self.control.events.clear()

    def payload_side(self, build: dict[str, Any]) -> dict[str, Any]:
        if self.frags is None:
            raise RuntimeError(
                f"no frag oracle for side {self.side}: neither KTX demoinfo nor "
                "the client status exposed frags"
            )
        bots = self.bots_seen or 1
        return {
            "side": self.side,
            "build": build,
            "frags": self.frags,
            "stats": {
                "speed_1s": _mean(self.per_second),
                "still_s_per_bot": round(self.still_s / bots, 1),
                "stall_firings": len(self.stalls),
                "bots": self.bots_seen,
                "polls": self.polls,
            },
            "cells": _summarize_cells(self.stalls),
        }

    def shutdown(self) -> None:
        if self.control is not None:
            self.control.close()
            self.control = None
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(10)
            self.process = None
        log = getattr(self, "log", None)
        if log is not None:
            log.close()
            self.log = None


def _preflight_serverinfo(
    config: dict[str, Any], host: str, port: int
) -> dict[str, str]:
    seats = config["t3"]["seats_per_side"]
    duration = config["t3"]["duration_s"]
    info = _udp_serverinfo(host, port)
    if info.get("status") != "Standby":
        raise RuntimeError(
            f"match server is not in Standby (status={info.get('status')!r}); "
            "refuse to start on a busy server"
        )
    expected_mode = f"{seats}on{seats}"
    if info.get("mode") != expected_mode:
        raise RuntimeError(
            f"match server mode is {info.get('mode')!r} but seats_per_side={seats} "
            f"requires {expected_mode!r} — fix the server's default usermode"
        )
    timelimit = info.get("timelimit")
    if timelimit != str(duration // 60):
        raise RuntimeError(
            f"server timelimit is {timelimit!r} but t3.duration_s={duration} "
            f"requires {duration // 60} — fix the usermode timelimit"
        )
    return info


class GateError(RuntimeError):
    """A readiness gate failed — the match never became a valid measurement."""


def _seats_gate(sides: list[_Side], seats: int, timeout_s: float = 60.0) -> None:
    """Every seat must be alive (joined and spawned) before the match may start."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            alive = [
                sum(bool(bot.get("alive")) for bot in side.status_bots())
                for side in sides
            ]
        except Exception:
            time.sleep(1.0)
            continue
        if all(count >= seats for count in alive):
            return
        time.sleep(1.0)
    raise RuntimeError(f"not all {seats}+{seats} seats came alive in {timeout_s}s")


def _movement_check(
    sides: list[_Side], expected: int, window_s: float = 45.0
) -> int:
    """Every seat must move once the match runs.

    Movement cannot be gated before the start: KTX freezes players during the
    pre-match countdown, so the proof window is the first seconds of play. The
    proof is cumulative — a seat counts once it has moved >32u from where it
    was first seen, whenever in the window that happens — since a healthy bot
    may still idle through any single short sampling slice.
    """
    first_seen: dict[tuple[str, int], list[float]] = {}
    moved: set[tuple[str, int]] = set()
    last: dict[tuple[str, int], tuple[list[float], bool]] = {}
    deadline = time.monotonic() + window_s
    while time.monotonic() < deadline:
        for side in sides:
            try:
                bots = side.status_bots()
            except Exception:
                continue
            for bot in bots:
                key = (side.side, int(bot["ent"]))
                last[key] = (bot.get("origin"), bool(bot.get("alive")))
                if not bot.get("alive"):
                    continue
                if key not in first_seen:
                    first_seen[key] = bot["origin"]
                elif key not in moved:
                    if math.dist(bot["origin"], first_seen[key]) > 32:
                        moved.add(key)
        if len(moved) >= expected:
            return len(moved)
        time.sleep(1.0)
    detail = "; ".join(
        f"{side_name}/ent{ent}: "
        + (
            "never seen"
            if (side_name, ent) not in first_seen
            else f"alive={alive} at {origin} from {first_seen[(side_name, ent)]}"
        )
        for (side_name, ent), (origin, alive) in sorted(last.items())
        if (side_name, ent) not in moved
    )
    raise GateError(
        f"movement gate failed: only {len(moved)}/{expected} seats moved >32u "
        f"within {window_s:.0f}s of match start [{detail}]"
    )


# The serverinfo status walks Standby -> Countdown -> "<n> min left" -> Standby.
# Players are frozen until the countdown ends, so "match running" specifically
# means neither of the first two.
_IDLE_STATUSES = {"Standby", "Countdown"}


def _wait_serverinfo(
    host: str,
    port: int,
    *,
    until_running: bool,
    timeout_s: float,
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            status = _udp_serverinfo(host, port).get("status")
        except (OSError, RuntimeError):
            status = None
        if status is not None:
            running = status not in _IDLE_STATUSES
            if running == until_running:
                return
        time.sleep(2.0)
    state = "start the match" if until_running else "return to Standby"
    raise RuntimeError(f"match server did not {state} within {timeout_s:.0f}s")


def _combat_lock(
    config: dict[str, Any], mvd_name: str
) -> dict[str, Any] | None:
    """Analyze the match MVD for per-side combat lock, or None if unable.

    The result stays null (per the contract) rather than failing the run when
    the analyzer or the demo is unavailable — the score and movement stats are
    already measured; this is an enrichment pass.
    """
    analyzer = config.get("tools", {}).get("qw_analyze", "")
    demo_dir = config["t3"].get("demoinfo_dir", "")
    if not analyzer or not demo_dir or not mvd_name:
        return None
    analyzer_path = config_path(config, analyzer)
    mvd_path = config_path(config, demo_dir) / mvd_name
    if not analyzer_path.is_file() or not mvd_path.is_file():
        return None
    # mvdfinish keeps flushing for several seconds after the match ends; an
    # MVD read too early parses as a demo with no players. Wait until the file
    # size holds still.
    last_size = -1
    for _ in range(15):
        size = mvd_path.stat().st_size
        if size > 0 and size == last_size:
            break
        last_size = size
        time.sleep(2.0)
    import json

    try:
        completed = subprocess.run(
            [
                str(analyzer_path),
                "-view", "full",
                "-include", "positions,view",
                str(mvd_path),
            ],
            capture_output=True,
            timeout=300,
            check=True,
        )
        document = json.loads(completed.stdout)
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        print(f"combat lock skipped: {exc}", flush=True)
        return None
    by_team = combat_lock_mod.per_team_s_per_bot(document)
    s_per_bot = {
        side: by_team.get(team)
        for side, team in TEAM_BY_SIDE.items()
    }
    if any(value is None for value in s_per_bot.values()):
        print(
            f"combat lock skipped: teams {sorted(by_team)} in the demo do not "
            f"cover {sorted(TEAM_BY_SIDE.values())}",
            flush=True,
        )
        return None
    return {
        "s_per_bot": s_per_bot,
        "source": "qw-analyze",
        "version": _md5_file(analyzer_path),
    }


def match_demoinfo(demo_dir: Path | None, started_wallclock: float) -> Path | None:
    """The KTX card file this match produced, or None.

    Public because T4 needs the *path* as well as the contents: a measurement
    read out of this card is only checkable if the card it came from is
    archived beside the envelope and named there (Sol, 2026-08-24). One
    chooser, so the number and its provenance can never point at two files.

    Was `newest_demoinfo` until 2026-08-25, and picking the newest card is the
    same mistake `select_match_demo` was written for: KTX writes a second card
    for the recording it opens after the match. The chooser is now shared with
    the demo flush wait, so the card, the MVD and the wait can never disagree
    about which match they are talking about.
    """
    if demo_dir is None:
        return None
    try:
        candidates = [
            path
            for path in demo_dir.glob("*.txt")
            if path.stat().st_mtime >= started_wallclock - 5
        ]
        return select_match_demo(candidates, started_wallclock)
    except OSError:
        return None


def archive_card(
    demo_dir: Path | None, started_wallclock: float, demos_dir: Path
) -> dict[str, Any] | None:
    """Copy this match's KTX card beside the envelope and pin its sha256.

    A number read out of the card is only auditable if the card is still there
    to be read (Sol, 2026-08-24). The runner therefore archives it into the
    same `evidence/demos/` the MVDs go to and records the relative path plus
    the digest of the bytes it wrote — the validator resolves exactly that path
    and recounts the number out of exactly those bytes.

    The source file is chosen by the same function that produced the numbers,
    so the provenance can never point at a different card than the reading.

    Lives beside `match_demoinfo` rather than in `t4.py` since 2026-08-25:
    T3's K2 team-damage gate reads the same card and must archive it the same
    way, and two copies of "how a card is archived" is how the two tiers would
    start pinning different bytes. `t4.py` re-exports it, so its callers are
    unchanged.
    """
    source = match_demoinfo(demo_dir, started_wallclock)
    if source is None:
        return None
    try:
        raw = source.read_bytes()
        demos_dir.mkdir(parents=True, exist_ok=True)
        target = demos_dir / source.name
        target.write_bytes(raw)
    except OSError as exc:
        print(f"KTX card not archived: {exc}", flush=True)
        return None
    return {
        "path": f"{demos_dir.name}/{source.name}",
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _read_demoinfo_document(
    demo_dir: Path | None, started_wallclock: float
) -> dict[str, Any] | None:
    """KTX's own card for the match that just ended, parsed, or None.

    Split out of `_read_demoinfo` because the card carries more than the frag
    oracle ever read out of it: per-player `stats.kills`, `stats.tk` and
    `weapons.<w>.acc.attacks`. T4 reads those as a second measurement source
    when the MVD is missing or empty (spec addendum to v6 §3). Both callers
    must see the same file, so the choosing happens once, here.
    """
    card = match_demoinfo(demo_dir, started_wallclock)
    if card is None:
        return None
    import json

    try:
        document = json.loads(card.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def _read_demoinfo(
    demo_dir: Path | None,
    started_wallclock: float,
    document: dict[str, Any] | None = None,
) -> tuple[dict[str, int], str] | None:
    if document is None:
        document = _read_demoinfo_document(demo_dir, started_wallclock)
    if document is None:
        return None
    frags_by_team: dict[str, int] = {}
    for player in document.get("players", []):
        team = str(player.get("team", ""))
        stats = player.get("stats", {})
        if isinstance(stats, dict) and isinstance(stats.get("frags"), int):
            frags_by_team[team] = frags_by_team.get(team, 0) + stats["frags"]
    demo = document.get("demo")
    return frags_by_team, demo if isinstance(demo, str) else ""


def run(config: dict[str, Any]) -> Path:
    t3 = config["t3"]
    duration = t3["duration_s"]
    seats = t3["seats_per_side"]
    host, port = _match_server(config)
    reference_commit = t3["reference_commit"]
    if not re.fullmatch(r"[0-9a-f]{40}", reference_commit):
        raise RuntimeError(
            "t3.reference_commit must be the full 40-character commit the "
            "reference client was built from"
        )
    binaries = {side: _client_binary(config, side) for side in SIDES}
    digests = {side: _md5_file(binaries[side]) for side in SIDES}
    port_base = t3["control_port_base"]
    evidence_dir = config_path(config, config["paths"]["evidence_dir"])
    evidence_dir.mkdir(parents=True, exist_ok=True)
    # Where K2's card is archived, beside the MVDs and beside the envelope.
    demos_dir = config_path(config, config["paths"]["demos_dir"])
    with RigLock(port), RigLifecycle(t3):
        serverinfo = _preflight_serverinfo(config, host, port)
        map_name = serverinfo.get("map", "unknown")
        with RunRecorder(
            config,
            "T3",
            map_name,
            server_status={"digest_md5": digests["branch"]},
        ) as recorder:
            side_builds = {
                "branch": dict(recorder.build),
                "reference": {
                    "branch": t3["reference_branch"],
                    "commit": reference_commit,
                    "digest_md5": digests["reference"],
                    "dirty": False,
                },
            }
            sides = [
                _Side("branch", binaries["branch"], port_base),
                _Side("reference", binaries["reference"], port_base + 1),
            ]
            started_wallclock = time.time()
            soak = duration + 240
            try:
                for side in sides:
                    side.launch(
                        f"{host}:{port}", t3["basedir"], seats, soak, evidence_dir
                    )
                connect_deadline = time.monotonic() + 30.0
                for side in sides:
                    side.connect(connect_deadline)
                _seats_gate(sides, seats)
                _wait_serverinfo(
                    host, port, until_running=True, timeout_s=120.0
                )
                match_began = time.monotonic()
                seats_ok = _movement_check(sides, 2 * seats)
                readiness = {
                    "seats_ok": seats_ok,
                    "gate": "status+movement",
                    "passed": True,
                }
                hard_stop = match_began + duration + 180.0
                last_lifecycle = 0.0
                ended = False
                while time.monotonic() < hard_stop:
                    cycle_began = time.monotonic()
                    for side in sides:
                        side.sample()
                    if cycle_began - last_lifecycle >= 2.0:
                        last_lifecycle = cycle_began
                        try:
                            status = _udp_serverinfo(host, port).get("status")
                            if status in _IDLE_STATUSES:
                                ended = True
                                break
                        except (OSError, RuntimeError):
                            pass
                    time.sleep(max(0.0, 0.25 - (time.monotonic() - cycle_began)))
                if not ended:
                    raise RuntimeError(
                        f"match did not finish within {duration + 180:.0f}s"
                    )
                for side in sides:
                    if side.process is None or side.process.poll() is not None:
                        raise RuntimeError(
                            f"{side.side} client process died before the match "
                            "ended — the score does not cover the full match"
                        )
                # mvdsv holds the recording in memory and writes it when KTX
                # stops recording; a fixed sleep let the teardown kill 19 of 53
                # T3 demos mid-cache. Wait for the file, bounded.
                demo_dir_value = t3.get("demoinfo_dir", "")
                demo_dir = (
                    config_path(config, demo_dir_value) if demo_dir_value else None
                )
                wait_for_demo_flush(demo_dir, started_wallclock)
                oracle = "control-status"
                mvd = ""
                demoinfo_document = _read_demoinfo_document(
                    demo_dir, started_wallclock
                )
                demoinfo = _read_demoinfo(
                    demo_dir, started_wallclock, document=demoinfo_document
                )
                if demoinfo is not None:
                    frags_by_team, mvd = demoinfo
                    for side in sides:
                        team_frags = frags_by_team.get(TEAM_BY_SIDE[side.side])
                        if team_frags is not None:
                            side.frags = team_frags
                    oracle = "ktx-demoinfo"
                lock = _combat_lock(config, mvd)
                card = evidence_mod.match_scoreboard(
                    config,
                    demo_dir,
                    mvd,
                    map_name,
                    duration,
                    config_path,
                )
                # K2 (v7 §B): the team-damage quota, off the server's own count.
                # The card is archived beside the envelope first — a quota the
                # validator cannot recount out of pinned bytes is unavailable,
                # never a number, and `team_damage.block` drops the reading when
                # the archiving failed.
                k2_card = (
                    archive_card(demo_dir, started_wallclock, demos_dir)
                    if demoinfo_document is not None
                    else None
                )
                if demoinfo_document is not None and k2_card is None:
                    print(
                        "KTX card could not be archived; K2 goes unavailable "
                        "rather than reporting a quota with no provenance",
                        flush=True,
                    )
                k2 = team_damage.block(
                    demoinfo_document if k2_card is not None else None,
                    k2_card,
                    TEAM_BY_SIDE["branch"],
                )
                if k2["verdict"] == "OMÄTT":
                    # The same declaration `t1:stall` makes: an absence is
                    # named where every other tier names it, or it is silent.
                    recorder.capabilities = {
                        "telemetry": True,
                        "unavailable": [team_damage.CAP_TEAM_DAMAGE],
                        "note": (
                            "K2 could not be measured: "
                            + k2["reason"]
                        ),
                    }
                payload_sides = [
                    side.payload_side(side_builds[side.side]) for side in sides
                ]
                frags = {item["side"]: item["frags"] for item in payload_sides}
                diff = frags["branch"] - frags["reference"]
                recorder.payload = {
                    "t3_schema": team_damage.T3_SCHEMA,
                    "duration_s": duration,
                    "sides": payload_sides,
                    "result": {
                        "diff": diff,
                        "winner": "branch"
                        if diff > 0
                        else "reference"
                        if diff < 0
                        else "draw",
                        "oracle": oracle,
                        "mvd": mvd,
                    },
                    "readiness": readiness,
                    "scoreboard": card,
                    "combat_lock": lock,
                    "team_damage": k2,
                    "replicate_of": None,
                    "verdict": "PIPELINE-OK",
                }
            finally:
                # Stagger the teardown: dropping eight clients in the same
                # server frame mid-recording has crashed mvdsv (SZ_GetSpace
                # overflow during mvdfinish).
                for side in sides:
                    side.shutdown()
                    time.sleep(2.0)
    return recorder.path
