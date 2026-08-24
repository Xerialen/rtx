"""The T4 verdict: five values, four measured gates, one priority order.

`SPEC — T4-domen v6` (owner definition 2026-08-24, Sol-approved). Every rule
here is the spec's, and the spec wins over prose anywhere else.

Why a module of its own: the runner produces the verdict and the validator has
to be able to *disagree* with it. Both call the same pure functions on the same
numbers, so a hand-edited envelope whose verdict does not follow from its own
measurements is caught by `checks.py` rather than believed. Nothing here
imports anything but the standard library — the validator must stay offline.

The vocabulary (§2):

* ``VINST``    — won against level 20, all four fields measured and green.
* ``OK``       — a played ladder that lost somewhere, all four measured, green.
* ``FAIL``     — some *measured* gate (a)-(d) fell. Beats every other value,
                 draw included.
* ``OMÄTT``    — no measured gate fell, but at least one of the four fields is
                 unavailable. Never green, never OK.
* ``OAVGJORD`` — the ladder ended in a draw with all four measured and green.

An unmeasured field is never a zero and never a pass: that is the one hard
rule the bench already lives by (`docs/SPEC-telemetry-availability.md`).
"""
from __future__ import annotations

from typing import Any

T4_SCHEMA = 2

VERDICTS = ("VINST", "OK", "FAIL", "OMÄTT", "OAVGJORD")
#: The verdicts a reader may treat as "the tier is green".
GREEN_VERDICTS = ("VINST", "OK")

#: Capability names, one per measurement path (§3). The envelope names the ones
#: it could not measure in `capabilities.unavailable`, exactly like `t1:stall`.
CAP_SHOTS = "t4:shots_fired"
CAP_TEAMKILLS = "t4:teamkills"
CAP_STILL = "t4:still_s"
CAP_ITEMS = "t4:item_chase"
T4_CAPABILITIES = (CAP_SHOTS, CAP_TEAMKILLS, CAP_STILL, CAP_ITEMS)

#: The measurement each capability stands for, in the payload's `measurements`.
CAP_MEASUREMENT = {
    CAP_SHOTS: "shots_fired",
    CAP_TEAMKILLS: "teamkills",
    CAP_STILL: "still_s_per_bot_max",
    CAP_ITEMS: "item_pickups",
}

#: `item_pickups` counts observed take-edges in the world item channel, which
#: cannot say *who* took the item. It is a proxy for "chased items" and every
#: reported (d) outcome has to say so.
LABEL_ITEM_PROXY = "item-pickups-proxy"

#: Our side's team name on the scoreboard card. Lives here rather than only in
#: `t4.py` because the validator recounts the teamkill derivation off the card
#: and has to look at the same row the runner did.
BRANCH_TEAM = "brch"

#: What a FAIL writes when no T1/T3 run of the same commit precedes it (§6).
NO_CROSS_ALARM = "no matching T1/T3 run found"

#: What an OAVGJORD envelope writes: the draw semantics (stop / replay /
#: continue) is a flagged owner question, not something this code decided.
DRAW_SEMANTICS = "ägarbeslut saknas"

# ---------------------------------------------------------------------------
# Calibrated constants (§4). Every one of them was computed from the existing
# corpus before the code was locked; the raw numbers are in
# `WORK_LOGS/2026-08-24-korkvitto-t4-bygge.md`. They live here as named
# constants, are copied into every envelope, and the validator refuses an
# envelope whose declared thresholds differ — a run cannot loosen its own gate.
# ---------------------------------------------------------------------------

#: (b) teamkills / max(1, kills_total). Highest share seen on a real scoreboard
#: card with a denominator that can carry a ratio (kills_total >= 20) was
#: 0.1778 (t3-20260819T184519Z-4f0b9106, team brch, 8/45). The spec's start
#: interval is 15-20 %; 0.20 is the top of it and leaves the observed maximum
#: below the gate. Seam: strictly greater fells, equal does not.
TEAMKILL_SHARE_MAX = 0.20

