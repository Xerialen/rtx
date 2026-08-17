//! ben3d extraktor — etapp 3: offline härledning av fork-grafdumpen.
//!
//! Det finns INGEN förseglad dump för fork-grafen 5983/48216 (nivå-2 `cd800200…`)
//! i manifesten eller `~/lab` — bara basdumpen `~/lab/toolbox/dm3-base-full-graph.json`
//! (5977/48207, restore-verifierad) och komponatets op-lista (förseglat manifest
//! `bcba5897…`). Per G1b kräver färg 1/3 en dump vars stamp matchar körkvittot på
//! BÅDA nivåerna, så fork-grafen härleds här OFFLINE: basdumpen restoreas, komponatets
//! op-lista körs genom motorns EGEN apply-kod (`rtx::komponat_apply` =
//! `plant_speed_jump_into` + `apply_in_chain`, 72d5733), och resultatet verifieras
//! mot körkvittots `slut_observed`. Avvikelse = STOPP (exit 2), ingen dump produceras.
//!
//! Ingen socket, ingen Control-port, ingen körande instans, ingen ~/lab-skrivning.

use rtx::graph_content_hash;
use rtx_ctlproto::{GraphIdent, KomponatOp, KomponatStep};
use rtx_nav::navmesh::NavGraph;
use serde::Deserialize;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::path::Path;

use crate::restore::{self, kind_token, Dump};

/// Körkvittots slut_observed (rokdeploy-kvitto-20260817.json) — bindande mål.
const TARGET_CELLS: u32 = 5983;
const TARGET_LINKS: u32 = 48216;
const TARGET_STAMP: &str = "11908727279900740725";
const TARGET_HASH: &str = "cd800200cad72431e0cbfe0a2fc947bd94309e334103d6cc0abd076155ecf051";

// ---- recept (komponat-v296-ram.json) — op-parametrarna ----

#[derive(Deserialize)]
struct Recept {
    base: ReceptIdent,
    ops: Vec<ReceptOp>,
}

#[derive(Deserialize)]
struct ReceptIdent {
    cells: u32,
    links: u32,
    rj_links: u32,
    graph_stamp: String,
    graph_content_hash: String,
}

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
enum ReceptOp {
    PlanLink {
        from: [f32; 3],
        takeoff: [f32; 3],
        tgt: [f32; 3],
        v_req: f32,
        #[serde(default)]
        gain: Option<f32>,
    },
    ShelfPatch {
        name: String,
    },
}

// ---- förseglat manifest (komponat-v296-ram.manifest.json) — stegidentiteterna ----

#[derive(Deserialize)]
struct Manifest {
    steg: Vec<Steg>,
    slut: Identitet,
}

#[derive(Deserialize)]
struct Steg {
    name: String,
    op: String,
    identitet: Identitet,
}

#[derive(Deserialize)]
struct Identitet {
    cells: u32,
    links: u32,
    rj_links: u32,
    graph_stamp: String,
    #[serde(rename = "graph_content_hash_utan_params")]
    graph_content_hash: String,
}

fn ident_of(i: &Identitet) -> GraphIdent {
    GraphIdent {
        cells: i.cells,
        links: i.links,
        rj_links: i.rj_links,
        graph_stamp: i.graph_stamp.clone(),
        graph_content_hash: i.graph_content_hash.clone(),
    }
}

fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

