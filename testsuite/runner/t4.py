"""T4 frogbot ladder on a KTX server with frogbots enabled.

The branch build (the same [t3].branch_client binary) plays one 4on4 match per
rung against server-side frogbots at skill 10, 12, 14, 16, 18, 20. The ladder
advances on a win and stops at the first loss or draw.

How far it climbs is a sporting result. The verdict is about behaviour: did our
bots shoot at the enemy, chase items, avoid shooting each other, and keep
moving. Four measurement paths answer that (`t4_dom.T4_CAPABILITIES`), four
gates judge it, and a field that could not be measured makes the run OMÄTT
rather than quietly green. `t4_dom` owns the vocabulary and the arithmetic;
this module owns the measuring.

Frogbots cannot be seated over rcon — `botcmd` is a client console command —
so the branch client itself seats them through its control channel (`runcmd
botcmd addbot <skill> <team>`). KTX echoes each bot's skill to the client
console; the runner reads that echo back from the client log as the skill
verification recorded in `skill_verified_by`.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from . import combat_lock as combat_lock_mod
from . import evidence as evidence_mod
from . import t4_dom
from .control import ControlError
from .runlib import (
    RigLifecycle,
    RigLock,
    RunRecorder,
    config_path,
    utc_text,
    wait_for_demo_flush,
)
from .t3 import (
    _IDLE_STATUSES,
    GateError,
    _Side,
    _client_binary,
    _md5_file,
    _movement_check,
    _read_demoinfo,
    _read_demoinfo_document,
    newest_demoinfo,
    _udp_serverinfo,
    _wait_serverinfo,
)

FROG_TEAM = "frog"
#: Our side's row on the match card. The validator recounts the teamkill
#: derivation off the same row, so the name lives in `t4_dom`.
BRANCH_TEAM = t4_dom.BRANCH_TEAM
SEATS = 4
#: The accumulation window T4 hands its side channel. It has to be wider than
#: T4's own sampling period or `_Side` counts no stillness at all — the
#: 2026-08-24 failure, where a bot that never moved measured 0.0 s. Named so a
#: unit can assert the relation instead of trusting the call site.
SIDE_SAMPLE_WINDOW_S = t4_dom.STILL_SAMPLE_GAP_MAX_S
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


class _StillWatch:
    """The live side channel, sampled on a fixed period, with its own receipt.

    T4's match loop never sampled anything before this: it polled the server's
    status until the clock ran out (`utmaning-t4-spec.md` §3). `_Side.sample()`
    has always existed and T3 has always called it — the stillness gate needs
    it called here too, and needs to be able to say when it was not.

    The gap is the whole point. A sample that lands 4 s after the previous one
    did not measure the second in between, and interpolating over it would
    invent the very thing the gate judges. A gap over the ceiling makes
    `t4:still_s` unavailable for the run; it never becomes a smaller number.
    """

    def __init__(
        self,
        interval_s: float = t4_dom.STILL_SAMPLE_INTERVAL_S,
        gap_max_s: float = t4_dom.STILL_SAMPLE_GAP_MAX_S,
    ):
        self.interval_s = interval_s
        self.gap_max_s = gap_max_s
        self.samples = 0
        self.misses = 0
        self.gap_max_seen: float | None = None
        self._last_ok: float | None = None
        self._due: float | None = None

    def maybe_sample(self, side: Any, now: float) -> None:
        if self._due is not None and now < self._due:
            return
        self._due = now + self.interval_s
        before = side.polls
        side.sample(now)
        if side.polls == before:
            # `_Side.sample` swallows a dead control channel and returns; the
            # poll counter is the only way to tell a sample from a silence.
            self.misses += 1
            return
        if self._last_ok is not None:
            gap = now - self._last_ok
            self.gap_max_seen = (
                gap if self.gap_max_seen is None else max(self.gap_max_seen, gap)
            )
        self._last_ok = now
        self.samples += 1

    def measured(self, side: Any) -> bool:
        """Whether the number the side accumulated is a measurement at all.

        The third condition is the one 2026-08-24 taught: `_Side` only counts
        a stretch of stillness when two polls are closer together than its own
        accumulation window, so a watch that samples slower than that window
        measures nothing and reports 0.0 — the best possible value — for a bot
        that stood still the whole match. The gap instrument saw nothing wrong,
        because sampling *happened*; it just did not measure. A blind
        instrument has to say `unavailable`, not "green".
        """
        window = getattr(side, "sample_window_s", 0.0)
        if window <= self.interval_s:
            return False
        if self.samples < 2 or self.gap_max_seen is None:
            return False
        return self.gap_max_seen <= self.gap_max_s

    def still_s_per_bot(self, side: Any) -> float | None:
        if not self.measured(side):
            return None
        return round(side.still_s / max(1, side.bots_seen), 1)


def _item_key(item: Any, index: int) -> str | None:
    """A stable identity for one world item across polls.

    The reply carries a classname and, on the builds that have it, an origin.
    Several copies of the same classname exist on dm3, so the classname alone
    would merge them and hide takes; the position separates them. Without a
    position the list index is the only identity available, and it is used as
    such — an item the reply cannot identify is not counted.
    """
    if not isinstance(item, dict):
        return None
    name = None
    for key in ("classname", "name", "kind", "item"):
        value = item.get(key)
        if isinstance(value, str) and value:
            name = value.lower()
            break
    if name is None:
        return None
    origin = item.get("origin") or item.get("pos")
    if isinstance(origin, (list, tuple)) and len(origin) >= 3:
        try:
            return f"{name}@{float(origin[0]):.0f},{float(origin[1]):.0f},{float(origin[2]):.0f}"
        except (TypeError, ValueError):
            pass
    return f"{name}#{index}"


class _ItemWatch:
    """Item takes, counted the way T2 counts a powerup take.

    T2 watches quad and pent through the `items` control reply and reads a
    take as an available -> unavailable edge (`t2._observe_powerups`). The same
    edge over every item the reply identifies is the only pickup signal a live
    T4 has, and it is a **proxy**: the world channel says an item was taken,
    never by whom. Every reported (d) outcome carries `item-pickups-proxy` for
    exactly that reason.

    An item that is already gone at the first look is not a take we saw, so the
    first observation only seeds the state — the same left-censoring rule T2
    applies to a lay interval it joined in the middle.
    """

    def __init__(
        self,
        poll_s: float = t4_dom.ITEMS_POLL_S,
        gap_max_s: float = t4_dom.ITEMS_POLL_GAP_MAX_S,
    ):
        self.poll_s = poll_s
        self.gap_max_s = gap_max_s
        self.takes = 0
        self.polls = 0
        self.misses = 0
        self.gap_max_seen: float | None = None
        self._state: dict[str, bool] = {}
        self._last_ok: float | None = None
        self._due: float | None = None

    def observe(self, items: Any, now: float) -> None:
        if not isinstance(items, list):
            self.misses += 1
            return
        seen = False
        for index, item in enumerate(items):
            if not isinstance(item, dict) or not isinstance(item.get("available"), bool):
                continue
            key = _item_key(item, index)
            if key is None:
                continue
            seen = True
            available = item["available"]
            previous = self._state.get(key)
            if previous is True and available is False:
                self.takes += 1
            self._state[key] = available
        if not seen:
            self.misses += 1
            return
        if self._last_ok is not None:
            gap = now - self._last_ok
            self.gap_max_seen = (
                gap if self.gap_max_seen is None else max(self.gap_max_seen, gap)
            )
        self._last_ok = now
        self.polls += 1

    def maybe_poll(self, side: Any, now: float) -> None:
        if self._due is not None and now < self._due:
            return
        self._due = now + self.poll_s
        if side.control is None:
            self.misses += 1
            return
        try:
            reply = side.control.request("items", timeout=8.0)["data"]
        except (ControlError, OSError, KeyError, TypeError):
            self.misses += 1
            return
        self.observe(reply, now)

    def measured(self) -> bool:
        if self.polls < 2 or self.gap_max_seen is None:
            return False
        return self.gap_max_seen <= self.gap_max_s

    def tracked(self) -> int:
        """How many distinct items the channel could identify at all.

        No gate reads this. It decides whether gate (d) means anything: 46 of
        51 ten-minute T2 runs recorded zero quad+pent takes, so a channel that
        only ever sees those two powerups would make `item_pickups == 0` the
        normal reading instead of the alarm. The number rides along in the
        envelope so the first live ladder settles the question.
        """
        return len(self._state)


def _full_view(analyzer_path: Path, mvd_path: Path) -> dict[str, Any] | None:
    """The analyzer's full view of one match, or None when it cannot be had.

    Same best-effort contract as `t3._combat_lock` and `evidence.match_scoreboard`:
    no analyzer, no demo, or a demo the analyzer will not parse simply means no
    document. The caller turns that into `t4:shots_fired` unavailable — never
    into a shot count of zero.
    """
    if not analyzer_path.is_file() or not mvd_path.is_file():
        return None
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
        return json.loads(completed.stdout)
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        print(f"t4 shot count skipped: {exc}", flush=True)
        return None


def _rung_shots(
    config: dict[str, Any], demo_dir: Path | None, mvd_name: str
) -> int | None:
    analyzer = config.get("tools", {}).get("qw_analyze", "")
    if not analyzer or demo_dir is None or not mvd_name:
        return None
    document = _full_view(config_path(config, analyzer), demo_dir / mvd_name)
    if document is None:
        return None
    return combat_lock_mod.team_shots(document, BRANCH_TEAM)


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
    """
    source = newest_demoinfo(demo_dir, started_wallclock)
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