#: (c) still_s_per_bot for one 300 s match. p99 (nearest rank) of the recent
#: T3 corpus the spec names is 32.1 s; 75.0 s is ~2.3x that and the middle of
#: the spec's 60-90 s band. It is not decorative: 11 of the 34 side-rows in the
#: full historical corpus lie above it. Seam: strictly greater fells.
STILL_S_PER_BOT_MAX = 75.0

#: (d) item_pickups over the whole ladder. Zero fells. The lowest world-level
#: total on a real match card is **4** takes per 300 s match (12 cards carry
#: the item columns; the range is 4 to 29), so zero across up to six matches is
#: far outside anything observed.
#:
#: The T2 corpus says something that looks like the opposite and is not: 46 of
#: 51 ten-minute T2 runs recorded quad+pent takes of zero. That channel watches
#: exactly two slow, contested powerups, and zero is its ordinary reading. This
#: gate is on the wide world channel — every item the reply identifies, over up
#: to six matches — and the two are not the same measurement. That distinction
#: is the whole basis for the threshold, so the runner records
#: `measured.items_tracked` (how many distinct items the channel could see at
#: all): if a live ladder comes back tracking two, this gate is measuring the
#: T2 quantity and the threshold has to be revisited before it fells anything.
ITEM_PICKUPS_MIN = 1

#: (§3) The live side channel is sampled at this period, and a gap wider than
#: the ceiling makes `still_s` unavailable rather than interpolated.
STILL_SAMPLE_INTERVAL_S = 1.0
STILL_SAMPLE_GAP_MAX_S = 3.0

#: The item channel follows T2's poll pattern with T4's own period; the same
#: gap discipline applies.
ITEMS_POLL_S = 1.0
ITEMS_POLL_GAP_MAX_S = 3.0


def thresholds() -> dict[str, Any]:
    """The gate constants, as written into every v2 envelope."""
    return {
        "teamkill_share_max": TEAMKILL_SHARE_MAX,
        "still_s_per_bot_max": STILL_S_PER_BOT_MAX,
        "item_pickups_min": ITEM_PICKUPS_MIN,
        "still_sample_interval_s": STILL_SAMPLE_INTERVAL_S,
        "still_sample_gap_max_s": STILL_SAMPLE_GAP_MAX_S,
        "items_poll_s": ITEMS_POLL_S,
        "items_poll_gap_max_s": ITEMS_POLL_GAP_MAX_S,
    }


