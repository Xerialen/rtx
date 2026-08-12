// SPDX-License-Identifier: AGPL-3.0-or-later

//! Jump-link generation: the run-jump (`find_jumps`), rtx double-jump (`add_double_jumps`), and
//! bhop-carried speed-jump (`add_speed_jumps`) passes plus their per-cell solvers and the runway
//! measurer. Each pass floods candidates off ledge edges, dedups them per compass octant, arc-tests
//! clearance, and splices the survivors into the graph. Runs on the parallel build's worker cells.

use glam::{Vec2, Vec3, Vec3Swizzles};

use super::geom::*;
use super::physics::*;
use super::*;
use crate::bsp::Bsp;
use crate::math::{wrap180, yaw_of};
use crate::pmove::{pm_step, PmParams, PmState};
use crate::strafe::{air_accel_max, air_correct_held, Cmd, MOVE_SPEED};

impl NavGraph {
    /// Jump links out of `from`: only from a **ledge edge** (the adjacent column toward the
    /// target has no walkable ground, i.e. a gap/pit), within run-jump reach and apex, with a
    /// clear arc. Deduped to the single nearest target per (compass octant, elevation band) so a
    /// ledge sprouts a handful of jumps, not hundreds of redundant parallel ones — banded by
    /// elevation because targets a storey apart are distinct destinations: without the band, a
    /// short descending jump into the pit under a gap shadows the level jump *across* it onto a
    /// separate ledge, and the pit floor doesn't lead back up to that ledge.
    pub(super) fn find_jumps(&self, bsp: &Bsp, from: CellId) -> Vec<Link> {
        let a = self.cells[from as usize];
        if bsp.is_liquid_at(a.origin) {
            return Vec::new(); // submerged: the jump input swims up, so a jump takeoff here is a no-op
        }
        // best (distance, link) per compass direction bucket (3×3, center unused) × elevation band
        let mut best = [[None::<(f32, Link)>; JUMP_ELEV_BANDS]; 9];
        for to in self.neighbors_within(a.gx, a.gy, jump_grid_radius()) {
            let b = self.cells[to as usize];
            let (dgx, dgy) = (b.gx - a.gx, b.gy - a.gy);
            if dgx.abs() <= 1 && dgy.abs() <= 1 {
                continue; // adjacent — a grounded link if anything
            }
            let dz = b.origin.z - a.origin.z;
            if !(-MAX_DROP..=JUMP_APEX).contains(&dz) {
                continue;
            }
            let horiz = (b.origin.xy() - a.origin.xy()).length();
            if horiz > JUMP_REACH {
                continue;
            }
            // Must take off from a ledge: the column one step toward B isn't walkable ground.
            if self.has_ground_near(a.gx + dgx.signum(), a.gy + dgy.signum(), a.origin.z) {
                continue;
            }
            // Shallow crossings check the symmetric hop parabola; a deep plunge flies a very
            // different path (out at run speed, then mostly straight down), so sample that.
            let clear = if dz < -JUMP_ELEV_SPAN {
                ballistic_clear(bsp, a.origin, b.origin)
            } else {
                arc_clear(bsp, a.origin, b.origin)
            };
            if !clear {
                continue;
            }
            // A jump *down* must land in a spot the hull can descend into — arc sampling can skip a
            // thin floor lip (a slot too small for the hull) that the vertical hull trace catches.
            if dz < 0.0 && !descent_clear(bsp, a.origin.z, b.origin) {
                continue;
            }
            let slot = &mut best[dir_bucket(dgx, dgy)][jump_elev_band(dz)];
            if slot.is_none_or(|(d, _)| horiz < d) {
                *slot = Some((
                    horiz,
                    Link {
                        from,
                        to,
                        kind: LinkKind::JumpGap,
                        cost: link_cost(LinkKind::JumpGap, horiz, dz),
                    },
                ));
            }
        }
        best.into_iter().flatten().flatten().map(|(_, l)| l).collect()
    }

    /// Splice **double-jump** links: gaps/ledges beyond a single jump's reach but within a double
    /// jump's, gated on `rtx_doublejump`. Same ledge-edge/octant-dedup shape as [`find_jumps`], but
    /// the wider reach/apex and the taller arc-clearance envelope — and only for targets a plain
    /// jump can't already make (else a `JumpGap` covers it). The bot air-jumps mid-flight to cross.
    pub fn add_double_jumps(&mut self, bsp: &Bsp) {
        // Solve per source cell in parallel (read-only borrow), then splice serially. The indexed
        // `collect` returns per-cell results in cell order, so the splice — and thus link indices —
        // are identical to a sequential build. The solvers never observe each other's pending links
        // (same as the sequential drain), so within-stage parallelism is sound.
        let this = &*self;
        let pending: Vec<Vec<Link>> = (0..this.cells.len() as CellId)
            .into_par_iter()
            .map(|from| {
                let mut out = Vec::new();
                this.solve_double_jumps_from(bsp, from, &mut out);
                out
            })
            .collect();
        for link in pending.into_iter().flatten() {
            self.push_link(link);
        }
    }

    /// The double-jump links leaving cell `from`, appended to `out`.
    fn solve_double_jumps_from(&self, bsp: &Bsp, from: CellId, out: &mut Vec<Link>) {
        let a = self.cells[from as usize];
        if bsp.is_liquid_at(a.origin) {
            return; // submerged takeoff: can't jump (the jump input swims up)
        }
        let mut best: [Option<(f32, Link)>; 9] = Default::default();
        for to in self.neighbors_within(a.gx, a.gy, double_jump_grid_radius()) {
            if to == from {
                continue;
            }
            let b = self.cells[to as usize];
            let (dgx, dgy) = (b.gx - a.gx, b.gy - a.gy);
            if dgx.abs() <= 1 && dgy.abs() <= 1 {
                continue;
            }
            let dz = b.origin.z - a.origin.z;
            let horiz = (b.origin.xy() - a.origin.xy()).length();
            if !(-DJ_MAX_DROP..=DOUBLE_JUMP_APEX).contains(&dz) || horiz > DOUBLE_JUMP_REACH {
                continue;
            }
            // Only worthwhile beyond a single jump — otherwise `find_jumps` already linked it.
            if horiz <= JUMP_REACH && dz <= JUMP_APEX {
                continue;
            }
            // Take off from a ledge edge, clear the taller arc, and don't duplicate a route the
            // static graph already provides (walk/step/jump).
            if self.has_ground_near(a.gx + dgx.signum(), a.gy + dgy.signum(), a.origin.z)
                || !arc_clear_peak(bsp, a.origin, b.origin, DOUBLE_ARC_PEAK, 12)
                || (dz < 0.0 && !descent_clear(bsp, a.origin.z, b.origin))
                || self.has_direct_link(from, to)
            {
                continue;
            }
            let oct = dir_bucket(dgx, dgy);
            if best[oct].is_none_or(|(d, _)| horiz < d) {
                best[oct] = Some((
                    horiz,
                    Link {
                        from,
                        to,
                        kind: LinkKind::DoubleJump,
                        cost: link_cost(LinkKind::DoubleJump, horiz, dz),
                    },
                ));
            }
        }
        out.extend(best.into_iter().flatten().map(|(_, l)| l));
    }

