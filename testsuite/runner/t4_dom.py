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
#: total on a real card is 5 takes per 300 s match, so zero across up to six
#: matches is far outside anything observed.
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
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def teamkills_from_card(card: Any, team: str) -> tuple[int | None, int | None]:
    """`(teamkills, kills)` for one team row, or `(None, None)`.

    The only place a teamkill-like number can be derived is the qw-analyze
    match card: `kills - frags - suicides` (§3). A missing card, a missing
    field, a non-numeric field or a negative component is *unavailable*, never
    a numeric zero — a zero here would say "they never shot each other", which
    is precisely what was not observed.
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
    if kills < 0 or suicides < 0:
        return None, None
    derived = kills - frags - suicides
    if derived < 0:
        # A negative teamkill count is a card that does not mean what the
        # formula assumes. Refuse it rather than clamp it to zero.
        return None, None
    return int(derived), int(kills)


def reached_from_ladder(ladder: Any) -> int:
    """The highest *won* skill; 0 when nothing was won (§2).

    A loss on the first rung is 0. A draw after a won rung N is N, because the
    draw rung was not won. This is the same meaning the runner has always had,
    written down once so the validator can recompute it instead of trusting it.
    """
    reached = 0
    if not isinstance(ladder, list):
        return reached
    for rung in ladder:
        if isinstance(rung, dict) and rung.get("win") is True:
            skill = rung.get("skill")
            if isinstance(skill, int) and not isinstance(skill, bool):
                reached = skill
    return reached


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