def _number(value: Any) -> float | None:
    """A finite number, or None.

    NaN and the infinities are rejected on purpose: `json.loads` accepts the
    literals `NaN`/`Infinity`, and NaN compares False against every bound, so
    a hand-written envelope carrying one would walk past every range check in
    this module and then blow up in `int()` with a ValueError the validator
    does not catch. An unrepresentable measurement is an absent one.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def teamkills_from_card(card: Any, team: str) -> tuple[int | None, int | None]:
    """`(teamkills, kills)` for one team row, or `(None, None)`.

    The only place a teamkill-like number can be derived is the qw-analyze
    match card: `kills - frags - suicides` (§3). Everything else about this
    function is the fail-closed ruling Fable made on 2026-08-24 after QA
    finding A1, and it is the spec's own sentence taken literally: a missing
    card, a missing field, a non-numeric field, **any** negative component, or
    a derived count larger than the team's own kills makes `t4:teamkills`
    unavailable. Never a numeric zero — a zero here would claim they never
    shot each other, which is precisely what was not observed.

    The corpus is why. Five real team rows carry `frags < 0`
    (`t3-20260728T170516Z-764bc135`, team `brch`: `kills=6, frags=-5,
    suicides=0`). The formula turns that into 11 teamkills on 6 kills — a
    share of 1.83, and gate (b) fells a match on a number that cannot mean
    what it says. A teamkill count above the team's own kill count is not a
    teamkill count.

    **Bokfört: the size guard is subsumed and cannot be pinned by a test.**
    `derived > kills` means `-frags - suicides > 0`, which with non-negative
    frags and suicides is arithmetically impossible — so every row the size
    guard would catch, the sign guard catches too. A mutation that removes the
    size guard alone therefore survives the suite, and it is listed as such in
    the receipt rather than dressed up with a test that proves nothing. It
    stays because the ruling names both conditions and because it is the
    second lock if the sign guard is ever relaxed; it is not evidence of
    anything on its own. The pair as a whole IS pinned, by the real corpus row
    and by an envelope fixture whose card the validator recounts.
    """
    if not isinstance(card, dict):
        return None, None
    rows = card.get("teams")
    if not isinstance(rows, list):
        return None, None
    row = next(
        (
            item
            for item in rows
            if isinstance(item, dict) and item.get("name") == team
        ),
        None,
    )
    if row is None:
        return None, None
    kills = _number(row.get("kills"))
    frags = _number(row.get("frags"))
    suicides = _number(row.get("suicides"))
    if kills is None or frags is None or suicides is None:
        return None, None
    derived = kills - frags - suicides
    if derived < 0 or derived > kills:
        return None, None
    if kills < 0 or frags < 0 or suicides < 0:
        return None, None
    return int(derived), int(kills)


# ---------------------------------------------------------------------------
# The KTX demoinfo card as a second source (spec addendum to v6 §3, ordered by
# Fable 2026-08-24 after QA's finding 2).
#
# `evidence.match_scoreboard` needs a non-empty MVD and the analyzer. The
# evening of 2026-08-24 the server was torn down before it flushed the demo, so
# the MVD was 0 bytes and both `shots_fired` and `teamkills` went unavailable —
# while the frag oracle in the very same run had already read KTX's own
# demoinfo card, which carries per-player `stats.kills`, `stats.tk` and
# `weapons.<w>.acc.attacks`. The truth was in the bundle; nothing was reading
# it. The card is a scoreboard, so it is a §3 source.
#
# Two things about this source differ from the qw-analyze card and are written
# down rather than assumed:
#
#  * `teamkills` is the card's OWN `stats.tk`, not `kills - frags - suicides`.
#
#    The reason is narrower than it first looks, and the first version of this
#    comment got it wrong (QA delta, 2026-08-24). KTX's counters are NOT
#    independent: the identity `frags = kills - tk - suicides` holds on 15 of
#    the 16 player rows across the evening's two cards — all 8 on the T3 card,
#    where the derivation equals `tk` exactly for both teams, and 7 of 8 on
#    the T4 card. The single exception is `bot.brch3` (`frags = -8` where
#    `kills - tk - suicides = -7`), and that one row is the whole of the
#    difference between a derived 11 and a counted 10 at team level.
#
#    So the rule is not "always prefer `tk`". It is: **when a row breaks the
#    identity the derivation cannot carry the number and the direct counter
#    can.** Here the derivation gives 11 teamkills on 1 kill, which the A1
#    guard refuses — correctly — leaving no number at all, which is the very
#    failure this source exists to repair. Reading a field that exists beats
#    deriving one that does not.
#  * `tk > kills` is therefore not a malformed reading on this card: `kills`
#    counts enemy kills and `tk` team kills, and 10 team kills against 1 enemy
#    kill is exactly what the gate wants to hear about.
#
# The choice is not verdict-breaking either way: 10/1 and 11/1 both sit far
# above the 20 % threshold, so gate (b) fells under both readings.
#
# What still makes it unavailable: a missing card, no rows for our team, a
# missing or non-numeric or negative counter. Never a zero standing in for an
# absence.
KTX_AMMO_WEAPONS = ("sg", "ssg", "ng", "sng", "gl", "rl", "lg")

SOURCE_QW_CARD = "qw-analyze/card"
SOURCE_KTX_CARD = "ktx/demoinfo"
SOURCE_MVD_AMMO = "mvd/ammo"
MEASUREMENT_SOURCES = (SOURCE_QW_CARD, SOURCE_KTX_CARD, SOURCE_MVD_AMMO)


def _ktx_rows(document: Any, team: str) -> list[dict[str, Any]] | None:
    if not isinstance(document, dict):
        return None
    players = document.get("players")
    if not isinstance(players, list) or not players:
        return None
    rows = [
        player
        for player in players
        if isinstance(player, dict) and str(player.get("team", "")) == team
    ]
    return rows or None


def _counter(block: Any, field: str) -> int | None:
    """A non-negative whole counter out of a card, or None."""
    if not isinstance(block, dict):
        return None
    value = block.get(field)
    number = _number(value)
    if number is None or number < 0 or number != int(number):
        return None
    return int(number)


def _ktx_attacks(row: Any) -> int | None:
    """One player's attack count, or None when the row cannot be read.

    KTX leaves the `acc` block out entirely for a weapon that was never fired,
    so an absent block is a zero *for that weapon* — but only once the card has
    been shown to express accuracy at all. That check lives in `ktx_shots`.
    """
    if not isinstance(row, dict):
        return None
    weapons = row.get("weapons")
    if not isinstance(weapons, dict):
        return None
    total = 0
    for weapon in weapons.values():
        if not isinstance(weapon, dict):
            continue
        accuracy = weapon.get("acc")
        if accuracy is None:
            continue  # never fired this weapon; KTX omits the block
        attacks = _counter(accuracy, "attacks")
        if attacks is None:
            # An accuracy block we cannot read is a card we do not understand.
            return None
        total += attacks
    return total


def ktx_shots(document: Any, team: str) -> int | None:
    """Shots for one team out of the KTX card's per-weapon attack counters.

    A zero here is only believed once the card has been shown capable of a
    non-zero reading: KTX omits `acc` for a weapon that was never fired, so a
    team with no `acc` blocks anywhere looks identical to a card whose format
    does not carry accuracy at all. The document must therefore contain at
    least one readable `acc.attacks` somewhere — on either team — before this
    returns a number. That is the negative control the bench's own rule
    demands, applied to the instrument before its output is used.

    The axe carries no counter here either, exactly as in the ammo signal, so
    this is a floor on the count rather than the whole of it — and a team that
    only ever axed reads as zero shots and fells gate (a), which is the honest
    reading of "did not shoot at the enemy".
    """
    rows = _ktx_rows(document, team)
    if rows is None:
        return None
    everyone = document.get("players") if isinstance(document, dict) else None
    expresses_accuracy = any(
        isinstance(player, dict)
        and isinstance(player.get("weapons"), dict)
        and any(
            isinstance(weapon, dict) and _counter(weapon.get("acc"), "attacks") is not None
            for weapon in player["weapons"].values()
        )
        for player in (everyone if isinstance(everyone, list) else [])
    )
    if not expresses_accuracy:
        return None
    total = 0
    for row in rows:
        attacks = _ktx_attacks(row)
        if attacks is None:
            return None
        total += attacks
    return total


def ktx_teamkills(document: Any, team: str) -> tuple[int | None, int | None]:
    """`(teamkills, kills)` for one team out of the KTX card's own counters."""
    rows = _ktx_rows(document, team)
    if rows is None:
        return None, None
    teamkills = 0
    kills = 0
    for row in rows:
        stats = row.get("stats")
        row_tk = _counter(stats, "tk")
        row_kills = _counter(stats, "kills")
        if row_tk is None or row_kills is None:
            return None, None
        teamkills += row_tk
        kills += row_kills
    return teamkills, kills


