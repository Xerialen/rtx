// SPDX-License-Identifier: AGPL-3.0-or-later

//! Arrival-provenance lock (issue #2). Its own file for the same reason `ra_room_lock.rs` is
//! its own file: a source-text pin that lives *inside* the file it pins can be quieted by an
//! edit that changes the source and the pin together. That is not a hypothetical — the first
//! run of QA's Q1 mutation used a plain `sed` over `control.rs` and rewrote both the line in
//! `poll_goto` and the pinned literal in the test module below it, and the suite stayed green
//! at 412. An IDE rename would do the same thing.
//!
//! So the in-module pin in `control.rs` keeps the fast `--lib` loop honest, and this one stands
//! outside it: nothing that edits only `control.rs` can silence both.

use std::fs;
use std::path::PathBuf;

fn control_src() -> String {
    let p = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/control.rs");
    fs::read_to_string(&p).unwrap_or_else(|e| panic!("ARRIVAL_LOCK: cannot read {}: {e}", p.display()))
}

/// The arrival path in `poll_goto`: the gate, the branch it computes, the fields it reports.
///
/// `arrived` is an instrument reading, not an observation of the world. These five lines are
/// what make the reading judgeable against the facit's own goal predicate — the distance the
/// gate actually tested, and which limb of it fired.
#[test]
fn arrival_path_reports_the_distances_the_gate_measured() {
    let src = control_src();
    let gate = "if (dxy <= GOTO_ARRIVE_XY || crossed_finish) && dz <= GOTO_ARRIVE_Z {";
    let (_, body) = src
        .split_once(gate)
        .expect("ARRIVAL_LOCK: the arrival gate is gone from control.rs");
    let at = body
        .find("Event::Arrived {")
        .expect("ARRIVAL_LOCK: no arrived emission follows the gate");
    let end = at
        + body[at..]
            .find("},")
            .expect("ARRIVAL_LOCK: the emission block does not close");
    let lines: Vec<&str> = body[..end].lines().map(str::trim).collect();
    for pinned in [
        // Computed from the XY distance the gate itself tested — not from `dz`, not from a
        // constant. Reading the wrong distance here mislabels every finish-plane crossing.
        "let branch = arrival_branch(dxy);",
        // Every measured field reaches the wire.
        "dist: dxy,",
        "dxy,",
        "dz,",
        "branch,",
    ] {
        assert!(
            lines.contains(&pinned),
            "ARRIVAL_LOCK: the arrival path no longer contains `{pinned}` — an arrived row \
             without its measured dxy/dz/branch is not evidence of arrival (issue #2). \
             Lines found: {lines:?}"
        );
    }
}

/// The branch is named by a function, not decided inline.
///
/// If the labelling is inlined back into `poll_goto` the unit test that binds it
/// (`arrival_branch_names_the_limb_that_fired`) starts asserting about a copy of the rule
/// rather than the rule.
#[test]
fn the_branch_label_is_still_a_named_function() {
    let src = control_src();
    assert!(
        src.contains("fn arrival_branch(dxy: f32) -> proto::ArrivalBranch {"),
        "ARRIVAL_LOCK: `arrival_branch` is gone — the branch label is no longer bound by a test"
    );
}
