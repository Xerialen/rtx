// SPDX-License-Identifier: AGPL-3.0-or-later

//! Expressing a desired movement as the command that actually produces it.
//!
//! Everything upstream of here decides movement in world space: "carry 600 units per second along
//! that lane", "swim up and left toward the surface". The engine accepts none of that. A usercmd
//! carries three scalars — `forwardmove`, `sidemove`, `upmove` — which QuakeWorld's pmove combines
//! with the player's *view* basis to build a wish velocity. This module is the one place that
//! converts between the two, and getting it exactly right turns out to matter more than it sounds.
//!
//! The bot used to do it with two dot products, `forward·want` and `right·want`, and drop the third
//! component. Three separate defects came out of that, and they look unrelated until you write the
//! transform down:
//!
//! * **It cannot swim.** `PM_WaterMove` builds `wishvel` from the *unflattened* view basis and adds
//!   `upmove` to world z. With `upmove` hardwired to zero and the wish always horizontal, a bot in
//!   water has no way to ask to go up or down. It drowns next to a ladder of open water because
//!   nothing it can say means "up".
//! * **Looking away costs it speed.** On land pmove flattens and renormalises the basis, so the
//!   delivered direction is right — but `forward·want` on the *unflattened* vector is short by
//!   `cos(pitch)`. A bot glancing 40° down at a pickup asks for 77% of the move it wanted. This is
//!   worst exactly when it matters: in a fight, where the view is aimed at an enemy rather than
//!   along the path.
//! * **It silently mixes the two.** Off-axis pitch leaks the intended horizontal move into the
//!   vertical term and vice versa, so the error is not even a clean scaling.
//!
//! The fix is not a correction factor, it is to *solve the equation the engine is going to apply*.
//! Given the basis pmove will use, find the command whose wish velocity is the one we asked for.
//! Then movement is exactly decoupled from aim by construction — which is what lets a bot hold its
//! line through a fight instead of drifting whenever it turns to shoot.

use glam::{Vec2, Vec3};

use crate::math::angle_vectors;

/// A usercmd's movement axes, in units per second.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Wish {
    pub forward: f32,
    pub side: f32,
    pub up: f32,
}

/// Which basis the engine will interpret the command in this frame.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Regime {
    /// `PM_AirMove` / `PM_GroundMove`: the view basis is flattened to the horizontal plane and
    /// renormalised, and `upmove` is ignored entirely.
    Ground,
    /// `PM_WaterMove` (waterlevel >= 2): the full 3D view basis, plus `upmove` added straight to
    /// world z. Pitch is part of the transform here rather than being projected away.
    Water,
}

/// The command that makes the engine produce `want` (a world-space velocity) under `view`.
///
/// Exact wherever the transform is invertible, which is everywhere except looking within a few
/// degrees of straight up or down — see [`solve`]'s degenerate branch.
pub fn express(view: Vec3, want: Vec3, regime: Regime) -> Wish {
    let (f, r, _) = angle_vectors(view);
    match regime {
        // Ground: pmove uses `normalize(forward.xy)` and `normalize(right.xy)`, so the honest
        // decomposition is against *those*, not the pitched vectors. Yaw alone determines them, so
        // the result is independent of where the bot is looking vertically — which is the point.
        Regime::Ground => {
            let (fh, rh) = (f.truncate().normalize_or_zero(), r.truncate().normalize_or_zero());
            Wish {
                forward: want.truncate().dot(fh),
                side: want.truncate().dot(rh),
                up: 0.0,
            }
        }
        // Water: solve `f*fm + r*sm + z*um = want`. `right` has no z component at zero roll, so the
        // horizontal pair falls out of a 2x2 and the vertical term absorbs whatever pitch put into
        // `forward`.
        Regime::Water => solve(f, r, want),
    }
}

/// Solve `f*forward + r*side + (0,0,up) = want` for the three axes.
fn solve(f: Vec3, r: Vec3, want: Vec3) -> Wish {
    let (fh, rh) = (f.truncate(), r.truncate());
    // det = fh x rh. With QuakeWorld's basis at zero roll this is exactly -cos(pitch), so it only
    // vanishes when the view is straight up or down.
    let det = fh.x * rh.y - fh.y * rh.x;
    if det.abs() < DEGENERATE_DET {
        // Looking (near enough) straight up or down: `forward` has no horizontal component to steer
        // with, so the horizontal wish has to come from `side` alone and the rest is vertical. Not a
        // fudge — with a vertical view there genuinely is no command that both moves horizontally in
        // an arbitrary direction and leaves the vertical untouched.
        let side = want.truncate().dot(rh.normalize_or_zero());
        return Wish {
            forward: 0.0,
            side,
            up: want.z,
        };
    }
    let w = want.truncate();
    let forward = (w.x * rh.y - w.y * rh.x) / det;
    let side = (fh.x * w.y - fh.y * w.x) / det;
    Wish {
        forward,
        side,
        // Whatever pitch tilted `forward` into the vertical is already accounted for here, so the
        // caller's requested z is delivered rather than approached.
        up: want.z - f.z * forward,
    }
}

/// Below this the horizontal 2x2 is not usefully invertible — |det| is `cos(pitch)`, so this is a
/// view within about half a degree of vertical.
const DEGENERATE_DET: f32 = 0.01;