def pick_teamkills(
    card: Any, ktx_document: Any, team: str
) -> tuple[int | None, int | None, str | None]:
    """`(teamkills, kills, source)` from the best source that has an answer.

    The qw-analyze card wins whenever it derives a pair, so an envelope that
    carries a card can always be recounted against it. KTX's own card is the
    fallback for the case that produced this function: a demo the server never
    flushed leaves no MVD, no analyzer card, and the counters sitting unread in
    the demoinfo file the frag oracle already opened.
    """
    teamkills, kills = teamkills_from_card(card, team)
    if teamkills is not None:
        return teamkills, kills, SOURCE_QW_CARD
    teamkills, kills = ktx_teamkills(ktx_document, team)
    if teamkills is not None:
        return teamkills, kills, SOURCE_KTX_CARD
    return None, None, None


def pick_shots(
    mvd_shots: Any, ktx_document: Any, team: str
) -> tuple[int | None, str | None]:
    """`(shots, source)` — the MVD's ammo signal first, then the KTX card."""
    if _int_or_none(mvd_shots) is not None:
        return int(mvd_shots), SOURCE_MVD_AMMO
    shots = ktx_shots(ktx_document, team)
    if shots is not None:
        return shots, SOURCE_KTX_CARD
    return None, None


def reached_from_ladder(ladder: Any) -> int:
    """The highest *won* skill; 0 when nothing was won (§2).

    A loss on the first rung is 0. A draw after a won rung N is N, because the
    draw rung was not won.

    `max`, not "the last one that won". On a ladder that runs 10,12,14,16,18,20
    the two are the same number, and QA's mutation Q-M11 survived the whole
    suite for exactly that reason — the difference was invisible because the
    rung-order gate hid it. The spec says *highest won*, so that is what this
    computes, and a unit check feeds it an out-of-order ladder to keep the two
    readings apart even though the validator's own order gate would refuse
    such a ladder.
    """
    if not isinstance(ladder, list):
        return 0
    won = [
        rung["skill"]
        for rung in ladder
        if isinstance(rung, dict)
        and rung.get("win") is True
        and isinstance(rung.get("skill"), int)
        and not isinstance(rung.get("skill"), bool)
    ]
    return max(won) if won else 0


