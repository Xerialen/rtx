// SPDX-License-Identifier: AGPL-3.0-or-later

//! QuakeWorld movement kinematics: the pmove-derived constants and the closed-form jump/bhop math
//! (airtime, required entry speed, air-strafe speed gain) the graph build and the banded planner
//! share. Pure — no graph or BSP state. `STEP_HEIGHT` (pmove's free step-up) is shared from
//! [`crate::qphys`].

use crate::qphys::{AIR_CAP, JUMP_VZ};

// --- player + movement constants (QuakeWorld pmove) ---

/// Height delta treated as effectively flat ground (a `Walk`).
pub(super) const WALK_DZ: f32 = 8.0;
/// Largest one-way fall we'll encode as a landing (`Drop` links, and `JumpGap` links that leap
/// out and plunge). Deliberately huge: QW fall damage is a flat 5 HP past the 650 u/s landing
/// threshold, and deliberate multi-thousand-unit plunges are core movement — race maps are
/// *built* around them. The cost carries the real fall time (see `link_cost`), so a bot never
/// prefers a pit over a lift without reason.
pub(super) const MAX_DROP: f32 = 4096.0;
/// Double jumps keep the old, shallow landing floor: descent is already covered by the cheaper
/// Drop/JumpGap kinds, and the double-jump dedup is per-octant *without* elevation bands — a
/// deep pit target would shadow the level crossing the link kind exists for.
pub(super) const DJ_MAX_DROP: f32 = 240.0;
/// Fall height beyond which QW fall damage applies (`MAX_SAFE_FALL` ≈ when speed > 580).
pub(super) const SAFE_FALL: f32 = 88.0;
/// Landing vertical speed (u/s) above which QW inflicts fall damage. A ballistic arc (hook swing,
/// rocket jump) that lands harder than this is priced with the HP hit by the solvers.
pub(super) const FALL_DAMAGE_SPEED: f32 = 580.0;
/// Apex a standing jump adds: `jump_vel² / (2·gravity)` = `270² / 1600`. Public so a viewer can
/// re-fly a [`LinkKind::JumpGap`](super::LinkKind) arc with [`arc_point`](super::arc_point) using
/// the same apex the build cleared with.
pub const JUMP_APEX: f32 = 45.0;
/// Horizontal reach of a running jump (`maxspeed · air-time`), conservatively floored.
pub(super) const JUMP_REACH: f32 = 200.0;
/// Extra reach/rise unlocked by rtx's mid-air **double jump** (`rtx_doublejump`): a second jump near
/// the apex restacks a ~45u arc, roughly doubling both. Conservatively floored so a bot with slightly
/// off air-jump timing still clears the linked gap.
pub(super) const DOUBLE_JUMP_REACH: f32 = 300.0;
pub(super) const DOUBLE_JUMP_APEX: f32 = 80.0;
/// Clearance envelope for a double jump — the real two-arc path peaks ~91u above the launch, so
/// sample the arc a touch higher to be safe. Public for the same reason as [`JUMP_APEX`]: a viewer
/// re-flies a [`LinkKind::DoubleJump`](super::LinkKind) arc with [`arc_point`](super::arc_point) at
/// this apex.
pub const DOUBLE_ARC_PEAK: f32 = 100.0;
/// `sv_maxspeed` default — the cost denominator (travel time = distance / speed).
pub const MAX_SPEED: f32 = 320.0;
/// Speed a submerged bot makes: pmove drives swimming at 0.7× wishspeed, and there is no bunnyhop
/// under the surface. The banded planner prices a leg into water at this rather than crediting it the
/// speed gains a dry corridor of the same length would build.
pub const SWIM_SPEED: f32 = 0.7 * MAX_SPEED;

