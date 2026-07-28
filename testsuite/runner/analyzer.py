"""Metrics read out of an MVD by qw-analyze (mvd-api), through a registry.

Anything the analyzer already knows should come from the analyzer rather than
from a counter of our own — it sees the recorded match, not our sampling of
it. The registry below is the whole extension surface: one entry declares
which endpoint a metric reads, how to reduce that document to a number, and
which moments in the demo are worth linking as evidence. Adding a metric is
adding an entry, never touching a runner.

Availability is a property of the demo, not a failure: a non-KTX rig has no
demoinfo block and no match window, so metrics that need those simply report
`unavailable` and the caller keeps its own measurement (and says so in
`*_source`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import gzip
import hashlib
import math
import json
from pathlib import Path
from typing import Any, Callable
import urllib.error
import urllib.request

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "qw-mvd"


@dataclass(frozen=True)
class Moment:
    """A point in the demo worth linking, with who it is about."""

    t_s: float
    who: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class Measurement:
    value: Any
    source: str
    moments: list[Moment] = field(default_factory=list)


class AnalyzerError(RuntimeError):
    """The analyzer could not answer at all (transport, planting, schema)."""


def _ms(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) / 1000.0


def _powerup(kind: str) -> Callable[[Any], dict[str, Measurement]]:
    """Takes and average lay time for one powerup, from the items document.

    An item's phases carry `availableFrom` and, once someone takes it,
    `takenAt`/`takenBy` — so the count, the average time it lay untouched and
    the linkable moments all come from the same document.
    """

    def extract(document: Any) -> dict[str, Measurement]:
        items = document.get("items") if isinstance(document, dict) else None
        if not isinstance(items, list):
            raise AnalyzerError("items document has no item list")
        takes: list[float] = []
        moments: list[Moment] = []
        for item in items:
            if not isinstance(item, dict) or item.get("kind") != kind:
                continue
            for phase in item.get("phases") or []:
                if not isinstance(phase, dict):
                    continue
                taken = _ms(phase.get("takenAt"))
                if taken is None:
                    continue
                available = _ms(phase.get("availableFrom"))
                if available is not None:
                    takes.append(round(taken - available, 1))
                who = phase.get("takenBy")
                moments.append(
                    Moment(taken, who if isinstance(who, str) else None, f"{kind} taken")
                )
        average = round(sum(takes) / len(takes), 1) if takes else None
        return {
            f"{kind}_takes": Measurement(len(moments), "qw-analyze/items", moments),
            f"{kind}_lay_avg": Measurement(average, "qw-analyze/items", moments),
        }

    return extract


def _speeds(document: Any) -> dict[str, Measurement]:
    """Server-side per-player speed, from the KTX demoinfo block."""
    players = document.get("players") if isinstance(document, dict) else None
    if not isinstance(players, list):
        raise AnalyzerError("demoinfo document has no player list")
    maxima = [
        float(player["speed"]["max"])
        for player in players
        if isinstance(player, dict)
        and isinstance(player.get("speed"), dict)
        and isinstance(player["speed"].get("max"), (int, float))
    ]
    averages = [
        float(player["speed"]["avg"])
        for player in players
        if isinstance(player, dict)
        and isinstance(player.get("speed"), dict)
        and isinstance(player["speed"].get("avg"), (int, float))
    ]
    return {
        "speed_max": Measurement(
            round(max(maxima), 1) if maxima else None, "qw-analyze/demoinfo"
        ),
        "speed_avg": Measurement(
            round(sum(averages) / len(averages), 1) if averages else None,
            "qw-analyze/demoinfo",
        ),
    }


def _half_up(value: float) -> int:
    """Round half away from zero, the way the hub's JavaScript rounds.

    Python rounds halves to even, so a percentage landing exactly on .5 would
    differ from the site this table mirrors by one.
    """
    return int(math.floor(value + 0.5)) if value >= 0 else -int(math.floor(-value + 0.5))


def _number(value: Any, digits: int = 1) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), digits)


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _leaf(entry: dict[str, Any], *path: str) -> int:
    """A counter out of the demoinfo tree, treating absence as zero.

    ktxstats serialises with serde defaults, so a player who never took an
    armour or fired a shotgun simply has no key for it. Absence therefore means
    none, not unknown, and reading it as zero is what makes the totals add up.
    """
    node: Any = entry
    for key in path:
        if not isinstance(node, dict):
            return 0
        node = node.get(key)
    if isinstance(node, bool) or node is None:
        return 0
    if isinstance(node, (int, float)):
        return node
    # A counter that arrived as something other than a number is a reading
    # error, not a zero: silently counting it as none would hide the mismatch.
    raise AnalyzerError(f"demoinfo counter {'.'.join(path)} is {type(node).__name__}")


# The public hub's game page, column by column, from its own source
# (vikpe/servers.qwlan.pl DemoStats.tsx). Efficiency, the two accuracies and
# the team rows are computed exactly the way it computes them, so a lab match
# and a real match are read the same way.
def _line(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """One scoreboard row: a single player, or a team as the sum of its players."""
    kills = sum(_leaf(e, "stats", "kills") for e in entries)
    deaths = sum(_leaf(e, "stats", "deaths") for e in entries)
    taken = sum(_leaf(e, "dmg", "taken") for e in entries)
    sg_attacks = sum(_leaf(e, "weapons", "sg", "acc", "attacks") for e in entries)
    sg_hits = sum(_leaf(e, "weapons", "sg", "acc", "hits") for e in entries)
    lg_attacks = sum(_leaf(e, "weapons", "lg", "acc", "attacks") for e in entries)
    lg_hits = sum(_leaf(e, "weapons", "lg", "acc", "hits") for e in entries)
    return {
        "frags": sum(_leaf(e, "stats", "frags") for e in entries),
        "kills": kills,
        "deaths": deaths,
        # The share of a player's engagements they won, rounded as KTX rounds it.
        "efficiency": _half_up(100 * kills / (kills + deaths)) if kills + deaths else None,
        "suicides": sum(_leaf(e, "stats", "suicides") for e in entries),
        "tk": sum(_leaf(e, "stats", "tk") for e in entries),
        "spawn_frags": sum(_leaf(e, "stats", "spawn-frags") for e in entries),
        "dmg_given": sum(_leaf(e, "dmg", "given") for e in entries),
        "dmg_taken": taken,
        "dmg_enemy_weapons": sum(_leaf(e, "dmg", "enemy-weapons") for e in entries),
        # An average, so it is recomputed rather than summed — adding one
        # player's average to another's would be meaningless on a team row.
        # KTX truncates this division and so do we, to land on its own number.
        "taken_to_die": taken // deaths if deaths else None,
        "ga": sum(_leaf(e, "items", "ga", "took") for e in entries),
        "ya": sum(_leaf(e, "items", "ya", "took") for e in entries),
        "ra": sum(_leaf(e, "items", "ra", "took") for e in entries),
        "mh": sum(_leaf(e, "items", "health_100", "took") for e in entries),
        "quad": sum(_leaf(e, "items", "q", "took") for e in entries),
        "pent": sum(_leaf(e, "items", "p", "took") for e in entries),
        "ring": sum(_leaf(e, "items", "r", "took") for e in entries),
        # Blank rather than zero when nothing was fired — the hub hides the cell.
        "sg_acc": _half_up(100 * sg_hits / sg_attacks) if sg_attacks else None,
        "lg_acc": _half_up(100 * lg_hits / lg_attacks) if lg_attacks else None,
        "rl_direct": sum(_leaf(e, "weapons", "rl", "acc", "hits") for e in entries),
        "lg_taken": sum(_leaf(e, "weapons", "lg", "pickups", "taken") for e in entries),
        "lg_kills": sum(_leaf(e, "weapons", "lg", "kills", "enemy") for e in entries),
        "lg_dropped": sum(_leaf(e, "weapons", "lg", "pickups", "dropped") for e in entries),
        "rl_taken": sum(_leaf(e, "weapons", "rl", "pickups", "taken") for e in entries),
        "rl_kills": sum(_leaf(e, "weapons", "rl", "kills", "enemy") for e in entries),
        "rl_dropped": sum(_leaf(e, "weapons", "rl", "pickups", "dropped") for e in entries),
    }


def scoreboard(document: Any) -> dict[str, Any]:
    """The match as the scoreboard saw it, laid out the way the hub lays it out.

    Straight off the KTX demoinfo block, which is the same source the public hub
    renders its game pages from. The rows and their order are the hub's, so a lab
    match reads like a real one instead of like our own invention: team rows
    first when the match is teamplay, then the players.

    Our own additions — speed and the longest spree — ride along on each player
    for the panels that want them; the hub's table does not show them.
    """
    if not isinstance(document, dict) or not isinstance(document.get("players"), list):
        raise AnalyzerError("demoinfo document has no player list")
    entries = [entry for entry in document["players"] if isinstance(entry, dict)]
    mode = document.get("mode") if isinstance(document.get("mode"), str) else None
    by_team: dict[str, list[dict[str, Any]]] = {}
    players = []
    for entry in entries:
        team = entry.get("team") if isinstance(entry.get("team"), str) else ""
        by_team.setdefault(team, []).append(entry)
        speed = entry.get("speed") if isinstance(entry.get("speed"), dict) else {}
        spree = entry.get("spree") if isinstance(entry.get("spree"), dict) else {}
        players.append(
            {
                "name": entry.get("name") if isinstance(entry.get("name"), str) else "?",
                "team": team,
                "ping": _int(entry.get("ping")),
                "top_color": _int(entry.get("topColor")),
                "bottom_color": _int(entry.get("bottomColor")),
                "speed_max": _number(speed.get("max")),
                "speed_avg": _number(speed.get("avg")),
                "spree_max": _int(spree.get("max")),
                "link": None,
                **_line([entry]),
            }
        )
    players.sort(key=lambda player: (player["team"], -player["frags"]))
    teams = [
        {"name": name, **_line(group)}
        for name, group in sorted(
            by_team.items(), key=lambda item: -sum(_leaf(e, "stats", "frags") for e in item[1])
        )
        if name
    ]
    return {
        "teams": teams,
        "players": players,
        "map": document.get("map") if isinstance(document.get("map"), str) else None,
        "duration_s": _number(document.get("duration"), 0),
        "mode": mode,
        "hostname": document.get("hostname") if isinstance(document.get("hostname"), str) else None,
        "date": document.get("date") if isinstance(document.get("date"), str) else None,
        "source": "qw-analyze/demoinfo",
    }


@dataclass(frozen=True)
class MetricGroup:
    """One analyzer document reduced to one or more payload metrics."""

    endpoint: str
    extract: Callable[[Any], dict[str, Measurement]]
    about: str


# The extension surface. Every entry is independent; a group that the demo
# cannot answer is skipped, never fatal.
REGISTRY: dict[str, MetricGroup] = {
    "quad": MetricGroup("items", _powerup("quad"), "quad takes and lay time"),
    "pent": MetricGroup("items", _powerup("pent"), "pent takes and lay time"),
    "ring": MetricGroup("items", _powerup("ring"), "ring takes and lay time"),
    "speeds": MetricGroup("demoinfo", _speeds, "server-side player speed (KTX only)"),
}


class Analyzer:
    """Thin, caching client for a local mvd-api instance."""

    def __init__(self, base_url: str, cache_dir: Path | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self._documents: dict[tuple[str, str], Any] = {}
        self._version: str | None = None

    def _get(self, path: str, timeout: float = 30.0) -> Any:
        try:
            with urllib.request.urlopen(f"{self.base_url}{path}", timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:200]
            raise AnalyzerError(f"{path}: HTTP {exc.code}: {body}") from exc
        except (OSError, ValueError) as exc:
            raise AnalyzerError(f"{path}: {exc}") from exc

    def version(self) -> str:
        if self._version is None:
            health = self._get("/healthz", timeout=8.0)
            schema = health.get("schemaVersion") if isinstance(health, dict) else None
            self._version = f"qw-analyze schema v{schema}" if schema else "qw-analyze"
        return self._version

    def plant(self, mvd_path: Path) -> str:
        """Make a local demo readable by mvd-api and return its demo id.

        The cache is keyed by the sha256 of the raw MVD, so planting is just
        writing the gzipped demo where the API already looks.
        """
        try:
            raw = mvd_path.read_bytes()
        except OSError as exc:
            raise AnalyzerError(f"cannot read {mvd_path}: {exc}") from exc
        digest = hashlib.sha256(raw).hexdigest()
        target = self.cache_dir / "mvd" / digest[:2] / f"{digest}.mvd.gz"
        if not target.exists():
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix(".part")
                with gzip.open(temporary, "wb") as stream:
                    stream.write(raw)
                temporary.replace(target)
            except OSError as exc:
                raise AnalyzerError(f"cannot plant {mvd_path}: {exc}") from exc
        return f"sha:{digest}"

    def document(self, demo_id: str, endpoint: str) -> Any:
        key = (demo_id, endpoint)
        if key not in self._documents:
            self._documents[key] = self._get(f"/v1/demos/{demo_id}/{endpoint}")
        return self._documents[key]

    def measure(
        self, demo_id: str, groups: list[str] | None = None
    ) -> tuple[dict[str, Measurement], dict[str, str]]:
        """Run the requested registry groups; return measurements and skips."""
        measurements: dict[str, Measurement] = {}
        skipped: dict[str, str] = {}
        for name in groups or list(REGISTRY):
            group = REGISTRY.get(name)
            if group is None:
                skipped[name] = "no such metric group"
                continue
            try:
                measurements.update(group.extract(self.document(demo_id, group.endpoint)))
            except AnalyzerError as exc:
                skipped[name] = str(exc)
        return measurements, skipped


def open_analyzer(config: dict[str, Any], resolve: Any) -> Analyzer | None:
    """Build an Analyzer from `[tools].mvd_api`, or None when not configured."""
    tools = config.get("tools", {})
    base_url = str(tools.get("mvd_api", "") or "")
    if not base_url:
        return None
    cache = tools.get("mvd_cache_dir", "")
    return Analyzer(base_url, resolve(config, cache) if cache else None)


def match_card(
    analyzer: "Analyzer",
    mvd_path: Path,
    demo_name: str | None,
    link_for: Any = None,
) -> dict[str, Any] | None:
    """Build one match scoreboard from a recorded MVD, links included.

    `link_for(name)` returns a demo-player link for that player's POV, so a
    row in the card opens the match from that bot's eyes.
    """
    try:
        demo_id = analyzer.plant(mvd_path)
        card = scoreboard(analyzer.document(demo_id, "demoinfo"))
    except AnalyzerError as exc:
        print(f"scoreboard unavailable: {exc}", flush=True)
        return None
    card["demo"] = demo_name
    if link_for is not None:
        for player in card["players"]:
            player["link"] = link_for(player["name"])
    return card
