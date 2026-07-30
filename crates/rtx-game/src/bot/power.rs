// SPDX-License-Identifier: AGPL-3.0-or-later

//! **Power score** — how dangerous a fighter is, given what they carry.
//!
//! This is a port of a frozen, measured scoring function (v1.2), not a hand-tuned heuristic. It was
//! fitted on 550 dm3 4on4 games (968k player-samples on a 5 s grid) as a Poisson GLM of *kills over
//! the next 60 seconds* against the player's material state, with **a fixed effect per player per
//! game**. The fixed effects are the whole trick: they absorb who is playing and how fast the game
//! is, so the surviving curve is identified only from within-player contrasts — you-with-the-RL
//! versus you-without-it, same game, same opponents. Skill never leaks into the item values.
//!
//! Because the model has a log link the pieces *multiply*, and because the score is published as a
//! ratio against a fresh spawn the fixed effects cancel. So the number this module returns reads as:
//!
//! > **a multiplier on this fighter's own fresh-spawn scoring rate.**
//!
//! A fresh spawn (100 health, no armor, shotgun) is exactly `1.0`. A full red-armor stack holding a
//! fed RL+LG is `5.26`. Measured league average is ≈0.57 frags per 60 s at power 1.0, so power `4`
//! means roughly 2.3 frags a minute — but the bot only ever uses *ratios* of this quantity, which is
//! what the construction actually licenses.
//!
//! What the shape buys us over the [`total_strength`](super::goals::total_strength) EHP currency the
//! bot used before:
//!
//! - **The gun outweighs the armor.** An RL at spawn health is ×2.4; grinding 100 → 300 effective
//!   health with that RL only adds another ×1.7.
//! - **Ammo is state, not detail.** The cell curve is steep (a dry LG keeps 59% of its edge, a fed
//!   one reaches 94% of an RL); the rocket curve is shallow (a dry RL still keeps ~74%).
//! - **Armor quality mostly matters when you're weakly armed** — hence the heavy-gun adjustment,
//!   which nets the armor-class premium back to ≈1 once an RL or LG is in hand.
//! - **Powerups aren't flat.** A fresh quad turns a shotgun into an RL pickup (×2.52) but adds only
//!   ×1.32 on top of a heavy gun, and every powerup decays across the 30 s it is held.
//!
//! The constants are deliberately *not* per-map. The measurement was rerun on 500 dm2 games and the
//! laws held while every constant moved — scarcity sets the price — but a map's scarcity is already
//! visible to the bot through its own item catalog and respawn timers, so we keep one universal
//! table and let the map speak through availability rather than through a lookup keyed on its name.

use crate::defs::{Bits, Items};

/// How long quad/pentagram/ring last once picked up — the window the age bins divide up.
pub(crate) const POWERUP_DURATION: f32 = 30.0;

/// The power a dead player counts for when summing a team: a corpse is worth exactly the fresh spawn
/// it is about to become. Measured: at a fixed *equipment* gap, an advantage held as bodies is worth
/// −1.0 frags per body over the next minute — respawns give it straight back, while an advantage
/// held as equipment keeps paying. Counting corpses as zero would double-count a fading edge.
pub(crate) const DEAD_POWER: f32 = 1.0;

/// Forward team-frag prices for discrete events: what each one is measured to buy over the next 30
/// seconds *beyond what the pre-event state already implied*. These are what the power score cannot
/// see on its own — the value of an item to the side that takes it includes denying it to the other.
///
/// Note the two that overturn folklore: killing an unarmed enemy is worth slightly *less than
/// nothing* going forward (the frag already landed; the victim respawns at power 1.0, often better
/// off than the starved state he died in), and yellow armor prices equal to red as a *pickup* — red's
/// specialness on dm3 lives in the control channel, not in the jacket.
#[allow(
    dead_code,
    reason = "wired into goal pricing and the oracle's value tables in a later phase"
)]
pub(crate) mod price {
    /// Taking quad.
    pub(crate) const QUAD: f32 = 1.11;
    /// Taking pentagram.
    pub(crate) const PENT: f32 = 1.55;
    /// Taking red armor.
    pub(crate) const RED_ARMOR: f32 = 0.65;
    /// Taking yellow armor — measured equal to red as a pickup.
    pub(crate) const YELLOW_ARMOR: f32 = 0.64;
    /// Taking megahealth.
    pub(crate) const MEGA: f32 = 0.31;
    /// Killing an enemy who holds no big weapon: the spawn frag, worth nothing forward.
    pub(crate) const KILL_UNARMED: f32 = -0.12;
    /// Killing an RL/LG carrier: +0.48 of denial plus +0.51 more if the weapon changes sides.
    pub(crate) const KILL_ARMED: f32 = 0.55;
    /// Killing a quad carrier.
    pub(crate) const KILL_QUAD: f32 = 0.92;
}