    /// Splice **speed-jump** links: leaps across gaps too wide for any single/double jump, cleared by
    /// arriving at the ledge with bunnyhop-built speed. For each ledge edge, measure the straight
    /// runway feeding it, cap the attainable speed to that, and link the widest reachable targets —
    /// but with `from` set to the *runway start* so A* commits the whole run-up (the bot is thus
    /// guaranteed the speed). Only where a plain/double jump can't already make it. Gated on bhop.
    ///
    /// A jump with no self-contained runway also emits a **chained** variant (`from` = the ledge
    /// itself) for the case a human relies on: a chain of gaps with only a short platform between
    /// them, where speed carried from the previous jump's landing clears the next. These have no
    /// runway budget of their own, so they are traversable only by the speed-band planner
    /// ([`Self::find_path_banded`]), which proves the entry band carries `v_req`; the speed-unaware
    /// `find_path`/`costs_from` price them away ([`Self::chained_block`]) since a standing start
    /// can't make them. Chained candidates use a separate small per-cell cap so they never evict a
    /// self-contained jump.
    pub fn add_speed_jumps(&mut self, bsp: &Bsp, params: SpeedJumpParams, double_jump: bool) {
        let k = bhop_k(params.accel, params.maxspeed);
        self.sj_k = k; // the banded planner prices carried speed with this map's k
                       // Solve per ledge in parallel (read-only borrow); indexed `collect` keeps cell order, so the
                       // serial splice below reproduces the sequential build's link indices exactly.
        let this = &*self;
        let pending: Vec<Vec<(Link, SpeedJumpTraversal)>> = (0..this.cells.len() as CellId)
            .into_par_iter()
            .map(|ledge| {
                let mut out = Vec::new();
                this.solve_speed_jumps_from(bsp, ledge, params, k, double_jump, &mut out);
                out
            })
            .collect();
        for (link, tr) in pending.into_iter().flatten() {
            self.push_speed_jump(link, tr);
        }
        // Curl jumps second (after the straight speed jumps are spliced): a separate certified pass for
        // gaps that need a run-up *and* an air-turn.
        if params.curl {
            let this = &*self;
            let curls: Vec<Vec<(Link, SpeedJumpTraversal)>> = (0..this.cells.len() as CellId)
                .into_par_iter()
                .map(|ledge| {
                    let mut out = Vec::new();
                    this.solve_curl_jumps_from(bsp, ledge, params, k, &mut out);
                    // Side jumps share the corridor curls' per-target dedup below: both certify the same
                    // way and land the same platforms, so they must compete on cost rather than each
                    // spending a separate budget on the same landing.
                    this.solve_side_jumps_from(bsp, ledge, params, k, &mut out);
                    out
                })
                .collect();
            // Global dedup by target cell: many source ledges certify a curl onto the same platform, so
            // keep only the cheapest few per target (the same landing from a dozen corridors is noise the
            // planner never needs). Deterministic: iterate the indexed collect in cell order, and among
            // equal-cost keep the earliest. `CURL_TARGET_MAX` distinct sources per target land here.
            let mut per_target: std::collections::HashMap<CellId, Vec<(Link, SpeedJumpTraversal)>> =
                std::collections::HashMap::new();
            for (link, tr) in curls.into_iter().flatten() {
                let slot = per_target.entry(link.to).or_default();
                slot.push((link, tr));
            }
            // Stable target order (grid/cell id) so the splice is deterministic across builds.
            let mut targets: Vec<CellId> = per_target.keys().copied().collect();
            targets.sort_unstable();
            for tgt in targets {
                let mut v = per_target.remove(&tgt).unwrap();
                v.sort_by(|a, b| a.0.cost.total_cmp(&b.0.cost).then(a.0.from.cmp(&b.0.from)));
                v.truncate(CURL_TARGET_MAX);
                for (link, tr) in v {
                    self.push_speed_jump(link, tr);
                }
            }
        }
    }

