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
        }
        assert_eq!(sedda.len(), alla.len());
    }
}