/// How much *future* stack holding an area is worth, beyond the stack currently standing in it:
/// frags per minute swung by controlling the item's room over the trailing 30 seconds. Control is
/// the momentum channel — a team at even material that owns the armor timers is measurably ahead.
///
/// Weapon rooms are absent on purpose: once stack is held fixed, controlling them measured at zero
/// on dm3. (On dm2 the geography reshuffles entirely and the RL room *does* carry value; that is the
/// one place the "no per-map constants" rule costs us something, and it costs us a positive we
/// decline to claim rather than a negative we suffer.)
#[allow(dead_code, reason = "wired into the oracle's area-control planning in a later phase")]
pub(crate) mod control {
    /// Red-armor area.
    pub(crate) const RED_ARMOR: f32 = 1.75;
    /// Quad area.
    pub(crate) const QUAD: f32 = 1.60;
    /// Yellow-armor area.
    pub(crate) const YELLOW_ARMOR: f32 = 1.00;
}

/// The material state a power score is computed from. Deliberately plain data with no engine types:
/// the server-side bot fills it from its own entity, the netclient fills it from mirrored state, and
/// [`model`](super::model) fills it from a *believed* opponent estimate — same function, three
/// sources, so a bot never rates an enemy on a currency different from its own.
#[derive(Clone, Copy, Debug)]
pub(crate) struct PowerInput {
    pub(crate) health: f32,
    pub(crate) armor_value: f32,
    /// Absorption fraction: `0.3` green, `0.6` yellow, `0.8` red, `0` none.
    pub(crate) armor_type: f32,
    /// `v.items` bitfield (carried as `f32`, the engine type; tested via the [`Bits`] trait).
    pub(crate) items: f32,
    pub(crate) rockets: f32,
    pub(crate) cells: f32,
    /// Seconds since the quad was picked up, or `None` if not held. See [`powerup_age`].
    pub(crate) quad_age: Option<f32>,
    /// Seconds since the pentagram was picked up, or `None` if not held.
    pub(crate) pent_age: Option<f32>,
    pub(crate) ring: bool,
}

/// Seconds a powerup has been held, from the engine's `*_finished` timestamp, or `None` if it isn't
/// held. The fit bins this into thirds of the 30 s window because both quad and pent decay steadily
/// across it — quad especially, whose whole value is front-loaded into the seconds you hold it.
pub(crate) fn powerup_age(finished: f32, now: f32) -> Option<f32> {
    (finished > now).then(|| (POWERUP_DURATION - (finished - now)).clamp(0.0, POWERUP_DURATION))
}

/// Effective health: health plus the part of the armor that health can actually spend. Armor
/// absorbs a fraction `f` of incoming damage, so it runs out after `h·f/(1−f)` points no matter how
/// much is on the counter — 150 is all a yellow jacket can ever spend at 100 health.
///
/// Public because it is also the honest way to ask "can this fighter take a hit *right now*", which
/// is a different question from [`power`] and must not be answered with it. Power is an expectation
/// over the next minute, fitted on humans who break off and heal; a fighter on 19 health under 200
/// red armor scores about one and a half fresh spawns there while dying to a single direct rocket.
/// Effective health sees that for what it is: `19 + min(200, 76) = 95`.
pub(crate) fn effective_health(health: f32, armor_value: f32, armor_type: f32) -> f32 {
    let f = armor_type.clamp(0.0, 0.95);
    let spendable = if f > 0.0 { health * f / (1.0 - f) } else { 0.0 };
    health + armor_value.max(0.0).min(spendable)
}

