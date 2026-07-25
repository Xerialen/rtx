// SPDX-License-Identifier: AGPL-3.0-or-later

//! Repo-owned, version-pinned post-build navigation patches.
//!
//! These are deliberately narrower than a second nav generator. A patch may only remove links by
//! resolved endpoint geometry and add fully-described traversal profiles. Both the BSP and the
//! canonical source graph are SHA-bound, every selector has an exact expected multiplicity, and the
//! resulting graph is SHA-bound again. Any discrepancy is an error; callers must not install the
//! graph.

use std::collections::BTreeSet;
use std::fmt;

use glam::{Vec3, Vec3Swizzles};
use serde::Deserialize;
use sha2::{Digest, Sha256};

use super::{GroundTurnCurl, HookParams, LinkKind, NavGraph, RocketJumpParams, SpeedJumpParams, SpeedJumpTraversal};

const DM3_MEGA_PATCH: &str = include_str!("../../data/navpatches/dm3-mega-v1.json");
const DM3_RA_PATCH: &str = include_str!("../../data/navpatches/dm3-ra-v1.json");
const PATCH_SCHEMA: &str = "rtx-nav-postbuild-patch/2";
const ROUTE_PATCH_SCHEMA: &str = "rtx-nav-route-patch/1";
const GRAPH_HASH_SCHEMA: &[u8] = b"rtx-nav-graph-sha256/1\0";

/// Successful application provenance, retained on the per-map nav state and exposed to harnesses.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BuiltinPatchReport {
    pub id: String,
    pub manifest_sha256: String,
    pub source_graph_sha256: String,
    pub patched_graph_sha256: String,
    pub removed_links: u32,
    pub added_links: u32,
    pub total_links: u32,
    pub active_links: u32,
}

/// Exact nav-generator inputs whose unpatched graph a built-in patch is pinned to.
///
/// A different, legitimate server configuration makes the patch inapplicable rather than invalid.
/// The graph itself remains SHA-bound whenever these inputs match.
#[derive(Clone, Copy, Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct NavPatchSourceConfig {
    stock_movement: bool,
    hooks: Option<PinnedHookParams>,
    double_jump: bool,
    speed_jump: Option<PinnedSpeedJumpParams>,
    rocket_jump: Option<PinnedRocketJumpParams>,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct PinnedHookParams {
    gravity: f32,
    pull: f32,
    throw: f32,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct PinnedSpeedJumpParams {
    gravity: f32,
    accel: f32,
    maxspeed: f32,
    friction: f32,
    stopspeed: f32,
    curl: bool,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct PinnedRocketJumpParams {
    gravity: f32,
    rj_extra: f32,
    accel: f32,
    maxspeed: f32,
    friction: f32,
    stopspeed: f32,
    cost_scale: f32,
}

impl NavPatchSourceConfig {
    /// Snapshot the same inputs passed to [`super::build_navmesh`].
    pub fn from_build_inputs(
        stock_movement: bool,
        hooks: Option<HookParams>,
        double_jump: bool,
        speed_jump: Option<SpeedJumpParams>,
        rocket_jump: Option<RocketJumpParams>,
    ) -> Self {
        Self {
            stock_movement,
            hooks: hooks.map(|params| PinnedHookParams {
                gravity: params.gravity,
                pull: params.pull,
                throw: params.throw,
            }),
            double_jump,
            speed_jump: speed_jump.map(|params| PinnedSpeedJumpParams {
                gravity: params.gravity,
                accel: params.accel,
                maxspeed: params.maxspeed,
                friction: params.friction,
                stopspeed: params.stopspeed,
                curl: params.curl,
            }),
            rocket_jump: rocket_jump.map(|params| PinnedRocketJumpParams {
                gravity: params.gravity,
                rj_extra: params.rj_extra,
                accel: params.accel,
                maxspeed: params.maxspeed,
                friction: params.friction,
                stopspeed: params.stopspeed,
                cost_scale: params.cost_scale,
            }),
        }
    }

    fn mismatch(self, actual: Self) -> Option<String> {
        macro_rules! mismatch {
            ($field:ident) => {
                if self.$field != actual.$field {
                    return Some(format!(
                        "{} expected {:?}, got {:?}",
                        stringify!($field),
                        self.$field,
                        actual.$field
                    ));
                }
            };
        }
        mismatch!(stock_movement);
        mismatch!(hooks);
        mismatch!(double_jump);
        mismatch!(speed_jump);
        mismatch!(rocket_jump);
        None
    }
}

/// A built-in patch was deliberately not applied because this graph was built under another valid
/// movement configuration.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BuiltinPatchSkip {
    pub id: String,
    pub manifest_sha256: String,
    pub reason: String,
}

/// Decision made for one completed source graph.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum BuiltinPatchOutcome {
    NotApplicable,
    Skipped(BuiltinPatchSkip),
    Applied(BuiltinPatchReport),
}