// --- speed jumps (bunnyhop-carried leaps across wide gaps) ---
//
// Two *different* run-up models live in this module, and which one a pass may use is not a taste
// call — it follows from what the run-up physically is:
//
// * **Air-strafe** (`bhop_k` → [`attainable_speed`] → [`runway_len_for`]/[`runway_time`]) models a
//   bunnyhop chain: speed grows as `(v0³+3k·len)^⅓` with `k` set by the *air* accel cap. It is the
//   right model for a long carried runway and it is deliberately **pessimistic** — `bhop`'s
//   `navmesh_speed_jump_model_is_conservative` pins the live controller at or above it, which is
//   what lets the straight pass trust a closed form with no rollout. Its price is saturation: it
//   needs ~316u to go 320→399 ups, so it can never fund a short run-up.
// * **Ground prestrafe** ([`prestrafe_delivered`]) models a *committed* run-up along a corridor:
//   circle-strafe on the floor, which builds ~4× faster and saturates near 1.5·maxspeed. It is
//   **optimistic** (it counts ticks as if travelling at `MAX_SPEED` while the modelled speed climbs
//   past it), so nothing may trust it on its own — every pass that funds a jump this way certifies
//   the leap by `pm_step` rollout and takes the *lowest* speed whose envelope lands.
//
// So: long carried runways → air-strafe, closed form. Short committed run-ups → prestrafe, but only
// behind certification. Do not cross the wires; in particular do not "fix" `bhop_k` by unclamping it
// from `AIR_CAP` to make short runways work — that inverts the conservativeness guarantee the
// straight pass rests on. Ground truth for both: `demos/dm3_rastairs.qwd`, `demos/dm3_rlstrafejump.qwd`.

/// Conservative server tickrate assumed for the bhop acceleration model (see [`crate::qphys`] on why
/// this deliberately differs from the live controller's ~77 Hz).
const SJ_TICKRATE: f32 = 72.0;
/// Speed we'll plan bhop runways up to (reach ≈ `V·0.675` ≈ 600u); real runways bound it further.
pub(super) const SPEED_JUMP_V_CAP: f32 = 900.0;
/// Derate the ideal bhop model to attainable speed (the S-weave + a friction frame per landing).
/// Calibrated against the controller's own pmove-oracle sim (`bhop::sim`): a 10s run covers
/// ~4500u and lands at ~0.75 of the ideal `(v0³+3k·len)^⅓` — 0.8 rides just above it, with
/// [`SJ_MARGIN`] absorbing the difference.
pub const BHOP_EFF: f32 = 0.8;
/// Longest runway we bother measuring. Sized so the model can credit the speeds the controller
/// demonstrably reaches (its sim sustains gains past 550 u/s over ~4500u): at 4096u the
/// effective takeoff is ~605 u/s — flat gaps to ~350u, dropping gaps to ~620u — where the old
/// 2048 cap forfeited everything past ~490 u/s. Race maps are what need the far end.
pub(super) const RUNWAY_MAX: f32 = 4096.0;
/// The measured runway must reach this multiple of the jump's required entry speed.
///
/// Calibrated against `demos/dm3_rastairs.qwd`, not intuition: the human clears those two gaps
/// carrying **438** and **395** ups where the straight chord needs 397 and 388 — margins of ×1.10
/// and ×1.02. The old 1.15 demanded 457/446, *above what a human actually carries in either case*,
/// so the generator refused crossings that are trivial in real play and left the bot detouring.
/// 1.05 sits just above the tighter of the two measurements. Raising this is expensive twice over:
/// [`runway_len_for`] is cubic in speed, so the runway demand grows ~3× faster than the margin.
pub const SJ_MARGIN: f32 = 1.05;
/// Speed-jump landing floor — separate from (and smaller than) [`MAX_DROP`] because the
/// target-scan radius grows with fall airtime (`reach = v · t`): 1024 quadruples the old 240
/// envelope while keeping the per-ledge scan bounded.
pub(super) const SJ_MAX_DROP: f32 = 1024.0;
/// At most this many stand-start speed-jump links per source cell.
pub(super) const SPEED_JUMP_MAX_PER_CELL: usize = 3;
/// At most this many *chained* speed-jump links per source cell — kept separate (and small) so a
/// chained candidate never evicts a self-contained stand-start jump from the per-cell budget.
pub(super) const SPEED_JUMP_CHAINED_MAX_PER_CELL: usize = 2;

// --- curl jumps (run-up along a corridor, turn in the air onto an offset landing) ---------------
//
// A curl jump is a speed jump whose run-up and leap are *not* collinear: the bot circle-strafes a
// corridor to the lip (the game's takeoff regime builds far past the air-strafe model's credit — the
// ground prestrafe equilibrium, ~1.5·maxspeed), leaps along the corridor, then `air_correct`-curls the
// velocity onto a platform offset to the side. Certified by a `pm_step` rollout against the BSP, so
// these constants bound that search rather than a closed-form reach.