pub fn run(
    base_dump: &str,
    recept_path: &str,
    manifest_path: &str,
    out_dump: &str,
    out_register: &str,
) -> i32 {
    // 1) Basdump: read + restore + G1b-verifiering mot dumpens eget fält.
    let doc: Dump = match restore::read_dump(base_dump) {
        Ok(d) => d,
        Err(e) => {
            eprintln!("STOPP: {e}");
            return 2;
        }
    };
    let (base_graph, base_stamp, base_hash) = match restore::restore(&doc) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("STOPP: basdump-restore misslyckades: {e}");
            return 2;
        }
    };
    if base_hash != doc.graph_content_hash {
        eprintln!(
            "STOPP: basdump nivå-2 {base_hash} != dumpens graph_content_hash {}",
            doc.graph_content_hash
        );
        return 2;
    }
    if doc.map != "dm3" {
        eprintln!("STOPP: basdump map {:?} != dm3", doc.map);
        return 2;
    }

    // 2) Recept + förseglat manifest.
    let recept_bytes = std::fs::read(Path::new(recept_path)).unwrap_or_else(|e| {
        eprintln!("STOPP: kan inte läsa recept {recept_path}: {e}");
        std::process::exit(2);
    });
    let manifest_bytes = std::fs::read(Path::new(manifest_path)).unwrap_or_else(|e| {
        eprintln!("STOPP: kan inte läsa manifest {manifest_path}: {e}");
        std::process::exit(2);
    });
    let recept: Recept = serde_json::from_slice(&recept_bytes).unwrap_or_else(|e| {
        eprintln!("STOPP: recept är inte JSON: {e}");
        std::process::exit(2);
    });
    let manifest: Manifest = serde_json::from_slice(&manifest_bytes).unwrap_or_else(|e| {
        eprintln!("STOPP: manifest är inte JSON: {e}");
        std::process::exit(2);
    });

    let base_dump_sha = sha256(&std::fs::read(base_dump).expect("läst ovan"));
    let recept_sha = sha256(&recept_bytes);
    let manifest_sha = sha256(&manifest_bytes);

    // 3) Receptets base-identitet ska stämma med manifestets pin och basdumpen.
    let pin = &manifest.steg[0];
    if pin.op != "pin" {
        eprintln!("STOPP: manifest.steg[0].op {:?} != pin", pin.op);
        return 2;
    }
    let pin_ident = ident_of(&pin.identitet);
    if recept.base.cells != pin.identitet.cells
        || recept.base.links != pin.identitet.links
        || recept.base.rj_links != pin.identitet.rj_links
        || recept.base.graph_stamp != pin.identitet.graph_stamp
        || recept.base.graph_content_hash != pin.identitet.graph_content_hash
    {
        eprintln!("STOPP: recept.base != manifestets pin");
        return 2;
    }
    if (doc.cells.len() as u32, doc.links.len() as u32, doc.rj_links)
        != (pin.identitet.cells, pin.identitet.links, pin.identitet.rj_links)
        || base_stamp.to_string() != pin.identitet.graph_stamp
        || base_hash != pin.identitet.graph_content_hash
    {
        eprintln!(
            "STOPP: basdumpen ({}/{}/{}/{base_stamp}/{base_hash}) != manifestets pin \
             ({}/{}/{}/{}/{})",
            doc.cells.len(),
            doc.links.len(),
            doc.rj_links,
            pin.identitet.cells,
            pin.identitet.links,
            pin.identitet.rj_links,
            pin.identitet.graph_stamp,
            pin.identitet.graph_content_hash
        );
        return 2;
    }

    // 4) Bygg komponatstegen: manifestets identiteter + receptets op-parametrar.
    let op_steg = &manifest.steg[1..];
    if op_steg.len() != recept.ops.len() {
        eprintln!(
            "STOPP: manifestet har {} op-steg, receptet {} opar",
            op_steg.len(),
            recept.ops.len()
        );
        return 2;
    }
    let mut steps: Vec<KomponatStep> = Vec::with_capacity(op_steg.len());
    for (i, (steg, op)) in op_steg.iter().zip(recept.ops.iter()).enumerate() {
        let expect_before = ident_of(&manifest.steg[i].identitet);
        let expect_after = ident_of(&steg.identitet);
        let komp_op = match op {
            ReceptOp::PlanLink {
                from,
                takeoff,
                tgt,
                v_req,
                gain,
            } => {
                if steg.op != "plan_link" {
                    eprintln!(
                        "STOPP: steg {} ({}) manifest-op {:?} != receptets plan_link",
                        i + 1,
                        steg.name,
                        steg.op
                    );
                    return 2;
                }
                KomponatOp::PlanLink {
                    from: *from,
                    takeoff: *takeoff,
                    tgt: *tgt,
                    v_req: *v_req,
                    gain: *gain,
                }
            }
            ReceptOp::ShelfPatch { name } => {
                if steg.op != "shelf_patch" {
                    eprintln!(
                        "STOPP: steg {} ({}) manifest-op {:?} != receptets shelf_patch",
                        i + 1,
                        steg.name,
                        steg.op
                    );
                    return 2;
                }
                if steg.name != *name {
                    eprintln!(
                        "STOPP: steg {} manifest-namn {:?} != receptets {:?}",
                        i + 1,
                        steg.name,
                        name
                    );
                    return 2;
                }
                KomponatOp::Recipe { name: name.clone() }
            }
        };
        steps.push(KomponatStep {
            name: steg.name.clone(),
            op: komp_op,
            expect_before,
            expect_after,
        });
    }

    // 5) Kör motorns EGNA apply-kod offline (bsp=None).
    let expect_final = ident_of(&manifest.slut);
    let applied = rtx::komponat_apply(
        "dm3",
        None,
        800.0,  // sv_gravity (påverkar ej nivå-2 — cost/airtime hashas inte)
        12.0,   // rtx_jump_curl_gain-fallback (op har explicit gain=5.5)
        &base_graph,
        "v296-ram",
        &pin_ident,
        &steps,
        &expect_final,
    );
    let (published, resp) = match applied {
        Ok(v) => v,
        Err(resp) => {
            eprintln!("STOPP: komponat_apply vägrade: {}", resp.audit.trim());
            if let Some(r) = &resp.reason {
                eprintln!("STOPP: skäl: {r}");
            }
            return 2;
        }
    };
    eprintln!("{}", resp.audit.trim());

    // 6) Oberoende verifiering mot körkvittots slut_observed (inte bara manifestet).
    let cells = published.cells.len() as u32;
    let links = published.links.len() as u32;
    let rj = published.summary().rocket_jump;
    let stamp = rtx::graph_stamp("dm3", cells, links, rj);
    let hash = graph_content_hash(&published);
    if (cells, links, rj) != (TARGET_CELLS, TARGET_LINKS, 0)
        || stamp.to_string() != TARGET_STAMP
        || hash != TARGET_HASH
    {
        eprintln!(
            "STOPP: härledd fork-graf {cells}/{links}/{rj} stamp={stamp} hash={hash} \
             != körkvittots slut_observed {TARGET_CELLS}/{TARGET_LINKS}/0 {TARGET_STAMP} {TARGET_HASH}"
        );
        return 2;
    }

    // 7) Serialisera fork-dumpen (qw-nav-graph/1) + register (P1).
    let dump_json = serialize_dump(&published, &doc.map, doc.grid);
    let dump_bytes = serde_json::to_vec(&dump_json).expect("serialisering");
    if let Err(e) = std::fs::create_dir_all(Path::new(out_dump).parent().unwrap_or(Path::new("."))) {
        eprintln!("STOPP: kan inte skapa ut-katalog: {e}");
        return 2;
    }
    if let Err(e) = std::fs::write(out_dump, &dump_bytes) {
        eprintln!("STOPP: kan inte skriva fork-dump {out_dump}: {e}");
        return 2;
    }
    let dump_sha = sha256(&dump_bytes);
    let register = json!({
        "schema": "ben3d-dumpregister/1",
        "skrivet_utc": "2026-08-17T17:43:16Z",
        "dumps": [{
            "id": "dm3-fork-v296-ram",
            "map": "dm3",
            "path": out_dump,
            "byte_sha256": dump_sha,
            "cells": cells,
            "links": links,
            "rj_links": rj,
            "graph_stamp": stamp.to_string(),
            "graph_content_hash": hash,
            "harledning": {
                "basdump": {"path": base_dump, "sha256": base_dump_sha},
                "oplista_recept": {"path": recept_path, "sha256": recept_sha},
                "manifest": {"path": manifest_path, "sha256": manifest_sha},
                "apply": "rtx::komponat_apply (plant_speed_jump_into + apply_in_chain), bsp=None",
                "verifierad_mot": "rokdeploy-kvitto-20260817.json slut_observed"
            }
        }]
    });
    let register_bytes = serde_json::to_vec_pretty(&register).expect("register");
    if let Err(e) = std::fs::write(out_register, &register_bytes) {
        eprintln!("STOPP: kan inte skriva register {out_register}: {e}");
        return 2;
    }

    println!("fork-dump härledd och förseglad:");
    println!("  dump       {out_dump} ({} bytes, sha256 {dump_sha})", dump_bytes.len());
    println!("  register   {out_register}");
    println!("  identitet  {cells}/{links}/{rj} stamp={stamp} hash={hash}");
    println!("  matchar körkvittots slut_observed (nivå-1 + nivå-2)");
    0
}

