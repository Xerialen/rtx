// SPDX-License-Identifier: AGPL-3.0-or-later

//! Grafidentitet: nivå 1 (`graph_stamp`) och nivå 2 (`graph_content_hash`).
//!
//! **Portad oförändrad** ur den lokala huvudgrenens `nav_patch.rs` enligt
//! `WORK_LOGS/facit-receptautostart-v2.md` §2 punkt 2. Bara de tre funktionerna
//! (plus deras hjälpare `kind_token`) följer med — **inte** `PATCHES`-tabellen,
//! eftersom den bär west-shelf och dess pin, och design v2 §11 förbjuder att den
//! semantiken importeras hit.
//!
//! Att portningen är trogen visas mot en **namngiven fixturgraf** (facit §7 test 1),
//! inte mot målträdets egen graf: överensstämmelse certifierar att flytten är
//! trogen, inte att förväntan är oberoende framtagen (facit §8.1).
//!
//! Kostnads- och avfartsparametrar ingår **inte** i någon av hasharna — de bor i
//! sidotabeller. Två grafer med samma nivå-2 kan alltså prissättas olika.

use rtx_nav::navmesh::{LinkKind, NavGraph};
use sha2::{Digest, Sha256};

/// FNV-1a-64 over `map_utf8 ++ LE32(cells) ++ LE32(links) ++ LE32(rj_links)`.
pub fn graph_stamp(map: &str, cells: u32, links: u32, rj_links: u32) -> u64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in map
        .as_bytes()
        .iter()
        .copied()
        .chain(cells.to_le_bytes())
        .chain(links.to_le_bytes())
        .chain(rj_links.to_le_bytes())
    {
        h ^= b as u64;
        h = h.wrapping_mul(0x0000_0100_0000_01b3);
    }
    h
}

/// Nivå 1 för en färdig graf.
pub fn stamp_of(map: &str, graph: &NavGraph) -> u64 {
    graph_stamp(
        map,
        graph.cells.len() as u32,
        graph.links.len() as u32,
        graph.summary().rocket_jump,
    )
}

pub fn kind_token(kind: LinkKind) -> &'static str {
    match kind {
        LinkKind::Walk => "walk",
        LinkKind::Step => "step",
        LinkKind::Drop => "drop",
        LinkKind::JumpGap => "jump",
        LinkKind::DoubleJump => "doublejump",
        LinkKind::SpeedJump => "speedjump",
        LinkKind::Plat => "plat",
        LinkKind::Teleport => "teleport",
        LinkKind::Hook => "hook",
        LinkKind::RocketJump => "rocketjump",
        LinkKind::Swim => "swim",
    }
}

/// The inverse of [`kind_token`]. Lives next to it so the two cannot drift apart.
///
/// Unknown tokens are `None`, never a guess: a link kind that does not round-trip is a caller
/// talking about a graph this engine does not have.
pub fn kind_from_token(token: &str) -> Option<LinkKind> {
    let k = match token {
        "walk" => LinkKind::Walk,
        "step" => LinkKind::Step,
        "drop" => LinkKind::Drop,
        "jump" => LinkKind::JumpGap,
        "doublejump" => LinkKind::DoubleJump,
        "speedjump" => LinkKind::SpeedJump,
        "plat" => LinkKind::Plat,
        "teleport" => LinkKind::Teleport,
        "hook" => LinkKind::Hook,
        "rocketjump" => LinkKind::RocketJump,
        "swim" => LinkKind::Swim,
        _ => return None,
    };
    debug_assert_eq!(kind_token(k), token);
    Some(k)
}