/// Minimum run-up (units) behind the lip for a curl: enough corridor to fund the solved takeoff speed
/// and give the runtime's too-slow abort room. (The prestrafe oracle itself saturates by ~90-150u, so
/// this is a *corridor-quality* floor, not a speed one — dropping it to ~96 admits far more marginal
/// ledges than it buys and roughly doubled the per-map curl count without covering the demo jumps.)
pub(super) const CURL_MIN_RUNWAY: f32 = 192.0;
/// The run-up a curl link actually *commits* — capped short of the measured corridor (which can run
/// thousands of units). The ground prestrafe saturates by ~200u for the *speed*, but the from-cell is
/// placed this far back for *routing*: too short and the curl splits into walk-then-leap that ties a
/// competing route down the same corridor; far enough back it captures the whole approach as one leg
/// and wins cleanly (as short corridors like the bravado LG need), while race-map runways stay bounded.
pub(super) const CURL_RUNUP_CAP: f32 = 512.0;
/// The target must sit this far *off* the run-up heading (degrees, either side): below, the straight
/// speed-jump pass owns it; above, `air_correct` at curl speed can't converge within the airtime.
/// (Lowering this floor to admit near-straight prestrafe jumps — the dm3 `curl_mid` / dm4 chain demos —
/// roughly doubled the per-map curl count for no demo coverage: those are blocked by the *interior*
/// takeoff and off-compass run-up heading, not by the angle. See the curl-jump memory.)
pub(super) const CURL_ANGLE_LO: f32 = 5.0;
pub(super) const CURL_ANGLE_HI: f32 = 78.0;
/// Landing tolerances for accepting a certified curl: horizontal miss and vertical miss to the target
/// cell centre, across every envelope corner.
pub(super) const CURL_MISS_TOL: f32 = 24.0;
pub(super) const CURL_Z_TOL: f32 = 24.0;
/// Half-width (degrees) of the launch-heading envelope the certified gain must cover — the ground
/// prestrafe exits mid-weave, so the real takeoff heading wanders this much around the corridor.
///
/// **Measured, not chosen.** Flying dm3's balcony links repeatedly and reading the velocity heading on
/// the frame the bot leaves the ground gives −7° to +12° around the run-up axis: the leap fires when
/// the bot crosses the takeoff *line*, which can happen at any phase of the speed-building serpentine,
/// and above `sv_maxspeed` a straight coast cannot pull the heading back (ground accel adds nothing
/// once `v·wishdir` exceeds the wish speed). So the spread is a property of the takeoff regime, not
/// something to tune away — the certifier has to prove the arc across it. At the old 6° the build
/// certified a tighter envelope than the runtime delivers and links landed 3/5.
pub(super) const CURL_PSI_TOL: f32 = 12.0;
/// Run-up headings tried around the corridor's compass axis (degrees). A real lip's approach is rarely
/// exactly on an axis and certification is sharply heading-sensitive, so the from-cell is placed along
/// whichever of these certifies — the runtime then flies precisely the proven line. On-axis first, so
/// the common case costs nothing extra.
pub(super) const CURL_PSI_SAMPLES: [f32; 5] = [0.0, -6.0, 6.0, -12.0, 12.0];
/// Ceiling fraction of the run-up's delivered speed that a curl may be certified at — headroom so the
/// runtime can actually reach and hold the solved takeoff speed.
pub(super) const CURL_V_LO_FRAC: f32 = 0.94;
/// Step (ups) of the takeoff-speed ladder the certifier solves over. A curl is certified at the *lowest*
/// speed whose envelope lands — the human holds a controlled ~400 rather than maxing to the ~484
/// prestrafe equilibrium, whose 327u flat reach overshoots any moderate gap.
pub(super) const CURL_V_STEP: f32 = 12.0;
/// How tightly the runtime holds the solved takeoff speed (fraction): the takeoff regime coasts above
/// the band and circle-strafes below it, so the leap lands inside ±this. The certifier proves both
/// corners of the band, so it must match `bhop`'s hold tolerance.
pub const CURL_V_HOLD_TOL: f32 = 0.03;
/// Air-curl gains tried, gentlest first (the gentlest that lands the whole envelope is chosen — a firm
/// gain is needed only because the overbuilt takeoff would otherwise overshoot).
pub(super) const CURL_GAINS: [f32; 8] = [4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 20.0];
/// Commitment slack added to a certified curl's cost — a rollout-certified envelope carries far less
/// risk than the `+1.0` charged to a modelled speed jump, so price it like a plain JumpGap.
pub(super) const CURL_COMMIT: f32 = 0.3;
/// At most this many curl links per source cell (its own budget, so a curl never evicts a straight jump).
pub(super) const SPEED_JUMP_CURL_MAX_PER_CELL: usize = 2;
/// At most this many curl links *landing on the same target cell* (global dedup): a dozen corridors
/// certifying a curl onto one platform is noise the planner never needs — keep the cheapest couple.
pub(super) const CURL_TARGET_MAX: usize = 2;
/// How far back along the run-up the certifier may slide the takeoff (units). A leap right at the pit
/// edge overshoots at the delivered curl speed; sliding it back (over the near ground the arc clears)
/// lengthens the flight until the distance matches the speed. Bounded so the search stays cheap.
pub(super) const CURL_TAKEOFF_BACKOFF: f32 = 240.0;
/// How far *before* the certified takeoff the runtime actually leaps: the bhop takeoff regime jumps on
/// the frame it crosses the takeoff line (progress `< LIP_REACH`), so on average it leaps ~a lip-reach
/// early. The certifier adds this as a position corner so a whole population of curls doesn't land
/// short. MUST match `bhop::LIP_REACH` in the game crate (the runtime threshold it models).
pub const CURL_LIP_REACH: f32 = 28.0;
/// Rollout tick step (the quantized ~77 Hz bot tick) and a hard tick cap per rollout.
pub(super) const CURL_DT: f32 = 1.0 / 77.0;
pub(super) const CURL_MAX_TICKS: usize = 120;