/// Serialisera en NavGraph som `qw-nav-graph/1`, i motor-id-ordning (länkindex = motor-id).
pub(crate) fn serialize_dump(graph: &NavGraph, map: &str, grid: f32) -> serde_json::Value {
    let cells: Vec<[f32; 3]> = graph
        .cells
        .iter()
        .map(|c| [c.origin.x, c.origin.y, c.origin.z])
        .collect();
    let cell_ids: Vec<u32> = (0..graph.cells.len() as u32).collect();
    let in_adj: HashSet<u32> = graph.adjacency.iter().flatten().copied().collect();
    let links: Vec<serde_json::Value> = graph
        .links
        .iter()
        .enumerate()
        .map(|(i, l)| {
            let t = if in_adj.contains(&(i as u32)) { 1 } else { 0 };
            json!({"from": l.from, "to_cell": l.to, "kind": kind_token(l.kind), "T": t})
        })
        .collect();
    let link_ids: Vec<u32> = (0..graph.links.len() as u32).collect();
    json!({
        "schema": "qw-nav-graph/1",
        "map": map,
        "grid": grid,
        "cells": cells,
        "links": links,
        "cell_ids": cell_ids,
        "link_ids": link_ids,
        "provenance": "ben3d etapp 3: offline härledd ur dm3-base-full-graph.json + komponat-v296-ram op-lista via motorns komponat_apply; verifierad mot körkvittots slut_observed 5983/48216 cd800200…",
        "graph_content_hash": graph_content_hash(graph),
    })
}