/// Canonical inventory bytes (kontrakt §8.2, no per-kind params — matches the dm3 golden dump).
fn canonical_inventory(graph: &NavGraph) -> String {
    let mut lines: Vec<String> = graph
        // CellId == Vec index in rtx (kontrakt §8.2 sorts on cell_id). dm3 holds that
        // identity; a future non-index cell table would need an explicit id field.
        .cells
        .iter()
        .enumerate()
        .map(|(id, c)| {
            format!(
                "C\t{id}\t{}\t{}\t{}",
                c.origin.x as i32, c.origin.y as i32, c.origin.z as i32
            )
        })
        .collect();
    let in_adj: std::collections::HashSet<u32> = graph.adjacency.iter().flatten().copied().collect();
    let mut lrecs: Vec<(u32, u32, &'static str, u8)> = graph
        .links
        .iter()
        .enumerate()
        .map(|(i, l)| {
            let t = if in_adj.contains(&(i as u32)) { 1 } else { 0 };
            (l.from, l.to, kind_token(l.kind), t)
        })
        .collect();
    lrecs.sort_unstable();
    for (src, dst, kind, t) in lrecs {
        lines.push(format!("L\t{src}\t{dst}\t{kind}\t{t}"));
    }
    lines.join("\n")
}

/// Nivå-2 SHA-256 hex of [`canonical_inventory`].
pub fn graph_content_hash(graph: &NavGraph) -> String {
    let mut h = Sha256::new();
    h.update(canonical_inventory(graph).as_bytes());
    format!("{:x}", h.finalize())
}

/// Grafens niva-2 som `Status` ska bara den, oberoende av receptutfallet.
///
/// Star for sig sa att kravet gar att prova: identiteten ska kunna lasas ur en
/// levande rigg **aven nar `rtx_recept_dir` ar tom**, annars kan facitets
/// §8.2-kontroll inte matas utan att forst aktivera ett recept. Tom strang nar
/// ingen graf ar byggd — aldrig ett paihittat varde.
pub fn niva2_for_status(graph: Option<&NavGraph>) -> String {
    graph.map(graph_content_hash).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Nivå-1 är ren aritmetik och behöver ingen graf: samma indata ger samma stämpel,
    /// och varje fält bidrar.
    #[test]
    fn stampen_beror_pa_varje_falt() {
        let a = graph_stamp("dm3", 5977, 48207, 0);
        assert_eq!(a, graph_stamp("dm3", 5977, 48207, 0), "deterministisk");
        assert_ne!(a, graph_stamp("dm6", 5977, 48207, 0), "kartnamnet bidrar");
        assert_ne!(a, graph_stamp("dm3", 5978, 48207, 0), "cellantalet bidrar");
        assert_ne!(a, graph_stamp("dm3", 5977, 48208, 0), "länkantalet bidrar");
        assert_ne!(a, graph_stamp("dm3", 5977, 48207, 1), "rj-antalet bidrar");
    }


    // ---- facit §7 test 1: portningens trohet mot en NAMNGIVEN fixturgraf ----

    /// vF5:s basgraf, 5981 celler / 48217 länkar, byggd av lokal main `4f0b910`.
    /// Källa: `reference/recept/README.md`, "Grafidentitet — läs detta innan du
    /// applicerar". **Portningsfixtur, inte målriggens graf** — målträdet bygger
    /// 5977 / 48207 (facit §8.2).
    const FIXTUR_NIVA2: &str =
        "4c099331899d7aaecc8d23ccaa00ab6ca2ac192e135aecbb420853886c9643e5";

    fn kind_ur_token(t: &str) -> LinkKind {
        match t {
            "walk" => LinkKind::Walk,
            "step" => LinkKind::Step,
            "drop" => LinkKind::Drop,
            "jump" => LinkKind::JumpGap,
            "doublejump" => LinkKind::DoubleJump,
            "speedjump" => LinkKind::SpeedJump,
            "plat" => LinkKind::Plat,
            "teleport" => LinkKind::Teleport,
            "hook" => LinkKind::Hook,
            "rocketjump" => LinkKind::RocketJump,
            "swim" => LinkKind::Swim,
            annat => panic!("okänd länksort i fixturen: {annat}"),
        }
    }

    /// Bygg fixturen ur `tests/fixturer/vf5-bas.tsv`.
    ///
    /// Filen ligger i **länkarnas arrayordning**, inte kanoniskt sorterad — annars
    /// vore den själv den inventering testet ska räkna fram, och testet vore
    /// cirkulärt.
    ///
    /// T=0-länkarna läggs till EFTER `test_graph`, direkt på det publika
    /// `links`-fältet. De hamnar då utanför adjacensen och läses som prunade,
    /// vilket är exakt vad fixturen kräver.
    fn vf5_bas() -> rtx_nav::navmesh::NavGraph {
        use rtx_nav::navmesh::{Cell, Link, NavGraph};
        let text = std::fs::read_to_string(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixturer/vf5-bas.tsv"
        ))
        .expect("fixturfilen saknas");

        let mut celler = Vec::new();
        let mut i_adjacens = Vec::new();
        let mut prunade = Vec::new();
        for rad in text.lines() {
            let f: Vec<&str> = rad.split('\t').collect();
            match f[0] {
                "C" => celler.push(Cell {
                    origin: glam::Vec3::new(
                        f[1].parse().unwrap(),
                        f[2].parse().unwrap(),
                        f[3].parse().unwrap(),
                    ),
                    gx: 0,
                    gy: 0,
                }),
                "L" => {
                    let l = Link {
                        from: f[1].parse().unwrap(),
                        to: f[2].parse().unwrap(),
                        kind: kind_ur_token(f[3]),
                        cost: 0.0,
                    };
                    if f[4] == "1" {
                        i_adjacens.push(l);
                    } else {
                        prunade.push(l);
                    }
                }
                annat => panic!("okänd rad i fixturen: {annat}"),
            }
        }
        let mut g = NavGraph::test_graph(celler, i_adjacens);
        for l in prunade {
            g.links.push(l);
        }
        g
    }

    /// Facit §7 test 1. Faller den, faller allt: den är den enda kontroll som
    /// binder den portade hashen mot förlagans egen utdata.
    #[test]
    fn portningen_ger_fixturens_niva2() {
        let g = vf5_bas();
        assert_eq!(g.cells.len(), 5981, "fixturens cellantal");
        assert_eq!(g.links.len(), 48217, "fixturens länkantal inkl. prunade");
        let prunade = g.links.len() - g.adjacency.iter().map(Vec::len).sum::<usize>();
        assert_eq!(prunade, 15, "fixturen ska bära 15 prunade länkar (T=0)");
        assert_eq!(graph_content_hash(&g), FIXTUR_NIVA2);
    }

    /// Kravet att `Status` barer niva-2 **oberoende av receptutfallet**: funktionen
    /// tar bara grafen, sa den kan inte bero pa nagot recept. Utan graf: tom strang,
    /// aldrig ett paihittat varde.
    #[test]
    fn niva2_for_status_beror_bara_pa_grafen() {
        let g = vf5_bas();
        assert_eq!(niva2_for_status(Some(&g)), FIXTUR_NIVA2);
        assert_eq!(niva2_for_status(None), "");
    }

    /// nk1 (facit §10): störningen sitter i GRAFEN, inte i konstanten.
    ///
    /// En portning som returnerar ett konstant värde skulle passera testet ovan
    /// och falla korrekt i en ren konstantkontroll — båda gröna, funktionen
    /// trasig. Ändras en länk måste hashen ändras.
    #[test]
    fn nk1_andrad_lank_andrar_hashen() {
        let mut g = vf5_bas();
        let fore = graph_content_hash(&g);
        g.links[0].to += 1;
        assert_ne!(graph_content_hash(&g), fore, "hashen följer inte innehållet");
    }

    /// nk1b: en prunad länk som befordras till adjacensen ändrar T-fältet och
    /// därmed hashen. Utan den vore T-kolumnen oprövad.
    #[test]
    fn nk1b_prunad_lank_i_adjacensen_andrar_hashen() {
        let mut g = vf5_bas();
        let fore = graph_content_hash(&g);
        let sist = (g.links.len() - 1) as u32;
        let from = g.links[sist as usize].from as usize;
        g.adjacency[from].push(sist);
        assert_ne!(graph_content_hash(&g), fore, "T-fältet påverkar inte hashen");
    }

    /// Tokenbordet är hashens alfabet: två sorter får aldrig dela token, annars
    /// blir två olika grafer identiska i nivå 2.
    #[test]
    fn varje_lanksort_har_sin_egen_token() {
        let alla = [
            LinkKind::Walk,
            LinkKind::Step,
            LinkKind::Drop,
            LinkKind::JumpGap,
            LinkKind::DoubleJump,
            LinkKind::SpeedJump,
            LinkKind::Plat,
            LinkKind::Teleport,
            LinkKind::Hook,
            LinkKind::RocketJump,
            LinkKind::Swim,
        ];
        let mut sedda = std::collections::HashSet::new();
        for k in alla {
            assert!(sedda.insert(kind_token(k)), "dubblerad token: {}", kind_token(k));
            assert_eq!(kind_from_token(kind_token(k)), Some(k), "roundtrip {}", kind_token(k));
        }
        assert_eq!(sedda.len(), alla.len());
        assert_eq!(kind_from_token("krypa"), None);
        assert_eq!(kind_from_token(""), None);
    }
}
