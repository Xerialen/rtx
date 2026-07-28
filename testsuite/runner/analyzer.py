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


def _number(value: Any, digits: int = 1) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), digits)


def scoreboard(document: Any) -> dict[str, Any]:
    """The match as the scoreboard saw it: teams, order, per-player stats.

    Straight off the KTX demoinfo block, which is the same source the public
    hub renders its game pages from — so a lab match reads like a real one.
    """
    if not isinstance(document, dict) or not isinstance(document.get("players"), list):
        raise AnalyzerError("demoinfo document has no player list")
    players = []
    team_frags: dict[str, int] = {}
    for entry in document["players"]:
        if not isinstance(entry, dict):
            continue
        stats = entry.get("stats") if isinstance(entry.get("stats"), dict) else {}
        damage = entry.get("dmg") if isinstance(entry.get("dmg"), dict) else {}
        speed = entry.get("speed") if isinstance(entry.get("speed"), dict) else {}
        spree = entry.get("spree") if isinstance(entry.get("spree"), dict) else {}
        team = entry.get("team") if isinstance(entry.get("team"), str) else ""
        frags = stats.get("frags")
        if isinstance(frags, int):
            team_frags[team] = team_frags.get(team, 0) + frags
        players.append(
            {
                "name": entry.get("name") if isinstance(entry.get("name"), str) else "?",
                "team": team,
                "frags": frags if isinstance(frags, int) else None,
                "deaths": stats.get("deaths") if isinstance(stats.get("deaths"), int) else None,
                "kills": stats.get("kills") if isinstance(stats.get("kills"), int) else None,
                "tk": stats.get("tk") if isinstance(stats.get("tk"), int) else None,
                "dmg_given": _number(damage.get("given"), 0),
                "dmg_taken": _number(damage.get("taken"), 0),
                "speed_max": _number(speed.get("max")),
                "speed_avg": _number(speed.get("avg")),
                "spree_max": spree.get("max") if isinstance(spree.get("max"), int) else None,
                "link": None,
            }
        )
    players.sort(key=lambda player: (player["team"], -(player["frags"] or 0)))
    teams = [
        {"name": name, "frags": frags}
        for name, frags in sorted(team_frags.items(), key=lambda item: -item[1])
    ]
    return {
        "teams": teams,
        "players": players,
        "map": document.get("map") if isinstance(document.get("map"), str) else None,
        "duration_s": _number(document.get("duration"), 0),
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
