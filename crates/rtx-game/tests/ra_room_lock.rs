//! RA-room lock (issue #47 M4). Own crate so deleting `mod ra_room_kontrakt`
//! in `recept.rs` does not kill the grind. `default_of` is `pub(crate)` and
//! invisible here; pin the tokens that `RTX_CVAR_DEFAULTS` actually contains.

use std::fs;
use std::path::PathBuf;

fn read_src(rel: &str) -> String {
    let p = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(rel);
    fs::read_to_string(&p).unwrap_or_else(|e| panic!("RA_ROOM_LOCK: cannot read {}: {e}", p.display()))
}

#[test]
fn seed_tokens_in_cvars_source() {
    let src = read_src("src/cvars.rs");
    for (token, msg) in [
        (
            r#"("rtx_bot_edge_narrow", Bool(true))"#,
            "RA_ROOM_LOCK: edge_narrow≠true",
        ),
        (r#"("rtx_bot_walkplan", Bool(true))"#, "RA_ROOM_LOCK: walkplan≠true"),
        (r#"("rtx_bot_walkdiag", Bool(false))"#, "RA_ROOM_LOCK: walkdiag≠false"),
        (r#"("rtx_bot_count", Float(0.0))"#, "RA_ROOM_LOCK: bot_count≠0"),
    ] {
        assert!(src.contains(token), "{msg} (saknar token {token})");
    }
}

#[test]
fn mallinje_c1_c5_names_in_control_source() {
    let src = read_src("src/control.rs");
    for name in [
        "fn c1_obliquely_approached_point_is_a_miss_not_an_arrival",
        "fn c2_shrink_does_not_outrun_the_distance_past_the_plane",
        "fn c3_genuine_line_crossing_still_counts",
        "fn c4_near_field_on_the_plane_is_untouched",
        "fn c5_short_of_the_plane_returns_early",
    ] {
        assert!(src.contains(name), "RA_ROOM_LOCK: saknar {name} i control.rs");
    }
}

#[test]
fn kontrakt_prefix_still_in_recept_source() {
    let src = read_src("src/recept.rs");
    assert!(
        src.contains("RA_ROOM_LOCK: edge_narrow≠true"),
        "RA_ROOM_LOCK: edge_narrow≠true (prefix borta ur recept.rs)"
    );
}
