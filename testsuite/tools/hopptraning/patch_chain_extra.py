#!/usr/bin/env python3
"""ANDRING 2: rtx_sj_chain_extra — lankkostnad pa KEDJADE speedjump.

Obduktionsfynd (hopp 1, varv 1): alla 6 fall ar samma mekanism. Boten passerar
ringens ostra oppning vid x~496 for lagt och stoppas dod i luften (farten faller
412-484 -> under 60 pa EN bild), varefter den ramlar i grytan. Varje sadant fall
kommer ur en KEDJAD, curl-los speedjump (34501 syd / 34503 nord), vars avfart ar
sjalva cellcentrumet — det finns ingen egen ansats, sa banan ar ~9 u for lag vid
troskeln. Varje lyckad korsning i varv 1, och agarens bada egna demon, gick i
stallet over en CURL-lank med surveyed avfart langre bak (35738, avfart
[446,162]) och passerade oppningen pa z >= 90.

Andringen ar en lankkostnad, inte en grafmutation: en kedjad speedjump har ingen
egen ansats och ar darfor riskablare an en rollout-certifierad curl. Cvarn later
planeraren betala for den risken och valja curl-korsningen nar en finns.

Default 0.0 = dagens beteende, bit for bit. Ingen graf rors, inget recept behovs,
och atergang ar att satta cvarn till 0.
"""
import pathlib
import sys

ROOT = pathlib.Path("/home/xerial/rtx-ring2quad")


def patcha(rel, gammal, ny):
    p = ROOT / rel
    s = p.read_text()
    n = s.count(gammal)
    assert n == 1, f"{rel}: ankaret traffade {n} ganger, vill ha exakt 1"
    p.write_text(s.replace(gammal, ny, 1))
    print("patchad", rel)


# 1) Faltet pa LinkCosts (Default-derive ger 0.0 = inert).
patcha(
    "crates/rtx-nav/src/navmesh/mod.rs",
    """    /// Nonzero ⇒ charge every [`LinkKind::RocketJump`] link this many extra seconds — the per-bot
    /// capability gate.""",
    """    /// Nonzero ⇒ charge every **chained** speed jump this many extra seconds. A chained jump has no
    /// runway of its own — its `from` cell *is* the ledge — so its arc leaves from wherever the bot
    /// happens to cross the cell, with no surveyed run-up behind it. Measured on dm3's ring→quad
    /// crossing: every fall in the training series came off a chained gap link whose arc was ~9 u too
    /// low at the ring's east opening and stopped dead against it, while every crossing that landed
    /// used a rollout-certified curl with a surveyed takeoff. `0` (the default) leaves chained jumps
    /// at their solved cost — today's behaviour, bit for bit. Set it to make the planner buy the
    /// certified crossing when one exists. Far below [`CLOSED_GATE_PENALTY`], so it diverts a route
    /// and never forces one through a shut door.
    pub chain_extra: f32,
    /// Nonzero ⇒ charge every [`LinkKind::RocketJump`] link this many extra seconds — the per-bot
    /// capability gate.""",
)

# 2) Priset i link_extra, direkt efter rocket_jump-termen (samma form, samma skala).
patcha(
    "crates/rtx-nav/src/navmesh/mod.rs",
    """        if costs.rocket_jump_extra > 0.0 && link.kind == LinkKind::RocketJump {
            extra += costs.rocket_jump_extra;
        }""",
    """        if costs.rocket_jump_extra > 0.0 && link.kind == LinkKind::RocketJump {
            extra += costs.rocket_jump_extra;
        }
        if costs.chain_extra > 0.0
            && link.kind == LinkKind::SpeedJump
            && self.speed_jump_of_link(li).is_some_and(|t| t.chained)
        {
            extra += costs.chain_extra;
        }""",
)

# 3) Bar vardet genom bottens prissattning.
patcha(
    "crates/rtx-game/src/bot/mod.rs",
    """pub(crate) struct LinkPricing {
    gate_closed: Vec<bool>,
    penalties: Vec<(u32, f32)>,
    rj_extra: f32,
    hazard: Option<HazardPrice>,
}""",
    """pub(crate) struct LinkPricing {
    gate_closed: Vec<bool>,
    penalties: Vec<(u32, f32)>,
    rj_extra: f32,
    chain_extra: f32,
    hazard: Option<HazardPrice>,
}""",
)

patcha(
    "crates/rtx-game/src/bot/mod.rs",
    """            rocket_jump_extra: self.rj_extra,
            hazard: self.hazard,""",
    """            rocket_jump_extra: self.rj_extra,
            chain_extra: self.chain_extra,
            hazard: self.hazard,""",
)

patcha(
    "crates/rtx-game/src/bot/mod.rs",
    """        LinkPricing {
            gate_closed: self.gate_closed_flags(),
            penalties,
            rj_extra,""",
    """        LinkPricing {
            gate_closed: self.gate_closed_flags(),
            penalties,
            rj_extra,
            chain_extra: self.host().cvar(c"rtx_sj_chain_extra").max(0.0),""",
)

# 4) Cvarn, default 0.0.
patcha(
    "crates/rtx-game/src/cvars.rs",
    """        ("rtx_jump_curl_hold", Float(0.0)),""",
    """        // Extra seconds charged to every *chained* speed jump when routing (a jump whose `from`
        // cell is the ledge itself, so it has no surveyed run-up of its own). 0 = today's behavior.
        ("rtx_sj_chain_extra", Float(0.0)),
        ("rtx_jump_curl_hold", Float(0.0)),""",
)

print("KLART")