/// Which fitted health curve a fighter is on. Zero armor points clears the class, exactly as the
/// game clears `armortype` when the jacket is spent.
///
/// **Green armor is not a fitted class** — it is scarce enough in 4on4 that the fit never separated
/// it. It rides the armorless curve at its own (f = 0.3) effective health and takes no heavy-gun
/// adjustment, which prices it as pure padding: strictly better than bare health, strictly worse
/// than the same effective health in yellow. That ordering is asserted in the tests, and it is the
/// conservative reading — the alternative, interpolating between the none and yellow curves, would
/// invent a shape the measurement never saw.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum ArmorClass {
    None,
    Green,
    Yellow,
    Red,
}

fn armor_class(armor_value: f32, armor_type: f32) -> ArmorClass {
    if armor_value <= 0.0 {
        return ArmorClass::None;
    }
    if armor_type >= 0.7 {
        ArmorClass::Red
    } else if armor_type >= 0.45 {
        ArmorClass::Yellow
    } else if armor_type > 0.0 {
        ArmorClass::Green
    } else {
        ArmorClass::None
    }
}

/// First knot of the fitted health curves, and the spacing between knots.
const EH_BASE: f32 = 25.0;
const EH_STEP: f32 = 5.0;

/// Health curve, armorless. Stops at EH 250 (a mega stack) because nothing above that was observed
/// without armor. Note it is **not monotonic**: it peaks at ×1.404 around EH 215 and falls away —
/// stacking pure health past a mega measured very slightly negative, which is a real finding (health
/// boxes are uncontrolled, so a big armorless stack marks a player who isn't on the armor cycle)
/// rather than an artifact to smooth out.
#[rustfmt::skip]
const HEALTH_NONE: [f32; 46] = [
    0.531, 0.585, 0.637, 0.686, 0.732, 0.773, 0.810, 0.843, 0.872, 0.897, //  25– 70
    0.919, 0.938, 0.954, 0.970, 0.985, 1.000, 1.017, 1.034, 1.053, 1.073, //  75–120
    1.094, 1.115, 1.137, 1.160, 1.183, 1.206, 1.229, 1.251, 1.273, 1.294, // 125–170
    1.314, 1.333, 1.350, 1.365, 1.378, 1.389, 1.397, 1.402, 1.404, 1.402, // 175–220
    1.397, 1.387, 1.374, 1.357, 1.336, 1.310,                             // 225–250
];

/// Health curve, yellow armor.
#[rustfmt::skip]
const HEALTH_YA: [f32; 80] = [
    0.802, 0.866, 0.927, 0.987, 1.044, 1.099, 1.150, 1.199, 1.244, 1.285, //  25– 70
    1.324, 1.360, 1.393, 1.425, 1.455, 1.483, 1.512, 1.539, 1.566, 1.593, //  75–120
    1.619, 1.644, 1.669, 1.694, 1.718, 1.741, 1.763, 1.785, 1.807, 1.827, // 125–170
    1.848, 1.867, 1.887, 1.905, 1.923, 1.941, 1.958, 1.974, 1.990, 2.006, // 175–220
    2.021, 2.036, 2.051, 2.065, 2.079, 2.093, 2.106, 2.119, 2.132, 2.145, // 225–270
    2.158, 2.171, 2.183, 2.196, 2.209, 2.221, 2.234, 2.247, 2.261, 2.274, // 275–320
    2.288, 2.302, 2.317, 2.332, 2.347, 2.363, 2.380, 2.397, 2.415, 2.434, // 325–370
    2.453, 2.474, 2.495, 2.517, 2.541, 2.565, 2.591, 2.618, 2.646, 2.676, // 375–420
];