// --- side jumps (short run-up *across* a ledge, leap off the flank) ------------------------------
//
// The curl pass above discovers candidates *corridor-first*: it walks back along a compass axis from
// an open lip, so the run-up and the leap are always near-collinear. That is the wrong search order
// for the commonest human shortcut. `demos/dm3_rlstrafejump.qwd` crosses dm3's z=152 pit from a
// balcony **448u wide and two grid rows (64u) deep**: the run-up goes *along* the balcony and the
// leap goes *off its flank*, ~63° apart. `measure_runway` can never see that runway — it only walks
// anti-parallel to the leap (32u of the 448 available), and its "ground in both perpendicular
// columns" rule fails on every column of a two-row ledge, which has no interior column at all.
//
// So the side pass inverts the search: pick the target first, derive candidate takeoff headings from
// the **chord**, and measure the runway along whichever heading has floor under it (the same order
// `air_rocket_jumps_from` already uses for air-steered rocket jumps). Everything downstream is the
// curl machinery unchanged — `prestrafe_delivered` funds it, `certify_curl` proves it by rollout, and
// it is emitted as an ordinary curl-carrying `SpeedJump` the runtime already knows how to fly.

/// Minimum run-up for a side jump. Deliberately far below [`CURL_MIN_RUNWAY`]: that floor is about
/// *corridor quality* for a collinear search, and a flank run-up has no corridor to be measured
/// against. The demo's usable diagonal lines across the balcony run 87-141u, and the human buys
/// 320→399 ups in ~95u of it, so 96 is the point below which there is genuinely no room to build.
pub(super) const SIDE_MIN_RUNWAY: f32 = 96.0;
/// The run-up a side jump commits (mirrors [`CURL_RUNUP_CAP`]).
pub(super) const SIDE_RUNUP_CAP: f32 = 512.0;
/// Takeoff headings tried either side of the chord, and the step between them. The demo's certifying
/// heading sits 47-66° off the chord (the human leaves the lip mid-turn, having already swung 47° on
/// the ground), so a ±12° sweep like [`CURL_PSI_SAMPLES`] cannot reach it. The step is under twice
/// [`CURL_PSI_TOL`], so the ±6° guard each certify proves leaves no gap between samples.
pub(super) const SIDE_PSI_STEP: f32 = 9.0;
pub(super) const SIDE_PSI_MAX: f32 = 63.0;
/// How far *below* the straight-chord ballistic need [`v_required`] the certify ladder may start.
///
/// The chord speed is not actually a floor for a curled flight: `air_correct` keeps accelerating in
/// the air, so a rollout can land a target the straight-line formula calls out of reach. The demo is
/// exactly this case — it takes off at **399** ups where the cell-to-cell chord demands 411.6, and
/// gains to 425 in flight. Starting the ladder at the ballistic floor would refuse it. The rollout is
/// still the arbiter; this only decides where the ladder starts looking.
pub(super) const SIDE_V_FLOOR_FRAC: f32 = 0.88;
/// At most this many side jumps per source cell (its own budget, so it never evicts a straight or
/// corridor curl).
pub(super) const SIDE_MAX_PER_CELL: usize = 2;
/// Targets per (octant, elevation band) the pass will spend rollouts on before giving that bucket up.
/// Only one of them can survive the dedup, so this is purely "if the widest doesn't certify, how many
/// fallbacks get a try" — the knob that trades build time against coverage of awkward buckets.
pub(super) const SIDE_BUCKET_TRIES: usize = 3;
/// How far a side jump may *descend*, well under the straight pass's [`SJ_MAX_DROP`]. A side jump is
/// bought with an expensive rollout because it **crosses** something; descending is free and already
/// carried by Drop/JumpGap links, and the per-cell ranking deprioritises drops for exactly that
/// reason. Bounding it here is also what keeps the pass affordable: the target scan radius is
/// `v · airtime`, and airtime grows with the fall, so a 1024u envelope scans a ~750u disc around every
/// ledge on the map.
pub(super) const SIDE_MAX_DROP: f32 = 320.0;
/// Lateral floor a side-jump run-up wants either side of its line before the runtime's *uncapped*
/// prestrafe weave is safe. The weave swings ±30-45° at ~3 reversals per hop, which at 400 ups is
/// roughly ±1 grid column of excursion — so one column each side is the threshold, not a margin.
pub(super) const SIDE_WEAVE_CLEARANCE: f32 = 32.0;
/// Weave half-angle a side jump asks the runtime to hold when its run-up is narrower than
/// [`SIDE_WEAVE_CLEARANCE`]. Mirrors the corridor bhop's `zigzag_band_cap`, which exists for the same
/// reason: an uncapped serpentine sweeps wider than a narrow ledge has floor.
pub(super) const SIDE_WEAVE_NARROW_DEG: f32 = 15.0;

