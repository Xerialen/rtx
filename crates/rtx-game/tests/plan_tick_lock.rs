// SPDX-License-Identifier: AGPL-3.0-or-later

//! PlanTick lock — the emission invariants that live at a call site needing a live `GameState`,
//! pinned as source text from outside the file that holds them.
//!
//! Its own file for the reason `ra_room_lock.rs` and `recost_lock.rs` are theirs: a pin inside the
//! file it pins can be silenced by an edit that changes the source and the pin together.
//!
//! The gate itself is not pinned here — `plan_gate` and `plan_row_due` are pure functions with
//! real unit tests next to them (`plan_gate_needs_both_cvars`, `plan_row_due_requires_a_fresh_stamp`,
//! …). What needs a pin is the part no unit test can reach: the row's construction.

use std::fs;
use std::path::PathBuf;

fn control_src() -> String {
    let p = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/control.rs");
    fs::read_to_string(&p).unwrap_or_else(|e| panic!("PLANTICK_LOCK: cannot read {}: {e}", p.display()))
}

/// The row is built in **one** place, and that place wires the leg from `plan_leg`.
///
/// The behaviour — that `link`, `link_from` and `link_to` all name the same leg — is bound by
/// tests that *run* `plan_leg` (`the_row_reports_the_leg_steering_stamped` and its two
/// neighbours). What those cannot reach is `plan_tick` itself, which needs a live `GameState`. So
/// this pins the wiring, and only the wiring.
///
/// **Anchored on the construction literal, not the signature.** `fn plan_tick(..) ->
/// proto::PlanTick {` matches the bare type name first, and a window opened there spans the whole
/// body — so a second, lying literal inside it would still satisfy every check somewhere and pass.
/// Build honestly, lie afterwards is exactly the shape this must refuse, which is why the build
/// site is counted rather than merely found. Same hole `recost_lock` was fixed for; the lesson was
/// written down there and not carried here, so it had to be learned twice.
#[test]
fn the_row_is_built_once_and_wires_the_stamped_leg() {
    let src = control_src();

    let lit = "\n    proto::PlanTick {";
    assert_eq!(
        src.matches(lit).count(),
        1,
        "PLANTICK_LOCK: there must be exactly one place a PlanTick row is built. More than one and \
         this pin cannot know which of them is returned."
    );
    let (_, body) = src
        .split_once(lit)
        .expect("PLANTICK_LOCK: the PlanTick construction is gone from control.rs");
    let end = body
        .find("\n    }")
        .expect("PLANTICK_LOCK: the PlanTick literal does not close");
    let lines: Vec<&str> = body[..end].lines().map(str::trim).collect();

    for pinned in ["link,", "link_from,", "link_to,"] {
        assert!(
            lines.contains(&pinned),
            "PLANTICK_LOCK: the row must carry `{pinned}` from the destructured `plan_leg` result, \
             so the id and its endpoints cannot come from different sources. Lines: {lines:?}"
        );
    }
    assert!(
        lines.contains(&"kind: p.kind.map(|k| format!(\"{k:?}\")).unwrap_or_default(),"),
        "PLANTICK_LOCK: `kind` must come from the same stamp as `link`, or the two can disagree"
    );
    // Presence is not enough while a constant can sit beside the wired value and win.
    for lie in ["link: none,", "link_from: none,", "link_to: none,", "link: 0,"] {
        assert!(
            !lines.contains(&lie),
            "PLANTICK_LOCK: the row contains `{lie}` — a hardcoded leg. A row reporting a leg the \
             bot did not steer is worse than no row: it looks like an attestation."
        );
    }
}

/// The leg is destructured from `plan_leg` **exactly once**, so nothing can shadow it.
///
/// A second `let (link, link_from, link_to) = …` after the first silently wins, and the pin above
/// — which reads only the literal — would see the same three names and pass. Rust allows the
/// shadow without a warning, so nothing else catches it either.
#[test]
fn the_leg_is_bound_exactly_once() {
    let src = control_src();
    let binds: Vec<&str> = src
        .lines()
        .map(str::trim)
        .filter(|l| l.starts_with("let (link, link_from, link_to)"))
        .collect();
    assert_eq!(
        binds.len(),
        1,
        "PLANTICK_LOCK: the leg must be bound exactly once — a later binding shadows the first \
         without a warning and the literal reads identically. Found: {binds:?}"
    );
    assert_eq!(
        binds[0], "let (link, link_from, link_to) = plan_leg(p, game.nav.graph.as_deref());",
        "PLANTICK_LOCK: the leg must come from `plan_leg`, the function the behavioural tests bind"
    );
    // And `plan_leg` must still start from the stamp rather than deriving its own answer.
    let (_, f) = src
        .split_once("fn plan_leg(")
        .expect("PLANTICK_LOCK: plan_leg is gone from control.rs");
    let head: String = f.lines().take(8).collect::<Vec<_>>().join("\n");
    assert!(
        head.contains("let Some(l) = p.link else {"),
        "PLANTICK_LOCK: `plan_leg` must start from `p.link` — the leg steering stamped. Found:\n{head}"
    );
}

/// The row's price split is the planner's own, term by term — not a total, not a recomputation.
///
/// A sum cannot be acted on: a shut gate plus jitter and an unfit rocket jump plus a failed-link
/// strike reach the same number and want opposite fixes. `p_penalty` is also where an operator
/// `Cmd::Recost` surcharge lands, so collapsing the split would hide a priced arm inside an
/// ordinary-looking cost.
#[test]
fn the_row_carries_the_price_split_the_planner_used() {
    let src = control_src();
    // Construction literal, not the signature — see `the_row_is_built_once_and_wires_the_stamped_leg`.
    let (_, body) = src
        .split_once("\n    proto::PlanTick {")
        .expect("PLANTICK_LOCK: the PlanTick construction is gone");
    let end = body.find("\n    }").expect("PLANTICK_LOCK: the literal does not close");
    let lines: Vec<&str> = body[..end].lines().map(str::trim).collect();
    for pinned in [
        "p_gate: p.extra.gate,",
        "p_penalty: p.extra.penalty,",
        "p_jitter: p.extra.jitter,",
        "p_rj: p.extra.rj,",
        "p_water: p.extra.water,",
        "p_hazard: p.extra.hazard,",
    ] {
        assert!(
            lines.contains(&pinned),
            "PLANTICK_LOCK: `{pinned}` must come from the breakdown steering stamped. Lines: {lines:?}"
        );
    }
}

/// The fine switch stays subordinate to the master switch.
///
/// Turning on plan telemetry puts new `Event` variants on the wire, and a typed consumer built
/// before them dies on a variant it cannot decode rather than skipping it. An operator who sets
/// only the fine switch must therefore get nothing — that is the safe way for the mistake to fail,
/// and it is the reason both cvars default off.
#[test]
fn the_plan_gate_is_subordinate_to_rtx_telemetry() {
    let src = control_src();
    let (_, body) = src
        .split_once("pub(crate) fn plan_gate(")
        .expect("PLANTICK_LOCK: plan_gate is gone from control.rs");
    let head: String = body.lines().take(8).collect::<Vec<_>>().join("\n");
    assert!(
        head.contains("on: telemetry && plan_cvar > 0.0,"),
        "PLANTICK_LOCK: the plan gate must require `rtx_telemetry` as well as \
         `rtx_plan_telemetry`. Found:\n{head}"
    );
}
