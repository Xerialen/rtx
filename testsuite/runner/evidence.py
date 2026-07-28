"""Demo evidence: server-side recording, player identity, and POV deep links.

A measurement nobody can look at is hard to trust, so every tier that can
record its own match does: the runner asks the server to record an MVD, keeps
the demo clock, and emits deep links that open the hub demo player at the
moment in question, locked to the bot the number is about.

Links are host-relative (`/demo-player/?...`) so the same evidence file works
on the LAN hub, over Tailscale, and behind the Discord-gated Worker.
"""
from __future__ import annotations

from pathlib import Path
import re
import struct
import time
from typing import Any
from urllib.parse import quote

from .control import Control, ControlError

# Seconds of run-up shown before the moment being evidenced.
LEAD_S = 3.0

SVC_UPDATEUSERINFO = 0x28


# The engine's own readable-character table for the glyph range, so a name
# written in fun chars comes back as the scoreboard spells it (bot.brch1, not
# botbrch1 — the dot is glyph 5).
_GLYPHS = [
    ".", "_", "_", "_", "_", ".", "_", "_",
    "_", "_", "_", "_", "_", ">", ".", ".",
    "[", "]", "0", "1", "2", "3", "4", "5",
    "6", "7", "8", "9", ".", "<", "=", ">",
]


def _readable(raw: bytes) -> str:
    """Render a QuakeWorld player name as plain text."""
    out = []
    for byte in raw:
        byte &= 0x7F
        if byte < 0x20:
            out.append(_GLYPHS[byte] if byte < len(_GLYPHS) else "_")
            continue
        out.append(chr(byte))
    return "".join(out).strip()


def players(mvd_path: Path) -> list[dict[str, Any]]:
    """Read name, FTE userid and slot from the MVD's svc_updateuserinfo records.

    The demo player locks a POV by userid, not by name, and no analyzer
    endpoint exposes it — so the demo itself is the source.
    """
    try:
        data = mvd_path.read_bytes()
    except OSError:
        return []
    found: dict[str, dict[str, Any]] = {}
    offset = 0
    while True:
        offset = data.find(bytes([SVC_UPDATEUSERINFO]), offset + 1)
        if offset < 0 or offset + 7 > len(data):
            break
        slot = data[offset + 1]
        userid = struct.unpack_from("<i", data, offset + 2)[0]
        if not (0 <= slot < 32 and 0 < userid < 100_000):
            continue
        if data[offset + 6 : offset + 7] != b"\\":
            continue
        end = data.find(b"\x00", offset + 6)
        if end < 0 or end - offset > 600:
            continue
        info = data[offset + 6 : end]
        marker = info.find(b"\\name\\")
        if marker < 0:
            continue
        rest = info[marker + 6 :]
        stop = rest.find(b"\\")
        name = _readable(rest if stop < 0 else rest[:stop])
        if name:
            found.setdefault(name, {"name": name, "userid": userid, "slot": slot})
    return sorted(found.values(), key=lambda player: player["slot"])


def userid_for_slot(roster: list[dict[str, Any]], slot: int) -> int | None:
    return next(
        (player["userid"] for player in roster if player["slot"] == slot), None
    )


def userid_for_name(roster: list[dict[str, Any]], name: str) -> int | None:
    """Match a scoreboard name against the demo roster, colour codes aside."""
    wanted = name.lower()
    for player in roster:
        candidate = player["name"].lower()
        if candidate == wanted or candidate.replace(".", "") == wanted.replace(".", ""):
            return player["userid"]
    return None


def published_name(demo_name: str) -> str:
    """The file name a demo gets when published next to the dashboard.

    KTX names its demos `4on4_a_vs_b[dm3]…mvd`; the publisher rewrites
    everything outside the URL-safe set, so links must use the same rule or
    they point at a file that is not there.
    """
    return re.sub(r"[^A-Za-z0-9._-]", "_", demo_name)


def deep_link(
    demo_name: str,
    map_name: str,
    duration_s: float,
    at_s: float,
    userid: int | None = None,
    *,
    lead_s: float = LEAD_S,
) -> str:
    """A demo-player URL that opens `lead_s` before `at_s`, POV on `userid`.

    `from` is whole seconds because the player parses it with parseInt: a
    fractional value would silently truncate, and the URL would then claim a
    start the player never honours. Truncating downwards ourselves keeps the
    lead at or above `lead_s` rather than dropping under it.
    """
    start = int(max(0.0, float(at_s) - lead_s))
    query = [
        ("demoUrl", f"/demos/{published_name(demo_name)}"),
        ("map", map_name.lower()),
        ("duration", str(int(round(duration_s)))),
        ("from", str(start)),
    ]
    if userid is not None:
        query.append(("track", str(userid)))
    encoded = "&".join(f"{key}={quote(str(value), safe='')}" for key, value in query)
    return f"/demo-player/?{encoded}"