    /// The curl-jump links leaving ledge cell `ledge`: targets offset off the run-up heading that a
    /// straight speed jump can't own (too fast for the air-strafe credit, or its arc is blocked), each
    /// certified by a `pm_step` rollout of the game's takeoff regime (ground prestrafe to the lip, leap
    /// along the corridor, `air_correct`-curl onto the landing). Emitted as a self-contained SpeedJump
    /// carrying its certified `curl_gain`, so the banded planner prices it by its stored cost and the
    /// runtime flies it with the curl controller. Its own per-cell budget, so it never evicts a straight
    /// jump.
    fn solve_curl_jumps_from(
        &self,
        bsp: &Bsp,
        ledge: CellId,
        params: SpeedJumpParams,
        k: f32,
        out: &mut Vec<(Link, SpeedJumpTraversal)>,
    ) {
        let a = self.cells[ledge as usize];
        if bsp.is_liquid_at(a.origin) {
            return; // submerged takeoff: can't jump
        }
        // On a low-gravity server even a flat leap hangs longer than the rollout tick cap, so no curl
        // could ever certify — skip the whole (otherwise enormous) scan rather than roll futilely.
        if jump_airtime(0.0, params.gravity) > CURL_MAX_TICKS as f32 * CURL_DT {
            return;
        }
        let p = PmParams {
            gravity: params.gravity,
            accel: params.accel,
            friction: params.friction,
            stopspeed: params.stopspeed,
            maxspeed: params.maxspeed,
        };
        let mut cands: Vec<(f32, Link, SpeedJumpTraversal)> = Vec::new(); // (horiz, link, tr)
        for (dgx, dgy) in COMPASS {
            // Leap into a gap (no ground the leap way); measure the corridor run-up behind the lip.
            if self.has_ground_near(a.gx + dgx.signum(), a.gy + dgy.signum(), a.origin.z) {
                continue;
            }
            let runway = self.measure_runway(bsp, &a, dgx, dgy);
            if runway < CURL_MIN_RUNWAY {
                continue; // too little run-up for the ground prestrafe to build curl speed
            }
            // The takeoff speed is the ground-prestrafe equilibrium (saturates well inside CURL_RUNUP_CAP),
            // so it's the *committed* run-up — not the full measured corridor — that a curl builds over.
            let v_deliver = prestrafe_delivered(
                runway.min(CURL_RUNUP_CAP),
                params.accel,
                params.maxspeed,
                params.friction,
                params.stopspeed,
            );
            let v_max_straight = SPEED_JUMP_V_CAP.min(BHOP_EFF * attainable_speed(MAX_SPEED, runway, k));
            let psi0 = yaw_of(Vec2::new(dgx as f32, dgy as f32)); // corridor / takeoff heading
                                                                  // A rollout can only certify a landing it reaches inside the tick cap, so bound the target
                                                                  // scan (and the per-target airtime) by that flight time — not the full SJ_MAX_DROP fall, which
                                                                  // on low-gravity servers is many seconds of futile scan-and-rollout.
            let fly_cap = CURL_MAX_TICKS as f32 * CURL_DT;
            let reach = v_deliver * fly_cap;
            let scan = ((reach / GRID).ceil() as i32).max(1);
            for to in self.neighbors_within(a.gx, a.gy, scan) {
                if to == ledge {
                    continue;
                }
                let b = self.cells[to as usize];
                let dz = b.origin.z - a.origin.z;
                let horiz = (b.origin.xy() - a.origin.xy()).length();
                if !(-SJ_MAX_DROP..=JUMP_APEX).contains(&dz) || horiz <= JUMP_REACH {
                    continue;
                }
                // The target must sit off the corridor by [LO, HI]° — a genuine curl, not a straight leap.
                let off = wrap180(yaw_of(b.origin.xy() - a.origin.xy()) - psi0).abs();
                if !(CURL_ANGLE_LO..=CURL_ANGLE_HI).contains(&off) {
                    continue;
                }
                if self.has_direct_link(ledge, to) {
                    continue; // a plain jump / existing link already leaves the ledge for here
                }
                let airtime = jump_airtime(dz, params.gravity);
                if airtime <= 0.0 || airtime > fly_cap {
                    continue; // unreachable, or a drop too deep to land within the rollout tick cap
                }
                // Only curl what the straight pass could NOT own: too fast for its air-strafe credit, or
                // an arc it can't fly through. (A target the straight pass covers needs no curl.)
                let steps = ((horiz / 24.0).ceil() as i32).max(8);
                let arc_ok = arc_clear_peak(bsp, a.origin, b.origin, JUMP_APEX, steps);
                let v_req_straight = v_required(horiz, dz, params.gravity);
                if arc_ok && v_req_straight * SJ_MARGIN <= v_max_straight {
                    continue;
                }
                // (No separate slide-out check: `certify_curl` below requires an actual on-ground
                // touchdown resolving to the target cell within tolerance, which is the landing proof.)
                // The expensive step, reached only by the survivors: certify a curl by rollout. Search
                // the takeoff *back* along the run-up — a fast run-up overshoots a leap right at the pit
                // edge, so the leap point slides back (over the near ground, which the arc clears) until
                // the delivered speed matches the distance. First (latest) leap that certifies wins.
                let t_max = (runway - CURL_MIN_RUNWAY).clamp(0.0, CURL_TAKEOFF_BACKOFF);
                let mut solved: Option<(Vec3, Vec3, f32, f32, f32)> = None; // (takeoff, from_pt, v_req, gain, cost)
                                                                            // The runtime takes off along the from→takeoff line, so that heading is ours to choose —
                                                                            // and certification is sharply sensitive to it (a real lip's approach is rarely exactly on
                                                                            // a compass axis; the dm3 curl_mid certifies at 6° off but not at 0°). Sample a few
                                                                            // headings around the corridor axis and place the from-cell along whichever certifies, so
                                                                            // the bot flies precisely the line that was proven.
                'psi: for dpsi in CURL_PSI_SAMPLES {
                    let psi = psi0 + dpsi;
                    let (sp, cp) = psi.to_radians().sin_cos();
                    let dir = Vec3::new(cp, sp, 0.0);
                    let mut t = 0.0;
                    loop {
                        // Snap the leap point to an actual cell: correct z on a stepped run-up, and steps the
                        // search over the grid so a narrow certify window isn't jumped past.
                        if let Some(cell) = self.nearest_within(a.origin - dir * t, GRID * 0.75, STEP_HEIGHT * 2.0) {
                            let takeoff = self.cells[cell as usize].origin;
                            let back = (takeoff.xy() - a.origin.xy()).length();
                            // The committed run-up is capped (CURL_RUNUP_CAP) but must fit behind this takeoff.
                            let runup_len = (runway - back).min(CURL_RUNUP_CAP);
                            let v_del = prestrafe_delivered(
                                runup_len,
                                params.accel,
                                params.maxspeed,
                                params.friction,
                                params.stopspeed,
                            );
                            // Cheap scout first — one mid-gain rollout with a generous tolerance — so the full
                            // envelope certify only runs where a landing is already near the target (else the
                            // pass is ~50× slower).
                            let scout_ok =
                                curl_land_point(bsp, takeoff, b.origin, v_del, psi, 10.0, &p).is_some_and(|land| {
                                    (land.xy() - b.origin.xy()).length() <= CURL_MISS_TOL * 2.5
                                        && (land.z - b.origin.z).abs() <= CURL_Z_TOL * 2.0
                                });
                            if scout_ok {
                                if let Some((v_req, gain)) = certify_curl(bsp, takeoff, b.origin, psi, v_del, 1.0, &p) {
                                    // From-cell one committed run-up back *along the certified heading*, so the
                                    // runtime's run-up line is the one that was proven. Honest cost at the solved
                                    // takeoff speed the runtime will hold (not the equilibrium).
                                    let from_pt = takeoff - dir * runup_len;
                                    let cost = runup_len / ((MAX_SPEED + v_req) * 0.5) + airtime + CURL_COMMIT;
                                    solved = Some((takeoff, from_pt, v_req, gain, cost));
                                    break 'psi;
                                }
                            }
                        }
                        t += GRID;
                        if t > t_max {
                            break;
                        }
                    }
                }
                let Some((takeoff, from_pt, v_req, gain, cost)) = solved else {
                    continue;
                };
                let Some(start) = self.nearest_within(from_pt, GRID * 1.5, STEP_HEIGHT * 3.0) else {
                    continue;
                };
                if start == to || self.has_direct_link(start, to) {
                    continue;
                }
                // A same-plane curl whose lip already reaches the target through a short
                // grounded chain adds nothing but a stall-prone sprint (dm3 link 35363:
                // v_req 428 down a 64u corridor Walk already covers — 18/24 patrol runs
                // stalled there, and goto never picks the curl). Refuse the mint.
                if (takeoff.z - self.cells[to as usize].origin.z).abs() <= STEP_HEIGHT {
                    if let Some(lip) = self.nearest_within(takeoff, GRID * 1.5, STEP_HEIGHT) {
                        if self.grounded_reaches(lip, to, 8) {
                            continue;
                        }
                    }
                }
                let link = Link {
                    from: start,
                    to,
                    kind: LinkKind::SpeedJump,
                    cost,
                };
                let tr = SpeedJumpTraversal {
                    takeoff,
                    v_req,
                    airtime,
                    chained: false,
                    curl_gain: gain,
                    // `measure_runway` already demanded a walkable column either side of this
                    // corridor, so the uncapped speed-building weave has the floor it needs.
                    weave_cap: f32::INFINITY,
                };
                cands.push((horiz, link, tr)); // every certified curl; the per-cell cap trims below
            }
        }
        cands.sort_by(|x, y| x.0.total_cmp(&y.0));
        cands.truncate(SPEED_JUMP_CURL_MAX_PER_CELL);
        out.extend(cands.into_iter().map(|(_, l, t)| (l, t)));
    }

    /// The speed-jump links leaving ledge cell `ledge` (the takeoff), appended to `out`.
    fn solve_speed_jumps_from(
        &self,
        bsp: &Bsp,
        ledge: CellId,
        params: SpeedJumpParams,
        k: f32,
        double_jump: bool,
        out: &mut Vec<(Link, SpeedJumpTraversal)>,
    ) {
        let a = self.cells[ledge as usize];
        if bsp.is_liquid_at(a.origin) {
            return; // submerged takeoff: can't jump (the jump input swims up)
        }
        let mut cands: Vec<(f32, Link, SpeedJumpTraversal)> = Vec::new(); // stand-start (v_req, link, tr)
        let mut cands_chained: Vec<(f32, Link, SpeedJumpTraversal)> = Vec::new(); // chained
                                                                                  // The most speed a chained entry can ever carry into a jump (the top band's floor); a jump
                                                                                  // needing more than this is unroutable even chained, so it bounds the chained target scan.
        let v_chain_max = BAND_FLOOR[NBANDS - 1] / SJ_MARGIN;
        for (dgx, dgy) in COMPASS {
            // Take off from a ledge edge (a runway only *helps* — a chained jump needs none).
            if self.has_ground_near(a.gx + dgx.signum(), a.gy + dgy.signum(), a.origin.z) {
                continue;
            }
            let runway = self.measure_runway(bsp, &a, dgx, dgy);
            let v_max = SPEED_JUMP_V_CAP.min(BHOP_EFF * attainable_speed(MAX_SPEED, runway, k));
            // Scan out to whatever the better of a self-contained runway or a carried entry reaches.
            let v_scan = v_max.max(v_chain_max);
            if v_scan * jump_airtime(0.0, params.gravity) <= JUMP_REACH + 1.0 {
                continue; // neither a runway nor a carried entry buys anything past a normal jump
            }
            let reach_cap = v_scan * jump_airtime(-SJ_MAX_DROP, params.gravity);
            let scan = ((reach_cap / GRID).ceil() as i32).max(1);
            let mut best: Option<(f32, Link, SpeedJumpTraversal)> = None; // stand-start
            let mut best_chained: Option<(f32, Link, SpeedJumpTraversal)> = None;
            for to in self.neighbors_within(a.gx, a.gy, scan) {
                if to == ledge {
                    continue;
                }
                let b = self.cells[to as usize];
                let (bgx, bgy) = (b.gx - a.gx, b.gy - a.gy);
                if (bgx.abs() <= 1 && bgy.abs() <= 1) || dir_bucket(bgx, bgy) != dir_bucket(dgx, dgy) {
                    continue;
                }
                let dz = b.origin.z - a.origin.z;
                let horiz = (b.origin.xy() - a.origin.xy()).length();
                if !(-SJ_MAX_DROP..=JUMP_APEX).contains(&dz) || horiz <= JUMP_REACH {
                    continue;
                }
                // Skip what a double jump already covers (when enabled), and any existing direct link.
                if (double_jump && horiz <= DOUBLE_JUMP_REACH && dz <= DOUBLE_JUMP_APEX)
                    || self.has_direct_link(ledge, to)
                {
                    continue;
                }
                let airtime = jump_airtime(dz, params.gravity);
                let v_req = v_required(horiz, dz, params.gravity);
                if airtime <= 0.0 || v_req * SJ_MARGIN > v_scan {
                    continue; // beyond even a carried entry
                }
                // Arc clearance — required for either form. Deliberately *no* slide-out check: whether
                // a jump exists depends on whether the bot can land there, not on how much runout
                // follows. The landing cell existing already means the hull stands there; not
                // overshooting it (braking, or carrying on to the next platform) is the controller's
                // job, and the runtime reads no landing-depth number at all. The old 96u rule wanted
                // three grid columns past the landing, which deletes a lot of real map — dm3's red
                // armour has barely a column beyond it, and the `dm3_rlstrafejump` balcony's runout is
                // a wall ~70u past touchdown that the human simply runs into.
                let steps = ((horiz / 24.0).ceil() as i32).max(8);
                if !arc_clear_peak(bsp, a.origin, b.origin, JUMP_APEX, steps) {
                    continue;
                }
                // Stand-start form: a runway long enough behind the ledge to build v_req from a walk.
                if v_req * SJ_MARGIN <= v_max {
                    let need = runway_len_for(v_req * SJ_MARGIN, MAX_SPEED, k);
                    let dir = Vec3::new(dgx.signum() as f32, dgy.signum() as f32, 0.0).normalize_or_zero();
                    if let Some(start) = self.nearest_within(a.origin - dir * need, GRID * 1.5, STEP_HEIGHT * 3.0) {
                        if start != to {
                            let cost = runway_time(v_req * SJ_MARGIN, MAX_SPEED, k) + airtime + 1.0;
                            let link = Link {
                                from: start,
                                to,
                                kind: LinkKind::SpeedJump,
                                cost,
                            };
                            let tr = SpeedJumpTraversal {
                                takeoff: a.origin,
                                v_req,
                                airtime,
                                chained: false,
                                curl_gain: 0.0,
                                weave_cap: f32::INFINITY,
                            };
                            if best.is_none_or(|(bv, _, _)| v_req < bv) {
                                best = Some((v_req, link, tr));
                            }
                            continue; // a self-contained jump covers this target; no chained dup
                        }
                    }
                }
                // Chained form: no runway of its own — take off from the ledge itself, feasible only
                // when a prior jump delivers ≥ v_req (the banded planner proves it; unbanded queries
                // price it away). Bounded to what the top band can carry.
                if v_req * SJ_MARGIN <= v_chain_max {
                    let cost = airtime + 1.0;
                    let link = Link {
                        from: ledge,
                        to,
                        kind: LinkKind::SpeedJump,
                        cost,
                    };
                    let tr = SpeedJumpTraversal {
                        takeoff: a.origin,
                        v_req,
                        airtime,
                        chained: true,
                        curl_gain: 0.0,
                        weave_cap: f32::INFINITY,
                    };
                    if best_chained.is_none_or(|(bv, _, _)| v_req < bv) {
                        best_chained = Some((v_req, link, tr));
                    }
                }
            }
            if let Some(c) = best {
                cands.push(c);
            }
            if let Some(c) = best_chained {
                cands_chained.push(c);
            }
        }
        // Keep the cheapest-entry candidates in each pool (they never evict each other — separate
        // budgets), then splice link + traversal into the shared output.
        let mut keep_cheapest = |mut cs: Vec<(f32, Link, SpeedJumpTraversal)>, cap: usize| {
            cs.sort_by(|x, y| x.0.total_cmp(&y.0));
            cs.truncate(cap);
            out.extend(cs.into_iter().map(|(_, l, t)| (l, t)));
        };
        keep_cheapest(cands, SPEED_JUMP_MAX_PER_CELL);
        keep_cheapest(cands_chained, SPEED_JUMP_CHAINED_MAX_PER_CELL);
    }

    /// Measure the straight, flat, hop-wide runway feeding ledge cell `a` from behind (opposite the
    /// jump direction): walk grid columns back while each has a cell within `STEP_HEIGHT`, hop
    /// headroom, and ground in both perpendicular columns (so the air-strafe weave stays on floor).
    fn measure_runway(&self, bsp: &Bsp, a: &Cell, dgx: i32, dgy: i32) -> f32 {
        let (bx, by) = (-dgx.signum(), -dgy.signum());
        if bx == 0 && by == 0 {
            return 0.0;
        }
        let step_len = GRID * (((bx * bx + by * by) as f32).sqrt());
        let (px, py) = (-by, bx); // perpendicular grid direction
        let (mut gx, mut gy, mut z, mut len) = (a.gx, a.gy, a.origin.z, 0.0);
        while len < RUNWAY_MAX {
            let (ngx, ngy) = (gx + bx, gy + by);
            let Some(cid) = self.cell_near(ngx, ngy, z) else {
                break;
            };
            let c = self.cells[cid as usize].origin;
            if bsp.is_solid(c + Vec3::new(0.0, 0.0, JUMP_APEX))
                || self.cell_near(ngx + px, ngy + py, c.z).is_none()
                || self.cell_near(ngx - px, ngy - py, c.z).is_none()
            {
                break;
            }
            len += step_len;
            (gx, gy, z) = (ngx, ngy, c.z);
        }
        len
    }

    /// Measure the run-up feeding `lip` from behind along an **arbitrary** heading `psi` (degrees, the
    /// direction of travel), and how much lateral floor that line has. Returns `(length, min_clearance)`.
    ///
    /// The difference from [`Self::measure_runway`] is not just the free heading — it is the lateral
    /// rule. `measure_runway` *requires* a walkable column on both sides and stops dead without one,
    /// which makes it blind to any ledge under three columns wide: a two-row balcony has no interior
    /// column, so it scores 0 everywhere along its 448u length. That is a real 448u of run-up the
    /// `dm3_rlstrafejump` human uses. Here the sides are **measured, not demanded** — the caller turns a
    /// thin result into a weave cap for the runtime instead of discarding the run-up.
    ///
    /// Walks back in `GRID` steps while each sample snaps to a cell within a step's height of the
    /// running z, so a stepped approach is followed.
    ///
    /// Note there is **no hop-headroom probe** here, unlike [`Self::measure_runway`]. That probe asks
    /// for `JUMP_APEX` of clearance over every column because a straight speed jump's run-up is a
    /// bunnyhop chain — the bot is airborne most of the way and would bang the ceiling. A side jump's
    /// run-up is the *grounded* prestrafe: the takeoff regime circle-strafes on the floor and leaves it
    /// exactly once, at the lip, which the rollout certifies separately. Asking a grounded run-up for
    /// hop clearance rejects real runways — on dm3's balcony it cuts the demo's own 448u approach to
    /// 64u at a low beam the human runs straight under. The cell's existence already proves the
    /// standing hull fits, which is all a grounded run-up needs.
    pub(super) fn measure_runway_along(&self, is_solid: &impl Fn(Vec3) -> bool, lip: Vec3, psi: f32) -> (f32, f32) {
        let (s, c) = psi.to_radians().sin_cos();
        let back = Vec3::new(-c, -s, 0.0);
        let side = Vec3::new(-s, c, 0.0); // left of travel
        let (mut z, mut len) = (lip.z, 0.0);
        let mut clearance = f32::INFINITY;
        let mut prev = lip;
        while len < SIDE_RUNUP_CAP {
            let probe = lip + back * (len + GRID);
            let Some(cid) = self.nearest_within(Vec3::new(probe.x, probe.y, z), GRID * 0.75, STEP_HEIGHT) else {
                break;
            };
            let c0 = self.cells[cid as usize].origin;
            // Cells existing at both ends does **not** mean the bot can run between them: the carve
            // only says each column's centre is standable, and a pillar, wall corner or doorway jamb
            // can sit squarely on the line joining them. `path_clear` samples the hull-inflated clip
            // oracle along the segment — the same test the walk links are built with. Without it a
            // "run-up" is free to pass straight through geometry, and the bot finds out at 440 ups
            // with every reactive edge guard disabled, which reads as fumbling into the scenery.
            if !path_clear_with(is_solid, prev, c0) {
                break;
            }
            // The run-up must be **level**, not merely connected. `prestrafe_delivered` integrates
            // ground friction and accel on flat floor; down a slope the bot is part-falling and never
            // builds what the model credits, and up one it loses more than the model charges. A single
            // step per column is fine, a staircase is not — dm3 has a link whose "run-up" descends 40u
            // to its lip, where the bot arrives falling and simply hops in place at the edge.
            if (c0.z - lip.z).abs() > STEP_HEIGHT {
                break;
            }
            // Lateral room either side of this step, in whole columns out to the weave's reach — and
            // the same distinction applies: the weave needs somewhere to *go*, so the sideways span
            // must be walkable, not merely have floor at its far end. A run-up threading between
            // pillars has cells left and right of every column and is still a place the serpentine
            // puts the bot into a wall.
            let mut lat = 0.0f32;
            for i in 1..=2 {
                let off = GRID * i as f32;
                let open = |d: Vec3| {
                    self.nearest_within(c0 + d, GRID * 0.75, STEP_HEIGHT).is_some()
                        && path_clear_with(is_solid, c0, c0 + d)
                };
                if !(open(side * off) && open(-side * off)) {
                    break;
                }
                lat = off;
            }
            clearance = clearance.min(lat);
            len += GRID;
            z = c0.z;
            prev = c0;
        }
        (len, if len > 0.0 { clearance } else { 0.0 })
    }

    /// The **side jumps** leaving ledge cell `ledge`: short-run-up leaps whose run-up crosses the ledge
    /// rather than running at the gap. See the module note in [`physics`](super::physics) — this pass is
    /// target-first where [`Self::solve_curl_jumps_from`] is corridor-first, which is what lets it find a
    /// run-up perpendicular to the leap. Everything after discovery is the curl machinery: the ground
    /// prestrafe funds the takeoff speed and a `pm_step` rollout certifies the arc, so a candidate is
    /// only emitted if the simulated bot actually lands on the target cell across the whole envelope.
    fn solve_side_jumps_from(
        &self,
        bsp: &Bsp,
        ledge: CellId,
        params: SpeedJumpParams,
        k: f32,
        out: &mut Vec<(Link, SpeedJumpTraversal)>,
    ) {
        let a = self.cells[ledge as usize];
        if bsp.is_liquid_at(a.origin) {
            return; // submerged takeoff: the jump input swims
        }
        let p = PmParams {
            gravity: params.gravity,
            accel: params.accel,
            friction: params.friction,
            stopspeed: params.stopspeed,
            maxspeed: params.maxspeed,
        };
        let fly_cap = CURL_MAX_TICKS as f32 * CURL_DT;
        if jump_airtime(0.0, params.gravity) > fly_cap {
            return; // low-gravity server: a flat hop outlives the rollout budget
        }
        // Cheap gate before anything else: this only makes sense off an edge.
        if !COMPASS
            .iter()
            .any(|&(dgx, dgy)| !self.has_ground_near(a.gx + dgx.signum(), a.gy + dgy.signum(), a.origin.z))
        {
            return;
        }
        let v_cap = prestrafe_delivered(
            SIDE_RUNUP_CAP,
            params.accel,
            params.maxspeed,
            params.friction,
            params.stopspeed,
        );
        let reach_cap = v_cap * jump_airtime(-SIDE_MAX_DROP, params.gravity).min(fly_cap);
        let scan = ((reach_cap / GRID).ceil() as i32).max(1);
        // Run-up length and lateral clearance depend only on (ledge, heading) — never on the target —
        // so measure each heading **once** here rather than per target inside the sweep. Headings are
        // quantized to a `SIDE_PSI_STEP` compass grid; the ≤ half-step offset from a target's exact
        // chord is well inside the ±`CURL_PSI_TOL` guard every certify already proves, and the psi that
        // gets certified is the one that was measured. Without this hoist the pass re-walks the same
        // mesh lines once per (target × sample) and costs ~5× more to build.
        let n_head = (360.0 / SIDE_PSI_STEP).round() as usize;
        let headings: Vec<(f32, f32, f32)> = (0..n_head)
            .map(|i| {
                let psi = i as f32 * SIDE_PSI_STEP;
                let (len, clr) = self.measure_runway_along(&|p| bsp.is_solid(p), a.origin, psi);
                (psi, len, clr)
            })
            .collect();
        if headings.iter().all(|&(_, len, _)| len < SIDE_MIN_RUNWAY) {
            return; // nowhere on this ledge has room to build speed
        }
        // Per-octant straight-pass reach, also target-independent.
        let straight_v_max: Vec<f32> = COMPASS
            .iter()
            .map(|&(dgx, dgy)| {
                let rw = self.measure_runway(bsp, &a, dgx, dgy);
                SPEED_JUMP_V_CAP.min(BHOP_EFF * attainable_speed(MAX_SPEED, rw, k))
            })
            .collect();
        // Two stages, and the split is what makes the pass affordable. **Stage 1** is pure geometry:
        // survey every target in the scan disc and keep only the widest few per (octant, elevation
        // band) — the same bucketing `find_jumps` uses, and the same reason. **Stage 2** pays for
        // rollouts, and only for those survivors. Certifying every target instead costs ~5× more and
        // buys nothing: within one bucket the candidates are near-substitutes, so all but the winner
        // would be thrown away by the dedup at the end anyway.
        let mut buckets: HashMap<(usize, usize), Vec<(f32, CellId)>> = HashMap::new();
        for to in self.neighbors_within(a.gx, a.gy, scan) {
            if to == ledge {
                continue;
            }
            let b = self.cells[to as usize];
            let dz = b.origin.z - a.origin.z;
            let chord = b.origin.xy() - a.origin.xy();
            let horiz = chord.length();
            if !(-SIDE_MAX_DROP..=JUMP_APEX).contains(&dz) || horiz <= JUMP_REACH {
                continue;
            }
            if self.has_direct_link(ledge, to) {
                continue;
            }
            // The leap must actually leave a lip: no walkable column one step along the chord.
            let (sx, sy) = (chord.x.signum() as i32, chord.y.signum() as i32);
            if self.has_ground_near(a.gx + sx, a.gy + sy, a.origin.z) {
                continue;
            }
            // ...and it must actually *cross* something. A target reachable by walking the same floor
            // is not a shortcut, and paying two BSP rollouts to discover that is what makes this pass
            // expensive: on a big map the 600u scan disc around every ledge is overwhelmingly
            // same-floor. Sampling the chord's interior for ground at the takeoff level prunes those
            // for one hash lookup each, and is the definition of the thing we're generating.
            let crosses_gap = [0.34f32, 0.5, 0.66].iter().any(|&f| {
                let m = a.origin.xy() + chord * f;
                !self.has_ground_near((m.x / GRID).round() as i32, (m.y / GRID).round() as i32, a.origin.z)
            });
            if !crosses_gap {
                continue;
            }
            let airtime = jump_airtime(dz, params.gravity);
            if airtime <= 0.0 || airtime > fly_cap {
                continue;
            }
            // Nothing here can reach further than the best run-up on this ledge could throw it.
            if v_cap * airtime < horiz {
                continue;
            }
            // Leave alone what the straight pass can own on its own runway — its links are cheaper and
            // need no rollout. (`has_direct_link` can't see those: a straight link's `from` is the
            // runway start, not this ledge.)
            let steps = ((horiz / 24.0).ceil() as i32).max(8);
            let arc_ok = arc_clear_peak(bsp, a.origin, b.origin, JUMP_APEX, steps);
            let oct = COMPASS.iter().position(|&(dx, dy)| dx == sx && dy == sy);
            let v_max_straight = oct.map_or(0.0, |i| straight_v_max[i]);
            if arc_ok && v_required(horiz, dz, params.gravity) * SJ_MARGIN <= v_max_straight {
                continue;
            }
            let key = (dir_bucket(b.gx - a.gx, b.gy - a.gy), jump_elev_band(dz));
            let slot = buckets.entry(key).or_default();
            slot.push((horiz, to));
            slot.sort_by(|x, y| y.0.total_cmp(&x.0).then(x.1.cmp(&y.1)));
            slot.truncate(SIDE_BUCKET_TRIES);
        }

        // Stage 2: certify. Only the bucket survivors reach a rollout.
        let mut cands: Vec<(f32, Link, SpeedJumpTraversal)> = Vec::new();
        let mut keys: Vec<(usize, usize)> = buckets.keys().copied().collect();
        keys.sort_unstable(); // deterministic order across builds
        for key in keys {
            for (horiz, to) in buckets[&key].clone() {
                let b = self.cells[to as usize];
                let dz = b.origin.z - a.origin.z;
                let chord = b.origin.xy() - a.origin.xy();
                let airtime = jump_airtime(dz, params.gravity);
                // Takeoff headings, swept out from the chord: the gentlest curl that works wins, and a
                // near-chord heading is both likelier to certify and cheaper to fly. Samples come from
                // the per-ledge table, so a heading with no run-up costs nothing to reject here.
                let chord_yaw = yaw_of(chord);
                let mut psi_samples = vec![0.0f32];
                let mut d = SIDE_PSI_STEP;
                while d <= SIDE_PSI_MAX {
                    psi_samples.push(-d);
                    psi_samples.push(d);
                    d += SIDE_PSI_STEP;
                }
                let mut certified = false;
                for dpsi in psi_samples {
                    let slot = ((chord_yaw + dpsi) / SIDE_PSI_STEP).round().rem_euclid(n_head as f32) as usize;
                    let (psi, runway, clearance) = headings[slot];
                    if runway < SIDE_MIN_RUNWAY {
                        continue; // the cheap table lookup — what keeps this sweep affordable
                    }
                    let runup_len = runway.min(SIDE_RUNUP_CAP);
                    let v_del = prestrafe_delivered(
                        runup_len,
                        params.accel,
                        params.maxspeed,
                        params.friction,
                        params.stopspeed,
                    );
                    if v_del * airtime < horiz * 0.8 {
                        continue; // this heading's run-up cannot fund the chord; skip the rollouts
                    }
                    // Cheap scout before the full envelope, as the corridor pass does.
                    let scout = curl_land_point(bsp, a.origin, b.origin, v_del, psi, 10.0, &p).is_some_and(|land| {
                        (land.xy() - b.origin.xy()).length() <= CURL_MISS_TOL * 2.5
                            && (land.z - b.origin.z).abs() <= CURL_Z_TOL * 2.0
                    });
                    if !scout {
                        continue;
                    }
                    let Some((v_req, gain)) = certify_curl(bsp, a.origin, b.origin, psi, v_del, SIDE_V_FLOOR_FRAC, &p)
                    else {
                        continue;
                    };
                    // Plant the run-up start back along the proven heading, so the runtime flies the
                    // line that was certified. No cell there ⇒ this heading is unusable; try the next.
                    let (s, c) = psi.to_radians().sin_cos();
                    let from_pt = a.origin - Vec3::new(c, s, 0.0) * runup_len;
                    let Some(start) = self.nearest_within(from_pt, GRID * 1.5, STEP_HEIGHT * 3.0) else {
                        continue;
                    };
                    if start == to || start == ledge || self.has_direct_link(start, to) {
                        continue;
                    }
                    let cost = runup_len / ((MAX_SPEED + v_req) * 0.5) + airtime + CURL_COMMIT;
                    let link = Link {
                        from: start,
                        to,
                        kind: LinkKind::SpeedJump,
                        cost,
                    };
                    let tr = SpeedJumpTraversal {
                        takeoff: a.origin,
                        v_req,
                        airtime,
                        chained: false,
                        curl_gain: gain,
                        // A run-up narrower than the weave's reach gets a capped serpentine; anything
                        // wider keeps the uncapped one, which builds speed fastest.
                        weave_cap: if clearance < SIDE_WEAVE_CLEARANCE {
                            SIDE_WEAVE_NARROW_DEG
                        } else {
                            f32::INFINITY
                        },
                    };
                    cands.push((horiz, link, tr));
                    certified = true;
                    break; // first (nearest-chord) heading that certifies owns this target
                }
                if certified {
                    break; // this bucket is filled; its remaining candidates are near-substitutes
                }
            }
        }
        // Rank by **elevation band first, then width** — not by width alone, and not (as the corridor
        // pass does) by shortest.
        //
        // A side jump exists to be a *shortcut*, so within a band the wider one is the better link: the
        // short hops off a ledge are exactly the ones something else already covers. But width alone
        // ranks a 428u plunge into the pit above the 278u crossing to the far balcony, and the plunge is
        // near worthless — a Drop link already gets the bot down there, while nothing but this link
        // crosses the gap. That is the failure `jump_elev_band` was written for ("a short descending
        // jump into the pit under a gap shadows the level jump *across* it, and the pit floor doesn't
        // lead back up to that ledge"), so band the same way and let the level crossing outrank the
        // drop. One candidate per (octant, band); ties break on target id for a deterministic splice.
        let mut best: HashMap<(usize, usize), (f32, Link, SpeedJumpTraversal)> = HashMap::new();
        for (horiz, link, tr) in cands {
            let b = self.cells[link.to as usize];
            let key = (
                dir_bucket(b.gx - a.gx, b.gy - a.gy),
                jump_elev_band(b.origin.z - a.origin.z),
            );
            match best.get(&key) {
                Some((h, _, _)) if *h >= horiz => {}
                _ => {
                    best.insert(key, (horiz, link, tr));
                }
            }
        }
        let mut kept: Vec<((usize, usize), (f32, Link, SpeedJumpTraversal))> = best.into_iter().collect();
        kept.sort_by(|x, y| {
            (y.0 .1)
                .cmp(&x.0 .1) // higher elevation band (level/rising) first
                .then((y.1).0.total_cmp(&(x.1).0)) // then the wider gap
                .then((x.1).1.to.cmp(&(y.1).1.to))
        });
        kept.truncate(SIDE_MAX_PER_CELL);
        out.extend(kept.into_iter().map(|(_, (_, l, t))| (l, t)));
    }
}

