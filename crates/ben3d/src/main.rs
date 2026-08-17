//! ben3d extraktor — etapp 2a: h-index (P3).
//!
//! Universum = cNNN/<ben>_meta.json-medlemmarna i de två förseglade
//! datasetmanifesten. Identitet = (manifest_sha256, arm, cycle_id, ben).
//! Nämnare exkluderar ogiltig_tic/kasserad. H iff klassa_utfall@r6 ger
//! fall | fastnad | fall_plus_fastnad | fall_efter_framme; fall_plus_fastnad
//! räknas en gång. Saknad fil / SHA-miss / dublett / okänt utfall STOPPAR.
//!
//! Ingen socket, ingen Control, ingen ~/lab-skrivning — läser bara de sökvägar
//! den får. Härledd av motorns klassare (klassa_utfall@r6), inte en kopia.

use serde::Serialize;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

const SCHEMA: &str = "ben3d-h-index/1";

#[derive(Serialize)]
struct HIndex {
    schema: &'static str,
    dataset_manifests: Vec<ManifestRef>,
    #[serde(rename = "n_h")]
    n_h: usize,
    konton: BTreeMap<String, usize>,
    ben: Vec<BenRow>,
    index_sha256: String,
}

#[derive(Serialize)]
struct ManifestRef {
    id: String,
    sha256: String,
}

#[derive(Serialize, Clone)]
struct BenRow {
    dataset: String,
    arm: String,
    cycle_id: String,
    ben: String,
    utfall: String,
    meta_sha256: String,
}

mod fork;
mod restore;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() == 3 && args[1] == "restore" {
        std::process::exit(restore::run(&args[2]));
    }
    if args.len() == 7 && args[1] == "fork" {
        std::process::exit(fork::run(&args[2], &args[3], &args[4], &args[5], &args[6]));
    }
    if args.len() != 4 || args[1] != "h-index" {
        eprintln!(
            "usage: ben3d h-index <t1h-manifest> <t20m-manifest> | ben3d restore <dump> | \
             ben3d fork <basdump> <recept> <manifest> <ut-dump> <ut-register>"
        );
        std::process::exit(2);
    }
    let t1h = &args[2];
    let t20m = &args[3];
    let doc = h_index(t1h, t20m);
    println!("{}", serde_json::to_string_pretty(&doc).unwrap());
}

/// Arm-mappning ur den relativa sökvägens första komponent (P3-identiteten).
fn arm_of(rel: &str) -> &'static str {
    if rel.starts_with("t1h-d1-on/") || rel.starts_with("t20m-d1-on/") {
        "fork"
    } else if rel.starts_with("t1h-main-ref/") || rel.starts_with("t20m-main-ref/") {
        "main"
    } else {
        "unknown"
    }
}

/// cykel-id ur `cNNN/`-segmentet, ben-typ ur filnamnet `<ben>_meta.json`.
fn cycle_and_ben(rel: &str) -> (String, String) {
    let p = Path::new(rel);
    let cycle = p
        .parent()
        .and_then(|d| d.file_name())
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_default();
    let ben = p
        .file_name()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_default()
        .trim_end_matches("_meta.json")
        .to_string();
    (cycle, ben)
}

