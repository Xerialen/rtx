//! ben3d extraktor — etapp 2b: dump-restore + stamp (G1b).

use rtx::{graph_content_hash, graph_stamp};
use rtx_nav::navmesh::{Link, LinkKind, NavGraph};
use serde::Deserialize;
use std::path::Path;

#[derive(Deserialize)]
pub(crate) struct DumpLink {
    pub(crate) from: u32,
    #[serde(rename = "to_cell", alias = "to")]
    pub(crate) to_cell: u32,
    pub(crate) kind: String,
    #[serde(rename = "T", default = "default_t")]
    pub(crate) t: u8,
}

#[derive(Deserialize)]
pub(crate) struct Dump {
    pub(crate) map: String,
    #[serde(default = "default_grid")]
    pub(crate) grid: f32,
    pub(crate) cells: Vec<[f32; 3]>,
    #[serde(rename = "cell_ids")]
    #[allow(dead_code)] // cell-id = index för dm3; läses ej, men fältet hör till schemat
    pub(crate) cell_ids: Vec<u32>,
    pub(crate) links: Vec<DumpLink>,
    #[serde(rename = "link_ids")]
    pub(crate) link_ids: Vec<u32>,
    #[serde(rename = "graph_content_hash")]
    pub(crate) graph_content_hash: String,
    #[serde(default = "default_rj")]
    pub(crate) rj_links: u32,
}

fn default_t() -> u8 {
    1
}
fn default_grid() -> f32 {
    32.0
}
fn default_rj() -> u32 {
    0
}

fn parse_kind(s: &str) -> Option<LinkKind> {
    Some(match s {
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
    })
}

/// Read and deserialize a `qw-nav-graph/1` dump. Shared by the restore verb and the
/// fork-derivation (etapp 3).
pub(crate) fn read_dump(dump_path: &str) -> Result<Dump, String> {
    let bytes = std::fs::read(Path::new(dump_path))
        .map_err(|e| format!("kan inte läsa dump {dump_path}: {e}"))?;
    serde_json::from_slice(&bytes).map_err(|e| format!("dump är inte JSON: {e}"))
}

/// Motor kind string, the reverse of [`parse_kind`] — matches `nav_patch::kind_token`.
pub(crate) fn kind_token(kind: LinkKind) -> &'static str {
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

/// Restore a NavGraph from a dump, preserving motor link-ids and the T flag.
///
/// `link_ids[i]` is the motor's link-id for `links[i]` (the dump lists links in
/// cell order). T=1 links go into adjacency; T=0 links stay pruned (present in
/// `links`, absent from `adjacency`) — exactly the motor's inventory semantics.
pub(crate) fn restore(doc: &Dump) -> Result<(NavGraph, u64, String), String> {
    let origins: Vec<glam::Vec3> = doc
        .cells
        .iter()
        .map(|c| glam::Vec3::new(c[0], c[1], c[2]))
        .collect();

    // Motor-id ordning: links[link_ids[i]] = dump.links[i].
    if doc.link_ids.len() != doc.links.len() {
        return Err(format!(
            "link_ids ({}) != links ({})",
            doc.link_ids.len(),
            doc.links.len()
        ));
    }
    let mut motor_links: Vec<Option<(Link, bool)>> = vec![None; doc.links.len()];
    for (i, l) in doc.links.iter().enumerate() {
        let lid = doc.link_ids[i] as usize;
        if lid >= motor_links.len() {
            return Err(format!("link_ids[{i}] = {lid} utanför range"));
        }
        let kind = parse_kind(&l.kind).ok_or_else(|| format!("okänd kind {}", l.kind))?;
        let link = Link {
            from: l.from,
            to: l.to_cell,
            kind,
            cost: 1.0, // kostnad läses inte av stampen; banded_step räknar sin egen
        };
        motor_links[lid] = Some((link, l.t != 0));
    }
    let mut links: Vec<Link> = Vec::with_capacity(motor_links.len());
    let mut t1: Vec<usize> = Vec::new();
    for (i, slot) in motor_links.iter().enumerate() {
        let (link, traversable) = slot.as_ref().ok_or_else(|| format!("hål i link_ids vid {i}"))?;
        links.push(*link);
        if *traversable {
            t1.push(i);
        }
    }

    let mut graph = NavGraph::from_topology(&origins, &links);
    // from_topology lägger ALLA länkar i adjacensen; återställ T-flaggan:
    // bara T=1-länkar ska vara traverserbara (inventory-semantiken).
    graph.adjacency = vec![Vec::new(); origins.len()];
    for i in t1 {
        graph.adjacency[links[i].from as usize].push(i as u32);
    }

    let stamp = graph_stamp(&doc.map, origins.len() as u32, links.len() as u32, doc.rj_links);
    let hash = graph_content_hash(&graph);
    Ok((graph, stamp, hash))
}

pub fn run(dump_path: &str) -> i32 {
    let doc: Dump = match read_dump(dump_path) {
        Ok(d) => d,
        Err(e) => {
            eprintln!("STOPP: {e}");
            return 2;
        }
    };
    let (_, stamp, hash) = match restore(&doc) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("STOPP: restore misslyckades: {e}");
            return 2;
        }
    };
    println!("cells {}", doc.cells.len());
    println!("links {}", doc.links.len());
    println!("nivå-1 {}", stamp);
    println!("nivå-2 {}", hash);
    if hash != doc.graph_content_hash {
        eprintln!(
            "STOPP: nivå-2 {hash} != dumpens graph_content_hash {}",
            doc.graph_content_hash
        );
        return 2;
    }
    println!("stamp-verifierad: nivå-1 + nivå-2 matchar dumpen");
    0
}