/// A fail-closed built-in patch error. The game logs this and leaves the graph uninstalled.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NavPatchError(String);

impl NavPatchError {
    fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl fmt::Display for NavPatchError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for NavPatchError {}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PatchDocument {
    schema: String,
    id: String,
    map: String,
    bsp_sha256: String,
    source_build: NavPatchSourceConfig,
    source_graph_sha256: String,
    patched_graph_sha256: String,
    route_patches: Vec<RoutePatchReference>,
    counts: PatchCounts,
    physics: PatchPhysics,
    removes: Vec<RemoveSelector>,
    adds: Vec<AddProfile>,
    #[allow(dead_code)] // consumed by the external-runtime integration test from the same JSON
    verification: VerificationContract,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PatchCounts {
    source_cells: u32,
    source_links: u32,
    source_rocket_jump_links: u32,
    removed_links: u32,
    added_links: u32,
    patched_links: u32,
    patched_active_links: u32,
    patched_rocket_jump_links: u32,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RoutePatchReference {
    id: String,
    manifest_sha256: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RoutePatchDocument {
    schema: String,
    id: String,
    map: String,
    bsp_sha256: String,
    source_spec_sha256: String,
    demo_sha256: String,
    combined_patch_id: String,
    source_graph_sha256: String,
    patched_graph_sha256: String,
    counts: RoutePatchCounts,
    removes: Vec<RemoveSelector>,
    adds: Vec<AddProfile>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RoutePatchCounts {
    source_links: u32,
    source_rocket_jump_links: u32,
    remove_selector_start: u32,
    remove_selectors: u32,
    removed_links: u32,
    add_profile_start: u32,
    added_links: u32,
    patched_links: u32,
    patched_rocket_jump_links: u32,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PatchPhysics {
    gravity: f32,
    runup_speed: f32,
    commit_cost: f32,
}

#[derive(Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct RemoveSelector {
    kind: PatchLinkKind,
    from: [f32; 3],
    to: [f32; 3],
    expected_matches: u32,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
enum PatchLinkKind {
    Speedjump,
    Jump,
    Walk,
}

impl PatchLinkKind {
    fn matches(self, kind: LinkKind) -> bool {
        matches!(
            (self, kind),
            (Self::Speedjump, LinkKind::SpeedJump) | (Self::Jump, LinkKind::JumpGap) | (Self::Walk, LinkKind::Walk)
        )
    }
}

#[derive(Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct AddProfile {
    kind: AddLinkKind,
    from: [f32; 3],
    takeoff: [f32; 3],
    to: [f32; 3],
    v_req: f32,
    curl_gain: f32,
    curl_entry_aim: [f32; 3],
    curl_switch_dist: f32,
    curl_landing_aim: [f32; 3],
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
enum AddLinkKind {
    SpeedJump,
}

/// Coordinates used only by the committed fresh-boot acceptance test. Keeping them in the same
/// SHA-bound document preserves the "coordinates live in manifests" rule.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
#[allow(dead_code)] // consumed by crates/rtx-game/tests/dm3_mega_fresh_boot.rs
struct VerificationContract {
    attempts: u32,
    minimum_successes: u32,
    max_secs: f32,
    mega: [f32; 3],
    rockets: [f32; 3],
    scenarios: Vec<VerificationScenario>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
#[allow(dead_code)] // consumed by crates/rtx-game/tests/dm3_mega_fresh_boot.rs
struct VerificationScenario {
    name: String,
    wire: String,
    start: [f32; 3],
}

/// SHA-256 over arbitrary bytes.
pub fn sha256_bytes(bytes: &[u8]) -> [u8; 32] {
    Sha256::digest(bytes).into()
}

/// Lower-case hexadecimal representation of one SHA-256 digest.
pub fn hex_digest(digest: &[u8; 32]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(64);
    for byte in digest {
        out.push(HEX[(byte >> 4) as usize] as char);
        out.push(HEX[(byte & 0x0f) as usize] as char);
    }
    out
}

fn hash_u8(hasher: &mut Sha256, value: u8) {
    hasher.update([value]);
}

fn hash_u16(hasher: &mut Sha256, value: u16) {
    hasher.update(value.to_le_bytes());
}

fn hash_u32(hasher: &mut Sha256, value: u32) {
    hasher.update(value.to_le_bytes());
}

fn hash_i32(hasher: &mut Sha256, value: i32) {
    hasher.update(value.to_le_bytes());
}

fn hash_f32(hasher: &mut Sha256, value: f32) {
    hash_u32(hasher, value.to_bits());
}

fn hash_vec3(hasher: &mut Sha256, value: Vec3) {
    hash_f32(hasher, value.x);
    hash_f32(hasher, value.y);
    hash_f32(hasher, value.z);
}

fn kind_tag(kind: LinkKind) -> u8 {
    match kind {
        LinkKind::Walk => 0,
        LinkKind::Step => 1,
        LinkKind::Drop => 2,
        LinkKind::JumpGap => 3,
        LinkKind::DoubleJump => 4,
        LinkKind::SpeedJump => 5,
        LinkKind::Plat => 6,
        LinkKind::Teleport => 7,
        LinkKind::Hook => 8,
        LinkKind::RocketJump => 9,
    }
}

fn hash_ground_turn(hasher: &mut Sha256, gt: &GroundTurnCurl) {
    hash_u16(hasher, gt.version);
    hash_vec3(hasher, gt.runway_aim);
    hash_u8(hasher, u8::from(gt.blended_runway));
    hash_f32(hasher, gt.runway_yaw);
    hash_f32(hasher, gt.lip_reach);
    hash_f32(hasher, gt.hold_speed);
    hash_f32(hasher, gt.turn_dist);
    hash_f32(hasher, gt.launch_yaw);
    hash_f32(hasher, gt.yaw_min);
    hash_vec3(hasher, gt.box_min);
    hash_vec3(hasher, gt.box_max);
    hash_f32(hasher, gt.launch_gain);
    hash_vec3(hasher, gt.hold_aim);
    hash_vec3(hasher, gt.gate_point);
    hash_vec3(hasher, gt.gate_normal);
    hash_f32(hasher, gt.air_gain);
    hash_vec3(hasher, gt.landing_aim);
    hash_f32(hasher, gt.entry_speed_lo);
    hash_f32(hasher, gt.entry_speed_hi);
    hash_f32(hasher, gt.entry_yaw_lo);
    hash_f32(hasher, gt.entry_yaw_hi);
    hash_f32(hasher, gt.landing_speed_lo);
    hash_f32(hasher, gt.landing_yaw);
}

/// Deterministic digest of the ordered graph substrate and every speed-jump execution contract.
///
/// It intentionally excludes derived reachability/LOD caches: those are rebuilt after mutation and
/// contain redundant topology. Link order is included because runtime routes and side tables use link
/// indices as identities.
pub fn graph_sha256(graph: &NavGraph) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(GRAPH_HASH_SCHEMA);
    hash_u32(&mut hasher, graph.cells.len() as u32);
    for cell in &graph.cells {
        hash_vec3(&mut hasher, cell.origin);
        hash_i32(&mut hasher, cell.gx);
        hash_i32(&mut hasher, cell.gy);
    }
    hash_u32(&mut hasher, graph.links.len() as u32);
    for (index, link) in graph.links.iter().enumerate() {
        hash_u32(&mut hasher, link.from);
        hash_u32(&mut hasher, link.to);
        hash_u8(&mut hasher, kind_tag(link.kind));
        hash_f32(&mut hasher, link.cost);
        hash_u8(&mut hasher, u8::from(graph.link_removed(index as u32)));
        if let Some(speed) = graph.speed_jump_of_link(index as u32) {
            hash_u8(&mut hasher, 1);
            hash_vec3(&mut hasher, speed.takeoff);
            hash_f32(&mut hasher, speed.v_req);
            hash_f32(&mut hasher, speed.airtime);
            hash_f32(&mut hasher, speed.landing_speed_lo);
            hash_u8(&mut hasher, u8::from(speed.chained));
            hash_f32(&mut hasher, speed.curl_gain);
            hash_vec3(&mut hasher, speed.curl_entry_aim);
            hash_f32(&mut hasher, speed.curl_switch_dist);
            hash_vec3(&mut hasher, speed.curl_landing_aim);
            match &speed.ground_turn {
                Some(gt) => {
                    hash_u8(&mut hasher, 1);
                    hash_ground_turn(&mut hasher, gt);
                }
                None => hash_u8(&mut hasher, 0),
            }
        } else {
            hash_u8(&mut hasher, 0);
        }
    }
    hasher.finalize().into()
}

fn vec3(value: [f32; 3]) -> Vec3 {
    Vec3::from_array(value)
}

fn exact_vec3(left: Vec3, right: [f32; 3]) -> bool {
    left.to_array()
        .into_iter()
        .zip(right)
        .all(|(a, b)| a.to_bits() == b.to_bits())
}

fn active_link_count(graph: &NavGraph) -> u32 {
    graph
        .links
        .iter()
        .enumerate()
        .filter(|(index, _)| !graph.link_removed(*index as u32))
        .count() as u32
}

fn link_kind_count(graph: &NavGraph, kind: LinkKind) -> u32 {
    graph.links.iter().filter(|link| link.kind == kind).count() as u32
}

fn validate_route_patch(
    document: &PatchDocument,
    route: &RoutePatchDocument,
    route_manifest_sha256: &str,
) -> Result<(), NavPatchError> {
    if route.schema != ROUTE_PATCH_SCHEMA {
        return Err(NavPatchError::new(format!(
            "DM3 route patch schema mismatch: expected {ROUTE_PATCH_SCHEMA}, got {}",
            route.schema
        )));
    }
    let Some(reference) = document.route_patches.iter().find(|reference| reference.id == route.id) else {
        return Err(NavPatchError::new(format!(
            "DM3 combined patch does not reference route patch {}",
            route.id
        )));
    };
    if reference.manifest_sha256 != route_manifest_sha256 {
        return Err(NavPatchError::new(format!(
            "DM3 route patch {} SHA-256 mismatch: expected {}, got {route_manifest_sha256}",
            route.id, reference.manifest_sha256
        )));
    }
    if route.map != document.map
        || route.bsp_sha256 != document.bsp_sha256
        || route.combined_patch_id != document.id
        || route.source_graph_sha256 != document.source_graph_sha256
        || route.patched_graph_sha256 != document.patched_graph_sha256
        || route.counts.source_links != document.counts.source_links
        || route.counts.source_rocket_jump_links != document.counts.source_rocket_jump_links
        || route.counts.patched_links != document.counts.patched_links
        || route.counts.patched_rocket_jump_links != document.counts.patched_rocket_jump_links
    {
        return Err(NavPatchError::new(format!(
            "DM3 route patch {} does not match the combined graph contract",
            route.id
        )));
    }
    if route.source_spec_sha256.len() != 64 || route.demo_sha256.len() != 64 {
        return Err(NavPatchError::new(format!(
            "DM3 route patch {} has invalid source provenance",
            route.id
        )));
    }
    let remove_start = route.counts.remove_selector_start as usize;
    let remove_end = remove_start.saturating_add(route.counts.remove_selectors as usize);
    let add_start = route.counts.add_profile_start as usize;
    let add_end = add_start.saturating_add(route.counts.added_links as usize);
    if route.removes.len() as u32 != route.counts.remove_selectors
        || route.adds.len() as u32 != route.counts.added_links
        || document.removes.get(remove_start..remove_end) != Some(route.removes.as_slice())
        || document.adds.get(add_start..add_end) != Some(route.adds.as_slice())
        || route
            .removes
            .iter()
            .map(|selector| selector.expected_matches)
            .sum::<u32>()
            != route.counts.removed_links
    {
        return Err(NavPatchError::new(format!(
            "DM3 route patch {} does not match its combined selector/profile ranges",
            route.id
        )));
    }
    Ok(())
}

fn parse_document() -> Result<(PatchDocument, String, String), NavPatchError> {
    let document: PatchDocument = serde_json::from_str(DM3_MEGA_PATCH)
        .map_err(|error| NavPatchError::new(format!("embedded DM3 patch is invalid: {error}")))?;
    let route: RoutePatchDocument = serde_json::from_str(DM3_RA_PATCH)
        .map_err(|error| NavPatchError::new(format!("embedded DM3 RA patch is invalid: {error}")))?;
    let manifest_sha256 = hex_digest(&sha256_bytes(DM3_MEGA_PATCH.as_bytes()));
    let route_manifest_sha256 = hex_digest(&sha256_bytes(DM3_RA_PATCH.as_bytes()));
    validate_route_patch(&document, &route, &route_manifest_sha256)?;
    Ok((document, manifest_sha256, route_manifest_sha256))
}

/// Apply the built-in patch for `map` immediately after the ordinary graph build.
///
/// A source-build configuration mismatch returns [`BuiltinPatchOutcome::Skipped`]: the graph is
/// legitimate but outside this patch's domain, so the caller installs it unmodified. Every `Err`
/// after a matching configuration is fail-closed: the caller must discard the graph and keep bots
/// disabled.
pub fn apply_builtin_patch(
    map: &str,
    bsp_sha256: Option<[u8; 32]>,
    source_config: NavPatchSourceConfig,
    graph: &mut NavGraph,
) -> Result<BuiltinPatchOutcome, NavPatchError> {
    if map != "dm3" {
        return Ok(BuiltinPatchOutcome::NotApplicable);
    }
    let (document, manifest_sha256, _) = parse_document()?;
    if document.map != map {
        return Err(NavPatchError::new(format!(
            "DM3 patch map mismatch: expected dm3, got {}",
            document.map
        )));
    }
    if document.schema != PATCH_SCHEMA {
        return Err(NavPatchError::new(format!(
            "DM3 patch schema mismatch: expected {PATCH_SCHEMA}, got {}",
            document.schema
        )));
    }
    if let Some(reason) = document.source_build.mismatch(source_config) {
        return Ok(BuiltinPatchOutcome::Skipped(BuiltinPatchSkip {
            id: document.id,
            manifest_sha256,
            reason,
        }));
    }
    let actual_bsp = bsp_sha256
        .as_ref()
        .map(hex_digest)
        .ok_or_else(|| NavPatchError::new("DM3 patch requires a BSP SHA-256, got none"))?;
    if actual_bsp != document.bsp_sha256 {
        return Err(NavPatchError::new(format!(
            "DM3 BSP SHA-256 mismatch: expected {}, got {actual_bsp}",
            document.bsp_sha256
        )));
    }
    if graph.cells.len() as u32 != document.counts.source_cells
        || graph.links.len() as u32 != document.counts.source_links
    {
        return Err(NavPatchError::new(format!(
            "DM3 source graph count mismatch: expected cells={} links={}, got cells={} links={}",
            document.counts.source_cells,
            document.counts.source_links,
            graph.cells.len(),
            graph.links.len()
        )));
    }
    let source_rocket_jump_links = link_kind_count(graph, LinkKind::RocketJump);
    if source_rocket_jump_links != document.counts.source_rocket_jump_links {
        return Err(NavPatchError::new(format!(
            "DM3 source RocketJump count mismatch: expected {}, got {source_rocket_jump_links}",
            document.counts.source_rocket_jump_links
        )));
    }
    let source_graph_sha256 = hex_digest(&graph_sha256(graph));
    if source_graph_sha256 != document.source_graph_sha256 {
        return Err(NavPatchError::new(format!(
            "DM3 source graph SHA-256 mismatch: expected {}, got {source_graph_sha256}",
            document.source_graph_sha256
        )));
    }

    let mut selected = BTreeSet::new();
    for (selector_index, selector) in document.removes.iter().enumerate() {
        let matches = graph
            .links
            .iter()
            .enumerate()
            .filter(|(link_index, link)| {
                !graph.link_removed(*link_index as u32)
                    && selector.kind.matches(link.kind)
                    && exact_vec3(graph.cells[link.from as usize].origin, selector.from)
                    && exact_vec3(graph.cells[link.to as usize].origin, selector.to)
            })
            .map(|(link_index, _)| link_index as u32)
            .collect::<Vec<_>>();
        if matches.len() as u32 != selector.expected_matches {
            return Err(NavPatchError::new(format!(
                "DM3 remove selector {selector_index} matched {} links, expected {}",
                matches.len(),
                selector.expected_matches
            )));
        }
        for link in matches {
            if !selected.insert(link) {
                return Err(NavPatchError::new(format!(
                    "DM3 remove selector {selector_index} overlaps link {link}"
                )));
            }
        }
    }
    if selected.len() as u32 != document.counts.removed_links {
        return Err(NavPatchError::new(format!(
            "DM3 removal total mismatch: expected {}, selected {}",
            document.counts.removed_links,
            selected.len()
        )));
    }
    for link in selected {
        if !graph.unlink(link) {
            return Err(NavPatchError::new(format!("DM3 failed to unlink selected link {link}")));
        }
    }

    if document.adds.len() as u32 != document.counts.added_links {
        return Err(NavPatchError::new(format!(
            "DM3 add profile count mismatch: expected {}, got {}",
            document.counts.added_links,
            document.adds.len()
        )));
    }
    for (profile_index, profile) in document.adds.iter().enumerate() {
        if profile.kind != AddLinkKind::SpeedJump {
            return Err(NavPatchError::new(format!(
                "DM3 add profile {profile_index} has unsupported kind"
            )));
        }
        let from_cell = graph
            .nearest(vec3(profile.from))
            .ok_or_else(|| NavPatchError::new(format!("DM3 add profile {profile_index} has no source cell")))?;
        let to_cell = graph
            .nearest(vec3(profile.to))
            .ok_or_else(|| NavPatchError::new(format!("DM3 add profile {profile_index} has no target cell")))?;
        let takeoff = vec3(profile.takeoff);
        let dz = graph.cell_origin(to_cell).z - takeoff.z;
        let vz0 = crate::qphys::JUMP_VZ;
        let discriminant = (vz0 * vz0 - 2.0 * document.physics.gravity * dz).max(0.0);
        let airtime = (vz0 + discriminant.sqrt()) / document.physics.gravity;
        let runup = (takeoff.xy() - graph.cell_origin(from_cell).xy()).length();
        let cost = runup / document.physics.runup_speed + airtime + document.physics.commit_cost;
        graph.plant_speed_jump(
            from_cell,
            to_cell,
            cost,
            SpeedJumpTraversal {
                takeoff,
                v_req: profile.v_req,
                airtime,
                landing_speed_lo: 0.0,
                chained: false,
                curl_gain: profile.curl_gain,
                curl_entry_aim: vec3(profile.curl_entry_aim),
                curl_switch_dist: profile.curl_switch_dist,
                curl_landing_aim: vec3(profile.curl_landing_aim),
                ground_turn: None,
            },
        );
    }
    graph.rebuild_derived();

    let total_links = graph.links.len() as u32;
    let active_links = active_link_count(graph);
    if total_links != document.counts.patched_links || active_links != document.counts.patched_active_links {
        return Err(NavPatchError::new(format!(
            "DM3 patched graph count mismatch: expected total={} active={}, got total={total_links} active={active_links}",
            document.counts.patched_links, document.counts.patched_active_links
        )));
    }
    let patched_rocket_jump_links = link_kind_count(graph, LinkKind::RocketJump);
    if patched_rocket_jump_links != document.counts.patched_rocket_jump_links {
        return Err(NavPatchError::new(format!(
            "DM3 patched RocketJump count mismatch: expected {}, got {patched_rocket_jump_links}",
            document.counts.patched_rocket_jump_links
        )));
    }
    let patched_graph_sha256 = hex_digest(&graph_sha256(graph));
    if patched_graph_sha256 != document.patched_graph_sha256 {
        return Err(NavPatchError::new(format!(
            "DM3 patched graph SHA-256 mismatch: expected {}, got {patched_graph_sha256}",
            document.patched_graph_sha256
        )));
    }

    Ok(BuiltinPatchOutcome::Applied(BuiltinPatchReport {
        id: document.id,
        manifest_sha256,
        source_graph_sha256,
        patched_graph_sha256,
        removed_links: document.counts.removed_links,
        added_links: document.counts.added_links,
        total_links,
        active_links,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn matching_source_config() -> NavPatchSourceConfig {
        NavPatchSourceConfig::from_build_inputs(
            false,
            None,
            false,
            Some(SpeedJumpParams {
                gravity: 800.0,
                accel: 10.0,
                maxspeed: 320.0,
                friction: 4.0,
                stopspeed: 100.0,
                curl: true,
            }),
            Some(RocketJumpParams {
                cost_scale: 0.35,
                ..RocketJumpParams::default()
            }),
        )
    }

    #[test]
    fn embedded_dm3_contract_is_structurally_closed() {
        let (document, digest, route_digest) = parse_document().expect("embedded patch parses");
        assert_eq!(document.schema, PATCH_SCHEMA);
        assert_eq!(document.map, "dm3");
        assert_eq!(document.bsp_sha256.len(), 64);
        assert_eq!(document.source_graph_sha256.len(), 64);
        assert_eq!(document.patched_graph_sha256.len(), 64);
        assert_eq!(document.source_build, matching_source_config());
        assert_eq!(digest.len(), 64);
        assert_eq!(route_digest.len(), 64);
        assert_eq!(document.route_patches.len(), 1);
        assert_eq!(document.removes.len(), 174);
        assert_eq!(document.adds.len() as u32, document.counts.added_links);
        assert_eq!(
            document
                .removes
                .iter()
                .map(|selector| selector.expected_matches)
                .sum::<u32>(),
            document.counts.removed_links
        );
        assert_eq!(
            document.counts.source_links + document.counts.added_links,
            document.counts.patched_links
        );
        assert_eq!(
            document.counts.patched_links - document.counts.removed_links,
            document.counts.patched_active_links
        );
        assert_eq!(document.verification.attempts, 40);
        assert_eq!(document.verification.minimum_successes, 38);
        assert_eq!(document.verification.scenarios.len(), 2);
        assert!(document.verification.max_secs > 0.0);
        assert!(document.verification.mega != document.verification.rockets);
        assert!(document
            .verification
            .scenarios
            .iter()
            .all(|scenario| !scenario.name.is_empty() && !scenario.wire.is_empty() && scenario.start != [0.0; 3]));
    }

    #[test]
    fn dm3_bsp_mismatch_fails_before_graph_mutation() {
        let mut graph = NavGraph::test_graph(Vec::new(), Vec::new());
        let before = graph_sha256(&graph);
        let error = apply_builtin_patch("dm3", Some([0; 32]), matching_source_config(), &mut graph)
            .expect_err("wrong BSP must fail closed");
        assert!(error.to_string().contains("BSP SHA-256 mismatch"));
        assert_eq!(graph_sha256(&graph), before);
    }

    #[test]
    fn different_source_config_skips_before_graph_validation() {
        let mut graph = NavGraph::test_graph(Vec::new(), Vec::new());
        let before = graph_sha256(&graph);
        let alternate = NavPatchSourceConfig::from_build_inputs(
            false,
            None,
            false,
            Some(SpeedJumpParams {
                gravity: 800.0,
                accel: 10.0,
                maxspeed: 320.0,
                friction: 4.0,
                stopspeed: 100.0,
                curl: true,
            }),
            None,
        );
        let outcome =
            apply_builtin_patch("dm3", Some([0; 32]), alternate, &mut graph).expect("alternate config is valid");
        let BuiltinPatchOutcome::Skipped(skip) = outcome else {
            panic!("alternate config should skip the patch");
        };
        assert!(skip.reason.contains("rocket_jump"));
        assert_eq!(graph_sha256(&graph), before);
    }

    #[test]
    fn double_jump_source_config_also_skips_before_graph_validation() {
        let mut graph = NavGraph::test_graph(Vec::new(), Vec::new());
        let before = graph_sha256(&graph);
        let mut alternate = matching_source_config();
        alternate.double_jump = true;
        let outcome =
            apply_builtin_patch("dm3", Some([0; 32]), alternate, &mut graph).expect("alternate config is valid");
        let BuiltinPatchOutcome::Skipped(skip) = outcome else {
            panic!("alternate config should skip the patch");
        };
        assert!(skip.reason.contains("double_jump"));
        assert_eq!(graph_sha256(&graph), before);
    }

    #[test]
    fn maps_without_a_builtin_patch_are_unchanged() {
        let mut graph = NavGraph::test_graph(Vec::new(), Vec::new());
        let before = graph_sha256(&graph);
        assert_eq!(
            apply_builtin_patch("dm2", None, matching_source_config(), &mut graph).unwrap(),
            BuiltinPatchOutcome::NotApplicable
        );
        assert_eq!(graph_sha256(&graph), before);
    }
}