fn h_index(t1h_manifest: &str, t20m_manifest: &str) -> HIndex {
    let mut manifests = Vec::new();
    let mut ben_rows: Vec<BenRow> = Vec::new();
    let mut seen: BTreeSet<String> = BTreeSet::new();

    for (id, path) in [
        ("t1h-dataset-manifest-20260817T1536Z.sha256", t1h_manifest),
        ("t20m-dataset-manifest-20260817T1339Z.sha256", t20m_manifest),
    ] {
        let manifest_bytes = std::fs::read(path).unwrap_or_else(|e| {
            eprintln!("STOPP: kan inte läsa manifest {path}: {e}");
            std::process::exit(2);
        });
        let manifest_sha = hex(&sha256(&manifest_bytes));
        let display = Path::new(path).file_name().map(|s| s.to_string_lossy().to_string()).unwrap_or_else(|| id.to_string());
        let manifest_dir = Path::new(path).parent().unwrap_or(Path::new(".")).to_path_buf();
        let manifest_text = String::from_utf8_lossy(&manifest_bytes);
        manifests.push(ManifestRef { id: id.to_string(), sha256: manifest_sha.clone() });

        for line in manifest_text.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            let mut it = line.splitn(2, char::is_whitespace);
            let (sha, rel) = (it.next().unwrap_or(""), it.next().unwrap_or("").trim());
            if sha.is_empty() || rel.is_empty() {
                continue;
            }
            let rel = rel.trim_start_matches("./");
            // Endast meta-medlemmar bär H-klassningen.
            if !rel.ends_with("_meta.json") {
                continue;
            }
            let file = manifest_dir.join(rel);
            let bytes = std::fs::read(&file).unwrap_or_else(|e| {
                eprintln!("STOPP: medlem {rel} saknas ({e})");
                std::process::exit(2);
            });
            let got = hex(&sha256(&bytes));
            if got != sha.to_lowercase() {
                eprintln!("STOPP: SHA-miss {rel}: manifest {sha}, fil {got}");
                std::process::exit(2);
            }
            let meta: serde_json::Value = serde_json::from_slice(&bytes).unwrap_or_else(|e| {
                eprintln!("STOPP: {rel} är inte JSON ({e})");
                std::process::exit(2);
            });
            let utfall = meta
                .get("utfall")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            // klassa_utfall@r6 — den frysta klassarens egen regel, ordagrant.
            let is_h = matches!(
                utfall.as_str(),
                "fall" | "fastnad" | "fall_plus_fastnad" | "fall_efter_framme"
            );
            // Nämnaren exkluderar ogiltig_tic/kasserad; okänt utfall stoppar.
            if !matches!(
                utfall.as_str(),
                "framme" | "fall" | "fastnad" | "fall_plus_fastnad"
                    | "fall_efter_framme" | "ogiltig_tic" | "kasserad"
            ) {
                eprintln!("STOPP: okänt utfall {utfall:?} i {rel}");
                std::process::exit(2);
            }
            if !is_h {
                continue;
            }
            let arm = arm_of(rel);
            let (cycle, ben) = cycle_and_ben(rel);
            let ident = format!("{}:{}:{}:{}", manifest_sha, arm, cycle, ben);
            if !seen.insert(ident.clone()) {
                eprintln!("STOPP: dublettidentitet {ident}");
                std::process::exit(2);
            }
            ben_rows.push(BenRow {
                dataset: display.clone(),
                arm: arm.to_string(),
                cycle_id: cycle,
                ben,
                utfall,
                meta_sha256: got,
            });
        }
    }

    ben_rows.sort_by(|a, b| {
        (&a.dataset, &a.arm, &a.cycle_id, &a.ben).cmp(&(&b.dataset, &b.arm, &b.cycle_id, &b.ben))
    });

    let mut konton: BTreeMap<String, usize> = BTreeMap::new();
    for r in &ben_rows {
        *konton.entry(format!("{}:{}", r.dataset, r.arm)).or_insert(0) += 1;
    }

    // index_sha256 över en kanonisk (sorterad-nycklar) serialisering av benraderna.
    let rows_json = serde_json::to_string(&ben_rows).unwrap();
    let index_sha = hex(&sha256(rows_json.as_bytes()));

    HIndex {
        schema: SCHEMA,
        dataset_manifests: manifests,
        n_h: ben_rows.len(),
        konton,
        ben: ben_rows,
        index_sha256: index_sha,
    }
}

fn sha256(bytes: &[u8]) -> Vec<u8> {
    Sha256::digest(bytes).to_vec()
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
