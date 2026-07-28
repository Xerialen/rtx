"""T4 frogbot ladder on a KTX server with frogbots enabled.

The branch build (the same [t3].branch_client binary) plays one 4on4 match per
rung against server-side frogbots at skill 10, 12, 14, 16, 18, 20. The ladder
advances on a win and stops at the first loss or draw; the run is COMPLETE
whenever the observed ladder obeys those rules, regardless of how far it got.

Frogbots cannot be seated over rcon — `botcmd` is a client console command —
so the branch client itself seats them through its control channel (`runcmd
botcmd addbot <skill> <team>`). KTX echoes each bot's skill to the client
console; the runner reads that echo back from the client log as the skill
verification recorded in `skill_verified_by`.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from .runlib import RigLock, RunRecorder, config_path
from .t3 import (
    _IDLE_STATUSES,
    GateError,
    _Side,
    _client_binary,
    _md5_file,
    _movement_check,
    _read_demoinfo,
    _udp_serverinfo,
    _wait_serverinfo,
)

FROG_TEAM = "frog"
BRANCH_TEAM = "brch"
SEATS = 4
# KTX echoes e.g. `skill &cf0010&r` to the seating client: colour code &cf00
# followed by the skill digits, closed by &r.
SKILL_ECHO = re.compile(r"skill &c[0-9a-f]{3}(\d+)&r")


def _frogbot_server(config: dict[str, Any]) -> tuple[str, int]:
    raw = config["t4"]["frogbot_server"]
    host, _, port_text = raw.rpartition(":")
    if not host or not port_text.isdigit():
        raise RuntimeError(f"t4.frogbot_server must be host:port, got {raw!r}")
    return host, int(port_text)


def _preflight(config: dict[str, Any], host: str, port: int) -> dict[str, str]:
    duration = config["t4"]["duration_s"]
    info = _udp_serverinfo(host, port)
    if info.get("status") != "Standby":
        raise RuntimeError(
            f"frogbot server is not in Standby (status={info.get('status')!r})"
        )
    if info.get("mode") != f"{SEATS}on{SEATS}":
        raise RuntimeError(
            f"frogbot server mode is {info.get('mode')!r}; the ladder needs "
            f"{SEATS}on{SEATS}"
        )
    if info.get("timelimit") != str(duration // 60):
        raise RuntimeError(
            f"server timelimit is {info.get('timelimit')!r} but "
            f"t4.duration_s={duration} requires {duration // 60}"
        )
    return info


def _player_rows(host: str, port: int) -> int:
    sock_reply = None
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(3.0)
        sock.sendto(b"\xff\xff\xff\xffstatus\n", (host, port))
        sock_reply = sock.recvfrom(8192)[0].decode("latin1")
    finally:
        sock.close()
    rows = [
        line
        for line in sock_reply.split("\n")[1:]
        if line.strip() and line.strip() != "\x00"
    ]
    return len(rows)


def _verify_skill_echoes(log_path: Path, skill: int, offset: int = 0) -> None:
    """Every seated frogbot's skill echo must match this rung's skill.

    `offset` skips log content from earlier rungs — the seater's log
    accumulates across the whole ladder.
    """
    try:
        with open(log_path, encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            text = handle.read()
    except OSError as exc:
        raise RuntimeError(f"cannot read seater log for skill verification: {exc}")
    echoed = [int(value) for value in SKILL_ECHO.findall(text)]
    matching = [value for value in echoed if value == skill]
    wrong = [value for value in echoed if value != skill]
    if wrong:
        raise RuntimeError(
            f"frogbot skill echoes {wrong} do not match the rung skill {skill}"
        )
    if len(matching) < SEATS:
        raise RuntimeError(
            f"only {len(matching)}/{SEATS} frogbot skill echoes for skill {skill}"
        )


class _Seater:
    """A spectator client that seats and removes frogbots.

    Frogbots must be seated before the players arrive: `botcmd addbot` during
    a countdown or match is accepted but the bot never enters. Seating them
    from a playing client is impossible — the auto-ready players trigger the
    countdown the moment the squad is complete. A spectator triggers nothing,
    stays across all rungs, and its console receives KTX's per-bot skill
    echoes, which is the ladder's skill verification.
    """

    def __init__(self, binary: Path, control_port: int, log_path: Path):
        self.binary = binary
        self.control_port = control_port
        self.log_path = log_path
        self.process: Any = None
        self.control: Any = None
        self.log = None

    def launch(self, server: str, basedir: str, soak_s: int) -> None:
        import subprocess

        self.log = open(self.log_path, "w", encoding="utf-8")
        self.process = subprocess.Popen(
            [
                str(self.binary),
                "--server", server,
                "--basedir", basedir,
                "--bots", "1",
                "--spectate",
                "--name", "t4seat",
                "--control-port", str(self.control_port),
                "--soak", str(soak_s),
            ],
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        from .control import Control

        deadline = time.monotonic() + 30.0
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("seater client exited during startup")
            try:
                self.control = Control(
                    "127.0.0.1", self.control_port, timeout=10.0
                )
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.5)
        raise RuntimeError(f"seater control port never opened: {last_error}")

    def command(self, raw: str) -> None:
        self.control.request(f"runcmd {raw}", timeout=8.0)

    def log_size(self) -> int:
        try:
            return self.log_path.stat().st_size
        except OSError:
            return 0

    def shutdown(self) -> None:
        if self.control is not None:
            try:
                self.control.close()
            except Exception:
                pass
            self.control = None
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(10)
            except Exception:
                self.process.kill()
        if self.log is not None:
            self.log.close()
            self.log = None


def _play_rung(
    config: dict[str, Any],
    host: str,
    port: int,
    skill: int,
    binary: Path,
    evidence_dir: Path,
    demo_dir: Path | None,
    seater: _Seater,
) -> dict[str, Any]:
    t4 = config["t4"]
    duration = t4["duration_s"]
    side = _Side("branch", binary, t4["control_port"])
    started_wallclock = time.time()
    log_offset = seater.log_size()
    try:
        # Frogbots first. Their ready-up starts the pre-match countdown, and
        # that countdown is the only window in which the players can join and
        # still get a full clock — so the player launch follows immediately.
        seater.command("botcmd enable")
        time.sleep(0.5)
        seater.command("botcmd removeall")
        time.sleep(1.0)
        for _ in range(SEATS):
            seater.command(f"botcmd addbot {skill} {FROG_TEAM}")
            time.sleep(0.4)
        # No waiting here: the frogbots' ready-up already started the 20 s
        # countdown, and the players must join inside it. Both sides finish
        # entering within a few seconds; the roster is verified after start.
        side.launch(
            f"{host}:{port}",
            config["t3"]["basedir"],
            SEATS,
            duration + 300,
            evidence_dir,
            log_prefix="t4",
        )
        side.connect(time.monotonic() + 15.0)
        _wait_serverinfo(host, port, until_running=True, timeout_s=120.0)
        rows = _player_rows(host, port)
        if rows < 2 * SEATS:
            raise RuntimeError(
                f"match started with {rows} players, not {2 * SEATS} — the "
                "players missed the countdown window"
            )
        _movement_check([side], SEATS)
        hard_stop = time.monotonic() + duration + 180.0
        while time.monotonic() < hard_stop:
            try:
                if _udp_serverinfo(host, port).get("status") in _IDLE_STATUSES:
                    break
            except (OSError, RuntimeError):
                pass
            time.sleep(2.0)
        else:
            raise RuntimeError(f"rung {skill} did not finish in {duration + 180}s")
        if side.process is None or side.process.poll() is not None:
            raise RuntimeError(
                f"rung {skill}: client process died before the match ended"
            )
        time.sleep(3.0)  # let KTX finish the MVD and demoinfo embed
        _verify_skill_echoes(seater.log_path, skill, offset=log_offset)
        try:
            seater.command("botcmd removeall")
            time.sleep(1.0)
        except Exception:
            pass  # the next rung's removeall covers it
        demoinfo = _read_demoinfo(demo_dir, started_wallclock)
        if demoinfo is None:
            raise RuntimeError(
                f"rung {skill}: no KTX demoinfo found — the ladder has no other "
                "score oracle"
            )
        frags_by_team, mvd = demoinfo
        frags_for = frags_by_team.get(BRANCH_TEAM)
        frags_against = frags_by_team.get(FROG_TEAM)
        if frags_for is None or frags_against is None:
            raise RuntimeError(
                f"rung {skill}: demoinfo teams {sorted(frags_by_team)} do not "
                f"cover {BRANCH_TEAM} and {FROG_TEAM}"
            )
    finally:
        side.shutdown()
    rung = {
        "skill": skill,
        "frags_for": frags_for,
        "frags_against": frags_against,
        "win": frags_for > frags_against,
        "mvd": mvd,
    }
    if frags_for == frags_against:
        rung["draw"] = True
    return rung


def run(config: dict[str, Any]) -> Path:
    t4 = config["t4"]
    skills = t4["skills"]
    host, port = _frogbot_server(config)
    binary = _client_binary(config, "branch")
    digest = _md5_file(binary)
    evidence_dir = config_path(config, config["paths"]["evidence_dir"])
    evidence_dir.mkdir(parents=True, exist_ok=True)
    demo_dir_value = t4.get("demoinfo_dir", "")
    demo_dir = config_path(config, demo_dir_value) if demo_dir_value else None
    with RigLock(port):
        serverinfo = _preflight(config, host, port)
        map_name = serverinfo.get("map", "unknown")
        with RunRecorder(
            config,
            "T4",
            map_name,
            server_status={"digest_md5": digest},
        ) as recorder:
            seater = _Seater(
                binary,
                t4["control_port"] + 1,
                evidence_dir / "t4-seater.log",
            )
            seater.launch(
                f"{host}:{port}",
                config["t3"]["basedir"],
                len(skills) * (t4["duration_s"] + 300) + 300,
            )
            ladder: list[dict[str, Any]] = []
            reached = 0
            try:
                for skill in skills:
                    # A failed readiness gate is not a played match: the bots
                    # never became a valid squad (the branch has a known
                    # goalless-idle failure right after match start), so the
                    # rung is retried on a fresh match rather than recorded.
                    for attempt in range(3):
                        # An abandoned match (gate failure kills the
                        # players) can run its full clock before Standby.
                        _wait_serverinfo(
                            host, port, until_running=False, timeout_s=420.0
                        )
                        try:
                            rung = _play_rung(
                                config,
                                host,
                                port,
                                skill,
                                binary,
                                evidence_dir,
                                demo_dir,
                                seater,
                            )
                            break
                        except GateError as gate:
                            print(
                                f"rung {skill} attempt {attempt + 1}: {gate} "
                                "— retrying on a fresh match",
                                flush=True,
                            )
                    else:
                        raise GateError(
                            f"rung {skill}: readiness gate failed on three "
                            "consecutive matches"
                        )
                    ladder.append(rung)
                    print(
                        f"rung {skill}: {rung['frags_for']} vs "
                        f"{rung['frags_against']} — "
                        + (
                            "WIN"
                            if rung["win"]
                            else "draw"
                            if rung.get("draw")
                            else "loss"
                        ),
                        flush=True,
                    )
                    if rung["win"]:
                        reached = skill
                    else:
                        break
            finally:
                seater.shutdown()
            recorder.payload = {
                "duration_s_per_match": t4["duration_s"],
                "ladder": ladder,
                "reached": reached,
                "skill_verified_by": "client console skill echo (KTX addbot)",
                "verdict": "COMPLETE",
            }
    return recorder.path