def ladder_outcome(ladder: Any, top_skill: int = 20) -> dict[str, Any]:
    """Whether the ladder drew, and whether it won the top rung."""
    rungs = ladder if isinstance(ladder, list) else []
    last = rungs[-1] if rungs and isinstance(rungs[-1], dict) else {}
    return {
        "drew": last.get("draw") is True,
        "won_top": last.get("win") is True and last.get("skill") == top_skill,
        "reached": reached_from_ladder(rungs),
    }


#: The per-rung measurement block a v2 rung carries, so the ladder's four
#: numbers can be read back to the matches they came from.
RUNG_MEASURED_FIELDS = (
    "shots_fired",
    "teamkills",
    "kills",
    "still_s_per_bot",
    "still_gap_max_s",
    "item_takes",
    "items_poll_gap_max_s",
    # How many distinct items the world channel could identify at all. No gate
    # reads it; it is the receipt that decides whether gate (d) means anything.
    # 46 of 51 ten-minute T2 runs recorded zero quad+pent takes, so a channel
    # that only ever sees those two powerups would make `item_pickups == 0`
    # the normal reading rather than the alarm. The first live run has to show
    # this number before (d) can be trusted in anger.
    "items_tracked",
)

#: Fields a rung MAY carry beside the required ones. They are optional so that
#: envelopes written before they existed keep validating: a field added to the
#: receipt must not retroactively fell evidence that was accepted when it was
#: written. Nothing here feeds a gate.
RUNG_MEASURED_OPTIONAL = (
    # Seconds spent waiting for the server to flush this match's demo, or null
    # when it never flushed. The receipt that says whether every demo-derived
    # field had a demo to derive from.
    "demo_flush_s",
)


def _rung_measured(rung: Any) -> dict[str, Any]:
    if not isinstance(rung, dict):
        return {}
    block = rung.get("measured")
    return block if isinstance(block, dict) else {}


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def measure_ladder(ladder: Any) -> dict[str, Any]:
    """Fold the per-rung measurements into the four fields the dom judges.

    A field is measured for the *ladder* only when every played rung measured
    it. Summing the rungs that happened to answer would produce a number about
    some other ladder than the one the verdict is about — and it would drift
    quietly, which is the failure this whole spec exists to stop.
    """
    fields: dict[str, Any] = {
        "shots_fired": None,
        "teamkills": None,
        "kills_total": None,
        "still_s_per_bot_max": None,
        "item_pickups": None,
    }
    rungs = ladder if isinstance(ladder, list) else []
    if not rungs:
        return fields
    per_rung = [_rung_measured(rung) for rung in rungs]
    shots = [_int_or_none(item.get("shots_fired")) for item in per_rung]
    if all(value is not None for value in shots):
        fields["shots_fired"] = sum(shots)
    teamkills = [_int_or_none(item.get("teamkills")) for item in per_rung]
    kills = [_int_or_none(item.get("kills")) for item in per_rung]
    if all(value is not None for value in teamkills + kills):
        fields["teamkills"] = sum(teamkills)
        fields["kills_total"] = sum(kills)
    still = [_number(item.get("still_s_per_bot")) for item in per_rung]
    if all(value is not None for value in still):
        fields["still_s_per_bot_max"] = max(still)
    takes = [_int_or_none(item.get("item_takes")) for item in per_rung]
    if all(value is not None for value in takes):
        fields["item_pickups"] = sum(takes)
    return fields