class Recording:
    """One server-side MVD recording, with the demo clock and its artifacts.

    Recording is best effort by design: a rig without a readable demo
    directory still produces every measurement, just without links. It must
    never be able to fail a run that otherwise measured fine.
    """

    def __init__(
        self,
        control: Control,
        name: str,
        server_demo_dir: Path | None,
        destination: Path | None,
    ) -> None:
        self.control = control
        self.name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
        self.server_demo_dir = server_demo_dir
        self.destination = destination
        self.began: float | None = None
        self.path: Path | None = None
        self.duration_s: float = 0.0
        self.error: str | None = None

    @property
    def active(self) -> bool:
        return self.began is not None

    @property
    def demo_name(self) -> str | None:
        return self.path.name if self.path is not None else None

    def start(self) -> None:
        if self.server_demo_dir is None or self.destination is None:
            self.error = "no server demo directory configured"
            return
        try:
            self.control.request(f"runcmd record {self.name}")
        except (ControlError, OSError) as exc:
            self.error = f"record command failed: {exc}"
            return
        self.began = time.monotonic()

    def at(self, monotonic_ts: float | None = None) -> float | None:
        """Demo-clock seconds for a monotonic timestamp (None if not recording)."""
        if self.began is None:
            return None
        stamp = time.monotonic() if monotonic_ts is None else monotonic_ts
        return max(0.0, stamp - self.began)

    def _recorded_file(self) -> Path:
        """Where the demo actually landed — mvdsv lowercases the name it is given."""
        directory = self.server_demo_dir or Path()
        exact = directory / f"{self.name}.mvd"
        if exact.exists():
            return exact
        wanted = f"{self.name.lower()}.mvd"
        try:
            for candidate in directory.iterdir():
                if candidate.name.lower() == wanted:
                    return candidate
        except OSError:
            pass
        return exact

    def stop(self) -> None:
        if self.began is None:
            return
        self.duration_s = time.monotonic() - self.began
        try:
            self.control.request("runcmd stop")
        except (ControlError, OSError) as exc:
            self.error = f"stop command failed: {exc}"
        source = self._recorded_file()
        # mvdsv flushes the tail after the stop command is processed.
        previous = -1
        for _ in range(15):
            time.sleep(1.0)
            try:
                size = source.stat().st_size
            except OSError:
                continue
            if size == previous and size > 0:
                break
            previous = size
        if not source.exists():
            self.error = f"recorded demo not found: {source}"
            print(f"demo evidence unavailable: {self.error}", flush=True)
            return
        try:
            assert self.destination is not None
            self.destination.mkdir(parents=True, exist_ok=True)
            target = self.destination / source.name
            target.write_bytes(source.read_bytes())
            source.unlink(missing_ok=True)
            (source.with_suffix(".txt")).unlink(missing_ok=True)
            self.path = target
        except OSError as exc:
            self.error = f"could not collect demo: {exc}"

    def link(
        self, at_s: float | None, userid: int | None = None, **kwargs: Any
    ) -> str | None:
        if self.path is None or at_s is None:
            return None
        return deep_link(
            self.path.name, self.map_name, self.duration_s, at_s, userid, **kwargs
        )

    map_name: str = "dm3"


def open_recording(
    control: Control,
    name: str,
    config: dict[str, Any],
    map_name: str,
    resolve: Any,
) -> Recording:
    """Build a Recording from the `[server].demo_dir` / `[paths].demos_dir` pair."""
    server_dir = config["server"].get("demo_dir", "")
    recording = Recording(
        control,
        name,
        resolve(config, server_dir) if server_dir else None,
        resolve(config, config["paths"]["demos_dir"]) if server_dir else None,
    )
    recording.map_name = map_name
    return recording


def match_scoreboard(
    config: dict[str, Any],
    demo_dir: Path | None,
    mvd_name: str,
    map_name: str,
    duration_s: float,
    resolve: Any,
) -> dict[str, Any] | None:
    """Scoreboard card for a played match, with a POV link per player row.

    Best effort like every other piece of evidence: no analyzer, no demo, or
    a demo without a KTX block simply means no card.
    """
    from . import analyzer as analyzer_mod

    if not mvd_name or demo_dir is None:
        return None
    mvd_path = demo_dir / mvd_name
    if not mvd_path.exists():
        return None
    # An empty demo hashes to the sha of nothing, and the analyzer then answers
    # that it has no KTX block — which is true and useless, because it blames
    # the demo's contents for a file that has none. Say what is actually wrong.
    try:
        empty = mvd_path.stat().st_size == 0
    except OSError:
        return None  # it existed a line ago; it is not ours to complain about
    if empty:
        print(
            f"no match card: {mvd_name} is an empty file — the server named a "
            "demo it never recorded",
            flush=True,
        )
        return None
    analyzer = analyzer_mod.open_analyzer(config, resolve)
    if analyzer is None:
        return None
    roster = players(mvd_path)

    def link_for(name: str) -> str | None:
        return deep_link(
            mvd_name, map_name, duration_s, 0.0, userid_for_name(roster, name), lead_s=0.0
        )

    card = analyzer_mod.match_card(analyzer, mvd_path, mvd_name, link_for)
    if card is not None:
        card["link"] = deep_link(mvd_name, map_name, duration_s, 0.0, lead_s=0.0)
    return card
