//! ben3d extraktor — etapp 2c: per-tick-regelutvärdering + benbuntar (D1/D2).
//!
//! Per tick: cell = motorns `nearest(origin)` (G8); horisontell fart härleds ur
//! spåret (positionsderivat) och matas till predikaten — fart är INTE inspelad
//! motorstate, så varje färgpost bär speed_source (R1-b: "härledd-positionsderivat"
//! eller "oberoende" för rena topologi-/chain-flaggor) + formeln på tickserienivå.
//! Färg 1 emittas PER TICK (ingen per-cell-cache): varje raw_row_index får sin egen
//! predikatutvärdering. För VARJE utgående T=1-länk anropas motorns EGNA
//! `banded_step`/`chain_entry_blocked`; SJ-länkar UTAN sidotabell ⇒ OKÄND (G10,
//! predikaten anropas inte — fallbacken ljuger), SJ-länkar MED sidotabell (fixtur)
//! utvärderas på riktigt. Färg 2 = observerad cellsekvens. Färg 3 = OKÄND —
//! vald länk ej observerad (PlanTick saknas, P2). Main: färg 1/3 beräknas inte (G11).

use crate::canon;
use crate::restore::{self, Dump};
use rtx_nav::navmesh::{band_of, LinkKind, NavGraph};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::path::Path;

fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

#[derive(Deserialize)]
struct RawLine {
    t: Box<serde_json::value::RawValue>,
    #[serde(default)]
    players: Vec<RawPlayer>,
}
#[derive(Deserialize)]
struct RawPlayer {
    #[serde(default)]
    origin: Vec<Box<serde_json::value::RawValue>>,
}
#[derive(Clone)]
struct TickRaw {
    raw_row_index: u32,
    t: String,
    x: String,
    y: String,
    z: String,
    xf: f32,
    yf: f32,
    zf: f32,
    tf: f32,
}
#[derive(Deserialize, Default)]
#[allow(dead_code)]
struct Meta {
    #[serde(default)] utfall: String,
    #[serde(default)] measure_id: String,
    #[serde(default)] falls_measure_id: String,
    #[serde(default)] cykel: i64,
    #[serde(default)] ben: String,
    #[serde(default)] tid: Option<f64>,
    #[serde(default)] t_hit: Option<f64>,
}

fn read_ticks(jsonl_path: &str) -> Result<Vec<TickRaw>, String> {
    let text = std::fs::read_to_string(Path::new(jsonl_path))
        .map_err(|e| format!("kan inte läsa spår {jsonl_path}: {e}"))?;
    let mut ticks = Vec::new();
    for (i, line) in text.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() { continue; }
        let raw: RawLine = serde_json::from_str(line).map_err(|e| format!("{jsonl_path}: rad {i}: {e}"))?;
        let Some(p) = raw.players.first() else { return Err(format!("{jsonl_path}: rad {i}: ingen player")); };
        if p.origin.len() < 3 { return Err(format!("{jsonl_path}: rad {i}: origin < 3")); }
        let t = raw.t.get().to_string();
        let x = p.origin[0].get().to_string();
        let y = p.origin[1].get().to_string();
        let z = p.origin[2].get().to_string();
        let xf: f32 = x.parse().map_err(|e| format!("x-token {x}: {e}"))?;
        let yf: f32 = y.parse().map_err(|e| format!("y-token {y}: {e}"))?;
        let zf: f32 = z.parse().map_err(|e| format!("z-token {z}: {e}"))?;
        let tf: f32 = t.parse().map_err(|e| format!("t-token {t}: {e}"))?;
        ticks.push(TickRaw { raw_row_index: i as u32, t, x, y, z, xf, yf, zf, tf });
    }
    Ok(ticks)
}

fn git_head() -> String {
    std::process::Command::new("git").args(["rev-parse", "HEAD"]).output().ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_else(|| "OKÄND".to_string())
}

// Koder för kompakt färg 1-serialisering.
// klass: 0=räckhåll 1=okänd 2=blockerad · banded_step: 0=Some 1=None 2=okänd
// chain_entry_blocked: 0=false 1=true 2=okänd · speed_source: 0=oberoende 1=härledd
struct Farg1Post {
    link_id: u32,
    klass: u8,
    bs: u8,
    ceb: u8,
    speed_src: u8,
}