def sampling_receipt(ladder: Any) -> dict[str, Any]:
    """The worst gap either live channel saw anywhere in the ladder."""
    rungs = ladder if isinstance(ladder, list) else []
    per_rung = [_rung_measured(rung) for rung in rungs]

    def worst(key: str) -> float | None:
        seen = [_number(item.get(key)) for item in per_rung]
        seen = [value for value in seen if value is not None]
        return round(max(seen), 3) if seen else None

    return {
        "still_interval_s": STILL_SAMPLE_INTERVAL_S,
        "still_gap_max_s": worst("still_gap_max_s"),
        "items_poll_s": ITEMS_POLL_S,
        "items_poll_gap_max_s": worst("items_poll_gap_max_s"),
    }


def missing_fields(measurements: Any) -> list[str]:
    """The capability names whose measurement is absent, in a fixed order."""
    block = measurements if isinstance(measurements, dict) else {}
    missing = []
    for cap in T4_CAPABILITIES:
        value = block.get(CAP_MEASUREMENT[cap])
        if _number(value) is None:
            missing.append(cap)
            continue
        if cap == CAP_TEAMKILLS and _number(block.get("kills_total")) is None:
            # The share needs both halves; half a ratio is not a measurement.
            missing.append(cap)
    return missing


def failed_gates(measurements: Any, limits: Any = None) -> list[str]:
    """The measured gates (a)-(d) that fell. Unmeasured fields are not judged."""
    block = measurements if isinstance(measurements, dict) else {}
    limits = limits if isinstance(limits, dict) else thresholds()
    share_max = _number(limits.get("teamkill_share_max"))
    still_max = _number(limits.get("still_s_per_bot_max"))
    pickups_min = _number(limits.get("item_pickups_min"))
    failed: list[str] = []

    shots = _number(block.get("shots_fired"))
    if shots is not None and shots == 0:
        failed.append("a:shots_fired")

    teamkills = _number(block.get("teamkills"))
    kills_total = _number(block.get("kills_total"))
    if teamkills is not None and kills_total is not None and share_max is not None:
        # max(1, kills) keeps a lone teamkill in a kill-less match from
        # dividing by zero; it does not make the gate kinder.
        if teamkills / max(1.0, kills_total) > share_max:
            failed.append("b:teamkill_share")

    still = _number(block.get("still_s_per_bot_max"))
    if still is not None and still_max is not None and still > still_max:
        failed.append("c:still_s")

    pickups = _number(block.get("item_pickups"))
    if pickups is not None and pickups_min is not None and pickups < pickups_min:
        failed.append("d:item_pickups")
    return failed


def adjudicate(
    measurements: Any,
    outcome: Any,
    limits: Any = None,
) -> dict[str, Any]:
    """The whole verdict, in the spec's priority order (§5). First hit wins.

    `outcome` is `ladder_outcome()`'s dict. The order is FAIL > OMÄTT >
    OAVGJORD > VINST > OK, and it is not negotiable: a draw with a fallen
    measured gate is a FAIL, and a run with an unavailable field is never OK.
    """
    outcome = outcome if isinstance(outcome, dict) else {}
    limits = limits if isinstance(limits, dict) else thresholds()
    failed = failed_gates(measurements, limits)
    missing = missing_fields(measurements)
    if failed:
        verdict = "FAIL"
        reason = "fällda mätta grindar: " + ", ".join(failed)
    elif missing:
        verdict = "OMÄTT"
        reason = "omätta fält: " + ", ".join(missing)
    elif outcome.get("drew") is True:
        verdict = "OAVGJORD"
        reason = "stegen slutade oavgjort; alla fyra fält mätta och gröna"
    elif outcome.get("won_top") is True:
        verdict = "VINST"
        reason = "vann mot level 20; alla fyra fält mätta och gröna"
    else:
        verdict = "OK"
        reason = "spelad stege; alla fyra fält mätta och gröna"
    return {
        "verdict": verdict,
        "failed_gates": failed,
        "missing": missing,
        "reason": reason,
    }