#[cfg(test)]
mod tests {
    use super::*;
    use rtx_nav::navmesh::{Link, LinkKind, SpeedJumpTraversal};

    #[test]
    fn fork_serialize_roundtrips_through_restore() {
        let origins = vec![
            glam::Vec3::new(0.0, 0.0, 0.0),
            glam::Vec3::new(32.0, 0.0, 0.0),
        ];
        let links = vec![Link {
            from: 0,
            to: 1,
            kind: LinkKind::Walk,
            cost: 1.0,
        }];
        let mut g = NavGraph::from_topology(&origins, &links);
        // en T=0-länk (pruned) — måste överleva dump→restore med T=0
        g.insert_pruned_link(Link {
            from: 1,
            to: 0,
            kind: LinkKind::Drop,
            cost: 1.0,
        });
        let v = serialize_dump(&g, "dm3", 32.0);
        let bytes = serde_json::to_vec(&v).unwrap();
        let doc: Dump = serde_json::from_slice(&bytes).unwrap();
        let (g2, _stamp, hash) = restore::restore(&doc).unwrap();
        assert_eq!(g2.cells.len(), 2);
        assert_eq!(g2.links.len(), 2);
        assert_eq!(hash, doc.graph_content_hash);
        let in_adj: HashSet<u32> = g2.adjacency.iter().flatten().copied().collect();
        assert!(in_adj.contains(&0), "walk-länken ska vara T=1");
        assert!(!in_adj.contains(&1), "drop-länken ska vara T=0 (pruned)");
    }

    #[test]
    fn fork_komponat_apply_wiring_och_stopp() {
        let origins = vec![
            glam::Vec3::new(0.0, 0.0, 0.0),
            glam::Vec3::new(64.0, 0.0, 0.0),
        ];
        let links = vec![Link {
            from: 0,
            to: 1,
            kind: LinkKind::Walk,
            cost: 1.0,
        }];
        let g = NavGraph::from_topology(&origins, &links);
        let base = GraphIdent {
            cells: g.cells.len() as u32,
            links: g.links.len() as u32,
            rj_links: 0,
            graph_stamp: rtx::graph_stamp("dm3", g.cells.len() as u32, g.links.len() as u32, 0)
                .to_string(),
            graph_content_hash: graph_content_hash(&g),
        };

        let mut g2 = g.clone();
        g2.plant_speed_jump(
            0,
            1,
            1.0,
            SpeedJumpTraversal {
                takeoff: glam::Vec3::new(32.0, 0.0, 0.0),
                v_req: 320.0,
                ..Default::default()
            },
        );
        let after = GraphIdent {
            cells: g2.cells.len() as u32,
            links: g2.links.len() as u32,
            rj_links: 0,
            graph_stamp: rtx::graph_stamp("dm3", g2.cells.len() as u32, g2.links.len() as u32, 0)
                .to_string(),
            graph_content_hash: graph_content_hash(&g2),
        };

        let step = KomponatStep {
            name: "p".into(),
            op: KomponatOp::PlanLink {
                from: [16.0, 0.0, 0.0],
                takeoff: [32.0, 0.0, 0.0],
                tgt: [48.0, 0.0, 0.0],
                v_req: 320.0,
                gain: Some(5.5),
            },
            expect_before: base.clone(),
            expect_after: after.clone(),
        };

        let ok = rtx::komponat_apply("dm3", None, 800.0, 12.0, &g, "t", &base, &[step.clone()], &after);
        let (published, _resp) = ok.expect("syntetiskt komponat ska tillämpas");
        assert_eq!(published.links.len() as u32, after.links);

        // STOPP-vägen: fel baspin får inte röra något.
        let bad_base = GraphIdent {
            cells: 999,
            ..base.clone()
        };
        let refused = rtx::komponat_apply("dm3", None, 800.0, 12.0, &g, "t", &bad_base, &[step], &after);
        assert!(refused.is_err(), "fel pin ska vägra");
    }
}