/// The takeoff speed the ground circle-strafe delivers over a `runway` from a run start, rolled with
/// the shared ground oracles at the ground-optimal wish angle. Saturates at the friction equilibrium
/// (~1.5·maxspeed at stock cvars), so any run-up past [`CURL_MIN_RUNWAY`] arrives near the ceiling.
pub(super) fn prestrafe_delivered(runway: f32, accel: f32, maxspeed: f32, friction: f32, stopspeed: f32) -> f32 {
    prestrafe_delivered_from(MAX_SPEED, runway, accel, maxspeed, friction, stopspeed)
}

/// As [`prestrafe_delivered`] but from an arbitrary starting speed `v0` — so the runtime can ask
/// "given my *current* speed, will the remaining run-up still build `v_req` by the lip?" (the curl
/// too-slow abort). Rolls the shared ground oracles at the ground-optimal wish angle; the step count
/// is bounded so a bogus runway can't spin the loop.
pub fn prestrafe_delivered_from(v0: f32, runway: f32, accel: f32, maxspeed: f32, friction: f32, stopspeed: f32) -> f32 {
    use crate::strafe::{apply_friction, apply_groundaccel};
    use glam::Vec2;
    let dt = CURL_DT;
    let a_g = accel * maxspeed * dt; // ground accel cap per tick
    let u_star = (maxspeed - a_g).max(0.0); // speed above which angling the wish pays off
    let mut v = Vec2::new(v0.max(1.0), 0.0);
    let steps = ((runway.max(0.0) / (MAX_SPEED * dt)).ceil() as i32).clamp(1, 600);
    for _ in 0..steps {
        let speed = v.length().max(1.0);
        let theta = (u_star / speed).clamp(0.0, 1.0).acos(); // ground-optimal angle off the velocity
        let vel_yaw = v.y.atan2(v.x);
        let (s, c) = (vel_yaw + theta).sin_cos(); // one side is enough for a speed estimate
        v = apply_friction(v, friction, stopspeed, dt);
        v = apply_groundaccel(v, Vec2::new(c, s), maxspeed, accel, dt);
    }
    v.length()
}