/// What a curl probe saw. Every field is an *answer to a question the harness asked*, which is why
/// they're named rather than positional: a bare `(f32, Option<(f32, f32)>, Vec<(f32, Vec3)>)` needs
/// this comment read before it can be indexed at all.
pub struct CurlProbe {
    /// The takeoff speed the run-up actually delivers.
    pub v_deliver: f32,
    /// The certified envelope, if one lands: the gentlest gain that works, and the low corner of the
    /// speed envelope — what the runtime must at least deliver. `None` when nothing certifies, which
    /// is the case the harness is usually asking about.
    pub certified: Option<(f32, f32)>,
    /// Where the centre corner lands, per gain tried. The miss distances are the *why* behind a
    /// `certified: None`.
    pub landings: Vec<(f32, Vec3)>,
}

impl NavGraph {
    /// Debug probe (harness): from `takeoff` along `psi0` (degrees) with the speed a `runway` delivers,
    /// report the predicted takeoff speed, whether the full envelope certifies, and per-gain the
    /// center-corner landing point — so the harness can see *why* a curl candidate is/ isn't emitted.
    pub fn curl_probe(
        &self,
        bsp: &Bsp,
        takeoff: Vec3,
        target: Vec3,
        psi0: f32,
        runway: f32,
        params: SpeedJumpParams,
    ) -> CurlProbe {
        let p = PmParams {
            gravity: params.gravity,
            accel: params.accel,
            friction: params.friction,
            stopspeed: params.stopspeed,
            maxspeed: params.maxspeed,
        };
        let v_deliver = prestrafe_delivered(runway, params.accel, params.maxspeed, params.friction, params.stopspeed);
        CurlProbe {
            v_deliver,
            // The probe answers "could anything certify here?", so it uses the widest ladder floor any
            // pass uses (the side pass's) rather than the corridor pass's stricter 1.0.
            certified: certify_curl(bsp, takeoff, target, psi0, v_deliver, SIDE_V_FLOOR_FRAC, &p),
            landings: CURL_GAINS
                .iter()
                .map(|&gain| {
                    (
                        gain,
                        curl_land_point(bsp, takeoff, target, v_deliver, psi0, gain, &p).unwrap_or(Vec3::ZERO),
                    )
                })
                .collect(),
        }
    }
}