def cross_alarm(evidence_dir: Path, commit: str, started_utc: str) -> str:
    """The T1/T3 run that should have caught this, best effort (§6).

    There is no session id binding a battery together: `run_id` is
    `{tier}-{stamp}-{commit8}` and nothing else (`runlib.py`). The nearest
    preceding T1/T3 run of the same commit is therefore a heuristic label, not
    a proven link, and when there is none the envelope says so in words rather
    than leaving the field to be read as "nothing was wrong upstream".
    """
    best: tuple[str, str] | None = None
    for path in sorted(evidence_dir.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(document, dict) or document.get("tier") not in {"T1", "T3"}:
            continue
        build = document.get("build")
        if not isinstance(build, dict) or build.get("commit") != commit:
            continue
        started = document.get("started_utc")
        run_id = document.get("run_id")
        if not isinstance(started, str) or not isinstance(run_id, str):
            continue
        if started > started_utc:
            continue
        if best is None or started > best[0]:
            best = (started, run_id)
    return best[1] if best is not None else t4_dom.NO_CROSS_ALARM


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
    map_name: str,
    demos_dir: Path,
) -> dict[str, Any]:
    t4 = config["t4"]
    duration = t4["duration_s"]
    # The window has to cover T4's own sampling period or `still_s` measures
    # nothing at all and reports 0.0 — the best possible value — for a bot that
    # never moved (QA, 2026-08-24, punkt 3). The gap ceiling is already the
    # contract for how far apart two samples may be, so it is the window too.
    side = _Side(
        "branch",
        binary,
        t4["control_port"],
        sample_window_s=SIDE_SAMPLE_WINDOW_S,
    )
    still_watch = _StillWatch()
    item_watch = _ItemWatch()
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
        # The loop now measures while it waits. The server poll keeps its own
        # 2 s cadence (it decides when the match is over); the two live
        # channels run on their own periods beside it, because a stillness
        # number sampled every 2 s is a different number than one sampled
        # every second, and the gate was calibrated on the latter.
        next_status = 0.0
        while time.monotonic() < hard_stop:
            now = time.monotonic()
            still_watch.maybe_sample(side, now)
            item_watch.maybe_poll(side, now)
            if now >= next_status:
                next_status = now + 2.0
                try:
                    if _udp_serverinfo(host, port).get("status") in _IDLE_STATUSES:
                        break
                except (OSError, RuntimeError):
                    pass
            time.sleep(0.2)
        else:
            raise RuntimeError(f"rung {skill} did not finish in {duration + 180}s")
        if side.process is None or side.process.poll() is not None:
            raise RuntimeError(
                f"rung {skill}: client process died before the match ended"
            )
        # Not a fixed sleep: mvdsv holds the recording in memory and writes it
        # when KTX stops recording. Tearing the rig down before that happens is
        # what produced 14 of 17 zero-byte T4 demos.
        flush = wait_for_demo_flush(demo_dir, started_wallclock)
        _verify_skill_echoes(seater.log_path, skill, offset=log_offset)
        try:
            seater.command("botcmd removeall")
            time.sleep(1.0)
        except Exception:
            pass  # the next rung's removeall covers it
        demoinfo_document = _read_demoinfo_document(demo_dir, started_wallclock)
        demoinfo = _read_demoinfo(
            demo_dir, started_wallclock, document=demoinfo_document
        )
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
    scoreboard = evidence_mod.match_scoreboard(
        config, demo_dir, mvd, map_name, config["t4"]["duration_s"], config_path
    )
    # Primary: the qw-analyze card and the MVD's ammo signal. Both need a demo
    # the server actually wrote.
    # Second source: KTX's own card, which the frag oracle has already read in
    # this same function and which carries the counters outright. Only when the
    # primary produced nothing, so a present qw-analyze card always wins and
    # the validator can keep recounting it.
    teamkills, kills, teamkills_source = t4_dom.pick_teamkills(
        scoreboard, demoinfo_document, BRANCH_TEAM
    )
    shots, shots_source = t4_dom.pick_shots(
        _rung_shots(config, demo_dir, mvd), demoinfo_document, BRANCH_TEAM
    )
    # The card the two KTX readings came from, archived beside the envelope and
    # pinned by digest. Only written when a reading actually came from it.
    card = None
    if t4_dom.SOURCE_KTX_CARD in (shots_source, teamkills_source):
        card = archive_card(demo_dir, started_wallclock, demos_dir)
        if card is None:
            print(
                "KTX card could not be archived; dropping the readings taken "
                "from it rather than reporting numbers with no provenance",
                flush=True,
            )
        reading = t4_dom.drop_unprovenanced(
            {
                "shots": shots,
                "shots_source": shots_source,
                "teamkills": teamkills,
                "kills": kills,
                "teamkills_source": teamkills_source,
            },
            card,
        )
        shots, shots_source = reading["shots"], reading["shots_source"]
        teamkills, kills = reading["teamkills"], reading["kills"]
        teamkills_source = reading["teamkills_source"]
    rung = {
        "skill": skill,
        "frags_for": frags_for,
        "frags_against": frags_against,
        "win": frags_for > frags_against,
        "mvd": mvd,
        "scoreboard": scoreboard,
        # What this match contributed to the four judged fields, beside the
        # match it came from. A ladder number nobody can trace back to a rung
        # is a number nobody can check.
        "sources": {"shots_fired": shots_source, "teamkills": teamkills_source},
        **({"card": card} if card is not None else {}),
        "measured": {
            "shots_fired": shots,
            "teamkills": teamkills,
            "kills": kills,
            "demo_flush_s": flush["waited_s"] if flush["state"] == "flushed" else None,
            "still_s_per_bot": still_watch.still_s_per_bot(side),
            "still_gap_max_s": (
                round(still_watch.gap_max_seen, 3)
                if still_watch.gap_max_seen is not None
                else None
            ),
            "item_takes": item_watch.takes if item_watch.measured() else None,
            "items_poll_gap_max_s": (
                round(item_watch.gap_max_seen, 3)
                if item_watch.gap_max_seen is not None
                else None
            ),
            "items_tracked": item_watch.tracked() or None,
        },
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
    demos_dir = config_path(config, config["paths"]["demos_dir"])
    with RigLock(port), RigLifecycle(t4):
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
                                map_name,
                                demos_dir,
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
            measurements = t4_dom.measure_ladder(ladder)
            outcome = t4_dom.ladder_outcome(ladder)
            if outcome["reached"] != reached:
                # The loop and the recount disagree about what was won. That
                # is a bug in one of them, and guessing which would put an
                # invented number in front of the owner.
                raise RuntimeError(
                    f"reached {reached} from the ladder loop but "
                    f"{outcome['reached']} from the rungs"
                )
            dom = t4_dom.adjudicate(measurements, outcome)
            payload: dict[str, Any] = {
                "t4_schema": t4_dom.T4_SCHEMA,
                "duration_s_per_match": t4["duration_s"],
                "ladder": ladder,
                "reached": outcome["reached"],
                "skill_verified_by": "client console skill echo (KTX addbot)",
                "verdict": dom["verdict"],
                "measurements": measurements,
                "sampling": t4_dom.sampling_receipt(ladder),
                "thresholds": t4_dom.thresholds(),
                "dom": {
                    "failed_gates": dom["failed_gates"],
                    "missing": dom["missing"],
                    "reason": dom["reason"],
                    "labels": (
                        [t4_dom.LABEL_ITEM_PROXY]
                        if measurements["item_pickups"] is not None
                        else []
                    ),
                },
            }
            if dom["verdict"] == "FAIL":
                payload["cross_alarm"] = cross_alarm(
                    evidence_dir,
                    recorder.build["commit"],
                    utc_text(recorder.started),
                )
            if dom["verdict"] == "OAVGJORD":
                payload["draw_semantik"] = t4_dom.DRAW_SEMANTICS
            if dom["missing"]:
                # The same declaration `t1:stall` makes: an absence is named
                # where every other tier names it, or it is silent.
                recorder.capabilities = {
                    "telemetry": True,
                    "unavailable": list(dom["missing"]),
                    "note": (
                        "T4 could not measure "
                        + ", ".join(dom["missing"])
                        + ": the analyzer, the match card or a live channel"
                        " was unavailable for at least one rung"
                    ),
                }
            recorder.payload = payload
    return recorder.path
