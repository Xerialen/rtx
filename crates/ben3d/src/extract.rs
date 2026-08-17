//! ben3d extraktor — etapp 2c: per-tick-regelutvärdering + benbuntar (D1/D2).
//!
//! Per tick: cell = motorns `nearest(origin)` (G8), botens horisontella fart ur
//! spåret (indata till predikaten, inte en egen regel), band = `band_of(fart)`.
//! För VARJE utgående T=1-länk ur aktuell cell anropas motorns EGNA predikat
//! (`banded_step` / `chain_entry_blocked`) och svaret sparas i en färdig
//! per-tick-färglista (länk-id, färgklass, predikatets faktiska svar, orsakskod).
//! SJ-länkar: sidotabellen (`chained`) saknas i dumpen ⇒ OKÄND, predikaten anropas
//! INTE (deras fallback skulle ljuga) — G10. Färg 2 = observerad cellsekvens.
//! Färg 3 = "OKÄND — vald länk ej observerad" (PlanTick saknas, P2).
//! Main-armen: färg 1/3 beräknas INTE (G11) — endast geometri + observerad bana.

use crate::canon;
use crate::restore::{self, Dump};
use rtx_nav::navmesh::{band_of, LinkKind, NavGraph};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
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
    #[serde(default)]
    utfall: String,
    #[serde(default)]
    measure_id: String,
    #[serde(default)]
    falls_measure_id: String,
    #[serde(default)]
    cykel: i64,
    #[serde(default)]
    ben: String,
    #[serde(default)]
    tid: Option<f64>,
    #[serde(default)]
    t_hit: Option<f64>,
}

fn read_ticks(jsonl_path: &str) -> Result<Vec<TickRaw>, String> {
    let text = std::fs::read_to_string(Path::new(jsonl_path))
        .map_err(|e| format!("kan inte läsa spår {jsonl_path}: {e}"))?;
    let mut ticks = Vec::new();
    for (i, line) in text.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let raw: RawLine =
            serde_json::from_str(line).map_err(|e| format!("{jsonl_path}: rad {i}: {e}"))?;
        let Some(p) = raw.players.first() else {
            return Err(format!("{jsonl_path}: rad {i}: ingen player"));
        };
        if p.origin.len() < 3 {
            return Err(format!("{jsonl_path}: rad {i}: origin har {} < 3", p.origin.len()));
        }
        let t = raw.t.get().to_string();
        let x = p.origin[0].get().to_string();
        let y = p.origin[1].get().to_string();
        let z = p.origin[2].get().to_string();
        let xf: f32 = x.parse().map_err(|e| format!("x-token {x}: {e}"))?;
        let yf: f32 = y.parse().map_err(|e| format!("y-token {y}: {e}"))?;
        let zf: f32 = z.parse().map_err(|e| format!("z-token {z}: {e}"))?;
        let tf: f32 = t.parse().map_err(|e| format!("t-token {t}: {e}"))?;
        ticks.push(TickRaw {
            raw_row_index: i as u32,
            t,
            x,
            y,
            z,
            xf,
            yf,
            zf,
            tf,
        });
    }
    Ok(ticks)
}

