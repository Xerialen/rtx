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

/// The emitted link id is the leg **steering stamped**, not a re-derivation.
///
/// This is the whole point of the event. `PlanTick` exists so that "A\* chose link X" is a machine
/// fact instead of an inference from positions, and the moment the row computes its own answer the
/// event is worth less than nothing — it would look like attestation while being a second guess,
/// and a second guess that disagrees with the first is undetectable downstream.
///
/// `frame_end` runs at a different rate from think, so anything re-derived here would be a
/// different frame's answer to the same question even when the derivation is correct.
#[test]
fn the_row_reports_the_link_steering_chose() {
    let src = control_src();
    let (_, body) = src
        .split_once("proto::PlanTick {")
        .expect("PLANTICK_LOCK: the PlanTick construction is gone from control.rs");
    let end = body
        .find("\n    }")
        .expect("PLANTICK_LOCK: the PlanTick literal does not close");
    let lines: Vec<&str> = body[..end].lines().map(str::trim).collect();

    assert!(
        lines.contains(&"link: p.link.unwrap_or(none),"),
        "PLANTICK_LOCK: the row's `link` must be the leg steering stamped (`p.link`), verbatim. \
         Anything else is a re-derivation dressed as an attestation. Lines: {lines:?}"
    );
    assert!(
        lines.contains(&"kind: p.kind.map(|k| format!(\"{k:?}\")).unwrap_or_default(),"),
        "PLANTICK_LOCK: `kind` must come from the same stamp as `link`, or the two can disagree"
    );
    // The endpoints are resolved from that same `p.link` above the literal, so they cannot name a
    // different leg than `link` does.
    for pinned in ["link_from,", "link_to,"] {
        assert!(
            lines.contains(&pinned),
            "PLANTICK_LOCK: `{pinned}` must be the pre-resolved endpoints of `p.link`"
        );
    }
    // Each endpoint is bound to **its own** line. Checking the pair against a window with `any`
    // let a mutation that rewrote `link_from` pass, because the untouched `link_to` line still
    // satisfied the pattern — the pin proved "one of these two is right", which is not the claim.
    for (name, resolver) in [("link_from", "g.link_source(l)"), ("link_to", "g.link_target(l)")] {
        let decl = format!("let {name} = ");
        let line = src
            .lines()
            .find(|l| l.trim_start().starts_with(&decl))
            .unwrap_or_else(|| panic!("PLANTICK_LOCK: `{name}` is no longer resolved at all"))
            .trim();
        assert!(
            line.contains("p.link.map_or(none,") && line.contains(resolver),
            "PLANTICK_LOCK: `{name}` must resolve from `p.link` — the same stamp `link` reports — \
             not from a second source that can name a different leg. Found: {line:?}"
        );
    }
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
    let (_, body) = src
        .split_once("proto::PlanTick {")
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
