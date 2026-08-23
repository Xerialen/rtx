// SPDX-License-Identifier: AGPL-3.0-or-later

//! Recost lock — the three invariants of operator pricing that live at call sites a unit test
//! cannot reach, pinned as source text from outside the files that hold them.
//!
//! Its own file for the same reason `ra_room_lock.rs` is: a pin that sits inside the file it pins
//! can be quieted by an edit that changes the source and the pin together, which a `sed` or an
//! IDE rename does by default. Nothing here can be silenced by editing only the file under pin.
//!
//! Each of these is a place where the *right* code and the *wrong* code compile equally well and
//! differ only in what a measurement means afterwards.

use std::fs;
use std::path::PathBuf;

fn read(rel: &str) -> String {
    let p = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(rel);
    fs::read_to_string(&p).unwrap_or_else(|e| panic!("RECOST_LOCK: cannot read {}: {e}", p.display()))
}

/// The operator table is **merged** into the bot's penalties, never pushed or assigned.
///
/// `NavGraph::link_extra` keeps one extra per link and breaks at the first match, so a bare
/// `push` would mean whichever entry happened to be first wins: price a link the bot has also
/// just failed on and one of the two silently disappears. That changes the bot's own learning as
/// a side effect of measuring it — the priced arm would no longer be the same bot as the unpriced
/// one, and the comparison the arm exists for would be void.
#[test]
fn the_operator_table_is_merged_into_the_bot_penalties() {
    let src = read("src/bot/mod.rs");
    let (_, after) = src
        .split_once("for &(li, extra) in self.recost.entries_for(self.nav.graph.as_ref()) {")
        .expect("RECOST_LOCK: the recost feed is gone from bot_link_pricing");
    let line = after.lines().nth(1).unwrap_or_default().trim();
    assert_eq!(
        line, "merge_link_penalty(&mut penalties, li, extra);",
        "RECOST_LOCK: the recost feed must merge, not assign or push — `link_extra` honours \
         exactly one entry per link, so anything else silently drops a penalty. Found: {line:?}"
    );
}

/// Route jitter scales against a link's **base cost**, never against an operator surcharge.
///
/// Jitter is a tie-breaker sized to a link's own cost. If it were sized to the priced cost
/// instead, a large surcharge would buy a large random term, and a priced link would divert some
/// bots and not others — non-determinism introduced by the act of measuring, which is the one
/// thing a per-arm comparison cannot absorb. The unit test
/// `a_priced_link_is_diverted_for_every_jitter_seed` shows the behaviour; this pins the term it
/// depends on, in the crate that owns it.
#[test]
fn jitter_scales_against_base_cost_not_the_surcharge() {
    let p = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../rtx-nav/src/navmesh/mod.rs");
    let src = fs::read_to_string(&p).unwrap_or_else(|e| panic!("RECOST_LOCK: cannot read {}: {e}", p.display()));
    let (_, after) = src
        .split_once("if costs.jitter_seed != 0 {")
        .expect("RECOST_LOCK: the jitter term is gone from link_extra");
    let term = after
        .lines()
        .find(|l| l.contains("JITTER_FRAC"))
        .expect("RECOST_LOCK: the jitter term no longer mentions JITTER_FRAC")
        .trim();
    assert_eq!(
        term, "extra += (h as f32 / u32::MAX as f32) * JITTER_FRAC * self.links[li as usize].cost;",
        "RECOST_LOCK: jitter must scale against the link's own `cost`, not against `extra` or a \
         penalty — otherwise pricing a link makes it divert non-deterministically. Found: {term:?}"
    );
}

