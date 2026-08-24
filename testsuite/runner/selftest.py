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
    import tempfile as _tempfile

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
    if failures:
        raise AssertionError("\n".join(failures))
    return accepted, rejected