// --- speed bands (kinodynamic planning over (cell, band); see `find_path_banded`) ---

/// Coarse entry-speed classes for the banded planner. A route's carried speed changes both the
/// feasibility of a leg (a chained speed jump needs a minimum band) and its cost (a fast band
/// covers a Walk leg quicker). Four bands keep the search state at `cells · 4`.
/// `BAND_EDGES[i]` is the upper edge of band `i`: `<340 → 0`, `<430 → 1`, `<540 → 2`, else `3`.
pub const BAND_EDGES: [f32; 3] = [340.0, 430.0, 540.0];
/// Number of speed bands.
pub const NBANDS: usize = 4;
/// The speed *credited* to a band — its lower edge. Feasibility and cost always use this floor,
/// never a midpoint, so the planner never assumes more speed than a band guarantees.
pub const BAND_FLOOR: [f32; NBANDS] = [MAX_SPEED, 340.0, 430.0, 540.0];
/// Planning speed ceiling — the banded heuristic's denominator (matches [`SPEED_JUMP_V_CAP`]).
/// Larger than [`MAX_SPEED`], so the banded heuristic is *smaller* (more conservative) than the
/// cell-only one — at worst it expands more nodes, never less optimal than the existing search.
pub const BAND_V_MAX: f32 = SPEED_JUMP_V_CAP;
/// Runway a standing start (band 0) must spend spinning up before air-strafe gains begin, charged
/// against a Walk/Step leg's length. Mirrors the game's bhop controller engage runway.
pub(super) const BAND_SPINUP: f32 = 256.0;
/// A carried-speed leg only counts if the corridor continues roughly straight: if the turn from the
/// link that *reached* a cell to the candidate link exceeds this, the planner treats the entry as
/// band 0 (speed is not carried around a sharp corner).
pub(super) const SPEED_CONE_DEG: f32 = 45.0;

/// The speed band a given horizontal speed falls into (`0..NBANDS`).
pub fn band_of(speed: f32) -> u8 {
    BAND_EDGES.iter().position(|&e| speed < e).unwrap_or(NBANDS - 1) as u8
}

/// Airtime of a jump reaching a target `dz` above (or below) the takeoff, at gravity `g`: the
/// descending root of `JUMP_VZ·t − ½g·t² = dz`. `0` if `dz` is unreachable (above the apex).
pub(super) fn jump_airtime(dz: f32, gravity: f32) -> f32 {
    let disc = JUMP_VZ * JUMP_VZ - 2.0 * gravity * dz;
    if disc < 0.0 {
        return 0.0;
    }
    (JUMP_VZ + disc.sqrt()) / gravity
}

/// The horizontal entry speed needed to clear `horiz` while rising/falling `dz`, at gravity `g`.
pub(super) fn v_required(horiz: f32, dz: f32, gravity: f32) -> f32 {
    let t = jump_airtime(dz, gravity);
    if t <= 0.0 {
        f32::INFINITY
    } else {
        horiz / t
    }
}

/// Bhop speed-gain constant `k`: velocity² grows at `2k` per second while air-strafing. Derived from
/// the perpendicular air-accel cap and the tickrate (`k = tick · a² / 2`, `a = min(accel·maxspeed/tick, cap)`).
pub fn bhop_k(accel: f32, maxspeed: f32) -> f32 {
    let a = (accel * maxspeed / SJ_TICKRATE).min(AIR_CAP);
    SJ_TICKRATE * a * a / 2.0
}

/// Speed reached after air-strafing `len` units from `v0`: `(v0³ + 3k·len)^⅓`.
pub fn attainable_speed(v0: f32, len: f32, k: f32) -> f32 {
    (v0.powi(3) + 3.0 * k * len.max(0.0)).cbrt()
}

/// Runway length needed to air-strafe from `v0` up to `v`: `(v³ − v0³) / 3k`.
pub(super) fn runway_len_for(v: f32, v0: f32, k: f32) -> f32 {
    ((v.powi(3) - v0.powi(3)) / (3.0 * k)).max(0.0)
}

/// Time to air-strafe from `v0` up to `v`: `(v² − v0²) / 2k`.
pub(super) fn runway_time(v: f32, v0: f32, k: f32) -> f32 {
    ((v * v - v0 * v0) / (2.0 * k)).max(0.0)
}