/// Clamp a wish to what a usercmd can carry, preserving its *direction*.
///
/// Scaling all three axes by one factor rather than clamping each keeps the movement pointed where
/// it was aimed. Clamping them independently bends the wish toward the axis that saturated, which on
/// a diagonal is a real heading error and exactly the kind of quiet inaccuracy this module exists to
/// remove.
pub fn clamp(w: Wish, limit: f32) -> Wish {
    let peak = w.forward.abs().max(w.side.abs()).max(w.up.abs());
    if peak <= limit || peak <= 0.0 {
        return w;
    }
    let k = limit / peak;
    Wish {
        forward: w.forward * k,
        side: w.side * k,
        up: w.up * k,
    }
}

/// What the engine will actually produce from `w` — the inverse of [`express`], for tests and for
/// anything that wants to check its own command before sending it.
#[cfg_attr(not(test), allow(dead_code))]
pub fn realize(view: Vec3, w: Wish, regime: Regime) -> Vec3 {
    let (f, r, _) = angle_vectors(view);
    match regime {
        Regime::Ground => {
            let (fh, rh) = (f.truncate().normalize_or_zero(), r.truncate().normalize_or_zero());
            let v: Vec2 = fh * w.forward + rh * w.side;
            Vec3::new(v.x, v.y, 0.0)
        }
        Regime::Water => f * w.forward + r * w.side + Vec3::new(0.0, 0.0, w.up),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn close(a: Vec3, b: Vec3, tol: f32) -> bool {
        (a - b).length() <= tol
    }

    /// The property the whole module exists for: whatever the bot asks for is what the engine
    /// produces, at any view angle. Round-trip over a spread of pitches and yaws.
    #[test]
    fn a_wish_round_trips_through_any_view() {
        let want_ground = Vec3::new(220.0, -140.0, 0.0);
        let want_water = Vec3::new(150.0, 90.0, -180.0);
        for pitch in [-70.0, -40.0, -5.0, 0.0, 12.0, 45.0, 80.0] {
            for yaw in [-170.0, -90.0, -33.0, 0.0, 47.0, 120.0, 179.0] {
                let view = Vec3::new(pitch, yaw, 0.0);

                let g = realize(view, express(view, want_ground, Regime::Ground), Regime::Ground);
                assert!(close(g, want_ground, 0.05), "ground {pitch}/{yaw}: {g:?}");

                let w = realize(view, express(view, want_water, Regime::Water), Regime::Water);
                assert!(close(w, want_water, 0.05), "water {pitch}/{yaw}: {w:?}");
            }
        }
    }

    /// The bug this replaces: projecting onto the *unflattened* forward loses `cos(pitch)` of the
    /// move. A bot glancing down at a pickup, or up at an enemy on a ledge, quietly walks slower.
    #[test]
    fn looking_away_no_longer_costs_speed() {
        let want = Vec3::new(320.0, 0.0, 0.0);
        let level = express(Vec3::new(0.0, 0.0, 0.0), want, Regime::Ground);
        for pitch in [-60.0, -30.0, 30.0, 60.0] {
            let view = Vec3::new(pitch, 0.0, 0.0);
            let w = express(view, want, Regime::Ground);
            assert!(
                (w.forward - level.forward).abs() < 0.05,
                "pitch {pitch} changed the move: {} vs {}",
                w.forward,
                level.forward
            );
            // What the old two-dot-product form would have produced, for the record.
            let (f, _, _) = angle_vectors(view);
            let old = f.dot(want);
            assert!(
                old < level.forward - 1.0,
                "the old projection should be short at pitch {pitch}: {old}"
            );
        }
    }

    /// Swimming is the capability that was simply missing: a purely vertical wish has to come out as
    /// a purely vertical command, with no horizontal movement smuggled in.
    #[test]
    fn straight_up_is_expressible_underwater() {
        for pitch in [-45.0, 0.0, 45.0] {
            let view = Vec3::new(pitch, 30.0, 0.0);
            let w = express(view, Vec3::new(0.0, 0.0, 200.0), Regime::Water);
            let got = realize(view, w, Regime::Water);
            assert!(close(got, Vec3::new(0.0, 0.0, 200.0), 0.05), "pitch {pitch}: {got:?}");
            assert!(w.up.abs() > 1.0, "nothing asked for vertical at pitch {pitch}");
        }
    }

    /// Looking straight down has no horizontal `forward` to steer with. The solve must degrade to
    /// something sane rather than dividing by a vanishing determinant.
    #[test]
    fn a_vertical_view_degrades_without_exploding() {
        for pitch in [-90.0, 89.9, -89.95] {
            let view = Vec3::new(pitch, 15.0, 0.0);
            let w = express(view, Vec3::new(100.0, 60.0, -50.0), Regime::Water);
            assert!(
                w.forward.is_finite() && w.side.is_finite() && w.up.is_finite(),
                "pitch {pitch} produced {w:?}"
            );
            assert!(w.forward.abs() < 1e3 && w.side.abs() < 1e3 && w.up.abs() < 1e3, "{w:?}");
        }
    }

    /// Clamping keeps the direction. Independent per-axis clamping would bend a diagonal toward
    /// whichever axis saturated first.
    #[test]
    fn clamping_preserves_direction() {
        let w = Wish {
            forward: 800.0,
            side: 400.0,
            up: -200.0,
        };
        let c = clamp(w, 400.0);
        assert!((c.forward - 400.0).abs() < 1e-3);
        assert!((c.side - 200.0).abs() < 1e-3);
        assert!((c.up + 100.0).abs() < 1e-3);
        // Ratios intact.
        assert!((c.forward / c.side - w.forward / w.side).abs() < 1e-4);
        // And a wish already inside the limit is untouched.
        assert_eq!(clamp(c, 400.0), c);
    }
}