/// Roll a curl and return the landing origin (or `None` if it never touched down after the leap) — the
/// probe variant of [`curl_lands`], without the accept tolerances.
fn curl_land_point(bsp: &Bsp, takeoff: Vec3, target: Vec3, v0: f32, psi: f32, gain: f32, p: &PmParams) -> Option<Vec3> {
    let dt = CURL_DT;
    let amax = air_accel_max(p.accel, p.maxspeed, dt);
    let (s0, c0) = psi.to_radians().sin_cos();
    let mut s = PmState {
        origin: takeoff,
        vel: Vec3::new(v0 * c0, v0 * s0, 0.0),
        on_ground: true,
        jump_held: false,
    };
    let mut held = 0.0f32;
    for tick in 0..CURL_MAX_TICKS {
        let cmd = if tick == 0 {
            Cmd {
                view_yaw: psi,
                forward: MOVE_SPEED,
                side: 0.0,
                jump: true,
            }
        } else {
            let v_xy = s.vel.xy();
            // Same latched sweep as `curl_lands` / the runtime — the scout must fly the policy it is
            // scouting for, or it green-lights arcs the real controller never flies.
            let bearing = yaw_of(target.xy() - s.origin.xy());
            if held == 0.0 {
                let e = wrap180(bearing - yaw_of(v_xy));
                held = if e == 0.0 { 1.0 } else { e.signum() };
            }
            let st = air_correct_held(v_xy, bearing, amax, dt, gain, held);
            Cmd {
                view_yaw: st.view_yaw,
                forward: st.forward,
                side: st.side,
                jump: false,
            }
        };
        pm_step(bsp, &mut s, &cmd, p, dt);
        if tick > 3 && s.on_ground {
            return Some(s.origin);
        }
    }
    None
}