/// Health curve, red armor.
#[rustfmt::skip]
const HEALTH_RA: [f32; 80] = [
    1.002, 1.053, 1.102, 1.149, 1.192, 1.233, 1.272, 1.307, 1.340, 1.370, //  25– 70
    1.399, 1.425, 1.449, 1.472, 1.495, 1.516, 1.538, 1.559, 1.581, 1.602, //  75–120
    1.623, 1.644, 1.665, 1.685, 1.706, 1.726, 1.747, 1.767, 1.787, 1.807, // 125–170
    1.827, 1.846, 1.866, 1.885, 1.905, 1.924, 1.943, 1.962, 1.981, 1.999, // 175–220
    2.018, 2.037, 2.055, 2.073, 2.092, 2.110, 2.128, 2.146, 2.164, 2.182, // 225–270
    2.200, 2.218, 2.236, 2.255, 2.273, 2.291, 2.309, 2.327, 2.345, 2.363, // 275–320
    2.382, 2.400, 2.419, 2.438, 2.456, 2.476, 2.495, 2.514, 2.534, 2.554, // 325–370
    2.574, 2.594, 2.615, 2.635, 2.657, 2.678, 2.700, 2.722, 2.745, 2.768, // 375–420
];

/// The heavy-gun armor correction, applied only while an RL or LG is in hand. The armor-class
/// curves above are fitted on light-gun holders, where being on the armor cycle is a real signal
/// ("this player is climbing the item ladder and is about to be dangerous"). Once a big gun is in
/// hand the gun already carries that information and the premium collapses, so this divides it back
/// out — for an armed player, effective health is close to a sufficient statistic.
const HEAVY_ARMOR_ADJUST_YA: f32 = 0.668;
const HEAVY_ARMOR_ADJUST_RA: f32 = 0.699;

/// Shotgun only — the reference weapon state, ×1.0 by construction.
const WA_SG: f32 = 1.0;
/// Grenade launcher / super shotgun / super nailgun / nailgun: real but not decisive.
const WA_MID: f32 = 1.27;
/// LG alone, by cells held: 0 / 1–15 / 16–30 / 31+. Steep — cells are the scarce resource on dm3
/// (three boxes, heavily contested) and most of what the gun is worth. A fed LG reaches 94% of an
/// RL; the gun's apparent inferiority in coarser fits was starvation, not the weapon.
const WA_LG_BY_CELLS: [f32; 4] = [1.626, 1.933, 2.157, 2.762];
/// RL alone, by rockets held: 0 / 1–2 / 3–5 / 6–10 / 11–20 / 21+. Shallow — even a dry RL keeps
/// about three-quarters of its excess value, because there are seven rocket boxes and sixty seconds
/// is plenty to re-ammo. (The 3–5 bin sitting a hair under 1–2 is fit noise, kept as measured.)
const WA_RL_BY_ROCKETS: [f32; 6] = [2.151, 2.393, 2.317, 2.672, 2.784, 2.893];
/// Both big guns, by cells held — the strongest weapon state there is, when it's fed.
const WA_RLG_BY_CELLS: [f32; 4] = [2.669, 2.834, 3.059, 3.284];

/// Upper-inclusive bin edges for cells and rockets, matching the fit's bins.
const CELL_EDGES: [f32; 3] = [0.0, 15.0, 30.0];
const ROCKET_EDGES: [f32; 5] = [0.0, 2.0, 5.0, 10.0, 20.0];

/// Quad, by the gun it multiplies and by how long it has been held. Quad *substitutes* for a weapon
/// rather than multiplying one: ×4 damage makes a weak gun real, while an RL already one-shots. So a
/// fresh quad turns a shotgun player into the equivalent of an RL pickup, and adds almost nothing to
/// a fighter who already has one.
const QUAD_LIGHT: [f32; 3] = [2.522, 1.888, 1.323];
const QUAD_HEAVY: [f32; 3] = [1.316, 1.220, 1.117];
/// Pentagram, by time held.
const PENT_BY_AGE: [f32; 3] = [2.003, 1.690, 1.386];
/// Ring of shadows — nearly nothing once the rest of the state is accounted for.
const RING: f32 = 1.103;