/// Färg 1 för EN utgående länk med motorns EGNA predikat. SJ med sidotabell
/// utvärderas på riktigt (A3); SJ UTAN sidotabell ⇒ OKÄND (G10).
fn farg1_for_link(graph: &NavGraph, li: u32, band: u8, speed: f32) -> Farg1Post {
    let kind = graph.links[li as usize].kind;
    if kind == LinkKind::SpeedJump && graph.speed_jump_of_link(li).is_some() {
        // sidotabell finns (fixtur): härledd fart ÄR predikatindata (R1-b)
        let bs = graph.banded_step(li, band);
        let ceb = graph.chain_entry_blocked(li, speed);
        return Farg1Post {
            link_id: li,
            klass: if bs.is_some() { 0 } else { 2 },
            bs: if bs.is_some() { 0 } else { 1 },
            ceb: if ceb { 1 } else { 0 },
            speed_src: 1,
        };
    }
    if kind == LinkKind::SpeedJump {
        // G10: sidotabell saknas i dumpen; predikaten anropas INTE (fallbacken ljuger)
        return Farg1Post { link_id: li, klass: 1, bs: 2, ceb: 2, speed_src: 0 };
    }
    // non-SJ: banded_step alltid Some, chain_entry_blocked alltid false (band-/fart-oberoende)
    let bs = graph.banded_step(li, band);
    let ceb = graph.chain_entry_blocked(li, speed);
    Farg1Post {
        link_id: li,
        klass: if bs.is_some() { 0 } else { 2 },
        bs: if bs.is_some() { 0 } else { 1 },
        ceb: if ceb { 1 } else { 0 },
        speed_src: 0,
    }
}