/// Certify a curl from `takeoff` onto `target`: the run-up delivers ~`v_deliver` ups along `psi0` (the
/// corridor heading, degrees); find the gentlest [`CURL_GAINS`] gain whose `air_correct` arc lands the
/// target cell across the whole delivered-speed × launch-heading envelope. Returns `(v_req, gain)` —
/// `v_req` the envelope's low corner (what the runtime must at least deliver) — or `None`.
///
/// `v_floor_frac` scales where the speed ladder *starts*, as a fraction of the straight-chord
/// ballistic need. The corridor pass passes `1.0` (the chord speed is a true floor for a near-collinear
/// leap). The side pass passes [`SIDE_V_FLOOR_FRAC`], because a strongly curled flight keeps
/// air-accelerating and can land a target the straight-line formula calls unreachable — the
/// `dm3_rlstrafejump` demo leaves at 399 ups where its chord demands 411.6 and arrives at 425. Only the
/// ladder's starting point moves; the rollout still decides what actually certifies.
fn certify_curl(
    bsp: &Bsp,
    takeoff: Vec3,
    target: Vec3,
    psi0: f32,
    v_deliver: f32,
    v_floor_frac: f32,
    p: &PmParams,
) -> Option<(f32, f32)> {
    let (s0, c0) = psi0.to_radians().sin_cos();
    // The runtime leaps on crossing the takeoff *line*, up to a lip-reach *before* this point (the frame
    // progress < LIP_REACH, at ~6u/tick), so every corner is proven from both leap points.
    let early = takeoff - Vec3::new(c0, s0, 0.0) * CURL_LIP_REACH;
    // Solve the takeoff *speed*. Certifying only at what the run-up maxes out to (the ~484 prestrafe
    // equilibrium, 327u of flat reach) makes every moderate gap uncertifiable — it overshoots. A human
    // holds a controlled speed instead (396-416 across the recorded demos), so scan a ladder from the
    // ballistic floor up to what the run-up can deliver and take the *lowest* speed whose whole envelope
    // lands; the runtime's takeoff regime then holds exactly this (see `bhop`'s hold band).
    let horiz = (target.xy() - takeoff.xy()).length();
    let dz = target.z - takeoff.z;
    let v_floor = v_required(horiz, dz, p.gravity) * v_floor_frac;
    let v_ceil = v_deliver * CURL_V_LO_FRAC;
    if !v_floor.is_finite() || v_floor > v_ceil {
        return None;
    }
    let steps = (((v_ceil - v_floor) / CURL_V_STEP).ceil() as i32).clamp(1, 24);
    for i in 0..=steps {
        let v = (v_floor + i as f32 * CURL_V_STEP).min(v_ceil);
        // Cheap scout at this speed before the full envelope (keeps rejected candidates ~1 rollout each).
        let scout = curl_land_point(bsp, takeoff, target, v, psi0, 10.0, p).is_some_and(|land| {
            (land.xy() - target.xy()).length() <= CURL_MISS_TOL * 2.5 && (land.z - target.z).abs() <= CURL_Z_TOL * 2.0
        });
        if !scout {
            continue;
        }
        // Envelope: both leap points × the speed band the runtime holds × a ±heading guard.
        let (lo, hi) = (v * (1.0 - CURL_V_HOLD_TOL), v * (1.0 + CURL_V_HOLD_TOL));
        let corners = [
            (takeoff, hi, 0.0),
            (takeoff, lo, 0.0),
            (early, hi, 0.0),
            (early, lo, 0.0),
            (takeoff, v, CURL_PSI_TOL),
            (early, v, -CURL_PSI_TOL),
        ];
        for &gain in &CURL_GAINS {
            if corners
                .iter()
                .all(|&(tk, v0, dp)| curl_lands(bsp, tk, target, v0, psi0 + dp, gain, p))
            {
                return Some((v, gain)); // v* — the runtime holds this
            }
        }
    }
    None
}