/// Linear interpolation along a fitted curve sampled every [`EH_STEP`] from [`EH_BASE`], clamped at
/// both ends (the curves are only defined over the range that was observed).
fn interp(curve: &[f32], eh: f32) -> f32 {
    let last = curve.len() - 1;
    let x = ((eh - EH_BASE) / EH_STEP).clamp(0.0, last as f32);
    let i = x.floor() as usize;
    if i >= last {
        return curve[last];
    }
    let t = x - i as f32;
    curve[i] * (1.0 - t) + curve[i + 1] * t
}

/// Pick a factor by upper-inclusive bin edges: `value <= edges[i]` selects `factors[i]`, and
/// anything above the last edge takes the final factor.
fn binned(value: f32, edges: &[f32], factors: &[f32]) -> f32 {
    for (i, &edge) in edges.iter().enumerate() {
        if value <= edge {
            return factors[i];
        }
    }
    factors[factors.len() - 1]
}

/// Whether a bitfield holds a *heavy* gun — the RL or LG. This one predicate drives three separate
/// pieces of the score (the armor adjustment, the ammo table, and the quad tier), which is not a
/// coincidence: all three are asking "is this fighter already lethal?".
fn holds_heavy(items: f32) -> bool {
    items.has(Items::ROCKET_LAUNCHER) || items.has(Items::LIGHTNING)
}

/// The weapon-and-ammo factor: one number covering which guns are held *and* whether they are fed.
/// Weapon flags alone mislead in both directions, so ammo is a first-class part of the state.
fn weapon_ammo(items: f32, rockets: f32, cells: f32) -> f32 {
    let rl = items.has(Items::ROCKET_LAUNCHER);
    let lg = items.has(Items::LIGHTNING);
    let mid = items.has(Items::GRENADE_LAUNCHER)
        || items.has(Items::SUPER_SHOTGUN)
        || items.has(Items::SUPER_NAILGUN)
        || items.has(Items::NAILGUN);
    match (rl, lg) {
        // Both guns are priced on cells: the RL is nearly always fed on dm3, so what separates a
        // strong two-gun state from a weak one is whether the LG has anything to fire.
        (true, true) => binned(cells, &CELL_EDGES, &WA_RLG_BY_CELLS),
        (true, false) => binned(rockets, &ROCKET_EDGES, &WA_RL_BY_ROCKETS),
        (false, true) => binned(cells, &CELL_EDGES, &WA_LG_BY_CELLS),
        // The plain nailgun was folded in with the fitted GL/SSG/SNG class: it is the same kind of
        // gun for this purpose — one that can win a fight it starts but cannot end one at range.
        (false, false) if mid => WA_MID,
        (false, false) => WA_SG,
    }
}

/// Which third of its 30-second life a powerup is in.
fn age_bin(age: f32) -> usize {
    if age < 10.0 {
        0
    } else if age < 20.0 {
        1
    } else {
        2
    }
}

/// **The power score**: expected kills over the next 60 seconds as a multiple of this fighter's own
/// fresh-spawn rate. `1.0` is a fresh spawn; a full stack with both guns fed is a little over `5`;
/// powerup states go higher still, which is by design — they are worth more than the material
/// ceiling for as long as they last.
pub(crate) fn power(i: &PowerInput) -> f32 {
    let class = armor_class(i.armor_value, i.armor_type);
    let eh = effective_health(i.health.max(0.0), i.armor_value, i.armor_type);
    let heavy = holds_heavy(i.items);

    let mut p = match class {
        // Green armor has no fitted curve of its own; it rides the armorless one at its own EH.
        ArmorClass::None | ArmorClass::Green => interp(&HEALTH_NONE, eh),
        ArmorClass::Yellow => interp(&HEALTH_YA, eh),
        ArmorClass::Red => interp(&HEALTH_RA, eh),
    };
    if heavy {
        p *= match class {
            ArmorClass::Yellow => HEAVY_ARMOR_ADJUST_YA,
            ArmorClass::Red => HEAVY_ARMOR_ADJUST_RA,
            // No adjustment for the armorless curve — and none for green, which shares it.
            ArmorClass::None | ArmorClass::Green => 1.0,
        };
    }
    p *= weapon_ammo(i.items, i.rockets.max(0.0), i.cells.max(0.0));
    if let Some(age) = i.quad_age {
        let table = if heavy { &QUAD_HEAVY } else { &QUAD_LIGHT };
        p *= table[age_bin(age)];
    }
    if let Some(age) = i.pent_age {
        p *= PENT_BY_AGE[age_bin(age)];
    }
    if i.ring {
        p *= RING;
    }
    p
}

