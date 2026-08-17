//! ben3d extraktor — etapp 2c: per-tick-regelutvärdering + benbuntar (D1/D2).
//!
//! Per tick: cell = motorns `nearest(origin)` (G8). Färg 1 (RÄCKHÅLL) är per
//! utgående länk och tidsinvariant i en topologi-dump: `banded_step` ger alltid
//! `Some` för non-SpeedJump (bara en kedjad SJ kan ge `None`), och SJ-länkarnas
//! sidotabell (`chained`) saknas i dumpen ⇒ OKÄND (G10). Färg 2 = observerad
//! cellsekvens. Färg 3 = "OKÄND — vald länk ej observerad" (PlanTick saknas, P2).
//! Main-armen: endast geometri + observerad bana (G11) — färg 1/3 disabled.

use crate::canon;
use crate::restore::{self, Dump};
use rtx_nav::navmesh::LinkKind;
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::path::Path;

fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

/// En rad ur rå-JSONL med numeriska token bevarade EXAKT (RawValue) — A2(i):
/// varje rå-ticks (t,x,y,z) ska finnas bit-identiskt i bunten.
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
}

#[derive(Deserialize, Default)]
#[allow(dead_code)] // tid/t_hit läses ej av extraktorn, men hör till metats schema
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
        ticks.push(TickRaw {
            raw_row_index: i as u32,
            t,
            x,
            y,
            z,
            xf,
            yf,
            zf,
        });
    }
    Ok(ticks)
}