/// Both provenance readouts quote the **live table**, never a constant.
///
/// This is the one the mutation run actually caught me on. A `status_resp` that reports
/// `recost: Vec::new(), recost_hash: String::new()` while a table is in force compiles, passes
/// every unit test — the projection itself is faithful, and it is simply not being called — and
/// hands Hopparen a frame that says "unpriced" about a priced arm. Every band stamped from it
/// would then carry a truthful-looking description of the wrong regime, which is worse than no
/// stamp at all: an absent stamp stops a run, a wrong one is believed.
///
/// The reply is pinned for the same reason. Both readouts must name `game.recost`.
#[test]
fn the_provenance_readouts_quote_the_live_table() {
    let src = read("src/control.rs");

    // Anchor on the **construction literal**, not on `fn status_resp(..) -> proto::StatusResp {`.
    // Opening the window at the signature spans the whole body, so a second, lying literal
    // inside it still finds the pinned lines somewhere and passes — which is precisely how QA's
    // M4 (lying status frame built at another site) survived the first version of this pin.
    let lit = "\n    proto::StatusResp {";
    assert_eq!(
        src.matches(lit).count(),
        1,
        "RECOST_LOCK: there must be exactly one place a status frame is built. More than one and \
         this pin cannot know which of them is returned."
    );
    let (_, frame) = src.split_once(lit).expect("RECOST_LOCK: the status frame is gone");
    let end = frame
        .find("\n    }")
        .expect("RECOST_LOCK: the status frame literal does not close");
    let frame: Vec<&str> = frame[..end].lines().map(str::trim).collect();

    for pinned in [
        "recost: game.recost.as_entries_for(game.nav.graph.as_ref()),",
        "recost_hash: game.recost.hash_for(game.nav.graph.as_ref()).to_string(),",
    ] {
        assert!(
            frame.contains(&pinned),
            "RECOST_LOCK: the status frame must read the live table — `{pinned}` is gone. A frame \
             reporting no pricing while pricing is in force mis-stamps every band taken under it."
        );
    }
    // Presence is not enough: the frame must not *also* contain a constant that overrides it.
    for lie in ["recost: Vec::new(),", "recost_hash: String::new(),", "recost: vec![],"] {
        assert!(
            !frame.contains(&lie),
            "RECOST_LOCK: the status frame contains `{lie}` — a hardcoded 'unpriced' readout. \
             An absent stamp stops a run; a wrong one is believed."
        );
    }

    let (_, reply) = src
        .split_once("Ok(proto::RecostResp {")
        .expect("RECOST_LOCK: the Recost reply is gone from control.rs");
    let end = reply
        .find("\n    })")
        .expect("RECOST_LOCK: the Recost reply literal does not close");
    let reply: Vec<&str> = reply[..end].lines().map(str::trim).collect();
    for pinned in [
        "set: game.recost.as_entries_for(Some(&g)),",
        "graph_content_hash: game.recost.graph_hash_for(Some(&g)).to_string(),",
        "recost_hash: game.recost.hash_for(Some(&g)).to_string(),",
    ] {
        assert!(
            reply.contains(&pinned),
            "RECOST_LOCK: the Recost reply must state the table it just set — `{pinned}` is gone."
        );
    }
    for lie in ["set: Vec::new(),", "set: vec![],", "recost_hash: String::new(),"] {
        assert!(
            !reply.contains(&lie),
            "RECOST_LOCK: the Recost reply contains `{lie}` — a hardcoded readout."
        );
    }
}

/// The intended path: a map load drops the operator table.
///
/// **What this proves and what it does not.** It proves the assignment is present next to the
/// navmesh reset. It cannot prove control flow reaches it — a text pin sees text, which is how
/// an inverted gate walks past it. The guarantee itself is structural and lives in a behavioural
/// test, `recost::tests::a_table_does_not_survive_the_graph_it_was_anchored_to`: a table that
/// outlived its graph reads as no table at all, in the feed and in both readouts alike. This pin
/// is defence in depth on top of that, not the thing standing between a stale table and a band.
#[test]
fn a_map_load_drops_the_operator_table() {
    let src = read("src/game.rs");
    assert!(
        src.contains("crate::recost::drop_for_map_load(&mut self.nav, &mut self.recost);"),
        "RECOST_LOCK: the map load must drop navmesh and pricing in one call — two statements \
         can drift apart, and the drift is silent"
    );
    // The navmesh reset must not have grown a second, pricing-free path back.
    assert!(
        !src.contains("self.nav = navmesh::NavState::default();"),
        "RECOST_LOCK: a bare navmesh reset has reappeared in game.rs — that is the shape that \
         lets pricing outlive its graph. Use `drop_for_map_load`."
    );
}
