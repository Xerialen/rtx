"""K2: the team-damage gate on T3, and the three components beside it.

`SPEC — T4-domen v7, DEL B` (owner decision 2026-08-25). The owner picked the
strictest of the three readings QA laid out, so the gate is on the **full
envelope**:

    team_share = (damage to teammates + damage to self) / all damage dealt

with a ceiling of 20 %. The three components — weapon damage on a teammate,
telefrag damage on a teammate, self damage — are reported separately in every
envelope and are never themselves a gate. That split is QA's requirement from
2026-08-25 and it is what keeps the number readable: a team that telefrags its
way to 15 % and a team that shoots its way there are not the same team, and the
gate alone cannot tell them apart.

Why KTX's own card and not the analyzer's stream view: the stream reconstructs
**nominal** damage and the card counts what the server actually **applied**.
QA localised the whole difference to single events — one LG discharge in water
reads 854 nominal against 250 applied, because the victim did not have 854 left
to lose. The owner asked about damage that lands on the team, so the server's
count is the only source this module will read.

Nothing here imports anything but the standard library and `t4_dom`'s reading
primitives: the validator has to stay offline, and "a readable counter" must
mean exactly one thing across both tiers rather than two subtly different
things.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# Deliberately the same three primitives the T4 dom reads its card with. They
# are private to `t4_dom` only in the sense that nothing outside the runner
# should call them; duplicating "what counts as a readable non-negative whole
# counter" into a second module is how two tiers start disagreeing about the
# same card.
from .t4_dom import BRANCH_TEAM, SOURCE_KTX_CARD, _counter, _ktx_rows, _number

#: The T3 measurement contract. An envelope that carries it is judged under the
#: K2 gate; one that does not is from before the gate existed and is judged
#: exactly as it was (v7 §B.1).
T3_SCHEMA = 1
SUPPORTED_T3_SCHEMAS = (1,)

#: The capability name, declared in `capabilities.unavailable` exactly like
#: `t1:stall` and `t4:teamkills` when the quota cannot be measured.
CAP_TEAM_DAMAGE = "t3:team_damage"

#: The gate (v7 §B.3). Owner decision 2026-08-25: 20 % on the full envelope,
#: which is the strictest of the three readings and therefore the one that
#: contains all of them. Seam: strictly greater fells, equal does not — the
#: same discipline as the T4 dom's two calibrated seams.
TEAM_DAMAGE_SHARE_MAX = 0.20

VERDICTS = ("OK", "FAIL", "OMÄTT")

#: What the K2 verdict writes when it fell, in `failed_gates` form so the
#: string is greppable next to T4's `b:teamkill_share`.
GATE_TEAM_DAMAGE = "k2:team_damage_share"


def thresholds() -> dict[str, Any]:
    """The gate constant, as written into every T3 envelope that carries K2."""
    return {"team_damage_share_max": TEAM_DAMAGE_SHARE_MAX}


def expresses_weapon_damage(document: Any) -> bool:
    """Whether this card carries per-weapon damage at all.

    KTX omits a weapon's `damage` block when that weapon dealt none, so a team
    whose every weapon block lacks it looks identical to a card whose format
    does not carry per-weapon damage in the first place. The negative control
    is the same one `ktx_shots` applies to accuracy: the document must show the
    block somewhere — on either team — before a zero from it is believed.

    Without this, the two 2026-08-24 cards would report `team_weapon_damage: 0`
    for a match the analyzer knows nothing about, and a zero that means "the
    card cannot say" would be indistinguishable from one that means "they never
    hit each other".
    """
    if not isinstance(document, dict):
        return False
    players = document.get("players")
    if not isinstance(players, list):
        return False
    for player in players:
        if not isinstance(player, dict):
            continue
        weapons = player.get("weapons")
        if not isinstance(weapons, dict):
            continue
        for weapon in weapons.values():
            if not isinstance(weapon, dict):
                continue
            damage = weapon.get("damage")
            if not isinstance(damage, dict):
                continue
            if any(_counter(damage, field) is not None for field in ("enemy", "team")):
                return True
    return False


def _weapon_team_damage(row: Any, *, expressed: bool) -> int | None:
    """One player's weapon damage on teammates, or None when unreadable.

    A weapon with no `damage` block dealt none *with that weapon* — but only
    once the card has been shown to express the block at all, which is the
    caller's `expressed` flag. A block that is present and unreadable is a card
    we do not understand, and that is a None rather than a skipped term.
    """
    if not expressed or not isinstance(row, dict):
        return None
    weapons = row.get("weapons")
    if not isinstance(weapons, dict):
        return None
    total = 0
    for weapon in weapons.values():
        if not isinstance(weapon, dict):
            continue
        damage = weapon.get("damage")
        if damage is None:
            continue  # never dealt damage with this weapon
        team = _counter(damage, "team")
        if team is None:
            return None
        total += team
    return total


def measure(document: Any, team: str = BRANCH_TEAM) -> dict[str, Any]:
    """The K2 numbers for one team, straight off a parsed KTX card.

    Returns the envelope's `measured` and `components` blocks. Every field is a
    number or `None`; a `None` is an absence and is never rendered as a zero
    anywhere downstream. `total_given == 0` makes the quota `None` because a
    quota with no denominator is not a small number, it is no number — and it
    hides nothing: all three terms are non-negative, so a zero denominator
    forces a zero numerator.
    """
    rows = _ktx_rows(document, team)
    blank = {
        "measured": {
            "given_enemy": None,
            "given_team": None,
            "given_self": None,
            "total_given": None,
            "team_share": None,
        },
        "components": {
            "team_weapon_damage": None,
            "team_telefrag_damage": None,
            "self_damage": None,
        },
    }
    if rows is None:
        return blank
    given_enemy = given_team = given_self = 0
    for row in rows:
        damage = row.get("dmg") if isinstance(row, dict) else None
        enemy = _counter(damage, "given")
        mate = _counter(damage, "team")
        own = _counter(damage, "self")
        if enemy is None or mate is None or own is None:
            return blank
        given_enemy += enemy
        given_team += mate
        given_self += own
    total = given_enemy + given_team + given_self
    share = round((given_team + given_self) / total, 6) if total else None

    expressed = expresses_weapon_damage(document)
    weapon_team: int | None = 0
    for row in rows:
        part = _weapon_team_damage(row, expressed=expressed)
        if part is None:
            weapon_team = None
            break
        weapon_team += part
    telefrag: int | None = None
    if weapon_team is not None:
        # A card whose weapon posts exceed its own team total is internally
        # inconsistent; the difference is then not a telefrag count and is not
        # reported as one. The headline stands regardless — it reads `dmg.team`
        # directly and does not depend on this subtraction.
        telefrag = given_team - weapon_team if weapon_team <= given_team else None
    return {
        "measured": {
            "given_enemy": given_enemy,
            "given_team": given_team,
            "given_self": given_self,
            "total_given": total,
            "team_share": share,
        },
        "components": {
            "team_weapon_damage": weapon_team,
            "team_telefrag_damage": telefrag,
            "self_damage": given_self,
        },
    }


def failed_gates(measured: Any, limits: Any = None) -> list[str]:
    """The K2 gate, judged only when it was measured.

    The ratio is recomputed from the whole counters rather than read off the
    envelope's rounded `team_share`, so the seam is on the real number.

    The comparison is a float division against the envelope's ceiling —
    `(team + own) / total > ceiling`, where `ceiling` is read from
    `limits["team_damage_share_max"]` and not from the module literal —
    because that is the ratio the spec names, read straight off the whole
    counters. It is *not* a guard against a cross multiplication that would
    land elsewhere: at the seam the two forms agree. `0.20 * 1000` is exactly
    `200.0`, and across every total in 1..200000 that can express exactly 20 %
    there is no case where `t > 0.20 * N` differs from `t / N > 0.20`.

    That agreement is bounded by the ceilings this envelope actually carries —
    0.20 in the module, 0.2 and 0.5 in the schema fixtures, 0.05 in
    `selftest.py` — and must not be generalised to ceilings in general: at
    0.29 (`t=29`, `N=100`, because `0.29 * 100` is `28.999999999999996`) and
    at 0.7 (`t=63`, `N=90`) the two forms genuinely disagree, so the choice of
    form is free only here (QA deviation A-6, 2026-08-25).

    An earlier revision of this docstring claimed `0.20 * 1000` rounds to
    `200.00000000000003` and that the cross multiplication would therefore be
    "a hair kinder". That was false; QA disproved it exhaustively on
    2026-08-25 (deviation A1) and the motivation is corrected here. No number
    and no comparison changed — the seam has always been strict `>`, so an
    exact 20 % passes.
    """
    block = measured if isinstance(measured, dict) else {}
    limits = limits if isinstance(limits, dict) else thresholds()
    ceiling = _number(limits.get("team_damage_share_max"))
    team = _number(block.get("given_team"))
    own = _number(block.get("given_self"))
    total = _number(block.get("total_given"))
    if ceiling is None or team is None or own is None or total is None or total <= 0:
        return []
    return [GATE_TEAM_DAMAGE] if (team + own) / total > ceiling else []


def adjudicate(measured: Any, limits: Any = None) -> dict[str, Any]:
    """`OK` / `FAIL` / `OMÄTT` for the K2 block, with the reason it came from."""
    block = measured if isinstance(measured, dict) else {}
    limits = limits if isinstance(limits, dict) else thresholds()
    ceiling = _number(limits.get("team_damage_share_max"))
    failed = failed_gates(block, limits)
    if failed:
        share = block.get("team_share")
        return {
            "verdict": "FAIL",
            "failed_gates": failed,
            "reason": (
                f"lagskada {share} av all utdelad skada överstiger taket"
                f" {ceiling}"
            ),
        }
    if _number(block.get("team_share")) is None:
        return {
            "verdict": "OMÄTT",
            "failed_gates": [],
            "reason": (
                "lagskadekvoten kunde inte mätas ur KTX-kortet"
                f" ({CAP_TEAM_DAMAGE})"
            ),
        }
    return {
        "verdict": "OK",
        "failed_gates": [],
        "reason": (
            f"lagskada {block.get('team_share')} av all utdelad skada ligger"
            f" inom taket {ceiling}"
        ),
    }


def recount_card(raw: bytes, team: str = BRANCH_TEAM) -> dict[str, Any]:
    """Everything K2 can say about one team, straight from a card's bytes.

    Bytes rather than a parsed document, for the same reason `t4_dom` takes
    them: the sha256 an envelope pins hashes bytes, so the recount and the
    digest have to be about the same object.
    """
    digest = hashlib.sha256(raw).hexdigest()
    try:
        document = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return {"sha256": digest, "readable": False, **measure(None, team)}
    return {
        "sha256": digest,
        "readable": isinstance(document, dict),
        **measure(document, team),
    }


def block(document: Any, card: Any, team: str = BRANCH_TEAM) -> dict[str, Any]:
    """The whole `payload.team_damage` block a T3 run writes.

    A number whose card could not be archived is dropped rather than reported:
    the validator recounts K2 out of the archived bytes, so a reading nobody can
    check is unavailable — the same rule `drop_unprovenanced` applies to T4.
    """
    reading = measure(document, team) if card is not None else measure(None, team)
    dom = adjudicate(reading["measured"])
    return {
        "source": SOURCE_KTX_CARD if card is not None else None,
        "card": card,
        "team": team,
        "measured": reading["measured"],
        "components": reading["components"],
        "thresholds": thresholds(),
        "verdict": dom["verdict"],
        "failed_gates": dom["failed_gates"],
        "reason": dom["reason"],
    }