#[allow(clippy::too_many_arguments)]
pub fn run(
    dump_path: &str, dump_id: &str, meta_path: &str, jsonl_path: &str,
    manifest_path: &str, arm: &str, dataset: &str, out_path: &str,
) -> i32 {
    let doc: Dump = match restore::read_dump(dump_path) {
        Ok(d) => d, Err(e) => { eprintln!("STOPP: {e}"); return 2; }
    };
    let (graph, _stamp, hash) = match restore::restore(&doc) {
        Ok(v) => v, Err(e) => { eprintln!("STOPP: restore: {e}"); return 2; }
    };
    if hash != doc.graph_content_hash {
        eprintln!("STOPP: dump nivå-2 {hash} != {}", doc.graph_content_hash);
        return 2;
    }
    let dump_bytes = std::fs::read(dump_path).unwrap_or_default();
    let dump_sha = sha256_hex(&dump_bytes);

    let meta_bytes = std::fs::read(meta_path).unwrap_or_else(|e| { eprintln!("STOPP: {meta_path}: {e}"); std::process::exit(2); });
    let meta: Meta = serde_json::from_slice(&meta_bytes).unwrap_or_else(|e| { eprintln!("STOPP: {meta_path}: {e}"); std::process::exit(2); });
    let meta_sha = sha256_hex(&meta_bytes);
    let jsonl_bytes = std::fs::read(jsonl_path).unwrap_or_else(|e| { eprintln!("STOPP: {jsonl_path}: {e}"); std::process::exit(2); });
    let jsonl_sha = sha256_hex(&jsonl_bytes);
    let manifest_bytes = std::fs::read(manifest_path).unwrap_or_else(|e| { eprintln!("STOPP: {manifest_path}: {e}"); std::process::exit(2); });
    let manifest_sha = sha256_hex(&manifest_bytes);
    let manifest_id = Path::new(manifest_path).file_name().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
    let manifest_dir = Path::new(manifest_path).parent().unwrap_or(Path::new("."));
    let rel_of = |p: &str| Path::new(p).strip_prefix(manifest_dir).map(|r| r.to_string_lossy().to_string()).unwrap_or_else(|_| p.to_string());
    let meta_rel = rel_of(meta_path);
    let jsonl_rel = rel_of(jsonl_path);

    let ticks = match read_ticks(jsonl_path) { Ok(t) => t, Err(e) => { eprintln!("STOPP: {e}"); return 2; } };
    if ticks.is_empty() { eprintln!("STOPP: {jsonl_path}: inga ticks"); return 2; }

    let cycle_id = Path::new(&meta_rel).parent().and_then(|d| d.file_name()).map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
    let ben_typ = if meta.ben.is_empty() {
        Path::new(&meta_rel).file_name().map(|s| s.to_string_lossy().to_string()).unwrap_or_default().trim_end_matches("_meta.json").to_string()
    } else { meta.ben.clone() };
    let ben_id = format!("{dataset}:{arm}:{cycle_id}:{ben_typ}");

    let is_main = arm == "main";
    let mut cells = Vec::with_capacity(ticks.len());
    let mut farg1: Vec<Value> = Vec::with_capacity(ticks.len());

    // Färg 1 PER TICK — ingen per-cell-cache: varje raw_row_index utvärderas med sin egen
    // härledda fart/band.
    for (i, tk) in ticks.iter().enumerate() {
        let cell = match graph.nearest(glam::Vec3::new(tk.xf, tk.yf, tk.zf)) {
            Some(c) => c, None => { eprintln!("STOPP: nearest None vid rad {}", tk.raw_row_index); return 2; }
        };
        cells.push(cell);
        if is_main { farg1.push(Value::Null); continue; }
        let speed = if i == 0 { 0.0 } else {
            let (a, b) = (&ticks[i - 1], tk);
            let dt = (b.tf - a.tf).max(0.001);
            let dx = b.xf - a.xf; let dy = b.yf - a.yf;
            (dx * dx + dy * dy).sqrt() / dt
        };
        let band = band_of(speed);
        let posts: Vec<Value> = graph.adjacency[cell as usize].iter().copied()
            .map(|li| {
                let p = farg1_for_link(&graph, li, band, speed);
                json!([p.link_id, p.klass, p.bs, p.ceb, p.speed_src])
            })
            .collect();
        farg1.push(Value::Array(posts));
    }

    let mut overgangar = 0usize;
    for w in cells.windows(2) { if w[0] != w[1] { overgangar += 1; } }

    let tick_json: Vec<Value> = ticks.iter().zip(cells.iter()).map(|(tk, &cell)| {
        json!({"raw_row_index": tk.raw_row_index, "t": tk.t, "x": tk.x, "y": tk.y, "z": tk.z, "cell": cell})
    }).collect();

    let tickserie = json!({
        "farg2_observerad_bana": tick_json,
        "farg1": if is_main { Value::Null } else { Value::Array(farg1) },
        "farg1_formel": "horisontell fart = sqrt((dx)^2+(dy)^2)/dt ur origin(t)-origin(t-1); band = band_of(fart); banded_step(li,band) / chain_entry_blocked(li,fart)",
        "farg1_koder": {
            "klass": ["räckhåll", "okänd", "blockerad"],
            "banded_step": ["Some", "None", "okänd"],
            "chain_entry_blocked": ["false", "true", "okänd"],
            "speed_source": ["oberoende", "härledd-positionsderivat"],
        },
        "farg3": if is_main { Value::Null } else {
            json!({"klass": "okänd", "skal": "vald länk ej observerad (PlanTick saknas i körningarna)", "antal": overgangar})
        },
    });

    let geometri = json!({
        "dump_id": dump_id, "dump_sha256": dump_sha, "dump_schema": "qw-nav-graph/1",
        "arm": arm, "cells": graph.cells.len(), "links": graph.links.len(),
    });
    let payload = json!({"schema": "ben3d-bunt/1", "ben_id": ben_id, "geometri": geometri, "tickserie": tickserie});
    let bundle_payload_sha256 = sha256_hex(canon::canonical(&payload).as_bytes());

    let (sj, non_sj) = { let (mut s, mut n) = (0usize, 0usize); for l in &graph.links { match l.kind { LinkKind::SpeedJump => s += 1, _ => n += 1, } } (s, n) };

    let proveniens = json!({
        "dataset_manifest": {"id": manifest_id, "sha256": manifest_sha, "session": if dataset == "t1h" { "T1h" } else { "T20m" }, "arm": arm, "cycle_id": cycle_id, "ben_typ": ben_typ},
        "medlemmar": {"ra_jsonl": {"rel": jsonl_rel, "sha256": jsonl_sha}, "meta_json": {"rel": meta_rel, "sha256": meta_sha}},
        "grafdump": {"id": dump_id, "path": dump_path, "schema": "qw-nav-graph/1", "byte_sha256": dump_sha, "map": doc.map, "cells": graph.cells.len(), "links": graph.links.len(),
                     "graph_stamp": rtx::graph_stamp(&doc.map, graph.cells.len() as u32, graph.links.len() as u32, doc.rj_links).to_string(), "graph_content_hash": hash},
        "kvitto": if arm == "fork" {
            json!({"medlem": "rokdeploy-kvitto-20260817.json", "sha256": "OKÄND",
                   "slut_observed": {"cells":5983,"links":48216,"graph_stamp":"11908727279900740725","graph_content_hash":"cd800200cad72431e0cbfe0a2fc947bd94309e334103d6cc0abd076155ecf051"}})
        } else {
            // main: bunden till FAKTISK main-källa (basdumpens G1b-identitet), INTE fork-kvittot
            json!({"medlem": format!("{dump_id} (basdump, G1b)"), "sha256": dump_sha,
                   "slut_observed": {"cells": graph.cells.len(), "links": graph.links.len(),
                     "graph_stamp": rtx::graph_stamp(&doc.map, graph.cells.len() as u32, graph.links.len() as u32, doc.rj_links).to_string(),
                     "graph_content_hash": hash}})
        },
        "extractor": {"commit": git_head(), "motor_crate_commit": git_head(), "cargo_lock_sha256": "OKÄND", "binary_sha256": "OKÄND", "cli_config_sha256": "OKÄND",
                      "skal": "binary/cargo_lock/cli fylls av launcher; OKÄND här = ej ifyllt ännu", "restore_schema": "qw-nav-graph/1", "dump_schema": "qw-nav-graph/1"},
        "matt": {"measure_id": meta.measure_id, "falls_measure_id": meta.falls_measure_id,
                 "qwprogs_sha256": if arm == "fork" { "3fe70a8c6b22308901b3f4d1691d8f0988d56daa3cb958ff04ef21d82b6468e5" } else { "OKÄND" },
                 "cvarvarden": {"rtx_bot_bhop": Value::Null, "rtx_doublejump": Value::Null, "rtx_bot_chain_entry_gate": Value::Null, "rtx_nav_patch": Value::Null, "rtx_r1_lite": Value::Null,
                                "skal": "cvärdena ligger inte i kvittot; hämtas ej ur rigg — OKÄND"}},
        "viewer": {"commit": git_head(), "bundle_schema": "ben3d-bunt/1"},
        "bundle_payload_sha256": bundle_payload_sha256,
        "farg1_policy": {"regel": "per utgående T=1-länk per tick: motorns banded_step/chain_entry_blocked; non-SJ = faktiskt svar (band-oberoende), SJ utan sidotabell = OKÄND (G10), SJ med sidotabell = härledd fart som indata (R1-b)", "sj_okand": sj, "non_sj_rackhall": non_sj, "speed_source": "härledd-positionsderivat (R1-b) för fartberoende poster; oberoende för topologi/chain-flaggor"},
        "main_arm": if is_main { json!({"farg1": "disabled", "farg3": "disabled", "skal": "G11: annan motorbinär — endast geometri + observerad bana"}) } else { Value::Null },
    });

    let bunt = json!({"schema": "ben3d-bunt/1", "ben_id": ben_id, "geometri": geometri, "tickserie": tickserie, "proveniens": proveniens, "bundle_payload_sha256": bundle_payload_sha256});
    let bytes = serde_json::to_vec(&bunt).expect("serialisering");
    if let Err(e) = std::fs::write(out_path, &bytes) { eprintln!("STOPP: kan inte skriva {out_path}: {e}"); return 2; }
    println!("bunt {ben_id}: {} ticks, {} celler, {} övergångar, payload-sha {}", ticks.len(), graph.cells.len(), overgangar, bundle_payload_sha256);
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    use rtx_nav::navmesh::{Link, SpeedJumpTraversal};

    #[test]
    fn farg1_anropar_motorpredikaten_per_link() {
        let origins = vec![glam::Vec3::new(0.0,0.0,0.0), glam::Vec3::new(64.0,0.0,0.0)];
        let links = vec![Link { from: 0, to: 1, kind: LinkKind::Walk, cost: 1.0 }];
        let mut g = NavGraph::from_topology(&origins, &links);
        let sj_li = g.plant_speed_jump(0, 1, 1.0, SpeedJumpTraversal { takeoff: glam::Vec3::new(32.0,0.0,0.0), v_req: 320.0, chained: true, ..Default::default() });
        // non-SJ: band-oberoende, alltid räckhåll
        let p = farg1_for_link(&g, 0, 0, 0.0);
        assert_eq!((p.klass, p.bs, p.ceb, p.speed_src), (0, 0, 0, 0));
        // SJ MED sidotabell: farg1_for_link får INTE kortsluta — under/över tröskeln (A3)
        let low = farg1_for_link(&g, sj_li, 0, 100.0);
        let high = farg1_for_link(&g, sj_li, 3, 300.0);
        assert_eq!((low.klass, low.bs), (2, 1), "under tröskeln: banded_step None => blockerad");
        assert_eq!((low.ceb, low.speed_src), (1, 1), "chain_entry_blocked true, härledd fart");
        assert_eq!((high.klass, high.bs), (0, 0), "över tröskeln: banded_step Some => räckhåll");
        assert_eq!((high.ceb, high.speed_src), (0, 1), "chain_entry_blocked false");
    }

    #[test]
    fn farg1_sj_utan_sidotabell_okand() {
        // restore-semantik: SJ-länk utan sidotabell (topologi-dump) => OKÄND, predikat ej anropat
        let origins = vec![glam::Vec3::new(0.0,0.0,0.0), glam::Vec3::new(64.0,0.0,0.0)];
        let links = vec![Link { from: 0, to: 1, kind: LinkKind::SpeedJump, cost: 1.0 }];
        let g = NavGraph::from_topology(&origins, &links);
        let p = farg1_for_link(&g, 0, 3, 300.0);
        assert_eq!((p.klass, p.bs, p.ceb, p.speed_src), (1, 2, 2, 0));
    }
}