fn git_head() -> String {
    std::process::Command::new("git")
        .args(["rev-parse", "HEAD"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_else(|| "OKÄND".to_string())
}

/// En färdig färgpost för en utgående länk: predikatens FAKTISKA svar.
#[derive(Clone)]
struct Farg1Post {
    link_id: u32,
    klass: &'static str,
    banded_step: Option<bool>,   // Some(true)=Some, Some(false)=None, None=ej anropat (SJ/G10)
    chain_entry_blocked: Option<bool>, // None=ej anropat (SJ/G10)
    orsakskod: &'static str,
}

/// Utvärdera färg 1 för en utgående T=1-länk med motorns egna predikat.
fn farg1_for_link(graph: &NavGraph, li: u32, band: u8, speed: f32) -> Farg1Post {
    let kind = graph.links[li as usize].kind;
    if kind == LinkKind::SpeedJump {
        // G10: dumpen bär ingen sidotabell (`chained`/`v_req`); banded_step/chain_entry_blocked
        // skulle läsa fallbacken och ljuga. Därför anropas de inte — klass = OKÄND.
        return Farg1Post {
            link_id: li,
            klass: "okänd",
            banded_step: None,
            chain_entry_blocked: None,
            orsakskod: "G10_SIDOTABELL_SAKNAS",
        };
    }
    let bs = graph.banded_step(li, band);
    let ceb = graph.chain_entry_blocked(li, speed);
    Farg1Post {
        link_id: li,
        klass: if bs.is_some() { "räckhåll" } else { "blockerad" },
        banded_step: Some(bs.is_some()),
        chain_entry_blocked: Some(ceb),
        orsakskod: if bs.is_some() { "BANDED_STEP_SOME" } else { "BANDED_STEP_NONE" },
    }
}

#[allow(clippy::too_many_arguments)]
pub fn run(
    dump_path: &str,
    dump_id: &str,
    meta_path: &str,
    jsonl_path: &str,
    manifest_path: &str,
    arm: &str,
    dataset: &str,
    out_path: &str,
) -> i32 {
    // 1) Dump → graph, G1b-verifierad.
    let doc: Dump = match restore::read_dump(dump_path) {
        Ok(d) => d,
        Err(e) => {
            eprintln!("STOPP: {e}");
            return 2;
        }
    };
    let (graph, _stamp, hash) = match restore::restore(&doc) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("STOPP: restore: {e}");
            return 2;
        }
    };
    if hash != doc.graph_content_hash {
        eprintln!("STOPP: dump nivå-2 {hash} != {}", doc.graph_content_hash);
        return 2;
    }
    let dump_bytes = std::fs::read(dump_path).unwrap_or_default();
    let dump_sha = sha256_hex(&dump_bytes);

    // 2) Meta + spår + manifest.
    let meta_bytes = std::fs::read(meta_path).unwrap_or_else(|e| {
        eprintln!("STOPP: {meta_path}: {e}");
        std::process::exit(2);
    });
    let meta: Meta = serde_json::from_slice(&meta_bytes).unwrap_or_else(|e| {
        eprintln!("STOPP: {meta_path} är inte JSON: {e}");
        std::process::exit(2);
    });
    let meta_sha = sha256_hex(&meta_bytes);
    let jsonl_bytes = std::fs::read(jsonl_path).unwrap_or_else(|e| {
        eprintln!("STOPP: {jsonl_path}: {e}");
        std::process::exit(2);
    });
    let jsonl_sha = sha256_hex(&jsonl_bytes);
    let manifest_bytes = std::fs::read(manifest_path).unwrap_or_else(|e| {
        eprintln!("STOPP: {manifest_path}: {e}");
        std::process::exit(2);
    });
    let manifest_sha = sha256_hex(&manifest_bytes);
    let manifest_id = Path::new(manifest_path)
        .file_name()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_default();
    let manifest_dir = Path::new(manifest_path).parent().unwrap_or(Path::new("."));
    let rel_of = |p: &str| {
        Path::new(p)
            .strip_prefix(manifest_dir)
            .map(|r| r.to_string_lossy().to_string())
            .unwrap_or_else(|_| p.to_string())
    };
    let meta_rel = rel_of(meta_path);
    let jsonl_rel = rel_of(jsonl_path);

    let ticks = match read_ticks(jsonl_path) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("STOPP: {e}");
            return 2;
        }
    };
    if ticks.is_empty() {
        eprintln!("STOPP: {jsonl_path}: inga ticks");
        return 2;
    }

    let cycle_id = Path::new(&meta_rel)
        .parent()
        .and_then(|d| d.file_name())
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_default();
    let ben_typ = if meta.ben.is_empty() {
        Path::new(&meta_rel)
            .file_name()
            .map(|s| s.to_string_lossy().to_string())
            .unwrap_or_default()
            .trim_end_matches("_meta.json")
            .to_string()
    } else {
        meta.ben.clone()
    };
    let ben_id = format!("{dataset}:{arm}:{cycle_id}:{ben_typ}");

    // 3) Per tick: cell = nearest(origin); fart ur spåret; färg 1 per utgående länk.
    let is_main = arm == "main";
    let mut cells: Vec<u32> = Vec::with_capacity(ticks.len());
    let mut speeds: Vec<f32> = Vec::with_capacity(ticks.len());
    // per-cell färg 1-poster (dedup: samma cell ⇒ samma lista), första raw_row_index bevaras
    let mut farg1_by_cell: BTreeMap<u32, (u32, Vec<Farg1Post>)> = BTreeMap::new();

    for (i, tk) in ticks.iter().enumerate() {
        let cell = match graph.nearest(glam::Vec3::new(tk.xf, tk.yf, tk.zf)) {
            Some(c) => c,
            None => {
                eprintln!("STOPP: nearest None vid rad {}", tk.raw_row_index);
                return 2;
            }
        };
        cells.push(cell);
        // horisontell fart mellan tick i-1 och i (indata till predikaten)
        let speed = if i == 0 {
            0.0
        } else {
            let (a, b) = (&ticks[i - 1], tk);
            let dt = (b.tf - a.tf).max(0.001);
            let dx = b.xf - a.xf;
            let dy = b.yf - a.yf;
            (dx * dx + dy * dy).sqrt() / dt
        };
        speeds.push(speed);
        if is_main {
            continue; // G11: main-armen beräknar ingen färg 1
        }
        if farg1_by_cell.contains_key(&cell) {
            continue;
        }
        let band = band_of(speed);
        let posts: Vec<Farg1Post> = graph.adjacency[cell as usize]
            .iter()
            .copied()
            .map(|li| farg1_for_link(&graph, li, band, speed))
            .collect();
        farg1_by_cell.insert(cell, (tk.raw_row_index, posts));
    }

    // Observerade övergångar (cellbyte) — färg 3:s antal.
    let mut overgangar = 0usize;
    for w in cells.windows(2) {
        if w[0] != w[1] {
            overgangar += 1;
        }
    }

    // 4) Serialisera färg 1-poster (färdiga per-cell-listor med raw_row_index).
    let farg1_json: Vec<Value> = farg1_by_cell
        .iter()
        .map(|(cell, (raw_row_index, posts))| {
            let lankar: Vec<Value> = posts
                .iter()
                .map(|p| {
                    json!({
                        "link_id": p.link_id,
                        "klass": p.klass,
                        "banded_step": p.banded_step.map(|s| if s { "Some" } else { "None" }).unwrap_or("okänd"),
                        "chain_entry_blocked": p.chain_entry_blocked,
                        "orsakskod": p.orsakskod,
                    })
                })
                .collect();
            json!({"cell": cell, "raw_row_index": raw_row_index, "lankar": lankar})
        })
        .collect();

    let tick_json: Vec<Value> = ticks
        .iter()
        .zip(cells.iter())
        .map(|(tk, &cell)| {
            json!({
                "raw_row_index": tk.raw_row_index,
                "t": tk.t,
                "x": tk.x,
                "y": tk.y,
                "z": tk.z,
                "cell": cell,
            })
        })
        .collect();

    let tickserie = json!({
        "farg2_observerad_bana": tick_json,
        "farg1": if is_main { Value::Null } else { Value::Array(farg1_json) },
        "farg3": if is_main {
            Value::Null
        } else {
            json!({"klass": "okänd", "skal": "vald länk ej observerad (PlanTick saknas i körningarna)", "antal": overgangar})
        },
    });

    // 5) Payload + proveniens.
    let geometri = json!({
        "dump_id": dump_id,
        "dump_sha256": dump_sha,
        "dump_schema": "qw-nav-graph/1",
        "arm": arm,
        "cells": graph.cells.len(),
        "links": graph.links.len(),
    });
    let payload = json!({
        "schema": "ben3d-bunt/1",
        "ben_id": ben_id,
        "geometri": geometri,
        "tickserie": tickserie,
    });
    let bundle_payload_sha256 = sha256_hex(canon::canonical(&payload).as_bytes());

    let (sj, non_sj) = {
        let (mut s, mut n) = (0usize, 0usize);
        for l in &graph.links {
            match l.kind {
                LinkKind::SpeedJump => s += 1,
                _ => n += 1,
            }
        }
        (s, n)
    };

    let proveniens = json!({
        "dataset_manifest": {
            "id": manifest_id,
            "sha256": manifest_sha,
            "session": if dataset == "t1h" { "T1h" } else { "T20m" },
            "arm": arm,
            "cycle_id": cycle_id,
            "ben_typ": ben_typ,
        },
        "medlemmar": {
            "ra_jsonl": {"rel": jsonl_rel, "sha256": jsonl_sha},
            "meta_json": {"rel": meta_rel, "sha256": meta_sha},
        },
        "grafdump": {
            "id": dump_id,
            "path": dump_path,
            "schema": "qw-nav-graph/1",
            "byte_sha256": dump_sha,
            "map": doc.map,
            "cells": graph.cells.len(),
            "links": graph.links.len(),
            "graph_stamp": rtx::graph_stamp(&doc.map, graph.cells.len() as u32, graph.links.len() as u32, doc.rj_links).to_string(),
            "graph_content_hash": hash,
        },
        "kvitto": {
            "medlem": "rokdeploy-kvitto-20260817.json (slut_observed)",
            "sha256": "OKÄND", // fylls av ben3d_verify (källverifierare)
            "slut_observed": if arm == "fork" {
                json!({"cells":5983,"links":48216,"graph_stamp":"11908727279900740725","graph_content_hash":"cd800200cad72431e0cbfe0a2fc947bd94309e334103d6cc0abd076155ecf051"})
            } else {
                json!({"cells":5977,"links":48207,"graph_stamp":"906595427771298736","graph_content_hash":"58787ce0d27ddd49ef109fa380ad5aca1c5fb65ba5125d485ad0e2ebd0f88ad9"})
            },
        },
        "extractor": {
            "commit": git_head(),
            "motor_crate_commit": git_head(),
            "cargo_lock_sha256": "OKÄND",
            "binary_sha256": "OKÄND",
            "cli_config_sha256": "OKÄND",
            "skal": "binary/cargo_lock/cli fylls av ben3d_verify (källverifierare, P4)",
            "restore_schema": "qw-nav-graph/1",
            "dump_schema": "qw-nav-graph/1",
        },
        "matt": {
            "measure_id": meta.measure_id,
            "falls_measure_id": meta.falls_measure_id,
            "qwprogs_sha256": if arm == "fork" { "3fe70a8c6b22308901b3f4d1691d8f0988d56daa3cb958ff04ef21d82b6468e5" } else { "OKÄND" },
            "cvarvarden": {
                "rtx_bot_bhop": Value::Null,
                "rtx_doublejump": Value::Null,
                "rtx_bot_chain_entry_gate": Value::Null,
                "rtx_nav_patch": Value::Null,
                "rtx_r1_lite": Value::Null,
                "skal": "cvärdena ligger inte i rokdeploy-kvittot; hämtas ej ur rigg — OKÄND",
            },
        },
        "viewer": {
            "commit": git_head(),
            "bundle_schema": "ben3d-bunt/1",
        },
        "bundle_payload_sha256": bundle_payload_sha256,
        "farg1_policy": {
            "regel": "per utgående T=1-länk ur aktuell cell: motorns banded_step/chain_entry_blocked per tick; non-SJ = faktiskt svar, SJ = OKÄND (G10, predikaten ej anropade)",
            "sj_okand": sj,
            "non_sj_rackhall": non_sj,
        },
        "main_arm": if is_main {
            json!({"farg1": "disabled", "farg3": "disabled", "skal": "G11: annan motorbinär — endast geometri + observerad bana"})
        } else {
            Value::Null
        },
    });

    let bunt = json!({
        "schema": "ben3d-bunt/1",
        "ben_id": ben_id,
        "geometri": geometri,
        "tickserie": tickserie,
        "proveniens": proveniens,
        "bundle_payload_sha256": bundle_payload_sha256,
    });

    let bytes = serde_json::to_vec(&bunt).expect("serialisering");
    if let Err(e) = std::fs::write(out_path, &bytes) {
        eprintln!("STOPP: kan inte skriva {out_path}: {e}");
        return 2;
    }
    println!(
        "bunt {ben_id}: {} ticks, {} celler, {} övergångar, {} färg1-celler, payload-sha {}",
        ticks.len(),
        graph.cells.len(),
        overgangar,
        farg1_by_cell.len(),
        bundle_payload_sha256
    );
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    use rtx_nav::navmesh::{Link, SpeedJumpTraversal};

    #[test]
    fn farg1_anropar_motorpredikaten() {
        // syntetisk graf med en KEDJAD SJ (sidotabell via plant_speed_jump) — A3:s
        // under/över-tröskel: banded_step None vid låg band, Some vid hög; chain_entry_blocked
        // true vid låg fart, false vid hög.
        let origins = vec![
            glam::Vec3::new(0.0, 0.0, 0.0),
            glam::Vec3::new(64.0, 0.0, 0.0),
        ];
        let links = vec![Link { from: 0, to: 1, kind: LinkKind::Walk, cost: 1.0 }];
        let mut g = NavGraph::from_topology(&origins, &links);
        let sj_li = g.plant_speed_jump(
            0, 1, 1.0,
            SpeedJumpTraversal { takeoff: glam::Vec3::new(32.0,0.0,0.0), v_req: 320.0, chained: true, ..Default::default() },
        );
        // låg band (0): banded_step None; hög band (3): Some
        assert!(g.banded_step(sj_li, 0).is_none());
        assert!(g.banded_step(sj_li, 3).is_some());
        // chain_entry_blocked: fart 100 < 0.5*320 → true; fart 300 → false
        assert!(g.chain_entry_blocked(sj_li, 100.0));
        assert!(!g.chain_entry_blocked(sj_li, 300.0));
        // extraktorns färg1-post för en non-SJ-länk: banded_step Some + ceb false
        let p = farg1_for_link(&g, 0, 3, 300.0);
        assert_eq!(p.klass, "räckhåll");
        assert_eq!(p.banded_step, Some(true));
        assert_eq!(p.chain_entry_blocked, Some(false));
        // extraktorns färg1-post för SJ-länken (dump utan sidotabell): OKÄND, predikat ej anropat
        let psj = farg1_for_link(&g, sj_li, 3, 300.0);
        assert_eq!(psj.klass, "okänd");
        assert_eq!(psj.banded_step, None);
        assert_eq!(psj.chain_entry_blocked, None);
        assert_eq!(psj.orsakskod, "G10_SIDOTABELL_SAKNAS");
    }
}