/// Läs binärens egen commit (git rev-parse HEAD) om möjligt.
fn git_head() -> String {
    std::process::Command::new("git")
        .args(["rev-parse", "HEAD"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_else(|| "OKÄND".to_string())
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

    // 3) Cell per tick = motorns nearest(origin) (G8).
    let mut cells: Vec<u32> = Vec::with_capacity(ticks.len());
    for tk in &ticks {
        match graph.nearest(glam::Vec3::new(tk.xf, tk.yf, tk.zf)) {
            Some(c) => cells.push(c),
            None => {
                eprintln!("STOPP: nearest None vid rad {}", tk.raw_row_index);
                return 2;
            }
        }
    }

    // 4) Färg 1-policy + räknare (tidsinvariant), färg 3 = OKÄND.
    let (mut sj, mut non_sj) = (0usize, 0usize);
    for l in &graph.links {
        match l.kind {
            LinkKind::SpeedJump => sj += 1,
            _ => non_sj += 1,
        }
    }
    // Observerade övergångar (cellbyte) — färg 3:s "antal" och färg 2:s rörelse.
    let mut overgangar = 0usize;
    for w in cells.windows(2) {
        if w[0] != w[1] {
            overgangar += 1;
        }
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

    // 5) Payload = {schema, ben_id, geometri, tickserie} (D1).
    let geometri = json!({
        "dump_id": dump_id,
        "dump_sha256": dump_sha,
        "dump_schema": "qw-nav-graph/1",
        "arm": arm,
        "cells": graph.cells.len(),
        "links": graph.links.len(),
    });
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
        "farg3": {
            "klass": "okänd",
            "skal": "vald länk ej observerad (PlanTick saknas i körningarna)",
            "antal": overgangar,
        },
    });
    let payload = json!({
        "schema": "ben3d-bunt/1",
        "ben_id": ben_id,
        "geometri": geometri,
        "tickserie": tickserie,
    });
    let bundle_payload_sha256 = sha256_hex(canon::canonical(&payload).as_bytes());

    // 6) Proveniensblock (P1) — alla fält, saknat explicit null/OKÄND med skäl.
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
            "slut_observed": if arm == "fork" {
                json!({"cells":5983,"links":48216,"graph_stamp":"11908727279900740725","graph_content_hash":"cd800200cad72431e0cbfe0a2fc947bd94309e334103d6cc0abd076155ecf051"})
            } else {
                Value::Null
            },
        },
        "extractor": {
            "commit": git_head(),
            "motor_crate_commit": git_head(),
            "cargo_lock_sha256": Value::Null,
            "binary_sha256": Value::Null,
            "cli_config_sha256": Value::Null,
            "skal_for_null": "Cargo.lock-/binär-/CLI-SHA fylls av ben3d_verify/launcher (P4)",
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
            "commit": Value::Null,
            "bundle_schema": "ben3d-bunt/1",
            "skal": "viewern byggs i etapp 5; commit fylls då",
        },
        "farg1_policy": {
            "regel": "per utgående länk ur aktuell cell: motorns banded_step; non-SpeedJump = Some (räckhåll), SpeedJump = OKÄND (G10)",
            "sj_okand": sj,
            "non_sj_rackhall": non_sj,
            "orsakskoder": {"non_sj": "BANDED_STEP_SOME", "sj": "G10_SIDOTABELL_SAKNAS"},
        },
        "main_arm": if arm == "main" {
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

    let bytes = serde_json::to_vec_pretty(&bunt).expect("serialisering");
    if let Err(e) = std::fs::write(out_path, &bytes) {
        eprintln!("STOPP: kan inte skriva {out_path}: {e}");
        return 2;
    }
    println!(
        "bunt {ben_id}: {} ticks, {} celler, {} övergångar, payload-sha {}",
        ticks.len(),
        graph.cells.len(),
        overgangar,
        bundle_payload_sha256
    );
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    use rtx_nav::navmesh::{Link, NavGraph};

    fn tmp(p: &str) -> String {
        let d = std::env::temp_dir().join(format!("ben3d-extract-{}-{}", std::process::id(), p));
        d.to_string_lossy().to_string()
    }

    #[test]
    fn bunt_bitidentiska_ticks_och_celltilldelning() {
        // syntetisk graf: 0<->1 walk + 0->2 speedjump (SJ = OKÄND)
        let origins = vec![
            glam::Vec3::new(0.0, 0.0, 0.0),
            glam::Vec3::new(32.0, 0.0, 0.0),
            glam::Vec3::new(0.0, 32.0, 0.0),
        ];
        let links = vec![
            Link { from: 0, to: 1, kind: LinkKind::Walk, cost: 1.0 },
            Link { from: 1, to: 0, kind: LinkKind::Walk, cost: 1.0 },
            Link { from: 0, to: 2, kind: LinkKind::SpeedJump, cost: 2.0 },
        ];
        let g = NavGraph::from_topology(&origins, &links);
        let dump = crate::fork::serialize_dump(&g, "dm3", 32.0);
        let dump_path = tmp("graph.json");
        std::fs::write(&dump_path, serde_json::to_vec(&dump).unwrap()).unwrap();

        // spår med exakta token
        let jsonl = r#"{"t":1.5,"wall":0.0,"measure_id":"x","players":[{"ent":1,"origin":[0.0,0.0,0.0],"on_ground":true}]}
{"t":1.52,"wall":0.02,"measure_id":"x","players":[{"ent":1,"origin":[16.5,0.0,0.0],"on_ground":true}]}
{"t":1.54,"wall":0.04,"measure_id":"x","players":[{"ent":1,"origin":[32.0,0.0,0.0],"on_ground":true}]}
"#;
        let jsonl_path = tmp("in_vast.jsonl");
        std::fs::write(&jsonl_path, jsonl).unwrap();
        let meta = r#"{"utfall":"fall","cykel":1,"ben":"in_vast","measure_id":"k@r6","falls_measure_id":"f@r6"}"#;
        let meta_path = tmp("in_vast_meta.json");
        std::fs::write(&meta_path, meta).unwrap();
        let manifest_path = tmp("m.sha256");
        let msha = |b: &[u8]| format!("{:x}", Sha256::digest(b));
        let manifest = format!(
            "{}  in_vast_meta.json\n{}  in_vast.jsonl\n",
            msha(meta.as_bytes()),
            msha(jsonl.as_bytes())
        );
        std::fs::write(&manifest_path, &manifest).unwrap();

        let out = tmp("bunt.json");
        let rc = run(&dump_path, "dm3-test", &meta_path, &jsonl_path, &manifest_path, "fork", "t1h", &out);
        assert_eq!(rc, 0);

        let b: Value = serde_json::from_slice(&std::fs::read(&out).unwrap()).unwrap();
        let ticks = b["tickserie"]["farg2_observerad_bana"].as_array().unwrap();
        assert_eq!(ticks.len(), 3);
        assert_eq!(ticks[0]["t"], "1.5");
        assert_eq!(ticks[1]["x"], "16.5");
        assert_eq!(ticks[2]["z"], "0.0");
        // celltilldelning: tick0 -> 0, tick1 -> 1 (16.5 närmare 32 än 0), tick2 -> 1
        assert_eq!(ticks[0]["cell"], 0);
        assert_eq!(ticks[1]["cell"], 1);
        assert_eq!(ticks[2]["cell"], 1);
        // farg1-policy: 1 SJ (OKÄND), 2 non-SJ
        assert_eq!(b["proveniens"]["farg1_policy"]["sj_okand"], 1);
        assert_eq!(b["proveniens"]["farg1_policy"]["non_sj_rackhall"], 2);
        // payload-sha: räkna om oberoende
        let payload = json!({
            "schema": b["schema"],
            "ben_id": b["ben_id"],
            "geometri": b["geometri"],
            "tickserie": b["tickserie"],
        });
        let expect = sha256_hex(canon::canonical(&payload).as_bytes());
        assert_eq!(b["bundle_payload_sha256"], expect);
    }
}
