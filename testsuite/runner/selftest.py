"""Offline schema conformance test over versioned fixtures."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import tomllib

from .checks import ValidationError, validate_result, validate_scenario_result


def _t2_units() -> list[str]:
    """Unit checks for the T2 gate's own arithmetic.

    The fixtures prove the schema; these prove the two rules the schema cannot
    see — that a lay interval joined in the middle is counted as a take but not
    as a measurement, and that the analyzer cannot quietly zero a live one.
    """
    from . import t2

    failures: list[str] = []

    def check(name: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    def observe(seq: list[bool]) -> dict[str, Any]:
        state = {
            "takes": [],
            "available_since": None,
            "seen": False,
            "censored_open": False,
            "censored": 0,
        }
        powerups = {"quad": state}
        for index, available in enumerate(seq):
            t2._observe_powerups(
                [{"name": "item_artifact_super_damage", "available": available}],
                float(index),
                powerups,
            )
        return t2._lay_summary(state, "quad")

    # Available from the first look: the take counts, the interval does not.
    lead = observe([True, True, False])
    check("censored.quad_takes", lead["quad_takes"], 1)
    check("censored.quad_lay_n", lead["quad_lay_n"], 0)
    check("censored.quad_lay_censored", lead["quad_lay_censored"], 1)
    check("censored.quad_lay_avg", lead["quad_lay_avg"], None)

    # Unavailable first, then an observed edge: nothing is censored.
    edge = observe([False, True, True, False])
    check("edge.quad_takes", edge["quad_takes"], 1)
    check("edge.quad_lay_n", edge["quad_lay_n"], 1)
    check("edge.quad_lay_censored", edge["quad_lay_censored"], 0)
    check("edge.quad_lay_avg", edge["quad_lay_avg"], 2.0)

    # Both together: takes is the sum of the parts, the mean is over the
    # uncensored one only.
    both = observe([True, False, True, True, False])
    check("both.quad_takes", both["quad_takes"], 2)
    check("both.quad_lay_n", both["quad_lay_n"], 1)
    check("both.quad_lay_censored", both["quad_lay_censored"], 1)
    check("both.quad_lay_avg", both["quad_lay_avg"], 2.0)

    # The analyzer merge. This is the rule six retracted envelopes were
    # retracted for, so it gets its three cases spelled out.
    class M:
        def __init__(self, value: Any, source: str = "qw-analyze/items") -> None:
            self.value, self.source, self.moments = value, source, []

    def merge(live: dict[str, Any], answers: dict[str, Any]) -> tuple[dict, dict, list]:
        stats, sources = dict(live), {}
        bad = t2._merge_analyzer(stats, sources, answers)
        return stats, sources, bad

    # 1. Analyzer zeroes a live take count: disagreement, nobody wins quietly.
    stats, _sources, bad = merge(
        {"quad_takes": 3, "quad_lay_avg": 8.5},
        {"quad_takes": M(0), "quad_lay_avg": M(None)},
    )
    check("a2.disagreement", bool(bad), True)
    check("a2.live_takes_kept", stats["quad_takes"], 3)

    # 2. Analyzer has no answer: live stands, and the source says so.
    stats, sources, bad = merge(
        {"quad_takes": 3, "quad_lay_avg": 8.5}, {"quad_lay_avg": M(None)}
    )
    check("a1.no_disagreement", bad, [])
    check("a1.live_kept", stats["quad_lay_avg"], 8.5)
    check("a1.source_marked", sources["quad_lay_avg"].startswith("runner/live"), True)

    # 3. Analyzer answers: it owns the value, as before.
    stats, sources, bad = merge({"quad_lay_avg": 8.5}, {"quad_lay_avg": M(9.1)})
    check("a3.analyzer_wins", stats["quad_lay_avg"], 9.1)
    check("a3.source", sources["quad_lay_avg"], "qw-analyze/items")

    # 4. Both saw nothing: a legitimate null, not a disagreement.
    stats, _sources, bad = merge(
        {"quad_takes": 0, "quad_lay_avg": None},
        {"quad_takes": M(0), "quad_lay_avg": M(None)},
    )
    check("a4.no_disagreement", bad, [])
    check("a4.takes", stats["quad_takes"], 0)

    return failures


def _t4_units() -> list[str]:
    """The T4 dom's own arithmetic, one check per negative control in SPEC v6 §7.

    The fixtures prove what a written envelope may look like. These prove the
    rules underneath: the priority order, the two calibrated seams, and — most
    of the work — that an unmeasured field comes back as `None` and never as a
    zero. Every check here fails on the code as it stood before the change,
    because none of these functions existed.
    """
    import copy
    import tempfile as _tempfile

    from . import combat_lock as combat_lock_mod
    from . import runlib as runlib_mod
    from . import t3 as t3_mod
    from . import t4 as t4_mod
    from . import t4_dom

    failures: list[str] = []

    def check(name: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    green = {
        "shots_fired": 2412,
        "teamkills": 8,
        "kills_total": 45,
        "still_s_per_bot_max": 18.4,
        "item_pickups": 27,
    }
    played = {"drew": False, "won_top": False, "reached": 0}
    drew = {"drew": True, "won_top": False, "reached": 0}
    climbed = {"drew": False, "won_top": True, "reached": 20}

    def verdict(measurements: dict[str, Any], outcome: dict[str, Any]) -> str:
        return t4_dom.adjudicate(measurements, outcome)["verdict"]

    # NK 1. A dominant win with one accidental teamkill is not a FAIL. The
    # rejected rule (`teamkills >= enemy_frags`) fells it; the share does not.
    check("nk1.lone_teamkill", verdict({**green, "teamkills": 1}, climbed), "VINST")

    # NK 3 / NK 5.1: a draw with a fallen measured gate is a FAIL, not a draw.
    check("nk3.draw_with_gate", verdict({**green, "shots_fired": 0}, drew), "FAIL")
    # NK 4: a draw with all four green is OAVGJORD.
    check("nk4.clean_draw", verdict(green, drew), "OAVGJORD")
    # NK 11: the top rung with all four green is VINST; NK 5.5: a played and
    # lost ladder with all four green is OK.
    check("nk11.top_rung", verdict(green, climbed), "VINST")
    check("nk11.played", verdict(green, played), "OK")

    # NK 5. The stillness seam, both sides of the calibrated threshold. Equal
    # does not fell; strictly greater does.
    limit = t4_dom.STILL_S_PER_BOT_MAX
    check("nk5.at_threshold", verdict({**green, "still_s_per_bot_max": limit}, played), "OK")
    check(
        "nk5.above_threshold",
        verdict({**green, "still_s_per_bot_max": limit + 0.1}, played),
        "FAIL",
    )
    check(
        "nk5.below_threshold",
        verdict({**green, "still_s_per_bot_max": limit - 0.1}, played),
        "OK",
    )
    # The same seam on the teamkill share: 0.20 of 45 kills is 9 exactly.
    share = t4_dom.TEAMKILL_SHARE_MAX
    check("nk5.share_at", verdict({**green, "teamkills": share * 45}, played), "OK")
    check(
        "nk5.share_above",
        verdict({**green, "teamkills": share * 45 + 1}, played),
        "FAIL",
    )

    # NK 6, 7, 8, 9: each measured gate fells on its own.
    check("nk6.no_shots", verdict({**green, "shots_fired": 0}, played), "FAIL")
    check("nk7.teamkills", verdict({**green, "teamkills": 10}, played), "FAIL")
    check("nk8.still", verdict({**green, "still_s_per_bot_max": 151.5}, played), "FAIL")
    check("nk9.no_items", verdict({**green, "item_pickups": 0}, played), "FAIL")
    # NK 9, positive side: a real match's pickup count does not fell. 5 is the
    # lowest world total on a real card in the corpus.
    check("nk9.real_items", verdict({**green, "item_pickups": 5}, played), "OK")
    check(
        "nk9.gate_names",
        t4_dom.failed_gates({**green, "item_pickups": 0}),
        ["d:item_pickups"],
    )

    # NK 2 / NK 17. One unavailable field at a time, everything else green:
    # OMÄTT every time, and never OK — not even on a won ladder.
    for field, capability in (
        ("shots_fired", t4_dom.CAP_SHOTS),
        ("teamkills", t4_dom.CAP_TEAMKILLS),
        ("still_s_per_bot_max", t4_dom.CAP_STILL),
        ("item_pickups", t4_dom.CAP_ITEMS),
    ):
        blind = {**green, field: None}
        check(f"nk17.{field}.verdict", verdict(blind, climbed), "OMÄTT")
        check(f"nk17.{field}.missing", t4_dom.missing_fields(blind), [capability])
    check(
        "nk2.blind",
        t4_dom.missing_fields(dict.fromkeys(green, None)),
        list(t4_dom.T4_CAPABILITIES),
    )
    # A share needs both halves: teamkills without kills is not measured.
    check(
        "nk17.half_a_ratio",
        t4_dom.missing_fields({**green, "kills_total": None}),
        [t4_dom.CAP_TEAMKILLS],
    )
    # FAIL beats OMÄTT: an unmeasured field never hides a fallen gate.
    check(
        "nk3.fail_beats_unmeasured",
        verdict({**green, "shots_fired": 0, "item_pickups": None}, drew),
        "FAIL",
    )

    # NK 20. `reached` is the highest WON rung, not the last played one.
    def ladder(*states: tuple[int, str]) -> list[dict[str, Any]]:
        rungs = []
        for skill, state in states:
            rungs.append(
                {
                    "skill": skill,
                    "win": state == "win",
                    "draw": state == "draw",
                }
            )
        return rungs

    check("nk20.loss_first", t4_dom.reached_from_ladder(ladder((10, "loss"))), 0)
    check("nk20.draw_first", t4_dom.reached_from_ladder(ladder((10, "draw"))), 0)
    check(
        "nk20.draw_after_win",
        t4_dom.reached_from_ladder(ladder((10, "win"), (12, "draw"))),
        10,
    )
    check(
        "nk20.climb",
        t4_dom.reached_from_ladder(
            ladder((10, "win"), (12, "win"), (14, "win"), (16, "win"), (18, "win"), (20, "win"))
        ),
        20,
    )
    check("nk20.empty", t4_dom.reached_from_ladder([]), 0)
    # A2 / QA Q-M11. "Highest won" and "last won" are the same number on a
    # ladder the order gate has approved, which is why the mutation from one
    # to the other survived the whole suite. Feed it a ladder the order gate
    # would refuse and the two readings separate: highest is 20, last is 14.
    check(
        "a2.highest_not_last",
        t4_dom.reached_from_ladder(ladder((10, "win"), (20, "win"), (14, "win"))),
        20,
    )
    check(
        "a2.highest_not_last_with_a_loss",
        t4_dom.reached_from_ladder(ladder((18, "win"), (12, "win"), (16, "loss"))),
        18,
    )

    # NK 15. The teamkill card: a missing field, a non-numeric field or a
    # negative component is unavailable, never a numeric zero.
    def card(**team: Any) -> dict[str, Any]:
        return {"teams": [{"name": "brch", **team}, {"name": "frog", "frags": 1}]}

    check(
        "nk15.good_card",
        t4_dom.teamkills_from_card(card(kills=45, frags=34, suicides=3), "brch"),
        (8, 45),
    )
    check(
        "nk15.missing_field",
        t4_dom.teamkills_from_card(card(kills=45, frags=34), "brch"),
        (None, None),
    )
    check(
        "nk15.null_field",
        t4_dom.teamkills_from_card(card(kills=45, frags=34, suicides=None), "brch"),
        (None, None),
    )
    check(
        "nk15.non_numeric",
        t4_dom.teamkills_from_card(card(kills="45", frags=34, suicides=3), "brch"),
        (None, None),
    )
    check(
        "nk15.negative_component",
        t4_dom.teamkills_from_card(card(kills=-1, frags=-4, suicides=0), "brch"),
        (None, None),
    )
    check(
        "nk15.negative_result",
        t4_dom.teamkills_from_card(card(kills=10, frags=12, suicides=0), "brch"),
        (None, None),
    )
    check("nk15.no_card", t4_dom.teamkills_from_card(None, "brch"), (None, None))
    # A1 (Fables facitbeslut 2026-08-24, ur QA-domen). The real corpus row:
    # frags is negative, the formula derives 11 teamkills on 6 kills, and a
    # teamkill count above the team's own kills is not a teamkill count.
    check(
        "a1.real_row_derives_more_than_kills",
        t4_dom.teamkills_from_card(card(kills=6, frags=-5, suicides=0), "brch"),
        (None, None),
    )
    check(
        "a1.second_real_row",
        t4_dom.teamkills_from_card(card(kills=7, frags=-5, suicides=0), "brch"),
        (None, None),
    )
    # The size guard alone would let this one through — the negative frags are
    # cancelled by the suicides, so the derived count lands back in range. It
    # is still a card that does not mean what the formula assumes, and the
    # sign guard is what catches it. One check per guard, so neither is
    # decoration.
    check(
        "a1.negative_frags_cancelled_out",
        t4_dom.teamkills_from_card(card(kills=6, frags=-5, suicides=11), "brch"),
        (None, None),
    )
    # The seam of the size guard: derived == kills is still a card, one more
    # is not.
    # json.loads accepts the NaN literal, and NaN walks past every range
    # check by comparing False to all of them. An unrepresentable component is
    # an absent one, not a measurement.
    check(
        "a1.not_a_number",
        t4_dom.teamkills_from_card(
            card(kills=45, frags=float("nan"), suicides=3), "brch"
        ),
        (None, None),
    )
    check(
        "a1.infinite",
        t4_dom.teamkills_from_card(
            card(kills=float("inf"), frags=34, suicides=3), "brch"
        ),
        (None, None),
    )
    check("a1.nan_measurement", t4_dom.missing_fields({**green, "shots_fired": float("nan")}), [t4_dom.CAP_SHOTS])
    check(
        "a1.derived_equals_kills",
        t4_dom.teamkills_from_card(card(kills=5, frags=0, suicides=0), "brch"),
        (5, 5),
    )
    check(
        "nk15.wrong_team",
        t4_dom.teamkills_from_card(card(kills=45, frags=34, suicides=3), "nope"),
        (None, None),
    )

    # NK 14. The shot signal is an ammo count going down. A player with ammo
    # streams and one decrease has fired; a document with no ammo stream for
    # the team has not been measured, and answers None rather than 0.
    fired = {
        "streams": {
            "players": [
                {
                    "name": "bot.brch1",
                    "team": "brch",
                    "sh": [{"t": 0, "v": 25}, {"t": 700, "v": 24}],
                }
            ]
        }
    }
    check("nk14.one_shot", combat_lock_mod.team_shots(fired, "brch"), 1)
    silent = {
        "streams": {
            "players": [
                {
                    "name": "bot.brch1",
                    "team": "brch",
                    "sh": [{"t": 0, "v": 25}, {"t": 700, "v": 25}],
                }
            ]
        }
    }
    check("nk14.measured_zero", combat_lock_mod.team_shots(silent, "brch"), 0)
    check(
        "nk14.unmeasured",
        combat_lock_mod.team_shots(
            {"streams": {"players": [{"name": "b", "team": "brch"}]}}, "brch"
        ),
        None,
    )
    check("nk14.no_document", combat_lock_mod.team_shots(None, "brch"), None)
    check("nk14.other_team", combat_lock_mod.team_shots(fired, "frog"), None)
    # A respawn changes several ammo streams at once and is not a shot.
    respawn = {
        "streams": {
            "players": [
                {
                    "name": "bot.brch1",
                    "team": "brch",
                    "sh": [{"t": 0, "v": 25}, {"t": 500, "v": 10}],
                    "nl": [{"t": 0, "v": 50}, {"t": 500, "v": 20}],
                }
            ]
        }
    }
    check("nk14.respawn_is_not_a_shot", combat_lock_mod.team_shots(respawn, "brch"), 0)

    # NK 16. The sampling gap. A watch that polled twice inside the ceiling has
    # a number; one that fell behind it has none, and never an interpolation.
    class FakeSide:
        def __init__(self, alive: bool = True) -> None:
            self.polls = 0
            self.alive = alive
            self.still_s = 40.0
            self.bots_seen = 4
            self.sample_window_s = t4_dom.STILL_SAMPLE_GAP_MAX_S

        def sample(self, now: float | None = None) -> None:
            if self.alive:
                self.polls += 1

    def watch_over(times: list[float], alive: bool = True) -> tuple[Any, Any]:
        side = FakeSide(alive)
        watch = t4_mod._StillWatch()
        for now in times:
            watch.maybe_sample(side, now)
        return watch, side

    watch, side = watch_over([0.0, 1.0, 2.0, 3.0, 4.0])
    check("nk16.tight.samples", watch.samples, 5)
    check("nk16.tight.gap", round(watch.gap_max_seen, 3), 1.0)
    check("nk16.tight.measured", watch.measured(side), True)
    check("nk16.tight.value", watch.still_s_per_bot(side), 10.0)

    watch, side = watch_over([0.0, 1.0, 5.5, 6.5])
    check("nk16.gapped.gap", round(watch.gap_max_seen, 3), 4.5)
    check("nk16.gapped.measured", watch.measured(side), False)
    check("nk16.gapped.value", watch.still_s_per_bot(side), None)

    watch, side = watch_over([0.0, 1.0, 2.0], alive=False)
    check("nk16.dead_channel.samples", watch.samples, 0)
    check("nk16.dead_channel.measured", watch.measured(side), False)
    check("nk16.dead_channel.value", watch.still_s_per_bot(side), None)

    watch, side = watch_over([0.0])
    check("nk16.one_sample.measured", watch.measured(side), False)

    # NK 9 / NK 16, item side: a take is an available -> unavailable edge, the
    # first look only seeds the state, and a poll gap over the ceiling makes
    # the count unavailable rather than low.
    def item(available: bool, name: str = "item_artifact_super_damage") -> dict[str, Any]:
        return {"classname": name, "available": available, "origin": [10, 20, 30]}

    items = t4_mod._ItemWatch()
    for index, state in enumerate([True, True, False, True, False]):
        items.observe([item(state)], float(index))
    check("nk9.takes", items.takes, 2)
    check("nk9.measured", items.measured(), True)

    seeded = t4_mod._ItemWatch()
    for index, state in enumerate([False, False, True]):
        seeded.observe([item(state)], float(index))
    check("nk9.first_look_not_a_take", seeded.takes, 0)

    two = t4_mod._ItemWatch()
    for index, state in enumerate([True, False]):
        two.observe(
            [item(state), item(True, "item_health"), {"classname": "x"}], float(index)
        )
    check("nk9.identity_separates_items", two.takes, 1)

    gapped = t4_mod._ItemWatch()
    gapped.observe([item(True)], 0.0)
    gapped.observe([item(False)], 5.0)
    check("nk9.gapped.takes", gapped.takes, 1)
    check("nk9.gapped.measured", gapped.measured(), False)

    blind = t4_mod._ItemWatch()
    blind.observe("not a list", 0.0)
    blind.observe([{"classname": "x"}], 1.0)
    check("nk9.blind.misses", blind.misses, 2)
    check("nk9.blind.measured", blind.measured(), False)

    # How wide the world channel actually was. Gate (d) means nothing if the
    # reply only ever carries quad and pent — 46 of 51 ten-minute T2 runs saw
    # zero takes on that pair — so the count rides along as evidence.
    wide = t4_mod._ItemWatch()
    wide.observe(
        [
            item(True, "item_artifact_super_damage"),
            item(True, "item_armor2"),
            {"classname": "item_health", "available": True, "origin": [1, 2, 3]},
        ],
        0.0,
    )
    check("d.channel_width", wide.tracked(), 3)
    check("d.narrow_channel", seeded.tracked(), 1)

    # The ladder fold: a field is the ladder's only when every rung has it.
    rungs = [
        {"skill": 10, "win": True, "measured": {"shots_fired": 100, "item_takes": 3}},
        {"skill": 12, "win": False, "measured": {"shots_fired": 40, "item_takes": None}},
    ]
    folded = t4_dom.measure_ladder(rungs)
    check("fold.summed", folded["shots_fired"], 140)
    check("fold.partial_is_unavailable", folded["item_pickups"], None)
    check("fold.absent_block", t4_dom.measure_ladder([{"skill": 10}])["shots_fired"], None)

    # --- Rond 2, QA 2026-08-24 -------------------------------------------
    # Punkt 2: KTX's own card as the second measurement source. The fixture is
    # the evening's real card, byte for byte (sha b54bdbf1…), so this is the
    # actual match being re-judged rather than a story about it.
    card_path = (
        Path(__file__).resolve().parent.parent
        / "schema" / "fixtures" / "cards" / "ktx-demoinfo-20260824-1642.json"
    )
    ktx = json.loads(card_path.read_text(encoding="utf-8"))
    check("ktx.real_shots", t4_dom.ktx_shots(ktx, "brch"), 0)
    check("ktx.real_teamkills", t4_dom.ktx_teamkills(ktx, "brch"), (10, 1))
    check("ktx.enemy_shots", t4_dom.ktx_shots(ktx, "frog"), 1286)
    check("ktx.enemy_teamkills", t4_dom.ktx_teamkills(ktx, "frog"), (1, 82))
    # Fable's NK: this card must fell gates (a) AND (b).
    evening = {
        "shots_fired": t4_dom.ktx_shots(ktx, "brch"),
        "teamkills": t4_dom.ktx_teamkills(ktx, "brch")[0],
        "kills_total": t4_dom.ktx_teamkills(ktx, "brch")[1],
        "still_s_per_bot_max": None,
        "item_pickups": 211,
    }
    check(
        "ktx.gates_fell",
        t4_dom.failed_gates(evening),
        ["a:shots_fired", "b:teamkill_share"],
    )
    check(
        "ktx.verdict",
        t4_dom.adjudicate(evening, {"drew": False, "won_top": False, "reached": 0})[
            "verdict"
        ],
        "FAIL",
    )
    # The derivation §3 names would refuse this same card, which is why the KTX
    # source reads `stats.tk` instead: KTX counts enemy kills and team kills as
    # two independent counters, so 10 teamkills on 1 kill is a reading, not a
    # contradiction.
    check(
        "ktx.derivation_would_refuse",
        t4_dom.teamkills_from_card(
            {"teams": [{"name": "brch", "kills": 1, "frags": -10, "suicides": 0}]},
            "brch",
        ),
        (None, None),
    )
    # Malformed cards. A zero is only believed once the card has been shown
    # able to say something else: KTX omits `acc` for a weapon never fired, so
    # a card with no accuracy anywhere is unavailable, not zero.
    blind_card = copy.deepcopy(ktx)
    for player in blind_card["players"]:
        for weapon in (player.get("weapons") or {}).values():
            if isinstance(weapon, dict):
                weapon.pop("acc", None)
    check("ktx.no_accuracy_anywhere", t4_dom.ktx_shots(blind_card, "brch"), None)
    negative = copy.deepcopy(ktx)
    negative["players"][0]["weapons"]["sg"]["acc"]["attacks"] = -3
    check("ktx.negative_attacks", t4_dom.ktx_shots(negative, "frog"), None)
    no_tk = copy.deepcopy(ktx)
    for player in no_tk["players"]:
        if str(player.get("team")) == "brch":
            player["stats"].pop("tk", None)
    check("ktx.missing_tk", t4_dom.ktx_teamkills(no_tk, "brch"), (None, None))
    fractional = copy.deepcopy(ktx)
    for player in fractional["players"]:
        if str(player.get("team")) == "brch":
            player["stats"]["tk"] = 1.5
    check("ktx.fractional_counter", t4_dom.ktx_teamkills(fractional, "brch"), (None, None))
    check("ktx.unknown_team", t4_dom.ktx_teamkills(ktx, "nope"), (None, None))
    check("ktx.no_document", t4_dom.ktx_shots(None, "brch"), None)
    check("ktx.empty_roster", t4_dom.ktx_shots({"players": []}, "brch"), None)

    # Why `stats.tk` and not the derivation — the corrected justification
    # (QA delta, 2026-08-24), pinned so nobody has to take the prose on trust.
    # KTX's counters are not independent: the identity holds almost everywhere,
    # and it is the one row that breaks it that decides the reading.
    def identity_rows(document: dict[str, Any]) -> list[tuple[str, bool]]:
        out = []
        for player in document.get("players", []):
            stats = player.get("stats") or {}
            holds = (
                stats["kills"] - stats["tk"] - stats["suicides"] == stats["frags"]
            )
            out.append((str(player.get("name")), holds))
        return out

    t3_card = json.loads(
        (card_path.parent / "ktx-demoinfo-20260824-1635-t3.json").read_text(
            encoding="utf-8"
        )
    )
    t3_rows = identity_rows(t3_card)
    t4_rows = identity_rows(ktx)
    check("identity.t3_all_hold", [name for name, ok in t3_rows if not ok], [])
    check("identity.t3_row_count", len(t3_rows), 8)
    check("identity.t4_row_count", len(t4_rows), 8)
    check("identity.t4_breakers", sum(1 for _, ok in t4_rows if not ok), 1)
    # Named, not indexed: a test that raises IndexError when its own premise
    # fails reports a crash where it owes a verdict.
    breakers = [name for name, ok in t4_rows if not ok]
    check(
        "identity.t4_breaker_is_brch3",
        breakers[0].endswith("brch3") if breakers else f"no breaker: {breakers}",
        True,
    )
    # On the T3 card the derivation and the counter agree exactly, which is the
    # proof that the counters are not independent.
    for team, expected in (("brch", 10), ("ref", 11)):
        rows = [
            player
            for player in t3_card["players"]
            if str(player.get("team")) == team
        ]
        derived = sum(
            row["stats"]["kills"] - row["stats"]["frags"] - row["stats"]["suicides"]
            for row in rows
        )
        counted = sum(row["stats"]["tk"] for row in rows)
        check(f"identity.t3_{team}_derived", derived, expected)
        check(f"identity.t3_{team}_counted", counted, expected)
    # On the T4 card the single broken row is the whole difference: 11 derived
    # against 10 counted.
    brch_rows = [
        player for player in ktx["players"] if str(player.get("team")) == "brch"
    ]
    derived = sum(
        row["stats"]["kills"] - row["stats"]["frags"] - row["stats"]["suicides"]
        for row in brch_rows
    )
    check("identity.t4_derived", derived, 11)
    check("identity.t4_counted", sum(row["stats"]["tk"] for row in brch_rows), 10)
    # And the choice is not verdict-breaking: both readings fell gate (b).
    for teamkills in (10, 11):
        check(
            f"identity.gate_b_fells_on_{teamkills}",
            t4_dom.failed_gates({"teamkills": teamkills, "kills_total": 1}),
            ["b:teamkill_share"],
        )

    # The precedence between the two sources, and that the second one is
    # actually reached. The qw-analyze card wins when it derives a pair, so an
    # envelope carrying a card stays recountable; KTX answers when it does not.
    good_card = {"teams": [{"name": "brch", "kills": 45, "frags": 34, "suicides": 3}]}
    check(
        "source.card_wins",
        t4_dom.pick_teamkills(good_card, ktx, "brch"),
        (8, 45, t4_dom.SOURCE_QW_CARD),
    )
    check(
        "source.ktx_when_no_card",
        t4_dom.pick_teamkills(None, ktx, "brch"),
        (10, 1, t4_dom.SOURCE_KTX_CARD),
    )
    check(
        "source.ktx_when_card_derives_nothing",
        t4_dom.pick_teamkills(
            {"teams": [{"name": "brch", "kills": 6, "frags": -5, "suicides": 0}]},
            ktx,
            "brch",
        ),
        (10, 1, t4_dom.SOURCE_KTX_CARD),
    )
    check(
        "source.nothing_anywhere",
        t4_dom.pick_teamkills(None, None, "brch"),
        (None, None, None),
    )
    check(
        "source.mvd_wins_for_shots",
        t4_dom.pick_shots(17, ktx, "brch"),
        (17, t4_dom.SOURCE_MVD_AMMO),
    )
    check(
        "source.ktx_shots_when_no_mvd",
        t4_dom.pick_shots(None, ktx, "brch"),
        (0, t4_dom.SOURCE_KTX_CARD),
    )
    check(
        "source.no_shots_anywhere",
        t4_dom.pick_shots(None, None, "brch"),
        (None, None),
    )

    # --- Rond 3, Sol 2026-08-24 ------------------------------------------
    # A KTX-sourced number has to be recountable out of the archived bytes,
    # and the path an envelope may name has to be a path it cannot abuse.
    card_bytes = (
        card_path.parent.parent / "valid" / "demos"
        / "4on4_frog[dm3]20260824-1642.txt"
    ).read_bytes()
    recount = t4_dom.recount_card(card_bytes, t4_dom.BRANCH_TEAM)
    check(
        "card.sha256",
        recount["sha256"],
        "b54bdbf1a0a5acbd167aaf5abbe00fd5436f069ae5bde4f5f79a42ad7ed9513c",
    )
    check("card.recount_shots", recount["shots"], 0)
    check("card.recount_teamkills", recount["teamkills"], 10)
    check("card.recount_kills", recount["kills"], 1)
    check("card.readable", recount["readable"], True)
    # One byte different is a different card, and it says so before it says
    # anything else.
    tampered = t4_dom.recount_card(card_bytes + b"\n", t4_dom.BRANCH_TEAM)
    check("card.one_byte_changes_the_digest", tampered["sha256"] != recount["sha256"], True)
    unreadable = t4_dom.recount_card(b"not a card at all", t4_dom.BRANCH_TEAM)
    check("card.unreadable", unreadable["readable"], False)
    check("card.unreadable_counts", unreadable["teamkills"], None)
    # The path guard. The validator opens whatever the envelope names, so an
    # envelope must not be able to steer it out of its own directory.
    check("card.path_ok", t4_dom.safe_relative_card_path("demos/x.txt"), "demos/x.txt")
    for bad in (
        "/etc/passwd",
        "../cards/x.txt",
        "demos/../../etc/passwd",
        "demos\\x.txt",
        "C:/x.txt",
        "",
        "   ",
        " demos/x.txt",
        "demos//x.txt",
        "./x.txt",
        None,
        17,
    ):
        check(f"card.path_refused[{bad!r}]", t4_dom.safe_relative_card_path(bad), None)
    # The runner must not write a KTX number whose card it could not archive.
    reading = {
        "shots": 0,
        "shots_source": t4_dom.SOURCE_KTX_CARD,
        "teamkills": 10,
        "kills": 1,
        "teamkills_source": t4_dom.SOURCE_KTX_CARD,
    }
    kept = t4_dom.drop_unprovenanced(reading, {"path": "demos/x", "sha256": "a" * 64})
    check("archive.card_kept", kept, reading)
    dropped = t4_dom.drop_unprovenanced(reading, None)
    check(
        "archive.no_card_no_number",
        dropped,
        {
            "shots": None,
            "shots_source": None,
            "teamkills": None,
            "kills": None,
            "teamkills_source": None,
        },
    )
    # A reading from the MVD is untouched: its provenance is not the card.
    mvd_reading = {
        "shots": 402,
        "shots_source": t4_dom.SOURCE_MVD_AMMO,
        "teamkills": 8,
        "kills": 45,
        "teamkills_source": t4_dom.SOURCE_QW_CARD,
    }
    check("archive.other_sources_survive", t4_dom.drop_unprovenanced(mvd_reading, None), mvd_reading)

    # Containment is about where the bytes live, not about how the string
    # looks: a directory that is a symlink carries a path with no `..` in it
    # straight out of the bundle (QA, 2026-08-25). Built with a real symlink so
    # the check is exercised, not described.
    with _tempfile.TemporaryDirectory(prefix="rtx-t4-card-") as temp:
        bundle = Path(temp) / "evidence"
        (bundle / "demos").mkdir(parents=True)
        (bundle / "demos" / "card.txt").write_bytes(card_bytes)
        outside = Path(temp) / "someone-elses"
        outside.mkdir()
        (outside / "card.txt").write_bytes(card_bytes)
        (bundle / "utanfor").symlink_to(outside, target_is_directory=True)
        check(
            "card.contained_ok",
            t4_dom.contained_card_path(bundle, "demos/card.txt"),
            (bundle / "demos" / "card.txt").resolve(),
        )
        check(
            "card.symlink_escape_refused",
            t4_dom.contained_card_path(bundle, "utanfor/card.txt"),
            None,
        )
        # A symlink that stays inside the bundle is fine — the rule is
        # containment, not a ban on symlinks.
        (bundle / "inside").symlink_to(bundle / "demos", target_is_directory=True)
        check(
            "card.symlink_inside_allowed",
            t4_dom.contained_card_path(bundle, "inside/card.txt"),
            (bundle / "demos" / "card.txt").resolve(),
        )
        check(
            "card.textual_escape_still_refused",
            t4_dom.contained_card_path(bundle, "../someone-elses/card.txt"),
            None,
        )

    # The contract version is the compatibility mechanism, not optionality.
    check("card.contract_bumped", t4_dom.T4_SCHEMA, 3)
    check("card.old_contract_still_supported", 2 in t4_dom.SUPPORTED_T4_SCHEMAS, True)
    check(
        "card.required_from",
        t4_dom.T4_SCHEMA_CARD_REQUIRED <= t4_dom.T4_SCHEMA,
        True,
    )

    # Punkt 3: the stillness instrument. A bot that never moved, sampled at the
    # spec's own 1.0 s, over a whole 300 s match.
    class FakeControl:
        def __init__(self) -> None:
            self.events: list[Any] = []

        def request(self, *args: Any, **kwargs: Any) -> Any:
            return {"data": {"bots": []}}

    def still_over_a_match(window_s: float | None) -> tuple[Any, Any]:
        side = (
            t3_mod._Side("branch", Path("/nonexistent"), 1)
            if window_s is None
            else t3_mod._Side("branch", Path("/nonexistent"), 1, sample_window_s=window_s)
        )
        side.control = FakeControl()
        side.status_bots = lambda: [
            {"ent": 1, "alive": True, "origin": [0.0, 0.0, 0.0], "frags": 0}
        ]
        watch = t4_mod._StillWatch()
        for tick in range(301):
            watch.maybe_sample(side, 1000.0 + tick * t4_dom.STILL_SAMPLE_INTERVAL_S)
        return watch, side

    # The window the runner actually hands the side channel, not one the test
    # picked: a window at or below the sampling period measures nothing.
    check(
        "still.wiring_covers_the_period",
        t4_mod.SIDE_SAMPLE_WINDOW_S > t4_dom.STILL_SAMPLE_INTERVAL_S,
        True,
    )
    watch, side = still_over_a_match(t4_mod.SIDE_SAMPLE_WINDOW_S)
    check("still.motionless_match", watch.still_s_per_bot(side), 300.0)
    check("still.measured", watch.measured(side), True)
    check(
        "still.gate_fells",
        t4_dom.failed_gates({"still_s_per_bot_max": watch.still_s_per_bot(side)}),
        ["c:still_s"],
    )
    # The old window against the spec's own sampling period: it accumulated
    # nothing and would have reported 0.0 — the best possible value — for the
    # worst possible truth. It now reports unavailable instead.
    blind_watch, blind_side = still_over_a_match(None)
    check("still.old_window_accumulates_nothing", blind_side.still_s, 0.0)
    check("still.old_window_is_unavailable", blind_watch.still_s_per_bot(blind_side), None)
    check("still.old_window_not_measured", blind_watch.measured(blind_side), False)

    # A normally moving bot does not fell the gate. 200 units per second is
    # ordinary running; the stillness test is `speed < 16`.
    moving_side = t3_mod._Side("branch", Path("/nonexistent"), 1, sample_window_s=3.0)
    moving_side.control = FakeControl()
    position = {"x": 0.0}

    def moving_bots() -> list[dict[str, Any]]:
        position["x"] += 200.0
        return [{"ent": 1, "alive": True, "origin": [position["x"], 0.0, 0.0], "frags": 0}]

    moving_side.status_bots = moving_bots
    moving_watch = t4_mod._StillWatch()
    for tick in range(301):
        moving_watch.maybe_sample(moving_side, 2000.0 + tick * 1.0)
    check("still.moving_match", moving_watch.still_s_per_bot(moving_side), 0.0)
    check(
        "still.moving_does_not_fell",
        t4_dom.failed_gates(
            {"still_s_per_bot_max": moving_watch.still_s_per_bot(moving_side)}
        ),
        [],
    )

    # Punkt 5: the demo flush wait. A fixed sleep let the teardown kill the
    # recording while it was still in the server's cache.
    with _tempfile.TemporaryDirectory(prefix="rtx-t4-flush-") as temp:
        demo_dir = Path(temp)
        empty = demo_dir / "4on4_frog[dm3]-empty.mvd"
        empty.write_bytes(b"")
        # Tonight's exact shape: the file exists, correctly named, 0 bytes.
        receipt = runlib_mod.wait_for_demo_flush(
            demo_dir, 0.0, timeout_s=0.6, stable_s=0.2, poll_s=0.1
        )
        check("flush.zero_bytes_times_out", receipt["state"], "timeout")
        check("flush.zero_bytes_reported", receipt["bytes"], 0)
        written = demo_dir / "4on4_frog[dm3]-written.mvd"
        written.write_bytes(b"MVD" * 1000)
        receipt = runlib_mod.wait_for_demo_flush(
            demo_dir, 0.0, timeout_s=3.0, stable_s=0.2, poll_s=0.1
        )
        check("flush.written_is_flushed", receipt["state"], "flushed")
        check("flush.written_bytes", receipt["bytes"], 3000)
        check("flush.names_the_file", receipt["path"], written.name)
    receipt = runlib_mod.wait_for_demo_flush(None, 0.0, timeout_s=0.2)
    check("flush.no_demo_dir", receipt["state"], "no-demo-dir")
    with _tempfile.TemporaryDirectory(prefix="rtx-t4-flush-") as temp:
        receipt = runlib_mod.wait_for_demo_flush(
            Path(temp), 0.0, timeout_s=0.4, stable_s=0.2, poll_s=0.1
        )
        check("flush.nothing_appeared", receipt["state"], "timeout")
        check("flush.nothing_named", receipt["path"], None)

    # §6: a FAIL without a matching T1/T3 run says so in words.
    import tempfile

    with tempfile.TemporaryDirectory(prefix="rtx-t4-alarm-") as temp:
        empty = Path(temp)
        check(
            "cross_alarm.none",
            t4_mod.cross_alarm(empty, "a" * 40, "2026-08-24T12:00:00Z"),
            t4_dom.NO_CROSS_ALARM,
        )
        for name, tier, started in (
            ("t3-early.json", "T3", "2026-08-24T10:00:00Z"),
            ("t3-late.json", "T3", "2026-08-24T11:30:00Z"),
            ("t3-after.json", "T3", "2026-08-24T13:00:00Z"),
            ("t3-other.json", "T3", "2026-08-24T11:45:00Z"),
        ):
            (empty / name).write_text(
                json.dumps(
                    {
                        "tier": tier,
                        "run_id": name[:-5],
                        "started_utc": started,
                        "build": {
                            "commit": "b" * 40 if name == "t3-other.json" else "a" * 40
                        },
                    }
                ),
                encoding="utf-8",
            )
        check(
            "cross_alarm.nearest_preceding",
            t4_mod.cross_alarm(empty, "a" * 40, "2026-08-24T12:00:00Z"),
            "t3-late",
        )
    return failures


def _name_resolution_units() -> list[str]:
    """Every global name a runner function reads must exist in its module.

    PR #67 half-ported two lines out of `t4.py` into `t3.py`: it called
    `wait_for_demo_flush` without importing it and read a `demo_dir` that was
    never defined. Python resolves neither until the line runs, and the line
    runs only after a five-minute match on a live rig — so both shipped, and on
    2026-08-25 two played T3 matches raised `NameError` between the last frag
    and the envelope and lost their measurements.

    The check is the lookup the interpreter itself performs: for every function
    in the runner package, take the names `symtable` says are read as globals
    and require the module to have them (or the builtins). It costs
    milliseconds, needs no rig, and it is deliberately about names and not
    about types — a half-port is a spelling mistake with a five-minute fuse.
    """
    import builtins
    import importlib
    import symtable

    # Already found, already reported, never silently tolerated. The check went
    # red on main the first time it ran, on a site older than the regression it
    # was written for: `t2._analyzer_metrics` lost its
    # `for key, measurement in measurements.items():` loop header in 7301ee7
    # (2026-08-19). The body was left behind, re-indented under
    # `if disagreements:` *after* the `raise`, so T2's per-metric demo links
    # have been unreachable dead code since — and would raise `NameError` on
    # `measurement` and `key` if they ever were reached. It is the same
    # half-ported-edit family as the t3.py crash but a different tier's
    # evidence, so it is reported to Fable (2026-08-25) rather than
    # drive-by-fixed inside a regression PR. The pin is two-sided: a new
    # unresolved name fails, and a pinned one that gets fixed or moves fails
    # too, so this comment cannot outlive the defect it describes.
    reported = {
        ("t2", "t2._analyzer_metrics", "measurement"),
        ("t2", "t2._analyzer_metrics", "key"),
    }

    failures: list[str] = []
    unresolved: set[tuple[str, str, str]] = set()
    package = Path(__file__).resolve().parent
    for source_path in sorted(package.glob("*.py")):
        module_name = source_path.stem
        if module_name == "__init__":
            continue
        source = source_path.read_text(encoding="utf-8")
        try:
            module = importlib.import_module(f".{module_name}", __package__)
        except ImportError as exc:  # pragma: no cover - a broken tree, not a lint
            failures.append(f"names.{module_name}: cannot import: {exc}")
            continue
        top = symtable.symtable(source, str(source_path), "exec")

        def visit(table: "symtable.SymbolTable", scope: str, module: Any = module) -> None:
            for symbol in table.get_symbols():
                name = symbol.get_name()
                if not symbol.is_global() or not symbol.is_referenced():
                    continue
                if name.startswith("__") and name.endswith("__"):
                    continue
                if hasattr(module, name) or hasattr(builtins, name):
                    continue
                unresolved.add((module.__name__.rsplit(".", 1)[-1], scope, name))
            for child in table.get_children():
                visit(child, f"{scope}.{child.get_name()}")

        visit(top, module_name)
    for module_name, scope, name in sorted(unresolved - reported):
        failures.append(
            f"names.{module_name}: {scope} reads {name!r}, which is neither "
            f"defined nor imported in {module_name}.py"
        )
    for module_name, scope, name in sorted(reported - unresolved):
        failures.append(
            f"names.{module_name}: the pinned finding {scope}/{name!r} is gone "
            "— fix the code and the pin in the same change, never one alone"
        )
    return failures


def _demo_selector_units() -> list[str]:
    """The demo a match's evidence is read from is the match's own demo.

    KTX opens a new recording the moment the match ends, so "newest .mvd in the
    directory" names the wrong file exactly when the runner asks. Measured on
    2026-08-25: the flush wait sat out its full 90 s on a 0-byte post-match
    recording while the match's own 3 102 072-byte demo lay finished beside it,
    and the envelope's scoreboard went unavailable although the demo existed.
    """
    import os
    import tempfile as _tempfile
    from datetime import datetime

    from . import runlib as runlib_mod
    from . import t3 as t3_mod

    failures: list[str] = []

    def check(name: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    # The measured shape, to the byte and to the minute: the match began at
    # 07:03:50 and recorded `...-0703`; KTX's post-match recording is `...-0709`
    # and is the newer file on disk.
    began = datetime(2026, 8, 25, 7, 3, 50).timestamp()
    match_name = "4on4_brch_vs_ref[dm3]20260825-0703"
    after_name = "4on4_brch_vs_ref[dm3]20260825-0709"

    with _tempfile.TemporaryDirectory(prefix="rtx-demoval-") as temp:
        demo_dir = Path(temp)
        match_demo = demo_dir / f"{match_name}.mvd"
        match_demo.write_bytes(b"\0" * 3102072)
        after_demo = demo_dir / f"{after_name}.mvd"
        after_demo.write_bytes(b"")
        os.utime(match_demo, (began + 300, began + 300))
        os.utime(after_demo, (began + 400, began + 400))

        check(
            "demoval.match_not_newest",
            runlib_mod.select_match_demo(
                [after_demo, match_demo], began
            ).name,
            match_demo.name,
        )
        # And through the wait itself: the whole point is a receipt that names
        # the match's demo and its bytes instead of timing out on 0.
        receipt = runlib_mod.wait_for_demo_flush(
            demo_dir, began, timeout_s=3.0, stable_s=0.2, poll_s=0.1
        )
        check("demoval.flush_state", receipt["state"], "flushed")
        check("demoval.flush_path", receipt["path"], match_demo.name)
        check("demoval.flush_bytes", receipt["bytes"], 3102072)

        # The card chooser is the same chooser: the number and the demo it is
        # read out of can never point at two different matches.
        for stem in (match_name, after_name):
            (demo_dir / f"{stem}.txt").write_text("{}", encoding="utf-8")
        os.utime(demo_dir / f"{match_name}.txt", (began + 300, began + 300))
        os.utime(demo_dir / f"{after_name}.txt", (began + 400, began + 400))
        check(
            "demoval.card_is_the_match_card",
            t3_mod.match_demoinfo(demo_dir, began).name,
            f"{match_name}.txt",
        )

    # A directory holding nothing but the 0-byte post-match recording still
    # times out honestly — an unavailable field, never a quiet zero.
    with _tempfile.TemporaryDirectory(prefix="rtx-demoval-") as temp:
        demo_dir = Path(temp)
        (demo_dir / f"{after_name}.mvd").write_bytes(b"")
        receipt = runlib_mod.wait_for_demo_flush(
            demo_dir, began, timeout_s=0.6, stable_s=0.2, poll_s=0.1
        )
        check("demoval.zero_bytes_times_out", receipt["state"], "timeout")
        check("demoval.zero_bytes_named", receipt["path"], f"{after_name}.mvd")
        check("demoval.zero_bytes_reported", receipt["bytes"], 0)

    # An older match's demo, still being flushed while this one starts, is not
    # this match's demo however early its name sorts.
    with _tempfile.TemporaryDirectory(prefix="rtx-demoval-") as temp:
        demo_dir = Path(temp)
        previous = demo_dir / "4on4_brch_vs_ref[dm3]20260825-0650.mvd"
        previous.write_bytes(b"old")
        this_match = demo_dir / f"{match_name}.mvd"
        this_match.write_bytes(b"new")
        check(
            "demoval.previous_match_ignored",
            runlib_mod.select_match_demo([previous, this_match], began).name,
            this_match.name,
        )
        # ... and the minute-truncated stamp of our own match still counts: it
        # may sit up to 59 s before the wallclock the runner recorded.
        check(
            "demoval.own_stamp_within_the_minute",
            runlib_mod.select_match_demo([this_match], began).name,
            this_match.name,
        )

    # A demo whose name carries no stamp at all falls back to the old
    # newest-by-mtime rule rather than to nothing.
    with _tempfile.TemporaryDirectory(prefix="rtx-demoval-") as temp:
        demo_dir = Path(temp)
        first = demo_dir / "4on4_frog[dm3]-first.mvd"
        first.write_bytes(b"a")
        second = demo_dir / "4on4_frog[dm3]-second.mvd"
        second.write_bytes(b"b")
        os.utime(first, (began, began))
        os.utime(second, (began + 60, began + 60))
        check(
            "demoval.unstamped_falls_back_to_newest",
            runlib_mod.select_match_demo([first, second], began).name,
            second.name,
        )
    return failures


def _t3_path_units() -> list[str]:
    """T3's post-match path, driven end to end without a rig.

    Everything between "the match ended" and "the envelope is written" used to
    be reachable only through a live server, eight clients and five minutes of
    play, which is how two NameErrors shipped in it. Here the four rig-facing
    calls are fakes — the status socket, the client processes, the control
    channel and the build identity — and every line the regression lived in is
    the real one: the flush wait, the demo chooser, the KTX card read, the
    scoreboard call, the payload assembly and the envelope write.

    The assertion is deliberately the whole outcome and not the absence of a
    traceback: a run that crashes here still writes an envelope, but a *failed*
    one with no payload, which is exactly what the two lost matches produced.
    """
    import tempfile as _tempfile
    from datetime import datetime, timedelta

    from . import runlib as runlib_mod
    from . import t3 as t3_mod

    failures: list[str] = []

    def check(name: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    class _FakeProcess:
        returncode = None

        def poll(self) -> None:
            return None

    class _FakeControl:
        def __init__(self) -> None:
            self.events: list[Any] = []

        def request(self, *args: Any, **kwargs: Any) -> Any:
            return {"data": {"bots": []}}

        def close(self) -> None:
            pass

    frags_by_side = {"branch": 54, "reference": 5}

    class _OfflineSide(t3_mod._Side):
        """The real side, with only its four rig-facing calls stubbed."""

        def launch(self, *args: Any, **kwargs: Any) -> None:
            self.process = _FakeProcess()
            self.log = None
            self._moved = 0.0

        def connect(self, deadline: float) -> None:
            self.control = _FakeControl()

        def status_bots(self) -> list[dict[str, Any]]:
            self._moved += 64.0
            return [
                {
                    "ent": index,
                    "alive": True,
                    "origin": [self._moved, 0.0, 0.0],
                    "frags": frags_by_side[self.side] // 4,
                }
                for index in range(1, 5)
            ]

        def shutdown(self) -> None:
            self.control = None
            self.process = None

    # Standby for the preflight, running once for the start gate, Standby again
    # so the match ends on the next lifecycle poll.
    replies = {"count": 0}

    def _fake_serverinfo(host: str, port: int, timeout: float = 3.0) -> dict[str, str]:
        replies["count"] += 1
        status = "5 min left" if replies["count"] == 2 else "Standby"
        return {
            "status": status,
            "mode": "4on4",
            "timelimit": "5",
            "map": "dm3",
            "hostname": "offline",
        }

    def _fake_identity(config: dict[str, Any], server_status: Any = None) -> dict[str, Any]:
        return {
            "branch": "offline",
            "commit": "c" * 40,
            "digest_md5": "0badcafe",
            "dirty": False,
        }

    # The recording starts once the match does — after the runner did. Naming
    # the fixtures off the current minute keeps the chooser's real rule under
    # test instead of a date that fell out of its window years ago.
    soon = datetime.now() + timedelta(seconds=30)
    match_stem = f"4on4_brch_vs_ref[dm3]{soon.strftime('%Y%m%d-%H%M')}"
    after_stem = (
        "4on4_brch_vs_ref[dm3]"
        f"{(soon + timedelta(minutes=6)).strftime('%Y%m%d-%H%M')}"
    )

    with _tempfile.TemporaryDirectory(prefix="rtx-t3-path-") as temp:
        root = Path(temp)
        demo_dir = root / "ktxdemos"
        demo_dir.mkdir()
        (root / "evidence").mkdir()
        for side in ("branch", "reference"):
            (root / f"{side}-client").write_bytes(side.encode("ascii"))
        # The match's demo and its card, plus the post-match recording KTX opens
        # seconds later — the pair that made the scoreboard unavailable.
        (demo_dir / f"{match_stem}.mvd").write_bytes(b"\0" * 4096)
        (demo_dir / f"{after_stem}.mvd").write_bytes(b"")
        (demo_dir / f"{match_stem}.txt").write_text(
            json.dumps(
                {
                    "demo": f"{match_stem}.mvd",
                    "map": "dm3",
                    "players": [
                        {"name": f"brch{n}", "team": "brch", "stats": {"frags": f}}
                        for n, f in enumerate((14, 14, 13, 13), start=1)
                    ]
                    + [
                        {"name": f"ref{n}", "team": "ref", "stats": {"frags": f}}
                        for n, f in enumerate((2, 1, 1, 1), start=1)
                    ],
                }
            ),
            encoding="utf-8",
        )
        config_text = "\n".join(
            [
                'schema = "rtx-testflow-config/1"',
                "[server]",
                'host = "127.0.0.1"',
                "control_port = 65401",
                'protocol = "auto"',
                "[paths]",
                'evidence_dir = "evidence"',
                'demos_dir = "evidence/demos"',
                "[build]",
                'repo_dir = "."',
                'engine_binary = ""',
                "[t2]",
                "duration_s = 600",
                "[t3]",
                "duration_s = 300",
                'reference_client = "reference-client"',
                'branch_client = "branch-client"',
                "seats_per_side = 4",
                'match_server = "127.0.0.1:65402"',
                'basedir = "."',
                "control_port_base = 65403",
                'reference_branch = "main"',
                f'reference_commit = "{"a" * 40}"',
                'demoinfo_dir = "ktxdemos"',
                "[t4]",
                "duration_s = 300",
                "skills = [10, 12, 14, 16, 18, 20]",
                'frogbot_server = "127.0.0.1:65404"',
                "control_port = 65405",
                'demoinfo_dir = "ktxdemos"',
                "[restore]",
                'rtx_bot_count = "0"',
                "[tools]",
                'qw_analyze = ""',
                "",
            ]
        )
        config_file = root / "config.toml"
        config_file.write_text(config_text, encoding="utf-8")
        config = runlib_mod.load_config(config_file)

        saved = (
            t3_mod._Side,
            t3_mod._udp_serverinfo,
            runlib_mod.build_identity,
        )
        t3_mod._Side = _OfflineSide
        t3_mod._udp_serverinfo = _fake_serverinfo
        runlib_mod.build_identity = _fake_identity
        error: str | None = None
        path: Path | None = None
        try:
            path = t3_mod.run(config)
        except BaseException as exc:  # the regression itself, reported by name
            error = f"{exc.__class__.__name__}: {exc}"
        finally:
            (t3_mod._Side, t3_mod._udp_serverinfo, runlib_mod.build_identity) = saved

        check("t3path.no_exception", error, None)
        if path is None:
            return failures
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        check("t3path.envelope_complete", document["status"], "complete")
        check("t3path.envelope_has_no_error", document.get("error"), None)
        payload = document.get("payload", {})
        check("t3path.verdict", payload.get("verdict"), "PIPELINE-OK")
        check("t3path.oracle", payload.get("result", {}).get("oracle"), "ktx-demoinfo")
        # The frags come out of the card the chooser picked, so this number is
        # also the proof that it picked the match's card and not the empty one.
        check(
            "t3path.frags",
            {side["side"]: side["frags"] for side in payload.get("sides", [])},
            frags_by_side,
        )
        check(
            "t3path.mvd_is_the_match_demo",
            payload.get("result", {}).get("mvd"),
            f"{match_stem}.mvd",
        )
        # And the envelope validates as evidence, not merely as JSON.
        try:
            validate_result(document, str(path))
        except ValidationError as exc:
            failures.append(f"t3path.envelope_validates: {exc}")
    return failures


def run(fixtures: str | Path) -> tuple[int, int]:
    root = Path(fixtures)
    accepted = rejected = 0
    failures: list[str] = []
    for path in sorted((root / "valid").iterdir()):
        try:
            if path.suffix == ".json":
                validate_result(
                    json.loads(path.read_text(encoding="utf-8")), str(path)
                )
            elif path.suffix == ".toml":
                with path.open("rb") as stream:
                    validate_scenario_result(tomllib.load(stream), str(path))
            else:
                continue
            accepted += 1
        except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError, ValidationError) as exc:
            failures.append(f"valid fixture rejected: {path}: {exc}")
    # Every broken fixture promises a reason in its name, and rejection for
    # any other reason is a silent lie: an edit to the validator could trip an
    # earlier, unrelated check and this test would still count the fixture
    # rejected. The expected fragment pins each one to its promise.
    expected = json.loads(
        (root / "broken" / "expected.json").read_text(encoding="utf-8")
    )
    for path in sorted((root / "broken").iterdir()):
        if path.name == "expected.json":
            continue
        try:
            if path.suffix == ".json":
                validate_result(
                    json.loads(path.read_text(encoding="utf-8")), str(path)
                )
            elif path.suffix == ".toml":
                with path.open("rb") as stream:
                    validate_scenario_result(tomllib.load(stream), str(path))
            else:
                continue
        except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError, ValidationError) as exc:
            fragment = expected.get(path.name)
            if fragment is None:
                failures.append(f"broken fixture not pinned in expected.json: {path.name}")
            elif fragment not in str(exc):
                failures.append(
                    f"broken fixture rejected for the wrong reason: {path.name}:"
                    f" wanted {fragment!r} in {exc}"
                )
            else:
                rejected += 1
        else:
            failures.append(f"broken fixture accepted: {path}")
    stale = set(expected) - {p.name for p in (root / "broken").iterdir()}
    if stale:
        failures.append(f"expected.json names missing fixtures: {sorted(stale)}")
    failures.extend(_t2_units())
    failures.extend(_t4_units())
    failures.extend(_name_resolution_units())
    failures.extend(_demo_selector_units())
    failures.extend(_t3_path_units())
    if failures:
        raise AssertionError("\n".join(failures))
    return accepted, rejected