/// Roll one curl and test whether it lands on the target cell: `pm_step` from `takeoff` seeded at
/// (`v0`, `psi` degrees), leap on tick 0, then per-tick `air_correct` toward the target at `gain` — the
/// exact runtime air policy. Accepts the first touchdown after the leap that resolves to the target
/// within tolerance; rejects a heading that crosses the target bearing mid-flight (an overshoot the
/// held-sign air-strafe diverges from) or an arc that falls well below / flies past the target.
fn curl_lands(bsp: &Bsp, takeoff: Vec3, target: Vec3, v0: f32, psi: f32, gain: f32, p: &PmParams) -> bool {
    let dt = CURL_DT;
    let amax = air_accel_max(p.accel, p.maxspeed, dt);
    let (s0, c0) = psi.to_radians().sin_cos();
    let mut s = PmState {
        origin: takeoff,
        vel: Vec3::new(v0 * c0, v0 * s0, 0.0),
        on_ground: true,
        jump_held: false,
    };
    let mut prev_sign = 0.0f32;
    for tick in 0..CURL_MAX_TICKS {
        let cmd = if tick == 0 {
            Cmd {
                view_yaw: psi,
                forward: MOVE_SPEED,
                side: 0.0,
                jump: true,
            }
        } else {
            let v_xy = s.vel.xy();
            let bearing = yaw_of(target.xy() - s.origin.xy());
            let err = wrap180(bearing - yaw_of(v_xy));
            // A mid-flight bearing-sign flip is a real overshoot the runtime would diverge from — but
            // once abeam of a target it's about to land on, the bearing swings fast and flips benignly,
            // so only treat it as divergence while still well short of the target.
            // Latch the strafe side on the first airborne tick and hold it, exactly as the runtime
            // does (and as the human in `dm3_rlstrafejump` does — one continuous sweep, never
            // reversed). The divergence veto below is now about the *arc*, not the controller: if the
            // heading still crosses the bearing while far from the target, this candidate wants a turn
            // the held sweep can't take back.
            if prev_sign == 0.0 {
                prev_sign = if err == 0.0 { 1.0 } else { err.signum() };
            }
            let far = (s.origin.xy() - target.xy()).length() > CURL_MISS_TOL * 1.5;
            if far && err != 0.0 && err.signum() != prev_sign && err.abs() > 2.0 {
                return false;
            }
            let st = air_correct_held(v_xy, bearing, amax, dt, gain, prev_sign);
            Cmd {
                view_yaw: st.view_yaw,
                forward: st.forward,
                side: st.side,
                jump: false,
            }
        };
        pm_step(bsp, &mut s, &cmd, p, dt);
        if s.vel.z < 0.0 && s.origin.z < target.z - 100.0 {
            return false; // fell past the target's level — undershoot
        }
        if tick > 3 && s.on_ground {
            return (s.origin.xy() - target.xy()).length() <= CURL_MISS_TOL
                && (s.origin.z - target.z).abs() <= CURL_Z_TOL;
        }
    }
    false
}