/// How much harder it is worth working to deny something to a fighter of this power, as a multiplier
/// on the ordinary denial weight. `1.0` at around a bare rocket launcher; less against a fresh
/// spawn, nearly double against a full stack.
///
/// This is *not* the gap effect — that is already carried by the item's power gain to whoever takes
/// it, and the gap converts to frags one-for-one on its own. This is the second, separate finding:
/// at a **fixed** team total, power concentrated in one pair of hands is worth more than the same
/// power spread across four (+0.7 frags/min per unit of top-share gap on dm3, and four times that on
/// dm2's corridors, where one unbreakable carrier holds a chokepoint alone). So the same quad is a
/// worse thing to concede to their best player than to their worst, over and above what it adds to
/// their total — and a bot deciding whether to contest should feel that difference.
///
/// Clamped hard at both ends: this reweights a contest, it does not send a bot across the map.
pub(crate) fn threat_scale(power: f32) -> f32 {
    /// Power at which the scale is 1.0 — a fighter holding a fed rocket launcher and nothing else.
    const PIVOT: f32 = 2.7;
    (power / PIVOT).clamp(0.7, 1.8)
}

/// Sum a team's power, counting each dead member as [`DEAD_POWER`]. Pass `None` for a corpse.
///
/// Only the *gap* between two teams' totals predicts anything — the combined level does not — and
/// one unit of gap converts to about one team frag over the next minute. That 1:1 exchange is what
/// makes a threshold on this number interpretable rather than arbitrary.
#[allow(dead_code, reason = "wired into the oracle's team snapshot in a later phase")]
pub(crate) fn team_power<I: IntoIterator<Item = Option<f32>>>(members: I) -> f32 {
    members.into_iter().map(|p| p.unwrap_or(DEAD_POWER)).sum()
}

/// A fighter with nothing but the spawn kit — the model's own baseline, scoring exactly `1.0`.
///
/// The honest stand-in for an opponent nobody has seen: the same thing [`DEAD_POWER`] says about a
/// corpse. Pricing an unbelieved enemy at zero would read as "he wants nothing", which is the one
/// thing a player who just spawned certainly isn't.
pub(crate) fn spawn_input() -> PowerInput {
    PowerInput {
        health: 100.0,
        armor_value: 0.0,
        armor_type: 0.0,
        items: Items::SHOTGUN.as_f32(),
        rockets: 0.0,
        cells: 0.0,
        quad_age: None,
        pent_age: None,
        ring: false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A fighter with nothing but the spawn kit.
    fn spawn() -> PowerInput {
        PowerInput {
            health: 100.0,
            armor_value: 0.0,
            armor_type: 0.0,
            items: Items::SHOTGUN.as_f32(),
            rockets: 0.0,
            cells: 0.0,
            quad_age: None,
            pent_age: None,
            ring: false,
        }
    }

    fn close(a: f32, b: f32) {
        assert!((a - b).abs() < 0.01, "{a} != {b}");
    }

    /// The four reference points the frozen scorer publishes. If any of these move, the tables were
    /// transcribed wrong — they are the whole point of pinning a *measured* function in code.
    #[test]
    fn reference_points() {
        close(power(&spawn()), 1.00);

        let mut quaded = spawn();
        quaded.quad_age = Some(0.0);
        close(power(&quaded), 2.52);

        // A 250 EH mega stack carrying the RL with a working load of rockets.
        let mega_rl = PowerInput {
            health: 250.0,
            items: Items::SHOTGUN.as_f32() + Items::ROCKET_LAUNCHER.as_f32(),
            rockets: 10.0,
            ..spawn()
        };
        close(power(&mega_rl), 3.50);

        // The full red stack with both guns fed: the material ceiling.
        let full = PowerInput {
            health: 100.0,
            armor_value: 200.0,
            armor_type: 0.8,
            items: Items::SHOTGUN.as_f32() + Items::ROCKET_LAUNCHER.as_f32() + Items::LIGHTNING.as_f32(),
            rockets: 30.0,
            cells: 50.0,
            ..spawn()
        };
        close(power(&full), 5.26);

        // ...and the same fighter picking up a quad, above the material ceiling by design.
        let full_quad = PowerInput {
            quad_age: Some(0.0),
            ..full
        };
        close(power(&full_quad), 6.92);
    }

    /// Effective health caps armor by what the health behind it can spend.
    #[test]
    fn effective_health_caps_armor() {
        // 100 health behind yellow (0.6) can spend exactly 150 armor, no more.
        close(effective_health(100.0, 150.0, 0.6), 250.0);
        close(effective_health(100.0, 200.0, 0.6), 250.0);
        // Red absorbs 0.8, so 100 health could spend 400 — a full 200 jacket is all counted.
        close(effective_health(100.0, 200.0, 0.8), 300.0);
        close(effective_health(100.0, 0.0, 0.0), 100.0);
    }

    /// Zero armor points clears the class, as the game itself does.
    #[test]
    fn spent_armor_clears_class() {
        assert_eq!(armor_class(0.0, 0.8), ArmorClass::None);
        assert_eq!(armor_class(1.0, 0.8), ArmorClass::Red);
        assert_eq!(armor_class(1.0, 0.6), ArmorClass::Yellow);
        assert_eq!(armor_class(1.0, 0.3), ArmorClass::Green);
    }

    /// The gun outweighs the armor — the cleanest finding in the study, and the one that should
    /// reorder the bot's shopping list.
    #[test]
    fn weapon_beats_armor() {
        let rl_at_spawn_health = PowerInput {
            items: Items::SHOTGUN.as_f32() + Items::ROCKET_LAUNCHER.as_f32(),
            rockets: 10.0,
            ..spawn()
        };
        let full_stack_no_gun = PowerInput {
            armor_value: 200.0,
            armor_type: 0.8,
            ..spawn()
        };
        assert!(power(&rl_at_spawn_health) > power(&full_stack_no_gun));
    }

    /// Ammo moves the score on its own — a starved gun is a different state from a fed one.
    #[test]
    fn ammo_is_state() {
        let lg = |cells| PowerInput {
            items: Items::SHOTGUN.as_f32() + Items::LIGHTNING.as_f32(),
            cells,
            ..spawn()
        };
        assert!(power(&lg(50.0)) > power(&lg(20.0)));
        assert!(power(&lg(20.0)) > power(&lg(0.0)));
        // A fed LG comes within a few percent of an RL; the cell curve is the steep one.
        let rl = PowerInput {
            items: Items::SHOTGUN.as_f32() + Items::ROCKET_LAUNCHER.as_f32(),
            rockets: 25.0,
            ..spawn()
        };
        assert!(power(&lg(50.0)) > power(&rl) * 0.9);
        // The rocket curve is shallow: a dry RL keeps most of its edge.
        let dry_rl = PowerInput { rockets: 0.0, ..rl };
        assert!(power(&dry_rl) > power(&rl) * 0.7);
    }

    /// Bins are upper-inclusive and land exactly where the fit put them.
    #[test]
    fn bin_edges() {
        let lg = |cells| weapon_ammo(Items::LIGHTNING.as_f32(), 0.0, cells);
        close(lg(0.0), WA_LG_BY_CELLS[0]);
        close(lg(15.0), WA_LG_BY_CELLS[1]);
        close(lg(16.0), WA_LG_BY_CELLS[2]);
        close(lg(30.0), WA_LG_BY_CELLS[2]);
        close(lg(31.0), WA_LG_BY_CELLS[3]);

        let rl = |rockets| weapon_ammo(Items::ROCKET_LAUNCHER.as_f32(), rockets, 0.0);
        close(rl(0.0), WA_RL_BY_ROCKETS[0]);
        close(rl(2.0), WA_RL_BY_ROCKETS[1]);
        close(rl(20.0), WA_RL_BY_ROCKETS[4]);
        close(rl(21.0), WA_RL_BY_ROCKETS[5]);
    }

    /// Quad substitutes for a weapon: enormous on a shotgun, marginal on an RL. And it decays —
    /// which is why the bot must spend its quad seconds rather than bank them.
    #[test]
    fn quad_is_front_loaded_and_gun_dependent() {
        let light = |age| PowerInput {
            quad_age: Some(age),
            ..spawn()
        };
        let heavy = |age| PowerInput {
            items: Items::SHOTGUN.as_f32() + Items::ROCKET_LAUNCHER.as_f32(),
            rockets: 10.0,
            quad_age: Some(age),
            ..spawn()
        };
        // Decay across the three age bins, both tiers.
        assert!(power(&light(0.0)) > power(&light(12.0)));
        assert!(power(&light(12.0)) > power(&light(25.0)));
        assert!(power(&heavy(0.0)) > power(&heavy(25.0)));
        // The multiplier itself is far bigger for the weakly-armed fighter.
        let light_gain = power(&light(0.0)) / power(&spawn());
        let heavy_gain = power(&heavy(0.0)) / power(&heavy(35.0));
        assert!(light_gain > heavy_gain * 1.5, "{light_gain} vs {heavy_gain}");
    }

    /// Green armor is padding: better than bare health, worse than the same jacket in yellow.
    #[test]
    fn green_armor_is_padding() {
        let bare = spawn();
        let green = PowerInput {
            armor_value: 100.0,
            armor_type: 0.3,
            ..spawn()
        };
        let yellow = PowerInput {
            armor_value: 150.0,
            armor_type: 0.6,
            ..spawn()
        };
        assert!(power(&green) > power(&bare));
        assert!(power(&green) < power(&yellow));
    }

    /// The armor-class premium is real for a weakly-armed fighter and collapses once a big gun is
    /// in hand — the heavy-gun adjustment exists precisely to make that happen.
    #[test]
    fn armor_premium_collapses_when_armed() {
        // Same effective health (250), different jackets, shotgun only.
        let sg_ya = PowerInput {
            armor_value: 150.0,
            armor_type: 0.6,
            ..spawn()
        };
        let sg_bare = PowerInput {
            health: 250.0,
            ..spawn()
        };
        assert!(power(&sg_ya) > power(&sg_bare));

        let rl = |p: PowerInput| PowerInput {
            items: p.items + Items::ROCKET_LAUNCHER.as_f32(),
            rockets: 10.0,
            ..p
        };
        let armed_premium = power(&rl(sg_ya)) / power(&rl(sg_bare));
        assert!(armed_premium < 1.1, "premium should collapse, got {armed_premium}");
    }

    /// A powerup's age comes off the engine's expiry stamp, and a lapsed one is simply not held.
    #[test]
    fn powerup_age_from_expiry() {
        close(powerup_age(130.0, 100.0).unwrap(), 0.0);
        close(powerup_age(115.0, 100.0).unwrap(), 15.0);
        assert_eq!(powerup_age(100.0, 100.0), None);
        assert_eq!(powerup_age(0.0, 100.0), None);
    }

    /// Denial is weighted by who is about to take the thing, and bounded so it only ever reweights
    /// a contest.
    #[test]
    fn threat_scale_orders_the_enemy_team() {
        let spawn = threat_scale(1.0);
        let rl = threat_scale(2.7);
        let stacked = threat_scale(5.26);
        let quadded_stack = threat_scale(6.92);
        assert!(spawn < rl && rl < stacked);
        // A bare rocket launcher is the pivot: the historical flat weighting, unchanged.
        close(rl, 1.0);
        // Clamped at both ends, so neither a naked spawn nor a quadded full stack distorts the plan.
        assert_eq!(spawn, 0.7);
        assert_eq!(stacked, 1.8);
        assert_eq!(quadded_stack, 1.8);
    }

    /// A corpse counts as the fresh spawn it is about to become, so an advantage held as bodies
    /// doesn't read as an advantage held as equipment.
    #[test]
    fn dead_members_count_as_a_spawn() {
        close(team_power([Some(4.0), None, None]), 4.0 + 2.0 * DEAD_POWER);
        close(team_power([None, None]), 2.0);
    }
}
